import maplibregl, { type GeoJSONSource, type Map as MlMap } from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import { useEffect, useRef, useState } from 'react';
import { ui } from '../theme/tokens';
import type { Postflight, TrackPoint } from './api';

type FC = GeoJSON.FeatureCollection;
const line = (pts: TrackPoint[] | null | undefined, props: Record<string, unknown> = {}): GeoJSON.Feature[] =>
  pts && pts.length > 1 ? [{ type: 'Feature', properties: props, geometry: { type: 'LineString', coordinates: pts.map((p) => [p.lon, p.lat]) } }] : [];

/** Operator's live track split by status (live vs predicted), so blackout segments stay visibly different. */
function operatorFC(pts: TrackPoint[] | null): FC {
  const features: GeoJSON.Feature[] = [];
  if (!pts) return { type: 'FeatureCollection', features };
  let run: TrackPoint[] = [];
  let status = '';
  const flush = () => run.length > 1 && features.push(...line(run, { status }));
  for (const p of pts) {
    const s = p.status === 'PREDICTED' ? 'PREDICTED' : 'LIVE';
    if (s !== status && run.length) {
      flush();
      run = [run[run.length - 1]];
    }
    status = s;
    run.push(p);
  }
  flush();
  return { type: 'FeatureCollection', features };
}

const DARK_OSM: maplibregl.StyleSpecification = {
  version: 8,
  sources: { osm: { type: 'raster', tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'], tileSize: 256, maxzoom: 18, attribution: '© OpenStreetMap contributors' } },
  layers: [
    { id: 'bg', type: 'background', paint: { 'background-color': ui.bg } },
    { id: 'osm', type: 'raster', source: 'osm', paint: { 'raster-brightness-max': 0.5, 'raster-saturation': -0.6 } },
  ],
};

export default function PostflightMap({ report, reveal }: { report: Postflight; reveal: boolean }) {
  const el = useRef<HTMLDivElement>(null);
  const map = useRef<MlMap | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    if (!el.current) return;
    const m = new maplibregl.Map({ container: el.current, style: DARK_OSM, center: [80.23, 13.72], zoom: 8, attributionControl: { compact: true } });
    m.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right');
    m.on('load', () => {
      for (const id of ['operator', 'recon', 'truth', 'footprint', 'landing']) m.addSource(id, { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
      m.addLayer({ id: 'footprint', type: 'fill', source: 'footprint', paint: { 'fill-color': ui.info, 'fill-opacity': 0.12 } });
      m.addLayer({ id: 'footprint-line', type: 'line', source: 'footprint', paint: { 'line-color': ui.info, 'line-width': 1 } });
      m.addLayer({ id: 'operator-live', type: 'line', source: 'operator', filter: ['==', ['get', 'status'], 'LIVE'], paint: { 'line-color': ui.pass, 'line-width': 2.5, 'line-opacity': 0.9 } });
      m.addLayer({ id: 'operator-pred', type: 'line', source: 'operator', filter: ['==', ['get', 'status'], 'PREDICTED'], paint: { 'line-color': ui.hold, 'line-width': 2.5, 'line-dasharray': [2, 2] } });
      m.addLayer({ id: 'recon', type: 'line', source: 'recon', paint: { 'line-color': ui.info, 'line-width': 1.6 } });
      m.addLayer({ id: 'truth', type: 'line', source: 'truth', paint: { 'line-color': ui.text, 'line-width': 1.2, 'line-dasharray': [1, 1.5] } });
      m.addLayer({ id: 'landing', type: 'circle', source: 'landing', paint: { 'circle-radius': 5, 'circle-color': ['match', ['get', 'kind'], 'truth', ui.text, ui.info], 'circle-stroke-color': ui.bg, 'circle-stroke-width': 1.5 } });
      setReady(true);
    });
    map.current = m;
    return () => {
      m.remove();
      map.current = null;
      setReady(false);
    };
  }, []);

  useEffect(() => {
    const m = map.current;
    if (!ready || !m) return;
    const set = (id: string, data: FC) => (m.getSource(id) as GeoJSONSource | undefined)?.setData(data);
    set('operator', operatorFC(report.operator_track));
    set('recon', { type: 'FeatureCollection', features: line(report.reconstruction) });
    set('truth', { type: 'FeatureCollection', features: reveal ? line(report.truth_track) : [] });
    const fp = report.footprint_at_cutdown?.regions.find((r) => r.level === 90);
    set('footprint', {
      type: 'FeatureCollection',
      features: fp ? [{ type: 'Feature', properties: {}, geometry: { type: 'Polygon', coordinates: [[...fp.polygon.map(([la, lo]) => [lo, la]), [fp.polygon[0][1], fp.polygon[0][0]]]] } }] : [],
    });
    const pts: GeoJSON.Feature[] = [];
    if (report.landing_fix) pts.push({ type: 'Feature', properties: { kind: 'fix' }, geometry: { type: 'Point', coordinates: [report.landing_fix.lon, report.landing_fix.lat] } });
    if (reveal && report.scorecard?.landing) pts.push({ type: 'Feature', properties: { kind: 'truth' }, geometry: { type: 'Point', coordinates: [report.scorecard.landing.truth.lon, report.scorecard.landing.truth.lat] } });
    set('landing', { type: 'FeatureCollection', features: pts });
    const all = [...(report.operator_track ?? []), ...(report.reconstruction ?? [])];
    if (all.length) {
      const b = new maplibregl.LngLatBounds();
      all.forEach((p) => b.extend([p.lon, p.lat]));
      m.fitBounds(b, { padding: 40, duration: 0, maxZoom: 11 });
    }
  }, [ready, report, reveal]);

  return (
    <div className="relative h-full w-full overflow-hidden rounded-panel border border-line">
      <div ref={el} className="h-full w-full" role="region" aria-label="Map: operator, reconstructed and (in grading mode) true tracks" />
      <div className="pointer-events-none absolute left-2 top-2 flex flex-col gap-0.5 rounded-md bg-bg/70 px-2 py-1 text-[10.5px] text-muted backdrop-blur-panel">
        <span><span className="mr-1 inline-block h-0.5 w-3 bg-pass align-middle" />operator saw live</span>
        <span><span className="mr-1 inline-block h-0.5 w-3 border-t border-dashed border-hold align-middle" />operator saw predicted</span>
        <span><span className="mr-1 inline-block h-0.5 w-3 bg-info align-middle" />reconstructed (RTS)</span>
        {reveal && <span><span className="mr-1 inline-block h-0.5 w-3 border-t border-dotted border-ink align-middle" />truth (grading)</span>}
      </div>
    </div>
  );
}
