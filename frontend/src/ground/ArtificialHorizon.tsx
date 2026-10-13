import { useMemo } from 'react';

/** Non-finite (missing) values render as ---, never as a number. */
const num = (v: number, d: number) => (Number.isFinite(v) ? v.toFixed(d) : '---');

interface ArtificialHorizonProps {
  rollDeg: number;       // Roll angle in degrees (-180 to 180)
  pitchDeg: number;      // Pitch angle in degrees (-90 to 90)
  yawDeg: number;        // Heading / Yaw in degrees (0 to 360)
  altitudeM: number;     // Altitude in meters
  speedMps: number;      // Speed in m/s
  verticalSpeedMps: number;
  armedStatus?: string;  // e.g. "ARMED", "DISARMED", "BALLOON ASCENT"
  gpsStatus?: string;    // e.g. "GPS: 3D FIX", "GPS: NOMINAL"
  flightPhase?: string;  // e.g. "TROPOSPHERIC_ASCENT", "PRELAUNCH"
}

export function ArtificialHorizon({
  rollDeg = 0,
  pitchDeg = 0,
  yawDeg = 0,
  altitudeM = 0,
  speedMps = 0,
  verticalSpeedMps = 0,
  armedStatus,
  gpsStatus = 'GPS: 3D FIX (NOMINAL)',
  flightPhase,
}: ArtificialHorizonProps) {
  // Clamp pitch to +/- 85 degrees for rendering
  const clampedPitch = Math.max(-85, Math.min(85, pitchDeg));
  const normalizedYaw = ((yawDeg % 360) + 360) % 360;

  // Pitch ladder spacing: 6 pixels per degree
  const pitchOffset = clampedPitch * 6;

  // Heading tape markers: 0 to 360 with 15-deg steps
  const headingTape = useMemo(() => {
    const items: { deg: number; label: string }[] = [];
    for (let d = -180; d <= 540; d += 15) {
      const norm = ((d % 360) + 360) % 360;
      let label = String(norm);
      if (norm === 0) label = 'N';
      else if (norm === 45) label = 'NE';
      else if (norm === 90) label = 'E';
      else if (norm === 135) label = 'SE';
      else if (norm === 180) label = 'S';
      else if (norm === 225) label = 'SW';
      else if (norm === 270) label = 'W';
      else if (norm === 315) label = 'NW';
      items.push({ deg: d, label });
    }
    return items;
  }, []);

  // Compute armed color and label
  const isArmed = armedStatus?.toUpperCase().includes('ARM') && !armedStatus?.toUpperCase().includes('DISARM');
  const statusLabel = armedStatus ?? (flightPhase ? `${flightPhase}` : 'DISARMED');

  return (
    <div className="relative h-full min-h-[340px] w-full select-none overflow-hidden rounded border border-[#2a3649] bg-[#0c1422] font-mono shadow-inner">
      {/* ---------------- 1. TOP COMPASS HEADING TAPE ---------------- */}
      <div className="relative h-8 w-full border-b border-[#2a3649] bg-[#080d16] overflow-hidden">
        {/* Horizontal Scrolling Tape */}
        <div
          className="absolute top-0 flex h-full items-center transition-transform duration-100 ease-out"
          style={{
            left: '50%',
            transform: `translateX(-${normalizedYaw * 4}px)`,
          }}
        >
          {headingTape.map((h, i) => (
            <div
              key={i}
              className="absolute flex flex-col items-center justify-between"
              style={{
                left: `${h.deg * 4}px`,
                width: '40px',
                marginLeft: '-20px',
              }}
            >
              <span
                className={`text-[10px] font-bold ${
                  ['N', 'E', 'S', 'W'].includes(h.label)
                    ? 'text-[#5cd6ff]'
                    : ['NE', 'SE', 'SW', 'NW'].includes(h.label)
                    ? 'text-[#f5c542]'
                    : 'text-[#9cb2cf]'
                }`}
              >
                {h.label}
              </span>
              <div
                className={`w-[1.5px] bg-[#9cb2cf] ${
                  ['N', 'E', 'S', 'W', 'NE', 'SE', 'SW', 'NW'].includes(h.label) ? 'h-3 bg-white' : 'h-1.5'
                }`}
              />
            </div>
          ))}
        </div>

        {/* Center Pointer / Current Heading Box */}
        <div className="absolute top-0 left-1/2 -translate-x-1/2 flex flex-col items-center pointer-events-none z-20">
          <div className="rounded-b border border-[#f5c542]/80 bg-[#000000]/90 px-2 py-0.5 shadow">
            <span className="font-mono text-[11px] font-extrabold text-[#f5c542] leading-none">
              {Math.round(normalizedYaw).toString().padStart(3, '0')}°
            </span>
          </div>
          <div className="h-0 w-0 border-x-4 border-x-transparent border-t-4 border-t-[#f5c542]" />
        </div>
      </div>

      {/* ---------------- 2. PRIMARY ATTITUDE HORIZON SPHERE ---------------- */}
      <div className="relative h-[calc(100%-32px)] w-full overflow-hidden">
        {/* Rotating Sky / Ground Disk */}
        <div
          className="absolute -top-[120%] -left-[120%] h-[340%] w-[340%] origin-center transition-transform duration-75 ease-out"
          style={{
            transform: `rotate(${-rollDeg}deg) translateY(${pitchOffset}px)`,
          }}
        >
          {/* Sky (Upper Half: Aviation Blue) */}
          <div className="absolute top-0 left-0 h-1/2 w-full bg-gradient-to-t from-[#2074ba] to-[#124d80]" />

          {/* Ground (Lower Half: Mission Planner Olive Green / Earth) */}
          <div className="absolute bottom-0 left-0 h-1/2 w-full bg-gradient-to-b from-[#6b8529] to-[#455717]" />

          {/* Horizon Line (White) */}
          <div className="absolute top-1/2 left-0 h-[2px] w-full -translate-y-1/2 bg-white shadow-sm" />

          {/* Pitch Ladder Marks */}
          <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none">
            {[-60, -50, -40, -30, -25, -20, -15, -10, -5, 5, 10, 15, 20, 25, 30, 40, 50, 60].map((deg) => (
              <div
                key={deg}
                className="absolute flex items-center justify-center"
                style={{ top: `calc(50% - ${deg * 6}px)` }}
              >
                {/* Left number */}
                <span className="mr-2 text-[10px] font-bold text-white drop-shadow">
                  {Math.abs(deg)}
                </span>
                {/* Horizontal rung with small vertical end ticks */}
                <div
                  className={`flex items-center justify-between border-t-2 border-white ${
                    deg % 10 === 0 ? 'w-24' : 'w-12'
                  }`}
                >
                  <div className={`w-[2px] bg-white ${deg > 0 ? 'h-2 -mt-2' : 'h-2 mt-0'}`} />
                  <div className={`w-[2px] bg-white ${deg > 0 ? 'h-2 -mt-2' : 'h-2 mt-0'}`} />
                </div>
                {/* Right number */}
                <span className="ml-2 text-[10px] font-bold text-white drop-shadow">
                  {Math.abs(deg)}
                </span>
              </div>
            ))}
          </div>
        </div>

        {/* ---------------- 3. ROLL SCALE ARC & INDICATOR ---------------- */}
        <div className="absolute inset-0 pointer-events-none z-10 flex items-center justify-center">
          <svg className="h-[280px] w-[280px]" viewBox="-140 -140 280 280">
            {/* Roll Scale Arc */}
            <path
              d="M -110 0 A 110 110 0 0 1 110 0"
              fill="none"
              stroke="rgba(255, 255, 255, 0.4)"
              strokeWidth="1.5"
            />
            {/* Ticks at 0, 10, 20, 30, 45, 60 degrees */}
            {[0, 10, 20, 30, 45, 60, -10, -20, -30, -45, -60].map((a) => {
              const rad = ((a - 90) * Math.PI) / 180;
              const r1 = 110;
              const r2 = Math.abs(a) === 45 || Math.abs(a) === 30 || a === 0 ? 122 : 116;
              const x1 = r1 * Math.cos(rad);
              const y1 = r1 * Math.sin(rad);
              const x2 = r2 * Math.cos(rad);
              const y2 = r2 * Math.sin(rad);
              return (
                <g key={a}>
                  <line x1={x1} y1={y1} x2={x2} y2={y2} stroke="white" strokeWidth={a === 0 ? '2.5' : '1.5'} />
                  {Math.abs(a) > 0 && Math.abs(a) <= 60 && (
                    <text
                      x={(r2 + 8) * Math.cos(rad)}
                      y={(r2 + 8) * Math.sin(rad)}
                      fill="rgba(255, 255, 255, 0.8)"
                      fontSize="9"
                      fontWeight="bold"
                      textAnchor="middle"
                      dominantBaseline="central"
                    >
                      {Math.abs(a)}
                    </text>
                  )}
                </g>
              );
            })}

            {/* Zero Roll Index (Fixed top triangle) */}
            <polygon points="0,-108 -5,-118 5,-118" fill="#f5c542" />

            {/* Moving Roll Pointer Triangle (rotates with rollDeg) */}
            <g transform={`rotate(${-rollDeg})`}>
              <polygon points="0,-109 -6,-99 6,-99" fill="#ff4d4d" stroke="#ffffff" strokeWidth="0.8" />
            </g>
          </svg>
        </div>

        {/* ---------------- 4. FIXED AIRCRAFT / VEHICLE RETICLE ---------------- */}
        {/* Mission Planner-style red inverted-V center reticle with horizontal wing tabs */}
        <div className="absolute inset-0 pointer-events-none z-20 flex items-center justify-center">
          {/* Left Wing Bar */}
          <div className="absolute h-1.5 w-12 -translate-x-16 bg-[#ff3333] border border-black shadow" />
          {/* Right Wing Bar */}
          <div className="absolute h-1.5 w-12 translate-x-16 bg-[#ff3333] border border-black shadow" />
          {/* Center Red Inverted-V Reticle */}
          <svg className="h-10 w-16" viewBox="-32 -20 64 40">
            <polyline
              points="-24,12 0,-4 24,12"
              fill="none"
              stroke="#ff2222"
              strokeWidth="4"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
            <circle cx="0" cy="-4" r="2.5" fill="#ffffff" />
          </svg>
        </div>

        {/* ---------------- 5. LEFT SPEED / ASCENT RATE TAPE ---------------- */}
        <div className="absolute left-1 top-6 bottom-8 w-14 border border-white/20 bg-black/60 z-20 flex flex-col justify-between py-1 text-[10.5px]">
          <div className="px-1 text-[9px] uppercase tracking-wider text-muted font-bold">SPD m/s</div>
          <div className="flex flex-col items-center gap-2">
            <span className="text-white/60">{num(speedMps + 20, 0)}</span>
            <span className="text-white/60">{num(speedMps + 10, 0)}</span>
            {/* Center Box */}
            <div className="w-full bg-[#f5c542] px-1 py-0.5 text-center font-bold text-black shadow">
              {num(speedMps, 1)}
            </div>
            <span className="text-white/60">{num(Math.max(0, speedMps - 10), 0)}</span>
            <span className="text-white/60">{num(Math.max(0, speedMps - 20), 0)}</span>
          </div>
          <div className="px-1 text-[9px] text-[#f5c542] font-semibold">
            V/S: {Number.isFinite(verticalSpeedMps) ? (verticalSpeedMps >= 0 ? `+${verticalSpeedMps.toFixed(1)}` : verticalSpeedMps.toFixed(1)) : '---'}
          </div>
        </div>

        {/* ---------------- 6. RIGHT ALTITUDE TAPE ---------------- */}
        <div className="absolute right-1 top-6 bottom-8 w-16 border border-white/20 bg-black/60 z-20 flex flex-col justify-between py-1 text-[10.5px]">
          <div className="px-1 text-right text-[9px] uppercase tracking-wider text-muted font-bold">ALT (m)</div>
          <div className="flex flex-col items-center gap-2">
            <span className="text-white/60">{num(altitudeM + 200, 0)}</span>
            <span className="text-white/60">{num(altitudeM + 100, 0)}</span>
            {/* Center Box */}
            <div className="w-full bg-[#3fd68a] px-1 py-0.5 text-center font-bold text-black shadow">
              {num(altitudeM, 0)}
            </div>
            <span className="text-white/60">{num(Math.max(0, altitudeM - 100), 0)}</span>
            <span className="text-white/60">{num(Math.max(0, altitudeM - 200), 0)}</span>
          </div>
          <div className="px-1 text-right text-[9px] text-[#3fd68a] font-semibold">
            {num(altitudeM / 1000, 2)} km
          </div>
        </div>

        {/* ---------------- 7. STATUS ANNUNCIATOR OVERLAYS ---------------- */}
        {/* Top-Center Big Status (like DISARMED / ARMED in Mission Planner) */}
        <div className="absolute top-3 left-1/2 -translate-x-1/2 z-20 text-center pointer-events-none">
          <span
            className={`font-sans text-[20px] font-black tracking-widest drop-shadow-[0_2px_4px_rgba(0,0,0,0.9)] ${
              isArmed ? 'text-[#3fd68a]' : 'text-[#ff3333]'
            }`}
          >
            {statusLabel}
          </span>
        </div>

        {/* Bottom Status: GPS & Pitch/Roll Readouts */}
        <div className="absolute bottom-1 left-2 right-2 z-20 flex items-center justify-between text-[11px] font-bold drop-shadow">
          <span className={gpsStatus.includes('NOMINAL') || gpsStatus.includes('FIX') ? 'text-[#3fd68a]' : 'text-[#ff4d4d]'}>
            {gpsStatus}
          </span>
          <div className="flex items-center gap-3 text-white/90">
            <span>R: {rollDeg.toFixed(1)}°</span>
            <span>P: {pitchDeg.toFixed(1)}°</span>
            <span>Y: {normalizedYaw.toFixed(1)}°</span>
          </div>
        </div>
      </div>
    </div>
  );
}
