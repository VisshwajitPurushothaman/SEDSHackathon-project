import { useEffect, useRef, useState } from 'react';
import type { GeoJSONSource, Map as MlMap } from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import { ui } from '../theme/tokens';

export interface Zone {
  name: string;
  kind: string;
  polygon: [number, number][]; // [lat, lon]
}
export interface MapLine {
  id: string;
  coords: [number, number][]; // [lat, lon]
  color: string;
  dashed?: boolean;
  width?: number;
}
export interface MapPoly {
  id: string;
  polygon: [number, number][];
  color: string;
  opacity?: number;
}
export interface MapPoint {
  id: string;
  lat: number;
  lon: number;
  color: string;
  label: string;
}

export interface RangeMapProps {
  zones: Zone[];
  approved?: [number, number][] | null;
  lines?: MapLine[];
  polys?: MapPoly[];
  points?: MapPoint[];
  center: [number, number]; // [lat, lon]
  zoom?: number;
  label?: string;
}

const ZONE_COLOR: Record<string, string> = { restricted: ui.fail, military: ui.hold, no_fly: ui.fail };
const ring = (p: [number, number][]) => [...p.map(([lat, lon]) => [lon, lat]), [p[0][1], p[0][0]]];

function graticule(step = 0.25): GeoJSON.FeatureCollection {
  const f: GeoJSON.Feature[] = [];
  for (let lat = 10; lat <= 17; lat += step) f.push({ type: 'Feature', properties: {}, geometry: { type: 'LineString', coordinates: [[77, lat], [84, lat]] } });
  for (let lon = 77; lon <= 84; lon += step) f.push({ type: 'Feature', properties: {}, geometry: { type: 'LineString', coordinates: [[lon, 10], [lon, 17]] } });
  return { type: 'FeatureCollection', features: f };
}

function collections(p: RangeMapProps) {
  const zones: GeoJSON.FeatureCollection = {
    type: 'FeatureCollection',
    features: [
      ...(p.approved ? [{ type: 'Feature' as const, properties: { color: ui.pass, name: 'approved impact area', fill: 0.04 }, geometry: { type: 'Polygon' as const, coordinates: [ring(p.approved)] } }] : []),
      ...p.zones.map((z) => ({ type: 'Feature' as const, properties: { color: ZONE_COLOR[z.kind] ?? ui.fail, name: z.name, fill: 0.16 }, geometry: { type: 'Polygon' as const, coordinates: [ring(z.polygon)] } })),
    ],
  };
  const polys: GeoJSON.FeatureCollection = {
    type: 'FeatureCollection',
    features: (p.polys ?? []).map((q) => ({ type: 'Feature', properties: { color: q.color, fill: q.opacity ?? 0.15 }, geometry: { type: 'Polygon', coordinates: [ring(q.polygon)] } })),
  };
  const lines: GeoJSON.FeatureCollection = {
    type: 'FeatureCollection',
    features: (p.lines ?? []).filter((l) => l.coords.length > 1).map((l) => ({
      type: 'Feature',
      properties: { color: l.color, dashed: l.dashed ? 1 : 0, width: l.width ?? 2 },
      geometry: { type: 'LineString', coordinates: l.coords.map(([lat, lon]) => [lon, lat]) },
    })),
  };
  const points: GeoJSON.FeatureCollection = {
    type: 'FeatureCollection',
    features: (p.points ?? []).map((q) => ({ type: 'Feature', properties: { color: q.color, label: q.label }, geometry: { type: 'Point', coordinates: [q.lon, q.lat] } })),
  };
  return { zones, polys, lines, points };
}

/** MapLibre map: dimmed OSM raster when reachable, always over a lat/lon graticule (works offline). */
export function RangeMap(props: RangeMapProps) {
  const host = useRef<HTMLDivElement>(null);
  const map = useRef<MlMap | null>(null);
  const [ready, setReady] = useState(false);
  const [tiles, setTiles] = useState<'loading' | 'ok' | 'failed'>('loading');
  const latest = useRef(props);
  latest.current = props;

  useEffect(() => {
    let disposed = false;
    void import('maplibre-gl').then(({ default: maplibregl }) => {
      if (disposed || !host.current) return;
      const m = new maplibregl.Map({
        container: host.current,
        center: [props.center[1], props.center[0]],
        zoom: props.zoom ?? 8,
        attributionControl: { compact: true },
        style: {
          version: 8,
          sources: {
            osm: { type: 'raster', tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'], tileSize: 256, attribution: '© OpenStreetMap contributors' },
            grid: { type: 'geojson', data: graticule() },
          },
          layers: [
            { id: 'bg', type: 'background', paint: { 'background-color': ui.bg } },
            { id: 'osm', type: 'raster', source: 'osm', paint: { 'raster-opacity': 0.35, 'raster-saturation': -0.9, 'raster-brightness-max': 0.55 } },
            { id: 'grid', type: 'line', source: 'grid', paint: { 'line-color': ui.border, 'line-width': 1 } },
          ],
        },
      });
      m.on('error', (e) => {
        if (String((e as { error?: Error }).error?.message ?? '').length && (e as { sourceId?: string }).sourceId === 'osm') setTiles('failed');
      });
      m.on('sourcedata', (e) => {
        if (e.sourceId === 'osm' && e.isSourceLoaded) setTiles((t) => (t === 'failed' ? t : 'ok'));
      });
      m.on('load', () => {
        const c = collections(latest.current);
        for (const [id, data] of Object.entries(c)) m.addSource(id, { type: 'geojson', data });
        m.addLayer({ id: 'zones-fill', type: 'fill', source: 'zones', paint: { 'fill-color': ['get', 'color'], 'fill-opacity': ['get', 'fill'] } });
        m.addLayer({ id: 'zones-line', type: 'line', source: 'zones', paint: { 'line-color': ['get', 'color'], 'line-width': 1.2 } });
        m.addLayer({ id: 'polys-fill', type: 'fill', source: 'polys', paint: { 'fill-color': ['get', 'color'], 'fill-opacity': ['get', 'fill'] } });
        m.addLayer({ id: 'polys-line', type: 'line', source: 'polys', paint: { 'line-color': ['get', 'color'], 'line-width': 1 } });
        m.addLayer({ id: 'lines', type: 'line', source: 'lines', filter: ['==', ['get', 'dashed'], 0], paint: { 'line-color': ['get', 'color'], 'line-width': ['get', 'width'] } });
        m.addLayer({ id: 'lines-dashed', type: 'line', source: 'lines', filter: ['==', ['get', 'dashed'], 1], paint: { 'line-color': ['get', 'color'], 'line-width': ['get', 'width'], 'line-dasharray': [2, 2] } });
        m.addLayer({ id: 'points', type: 'circle', source: 'points', paint: { 'circle-radius': 4.5, 'circle-color': ['get', 'color'], 'circle-stroke-color': ui.bg, 'circle-stroke-width': 1.5 } });
        const popup = new maplibregl.Popup({ closeButton: false, closeOnClick: false, className: 'aerofire-popup' });
        for (const layer of ['points', 'zones-fill']) {
          m.on('mousemove', layer, (e) => {
            const f = e.features?.[0];
            if (!f) return;
            popup.setLngLat(e.lngLat).setText(String(f.properties?.label ?? f.properties?.name ?? '')).addTo(m);
          });
          m.on('mouseleave', layer, () => popup.remove());
        }
        setReady(true);
      });
      map.current = m;
    });
    return () => {
      disposed = true;
      map.current?.remove();
      map.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const m = map.current;
    if (!m || !ready) return;
    const c = collections(props);
    for (const [id, data] of Object.entries(c)) (m.getSource(id) as GeoJSONSource | undefined)?.setData(data);
  }, [props, ready]);

  return (
    <div className="relative h-full min-h-[260px] w-full overflow-hidden rounded-panel border border-line">
      <div ref={host} className="absolute inset-0" />
      <div className="pointer-events-none absolute left-2 top-2 flex flex-col gap-1">
        {props.label && <span className="tag tag-stale bg-bg/80">{props.label}</span>}
        {tiles === 'failed' && <span className="tag tag-stale bg-bg/80">base map unavailable: lat/lon grid only</span>}
      </div>
    </div>
  );
}
