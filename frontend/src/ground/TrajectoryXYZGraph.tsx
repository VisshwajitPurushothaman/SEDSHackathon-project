import { useMemo, useState } from 'react';

export interface TrajectoryXYZPoint {
  x: number; // Downrange (m)
  y: number; // Altitude (m)
  z: number; // Crossrange (m)
  met?: number;
}

interface TrajectoryXYZGraphProps {
  currentX: number;
  currentY: number;
  currentZ: number;
  slantRangeM: number;
  originName: string;
  originLat: number;
  originLon: number;
  computedLat: number;
  computedLon: number;
  history?: TrajectoryXYZPoint[];
}

export function TrajectoryXYZGraph({
  currentX,
  currentY,
  currentZ,
  slantRangeM,
  originName,
  originLat,
  originLon,
  computedLat,
  computedLon,
  history = [],
}: TrajectoryXYZGraphProps) {
  const [viewMode, setViewMode] = useState<'3d' | 'altitude_downrange' | 'ground_track'>('3d');

  // Compute bounding boxes for auto-scaling
  const bounds = useMemo(() => {
    let maxX = Math.max(100, Math.abs(currentX));
    let maxY = Math.max(1000, currentY);
    let maxZ = Math.max(100, Math.abs(currentZ));

    for (const p of history) {
      if (Math.abs(p.x) > maxX) maxX = Math.abs(p.x);
      if (p.y > maxY) maxY = p.y;
      if (Math.abs(p.z) > maxZ) maxZ = Math.abs(p.z);
    }
    return {
      maxX: Math.max(50, maxX * 1.2),
      maxY: Math.max(500, maxY * 1.15),
      maxZ: Math.max(50, maxZ * 1.2),
    };
  }, [currentX, currentY, currentZ, history]);

  // 3D Isometric projection math (angle: 30 deg isometric)
  // X (Downrange / Right-Down), Z (Crossrange / Left-Down), Y (Altitude / Up)
  const project3D = (x: number, y: number, z: number, w: number, h: number) => {
    const cx = w * 0.45;
    const cy = h * 0.72;
    const scaleX = (w * 0.38) / bounds.maxX;
    const scaleY = (h * 0.58) / bounds.maxY;
    const scaleZ = (w * 0.38) / bounds.maxZ;

    // Isometric projection angles: 30°
    const cos30 = Math.cos((30 * Math.PI) / 180);
    const sin30 = Math.sin((30 * Math.PI) / 180);

    const px = cx + (x * scaleX - z * scaleZ) * cos30;
    const py = cy + (x * scaleX + z * scaleZ) * sin30 - y * scaleY;
    const shadowPy = cy + (x * scaleX + z * scaleZ) * sin30; // Ground shadow

    return { px, py, shadowPy };
  };

  // Generate 3D Path data
  const svgWidth = 640;
  const svgHeight = 380;

  const current3D = project3D(currentX, currentY, currentZ, svgWidth, svgHeight);
  const origin3D = project3D(0, 0, 0, svgWidth, svgHeight);

  // Axis end points
  const xAxisEnd = project3D(bounds.maxX * 0.9, 0, 0, svgWidth, svgHeight);
  const yAxisEnd = project3D(0, bounds.maxY * 0.95, 0, svgWidth, svgHeight);
  const zAxisEnd = project3D(0, 0, bounds.maxZ * 0.9, svgWidth, svgHeight);

  // Trajectory points string
  const pointsList = useMemo(() => {
    const pts = history.length > 0 ? history : [{ x: 0, y: 0, z: 0 }, { x: currentX, y: currentY, z: currentZ }];
    if (pts[pts.length - 1].x !== currentX || pts[pts.length - 1].y !== currentY) {
      pts.push({ x: currentX, y: currentY, z: currentZ });
    }
    return pts;
  }, [history, currentX, currentY, currentZ]);

  const path3D = useMemo(() => {
    return pointsList
      .map((p, i) => {
        const { px, py } = project3D(p.x, p.y, p.z, svgWidth, svgHeight);
        return `${i === 0 ? 'M' : 'L'} ${px.toFixed(1)} ${py.toFixed(1)}`;
      })
      .join(' ');
  }, [pointsList, bounds]);

  const groundShadowPath = useMemo(() => {
    return pointsList
      .map((p, i) => {
        const { px, shadowPy } = project3D(p.x, p.y, p.z, svgWidth, svgHeight);
        return `${i === 0 ? 'M' : 'L'} ${px.toFixed(1)} ${shadowPy.toFixed(1)}`;
      })
      .join(' ');
  }, [pointsList, bounds]);

  return (
    <div className="flex h-full flex-col rounded border border-[#222e42] bg-[#080d16] p-3 text-white">
      {/* Top Bar: Selector & High-Precision Telemetry HUD */}
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[#222e42] pb-2">
        <div className="flex items-center gap-2">
          <span className="font-mono text-[13px] font-bold tracking-wide text-[#5cd6ff]">
            📐 HIGH-PRECISION XYZ TRAJECTORY
          </span>
          <span className="rounded bg-[#1a2942] px-2 py-0.5 font-mono text-[10px] text-[#3fd68a] border border-[#3fd68a]/30">
            ORIGIN: PAD [0, 0, 0]
          </span>
        </div>

        {/* View mode toggle */}
        <div className="flex items-center gap-1 rounded bg-[#0c1422] p-0.5 border border-[#222e42]">
          <button
            type="button"
            onClick={() => setViewMode('3d')}
            className={`rounded px-2.5 py-1 text-[11px] font-bold transition-all ${
              viewMode === '3d' ? 'bg-[#1e74ba] text-white shadow' : 'text-muted hover:text-white'
            }`}
          >
            3D Isometric
          </button>
          <button
            type="button"
            onClick={() => setViewMode('altitude_downrange')}
            className={`rounded px-2.5 py-1 text-[11px] font-bold transition-all ${
              viewMode === 'altitude_downrange' ? 'bg-[#1e74ba] text-white shadow' : 'text-muted hover:text-white'
            }`}
          >
            Y vs X (Profile)
          </button>
          <button
            type="button"
            onClick={() => setViewMode('ground_track')}
            className={`rounded px-2.5 py-1 text-[11px] font-bold transition-all ${
              viewMode === 'ground_track' ? 'bg-[#1e74ba] text-white shadow' : 'text-muted hover:text-white'
            }`}
          >
            Z vs X (Ground)
          </button>
        </div>
      </div>

      {/* High-Precision Readout Chips */}
      <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-4 font-mono text-[11.5px]">
        <div className="rounded border border-[#4d1616] bg-[#210909] p-2">
          <span className="text-[10px] text-[#ff5252] font-semibold uppercase">X (Downrange / East)</span>
          <div className="text-[17px] font-bold text-[#ff5252]">
            {currentX >= 0 ? `+${currentX.toFixed(2)}` : currentX.toFixed(2)}{' '}
            <span className="text-[10px] text-muted font-normal">m</span>
          </div>
        </div>

        <div className="rounded border border-[#30234a] bg-[#140e24] p-2">
          <span className="text-[10px] text-[#b388ff] font-semibold uppercase">Y (Altitude / Up)</span>
          <div className="text-[17px] font-bold text-[#b388ff]">
            {currentY.toFixed(2)}{' '}
            <span className="text-[10px] text-muted font-normal">m</span>
          </div>
        </div>

        <div className="rounded border border-[#0a3845] bg-[#041a21] p-2">
          <span className="text-[10px] text-[#00e5ff] font-semibold uppercase">Z (Crossrange / North)</span>
          <div className="text-[17px] font-bold text-[#00e5ff]">
            {currentZ >= 0 ? `+${currentZ.toFixed(2)}` : currentZ.toFixed(2)}{' '}
            <span className="text-[10px] text-muted font-normal">m</span>
          </div>
        </div>

        <div className="rounded border border-[#0d3b19] bg-[#051c0a] p-2">
          <span className="text-[10px] text-[#00e676] font-semibold uppercase">3D Slant Range</span>
          <div className="text-[17px] font-bold text-[#00e676]">
            {slantRangeM.toFixed(2)}{' '}
            <span className="text-[10px] text-muted font-normal">m</span>
          </div>
        </div>
      </div>

      {/* Geodetic Origin & High-Precision Coordinate Strip */}
      <div className="mt-2 rounded border border-[#222e42] bg-[#0c1422] px-3 py-1.5 font-mono text-[11px] flex flex-wrap items-center justify-between gap-2">
        <div>
          <span className="text-muted">Launch Origin: </span>
          <span className="text-[#f5c542] font-bold">{originName}</span>
          <span className="text-muted ml-2">({originLat.toFixed(6)}° N, {originLon.toFixed(6)}° E)</span>
        </div>
        <div>
          <span className="text-muted">Derived Geodetic Fix: </span>
          <span className="text-[#3fd68a] font-bold">
            {computedLat.toFixed(6)}° N, {computedLon.toFixed(6)}° E
          </span>
          <span className="text-muted ml-1.5">Alt: {Math.round(currentY).toLocaleString()} m</span>
        </div>
      </div>

      {/* Main SVG Visualization Canvas */}
      <div className="relative mt-2 flex-1 min-h-[300px] w-full overflow-hidden rounded border border-[#1a2638] bg-[#04070e]">
        {/* VIEW 1: 3D ISOMETRIC PROJECTION */}
        {viewMode === '3d' && (
          <svg className="h-full w-full select-none" viewBox={`0 0 ${svgWidth} ${svgHeight}`} preserveAspectRatio="xMidYMid meet">
            <defs>
              <linearGradient id="trajGradient" x1="0%" y1="100%" x2="0%" y2="0%">
                <stop offset="0%" stopColor="#00e5ff" stopOpacity="0.4" />
                <stop offset="100%" stopColor="#3fd68a" stopOpacity="1" />
              </linearGradient>
            </defs>

            {/* Grid ground plane */}
            <g opacity="0.25">
              {[-3, -2, -1, 0, 1, 2, 3].map((step) => {
                const p1 = project3D(bounds.maxX * 0.9, 0, (step / 3) * bounds.maxZ * 0.8, svgWidth, svgHeight);
                const p2 = project3D(-bounds.maxX * 0.9, 0, (step / 3) * bounds.maxZ * 0.8, svgWidth, svgHeight);
                return <line key={`gx-${step}`} x1={p1.px} y1={p1.shadowPy} x2={p2.px} y2={p2.shadowPy} stroke="#4a658a" strokeWidth="1" strokeDasharray="3,3" />;
              })}
              {[-3, -2, -1, 0, 1, 2, 3].map((step) => {
                const p1 = project3D((step / 3) * bounds.maxX * 0.8, 0, bounds.maxZ * 0.9, svgWidth, svgHeight);
                const p2 = project3D((step / 3) * bounds.maxX * 0.8, 0, -bounds.maxZ * 0.9, svgWidth, svgHeight);
                return <line key={`gz-${step}`} x1={p1.px} y1={p1.shadowPy} x2={p2.px} y2={p2.shadowPy} stroke="#4a658a" strokeWidth="1" strokeDasharray="3,3" />;
              })}
            </g>

            {/* Coordinate Axes */}
            {/* +X (Downrange / East) */}
            <line x1={origin3D.px} y1={origin3D.py} x2={xAxisEnd.px} y2={xAxisEnd.shadowPy} stroke="#ff5252" strokeWidth="2" />
            <text x={xAxisEnd.px + 8} y={xAxisEnd.shadowPy + 4} fill="#ff5252" fontSize="10" fontFamily="monospace" fontWeight="bold">
              +X (Downrange)
            </text>

            {/* +Z (Crossrange / North) */}
            <line x1={origin3D.px} y1={origin3D.py} x2={zAxisEnd.px} y2={zAxisEnd.shadowPy} stroke="#00e5ff" strokeWidth="2" />
            <text x={zAxisEnd.px - 60} y={zAxisEnd.shadowPy + 12} fill="#00e5ff" fontSize="10" fontFamily="monospace" fontWeight="bold">
              +Z (Crossrange)
            </text>

            {/* +Y (Altitude / Up) */}
            <line x1={origin3D.px} y1={origin3D.py} x2={yAxisEnd.px} y2={yAxisEnd.py} stroke="#3fd68a" strokeWidth="2.5" />
            <text x={yAxisEnd.px - 14} y={yAxisEnd.py - 10} fill="#3fd68a" fontSize="10" fontFamily="monospace" fontWeight="bold">
              +Y (Altitude)
            </text>

            {/* Ground shadow projection */}
            <path d={groundShadowPath} fill="none" stroke="#223b59" strokeWidth="2" strokeDasharray="4,3" />

            {/* Plumb line from current vehicle to ground */}
            <line
              x1={current3D.px}
              y1={current3D.py}
              x2={current3D.px}
              y2={current3D.shadowPy}
              stroke="#f5c542"
              strokeWidth="1.5"
              strokeDasharray="3,3"
              opacity="0.8"
            />
            {/* Ground shadow target dot */}
            <circle cx={current3D.px} cy={current3D.shadowPy} r="3.5" fill="#f5c542" opacity="0.6" />

            {/* Direct Vector from Origin to Vehicle */}
            <line x1={origin3D.px} y1={origin3D.py} x2={current3D.px} y2={current3D.py} stroke="#ffffff" strokeWidth="1" strokeDasharray="2,2" opacity="0.5" />

            {/* Primary 3D Trajectory Curve */}
            <path d={path3D} fill="none" stroke="url(#trajGradient)" strokeWidth="3.5" strokeLinecap="round" strokeLinejoin="round" />

            {/* Launch Pad Origin Base Pin [0, 0, 0] */}
            <circle cx={origin3D.px} cy={origin3D.py} r="5" fill="#f5c542" stroke="#ffffff" strokeWidth="1.5" />
            <text x={origin3D.px - 28} y={origin3D.py + 18} fill="#f5c542" fontSize="10" fontFamily="monospace" fontWeight="bold">
              PAD [0, 0, 0]
            </text>

            {/* Current Vehicle Position Beacon */}
            <circle cx={current3D.px} cy={current3D.py} r="7" fill="#3fd68a" stroke="#ffffff" strokeWidth="2" className="animate-pulse" />
            <circle cx={current3D.px} cy={current3D.py} r="3" fill="#ffffff" />
            <g transform={`translate(${current3D.px + 12}, ${current3D.py - 12})`}>
              <rect x="0" y="0" width="130" height="26" rx="4" fill="#080e18" stroke="#3fd68a" strokeWidth="1" opacity="0.9" />
              <text x="6" y="12" fill="#3fd68a" fontSize="9" fontFamily="monospace" fontWeight="bold">
                ALT: {Math.round(currentY).toLocaleString()} m
              </text>
              <text x="6" y="22" fill="#9cb2cf" fontSize="8" fontFamily="monospace">
                SLANT: {Math.round(slantRangeM).toLocaleString()} m
              </text>
            </g>
          </svg>
        )}

        {/* VIEW 2: 2D ALTITUDE PROFILE (Y vs X) */}
        {viewMode === 'altitude_downrange' && (
          <div className="flex h-full w-full flex-col p-4 font-mono text-[11px]">
            <div className="flex justify-between text-muted text-[10px] border-b border-[#1e2a3d] pb-1">
              <span>Y-Axis: Altitude (0 to {Math.round(bounds.maxY)} m)</span>
              <span>X-Axis: Downrange Distance ({Math.round(-bounds.maxX)} to +{Math.round(bounds.maxX)} m)</span>
            </div>
            <svg className="flex-1 w-full" viewBox="0 0 500 240" preserveAspectRatio="none">
              {/* Origin axes */}
              <line x1="250" y1="20" x2="250" y2="220" stroke="#3fd68a" strokeWidth="1.5" />
              <line x1="20" y1="220" x2="480" y2="220" stroke="#ff5252" strokeWidth="1.5" />

              {/* Trajectory profile curve */}
              {(() => {
                const pts = pointsList.map((p) => {
                  const px = 250 + (p.x / bounds.maxX) * 220;
                  const py = 220 - (p.y / bounds.maxY) * 200;
                  return `${px.toFixed(1)},${py.toFixed(1)}`;
                });
                return <polyline points={pts.join(' ')} fill="none" stroke="#3fd68a" strokeWidth="3" />;
              })()}

              {/* Current position marker */}
              {(() => {
                const cx = 250 + (currentX / bounds.maxX) * 220;
                const cy = 220 - (currentY / bounds.maxY) * 200;
                return <circle cx={cx} cy={cy} r="5" fill="#3fd68a" stroke="#ffffff" strokeWidth="2" />;
              })()}
            </svg>
            <div className="flex justify-between text-[#3fd68a] font-bold text-[11px]">
              <span>Pad Origin (0 m)</span>
              <span>Apogee / Float: {Math.round(currentY).toLocaleString()} m</span>
            </div>
          </div>
        )}

        {/* VIEW 3: 2D GROUND TRACK (Z vs X) */}
        {viewMode === 'ground_track' && (
          <div className="flex h-full w-full flex-col p-4 font-mono text-[11px]">
            <div className="flex justify-between text-muted text-[10px] border-b border-[#1e2a3d] pb-1">
              <span>Z-Axis: Crossrange North-South (m)</span>
              <span>X-Axis: Downrange East-West (m)</span>
            </div>
            <svg className="flex-1 w-full" viewBox="0 0 500 240" preserveAspectRatio="none">
              {/* Center Crosshair (0, 0 Origin) */}
              <line x1="250" y1="20" x2="250" y2="220" stroke="#00e5ff" strokeWidth="1" strokeDasharray="3,3" />
              <line x1="20" y1="120" x2="480" y2="120" stroke="#ff5252" strokeWidth="1" strokeDasharray="3,3" />

              {/* Ground Track Polyline */}
              {(() => {
                const pts = pointsList.map((p) => {
                  const px = 250 + (p.x / bounds.maxX) * 220;
                  const py = 120 - (p.z / bounds.maxZ) * 90;
                  return `${px.toFixed(1)},${py.toFixed(1)}`;
                });
                return <polyline points={pts.join(' ')} fill="none" stroke="#f5c542" strokeWidth="2.5" />;
              })()}

              {/* Pad Pin */}
              <circle cx="250" cy="120" r="4" fill="#f5c542" stroke="#fff" />

              {/* Current Pos */}
              {(() => {
                const cx = 250 + (currentX / bounds.maxX) * 220;
                const cy = 120 - (currentZ / bounds.maxZ) * 90;
                return <circle cx={cx} cy={cy} r="6" fill="#3fd68a" stroke="#fff" strokeWidth="2" />;
              })()}
            </svg>
            <div className="flex justify-between text-[#f5c542] font-bold text-[11px]">
              <span>Launch Site: {originName}</span>
              <span>Displacement: X={currentX.toFixed(1)}m, Z={currentZ.toFixed(1)}m</span>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
