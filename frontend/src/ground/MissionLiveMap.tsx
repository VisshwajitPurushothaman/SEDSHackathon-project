import { useEffect, useRef, useState } from 'react';
import type { Map as MlMap } from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';

interface MissionLiveMapProps {
  vehicleLat: number;
  vehicleLon: number;
  vehicleAltM: number;
  yawDeg: number;
  homeLat: number; // launch site (config)
  homeLon: number;
  slantRangeM?: number;
  flightPhase?: string;
  historyTrail?: [number, number][]; // [[lat, lon], ...]
}

export function MissionLiveMap({
  vehicleLat,
  vehicleLon,
  vehicleAltM,
  yawDeg,
  homeLat,
  homeLon,
  slantRangeM,
  flightPhase = 'PRELAUNCH',
  historyTrail = [],
}: MissionLiveMapProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MlMap | null>(null);
  const [autoPan, setAutoPan] = useState(true);
  const [zoomLevel, setZoomLevel] = useState<number>(6);
  const [mapLoaded, setMapLoaded] = useState(false);

  // Normalize longitude to [-180, 180]
  const normLon = (lon: number) => ((((lon + 180) % 360) + 360) % 360) - 180;
  const curLat = vehicleLat || homeLat;
  const curLon = normLon(vehicleLon || homeLon);
  const hLat = homeLat;
  const hLon = normLon(homeLon);

  // Calculate Distance to Launch Pad / Home in meters if not provided
  const distToHomeM = slantRangeM ?? (() => {
    const R = 6371000;
    const dLat = ((curLat - hLat) * Math.PI) / 180;
    const dLon = ((curLon - hLon) * Math.PI) / 180;
    const a =
      Math.sin(dLat / 2) * Math.sin(dLat / 2) +
      Math.cos((hLat * Math.PI) / 180) *
        Math.cos((curLat * Math.PI) / 180) *
        Math.sin(dLon / 2) *
        Math.sin(dLon / 2);
    const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
    return Math.round(R * c);
  })();

  // Initialize MapLibre Map
  useEffect(() => {
    let disposed = false;
    void import('maplibre-gl').then(({ default: maplibregl }) => {
      if (disposed || !containerRef.current) return;

      const m = new maplibregl.Map({
        container: containerRef.current,
        center: [curLon, curLat],
        zoom: 6,
        attributionControl: false,
        style: {
          version: 8,
          sources: {
            osm: {
              type: 'raster',
              tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
              tileSize: 256,
              attribution: '© OpenStreetMap contributors',
            },
            trackSource: {
              type: 'geojson',
              data: {
                type: 'FeatureCollection',
                features: [],
              },
            },
          },
          layers: [
            {
              id: 'osm-layer',
              type: 'raster',
              source: 'osm',
              paint: {
                'raster-opacity': 0.85,
                'raster-saturation': -0.3,
                'raster-contrast': 0.1,
              },
            },
            {
              id: 'track-line',
              type: 'line',
              source: 'trackSource',
              paint: {
                'line-color': '#ffcc00',
                'line-width': 3,
                'line-opacity': 0.9,
              },
            },
            {
              id: 'direct-line',
              type: 'line',
              source: 'trackSource',
              filter: ['==', '$type', 'LineString'],
              paint: {
                'line-color': '#ff3333',
                'line-width': 1.5,
                'line-dasharray': [2, 2],
              },
            },
          ],
        },
      });

      mapRef.current = m;

      m.on('load', () => {
        if (!disposed) setMapLoaded(true);
      });

      m.on('zoom', () => {
        setZoomLevel(Math.round(m.getZoom()));
      });
    });

    return () => {
      disposed = true;
      if (mapRef.current) {
        mapRef.current.remove();
        mapRef.current = null;
      }
    };
  }, []);

  // Update Track and Markers dynamically
  useEffect(() => {
    const m = mapRef.current;
    if (!m || !mapLoaded) return;

    // Build features for track, home line, and points
    const features: GeoJSON.Feature[] = [];

    // History track line
    const coords = (historyTrail.length > 0 ? historyTrail : [[curLat, curLon]]).map(([lat, lon]) => [
      normLon(lon),
      lat,
    ]);
    if (coords.length > 1) {
      features.push({
        type: 'Feature',
        geometry: {
          type: 'LineString',
          coordinates: coords,
        },
        properties: {},
      });
    }

    // Direct line from Home to Vehicle
    features.push({
      type: 'Feature',
      geometry: {
        type: 'LineString',
        coordinates: [
          [hLon, hLat],
          [curLon, curLat],
        ],
      },
      properties: {},
    });

    const src = m.getSource('trackSource') as maplibregl.GeoJSONSource | undefined;
    if (src) {
      src.setData({
        type: 'FeatureCollection',
        features,
      });
    }

    if (autoPan) {
      m.easeTo({
        center: [curLon, curLat],
        duration: 300,
      });
    }
  }, [curLat, curLon, hLat, hLon, historyTrail, mapLoaded, autoPan]);

  const handleZoomChange = (delta: number) => {
    if (!mapRef.current) return;
    const next = Math.max(1, Math.min(18, mapRef.current.getZoom() + delta));
    mapRef.current.zoomTo(next);
  };

  const handleRecenter = () => {
    if (!mapRef.current) return;
    mapRef.current.flyTo({ center: [curLon, curLat], zoom: 7 });
  };

  return (
    <div className="relative h-full w-full select-none overflow-hidden rounded border border-[#2a3649] bg-[#0c1422] font-mono shadow-inner">
      {/* MapLibre DOM Host */}
      <div ref={containerRef} className="h-full w-full" />

      {/* ---------------- 1. MISSION PLANNER DIST TO HOME BADGE (TOP-RIGHT) ---------------- */}
      <div className="absolute top-2 right-12 z-20 pointer-events-none">
        <div className="rounded border border-[#1e74ba] bg-white/95 px-3 py-1 shadow-md">
          <span className="font-mono text-[12px] font-extrabold text-[#004b99] tracking-wider">
            Dist to Home: {distToHomeM.toLocaleString()} m
          </span>
        </div>
      </div>

      {/* ---------------- 2. TOP-LEFT OVERLAY (ALTITUDE & VELOCITY) ---------------- */}
      <div className="absolute top-2 left-2 z-20 pointer-events-none">
        <div className="rounded border border-black/50 bg-black/75 px-2.5 py-1 text-[11px] text-white shadow">
          <div>
            <span className="text-muted">Alt: </span>
            <span className="font-bold text-[#3fd68a]">{Math.round(vehicleAltM).toLocaleString()} m</span>
          </div>
          <div>
            <span className="text-muted">Phase: </span>
            <span className="font-bold text-[#5cd6ff]">{flightPhase}</span>
          </div>
        </div>
      </div>

      {/* ---------------- 3. VERTICAL ZOOM SLIDER & BUTTONS (RIGHT) ---------------- */}
      <div className="absolute right-2 top-10 bottom-10 z-20 flex flex-col items-center justify-between rounded border border-white/20 bg-black/70 px-1 py-2 shadow">
        <button
          type="button"
          onClick={() => handleZoomChange(1)}
          className="flex h-6 w-6 items-center justify-center rounded bg-panel2 text-white hover:bg-line text-sm font-bold"
          title="Zoom In"
        >
          +
        </button>

        <div className="my-2 h-36 flex flex-col items-center justify-center">
          <input
            type="range"
            min="2"
            max="16"
            value={zoomLevel}
            onChange={(e) => {
              const z = Number(e.target.value);
              setZoomLevel(z);
              mapRef.current?.zoomTo(z);
            }}
            className="h-32 -rotate-90 cursor-pointer accent-[#1e74ba]"
          />
        </div>

        <button
          type="button"
          onClick={() => handleZoomChange(-1)}
          className="flex h-6 w-6 items-center justify-center rounded bg-panel2 text-white hover:bg-line text-sm font-bold"
          title="Zoom Out"
        >
          −
        </button>
      </div>

      {/* ---------------- 4. VEHICLE PIN & HEADING RETICLE (CENTER SVG OVERLAY) ---------------- */}
      {/* Dynamic icon anchored to center if autoPan is enabled */}
      <div className="absolute inset-0 pointer-events-none z-10 flex items-center justify-center">
        {/* Launch / Balloon / Rocket Icon rotated by yaw */}
        <div
          className="flex items-center justify-center transition-transform duration-100"
          style={{ transform: `rotate(${yawDeg}deg)` }}
        >
          <svg className="h-10 w-10 drop-shadow-[0_2px_8px_rgba(0,0,0,0.8)]" viewBox="-20 -20 40 40">
            {/* Heading vector line */}
            <line x1="0" y1="0" x2="0" y2="-28" stroke="#ff3333" strokeWidth="2.5" strokeDasharray="3,2" />
            <polygon points="0,-32 -4,-24 4,-24" fill="#ff3333" />
            {/* Center Vehicle Rocket / Plane Symbol */}
            <circle cx="0" cy="0" r="12" fill="#1e74ba" stroke="#ffffff" strokeWidth="2" opacity="0.9" />
            <polygon points="0,-10 6,8 0,4 -6,8" fill="#ffcc00" stroke="#000000" strokeWidth="0.8" />
          </svg>
        </div>
      </div>

      {/* ---------------- 5. BOTTOM STATUS BAR (MISSION PLANNER STYLE) ---------------- */}
      <div className="absolute bottom-0 left-0 right-0 z-20 flex flex-wrap items-center justify-between border-t border-[#2a3649] bg-black/85 px-3 py-1 text-[11px] text-white">
        <div className="flex items-center gap-4">
          <span className="text-white/80">
            hdop: <span className="font-bold text-white">0.8</span>
          </span>
          <span className="text-white/80">
            Sats: <span className="font-bold text-[#3fd68a]">14</span>
          </span>
          <span className="text-[#ff4d4d] font-bold">
            Heading: {Math.round(yawDeg)}°
          </span>
          <span className="text-[#ffcc00] font-semibold">
            ● GPS Track
          </span>
          <span className="text-[#5cd6ff]">
            Pos: {curLat.toFixed(4)}°, {curLon.toFixed(4)}°
          </span>
        </div>

        <div className="flex items-center gap-3">
          <label className="flex items-center gap-1 cursor-pointer">
            <input
              type="checkbox"
              checked={autoPan}
              onChange={(e) => setAutoPan(e.target.checked)}
              className="accent-[#1e74ba]"
            />
            <span className="text-white text-[11px]">Auto Pan</span>
          </label>
          <button
            type="button"
            onClick={handleRecenter}
            className="rounded border border-white/30 bg-panel px-2 py-0.5 text-[10.5px] hover:border-white"
          >
            Center Vehicle
          </button>
        </div>
      </div>
    </div>
  );
}
