import maplibregl, { type GeoJSONSource, type Map as MlMap } from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import { useEffect, useRef, useState } from 'react';
import { ui } from '../theme/tokens';
import type { GroundFrame, Region } from './api';
import { useFlight } from './store';

type FC = GeoJSON.FeatureCollection;
const empty = (): FC => ({ type: 'FeatureCollection', features: [] });
const ring = (poly: [number, number][]) => {
  const r = poly.map(([lat, lon]) => [lon, lat]);
  return [...r, r[0]];
};
const regionsFC = (regions: Region[] | undefined, kind: string): GeoJSON.Feature[] =>
  (regions ?? []).map((r) => ({ type: 'Feature', properties: { level: r.level, kind }, geometry: { type: 'Polygon', coordinates: [ring(r.polygon)] } }));

/** Track segments, split wherever the status changes, so each piece is coloured by its status. */
function trackFC(frames: GroundFrame[], cursor: number): FC {
  const features: GeoJSON.Feature[] = [];
  type Run = { status: string; coords: number[][] };
  let cur = null as Run | null;
  for (let i = 0; i <= cursor && i < frames.length; i++) {
    const E = frames[i].estimates;
    if (!E.lat || typeof E.lat.value !== 'number') continue;
    const status = E.lat.status === 'PREDICTED' ? 'PREDICTED' : 'LIVE';
    const pt = [E.lon.value as number, E.lat.value as number];
    if (!cur || cur.status !== status) {
      const prev: Run | null = cur;
      const prevLast: number[] | undefined = prev ? prev.coords[prev.coords.length - 1] : undefined;
      cur = { status, coords: prevLast ? [prevLast, pt] : [pt] };
      features.push({ type: 'Feature', properties: { status }, geometry: { type: 'LineString', coordinates: cur.coords } });
    } else {
      cur.coords.push(pt);
    }
  }
  return { type: 'FeatureCollection', features };
}

const DARK_OSM: maplibregl.StyleSpecification = {
  version: 8,
  sources: {
    osm: {
      type: 'raster',
      tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
      tileSize: 256,
      maxzoom: 18,
      attribution: '© OpenStreetMap contributors',
    },
  },
  layers: [
    { id: 'bg', type: 'background', paint: { 'background-color': ui.bg } },
    { id: 'osm', type: 'raster', source: 'osm', paint: { 'raster-brightness-max': 0.5, 'raster-saturation': -0.6, 'raster-contrast': 0.05 } },
  ],
};

/** Map: track, prediction regions, landing footprint, approved impact area, IIP markers. */
export default function MapPanel({ frame }: { frame: GroundFrame | null }) {
  const el = useRef<HTMLDivElement>(null);
  const map = useRef<MlMap | null>(null);
  const [ready, setReady] = useState(false);
  const [follow, setFollow] = useState(true);
  const config = useFlight((s) => s.config);
  const frames = useFlight((s) => s.frames);
  const cursor = useFlight((s) => s.cursor);

  useEffect(() => {
    if (!el.current || !config) return;
    const m = new maplibregl.Map({
      container: el.current,
      style: DARK_OSM,
      center: [config.site.lon_deg, config.site.lat_deg],
      zoom: 8,
      attributionControl: { compact: true },
    });
    m.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right');
    m.on('load', () => {
      const src = (id: string, data: FC = empty()) => m.addSource(id, { type: 'geojson', data });
      src('approved', {
        type: 'FeatureCollection',
        features: [{ type: 'Feature', properties: {}, geometry: { type: 'Polygon', coordinates: [ring(config.gates.approved_area)] } }],
      });
      ['track', 'regions', 'footprint', 'iip', 'vehicle', 'site', 'lastfix'].forEach((id) => src(id));
      m.addLayer({ id: 'approved-fill', type: 'fill', source: 'approved', paint: { 'fill-color': ui.pass, 'fill-opacity': 0.05 } });
      m.addLayer({ id: 'approved-line', type: 'line', source: 'approved', paint: { 'line-color': ui.pass, 'line-width': 1.2, 'line-dasharray': [3, 2] } });
      m.addLayer({
        id: 'footprint-fill', type: 'fill', source: 'footprint',
        paint: { 'fill-color': ui.info, 'fill-opacity': ['match', ['get', 'level'], 50, 0.25, 90, 0.14, 0.07] },
      });
      m.addLayer({ id: 'footprint-line', type: 'line', source: 'footprint', paint: { 'line-color': ui.info, 'line-width': 1, 'line-opacity': 0.8 } });
      m.addLayer({
        id: 'regions-fill', type: 'fill', source: 'regions',
        paint: { 'fill-color': ui.hold, 'fill-opacity': ['match', ['get', 'level'], 50, 0.3, 90, 0.16, 0.08] },
      });
      m.addLayer({ id: 'regions-line', type: 'line', source: 'regions', paint: { 'line-color': ui.hold, 'line-width': 1 } });
      // line-dasharray cannot be data-driven: one solid layer for live/reconstructed, one dashed for predicted.
      m.addLayer({
        id: 'track', type: 'line', source: 'track', filter: ['!=', ['get', 'status'], 'PREDICTED'],
        paint: { 'line-color': ['match', ['get', 'status'], 'RECONSTRUCTED', ui.info, ui.pass], 'line-width': 2.2 },
      });
      m.addLayer({
        id: 'track-predicted', type: 'line', source: 'track', filter: ['==', ['get', 'status'], 'PREDICTED'],
        paint: { 'line-color': ui.hold, 'line-width': 2.2, 'line-dasharray': [2, 2] },
      });
      m.addLayer({
        id: 'iip', type: 'circle', source: 'iip',
        paint: { 'circle-radius': 5, 'circle-color': ['case', ['get', 'inside'], ui.pass, ui.fail], 'circle-stroke-color': ui.bg, 'circle-stroke-width': 1.5 },
      });
      // Impact-point names on hover (a DOM popup: symbol labels would need an external glyph server).
      const popup = new maplibregl.Popup({ closeButton: false, closeOnClick: false, className: 'aerofire-popup' });
      m.on('mouseenter', 'iip', (e) => {
        const f = e.features?.[0];
        if (!f) return;
        m.getCanvas().style.cursor = 'pointer';
        const p = f.properties as { case: string; inside: boolean };
        popup.setLngLat(e.lngLat).setText(`${p.case}: ${p.inside ? 'inside' : 'OUTSIDE'} approved area (illustrative model)`).addTo(m);
      });
      m.on('mouseleave', 'iip', () => {
        m.getCanvas().style.cursor = '';
        popup.remove();
      });
      m.addLayer({ id: 'site', type: 'circle', source: 'site', paint: { 'circle-radius': 5, 'circle-color': ui.text, 'circle-stroke-color': ui.bg, 'circle-stroke-width': 2 } });
      m.addLayer({ id: 'lastfix', type: 'circle', source: 'lastfix', paint: { 'circle-radius': 4, 'circle-color': 'transparent', 'circle-stroke-color': ui.pass, 'circle-stroke-width': 2 } });
      m.addLayer({
        id: 'vehicle', type: 'circle', source: 'vehicle',
        paint: { 'circle-radius': 6, 'circle-color': ['match', ['get', 'status'], 'PREDICTED', ui.hold, ui.armed], 'circle-stroke-color': ui.bg, 'circle-stroke-width': 2 },
      });
      (m.getSource('site') as GeoJSONSource).setData({
        type: 'FeatureCollection',
        features: [{ type: 'Feature', properties: {}, geometry: { type: 'Point', coordinates: [config.site.lon_deg, config.site.lat_deg] } }],
      });
      const b = new maplibregl.LngLatBounds();
      config.gates.approved_area.forEach(([lat, lon]) => b.extend([lon, lat]));
      b.extend([config.site.lon_deg, config.site.lat_deg]);
      m.fitBounds(b, { padding: 30, duration: 0 });
      setReady(true);
    });
    map.current = m;
    return () => {
      m.remove();
      map.current = null;
      setReady(false);
    };
  }, [config]);

  // Update the data layers (at most ~4 Hz: frames arrive faster than a map needs).
  const lastUpdate = useRef(0);
  useEffect(() => {
    const m = map.current;
    if (!ready || !m || !frame) return;
    const now = performance.now();
    if (now - lastUpdate.current < 250 && frame !== frames[frames.length - 1]) return;
    lastUpdate.current = now;
    const E = frame.estimates;
    const set = (id: string, data: FC) => (m.getSource(id) as GeoJSONSource | undefined)?.setData(data);
    set('track', trackFC(frames, cursor));
    set('regions', { type: 'FeatureCollection', features: regionsFC(E.prediction?.regions, 'prediction') });
    set('footprint', { type: 'FeatureCollection', features: regionsFC(frame.landing_footprint?.regions, 'footprint') });
    set('iip', {
      type: 'FeatureCollection',
      features: (frame.impact_points ?? []).map((p) => ({ type: 'Feature', properties: { case: p.case, inside: p.inside }, geometry: { type: 'Point', coordinates: [p.lon, p.lat] } })),
    });
    if (E.lat && typeof E.lat.value === 'number') {
      const pos = [E.lon.value as number, E.lat.value as number];
      set('vehicle', { type: 'FeatureCollection', features: [{ type: 'Feature', properties: { status: E.lat.status }, geometry: { type: 'Point', coordinates: pos } }] });
      if (follow) m.easeTo({ center: pos as [number, number], duration: 300 });
    }
    // Last live fix marker while predicting.
    if (frame.predicting) {
      for (let i = cursor; i >= 0; i--) {
        const e = frames[i].estimates;
        if (e.lat && e.lat.status !== 'PREDICTED' && typeof e.lat.value === 'number') {
          set('lastfix', { type: 'FeatureCollection', features: [{ type: 'Feature', properties: {}, geometry: { type: 'Point', coordinates: [e.lon.value as number, e.lat.value as number] } }] });
          break;
        }
      }
    } else {
      set('lastfix', empty());
    }
  }, [ready, frame, frames, cursor, follow]);

  return (
    <div className="relative h-full w-full overflow-hidden rounded-panel border border-line">
      <div ref={el} className="h-full w-full" aria-label="Map: track, prediction regions, landing footprint and approved impact area" role="region" />
      <div className="pointer-events-none absolute left-2 top-2 flex flex-col gap-0.5 rounded-md bg-bg/70 px-2 py-1 text-[10.5px] text-muted backdrop-blur-panel">
        <span><span className="mr-1 inline-block h-0.5 w-3 bg-pass align-middle" />track (measured/fused)</span>
        <span><span className="mr-1 inline-block h-0.5 w-3 bg-hold align-middle" />predicted (blackout) · 50/90/99 %</span>
        <span><span className="mr-1 inline-block h-2 w-3 bg-info/40 align-middle" />landing footprint</span>
        <span><span className="mr-1 inline-block h-0.5 w-3 border-t border-dashed border-pass align-middle" />approved impact area (illustrative)</span>
      </div>
      <button type="button" className="btn absolute bottom-2 left-2 bg-bg/70 px-2 py-0.5 text-[11px]" onClick={() => setFollow(!follow)} aria-pressed={follow}>
        Follow {follow ? 'on' : 'off'}
      </button>
      {!config && <div className="absolute inset-0 grid place-items-center text-xs text-muted">Map needs the backend config.</div>}
    </div>
  );
}
