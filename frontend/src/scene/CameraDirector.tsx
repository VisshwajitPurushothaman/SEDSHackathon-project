import { CameraControls } from '@react-three/drei';
import { useFrame } from '@react-three/fiber';
import { useEffect, useRef } from 'react';
import { deriveChecks, type CheckMap } from '../lib/checks';
import { selectFrame, useMission, type HoverTarget } from '../store/useMission';
import { driftAt, IGNITION, ignitionElapsed, riseAt, TANK_Y, triggerShake } from './shared';

type Pose = { pos: [number, number, number]; target: [number, number, number] };
type PoseId = HoverTarget | 'overview' | 'liftoffWide';

/** Camera poses. Vehicle coordinates: gondola centre near the origin, suspension point at y = 4. */
export const POSES: Record<PoseId, Pose> = {
  overview: { pos: [7.4, 2.0, 11.6], target: [0.8, 0.95, 0] },
  tank: { pos: [2.1, 0.5, 2.9], target: [0.05, TANK_Y + 0.05, 0] },
  pressure: { pos: [1.9, 1.3, 2.7], target: [0, 0.3, 0] },
  mass: { pos: [2.6, 0.9, 3.0], target: [0.5, 0.35, 0.2] },
  altitude: { pos: [5.3, 3.6, 5.6], target: [2.7, 2.9, 0] },
  tilt: { pos: [0.3, 1.2, 10.5], target: [0, 0.6, 0] },
  motor: { pos: [1.9, -0.5, 2.8], target: [0, -0.95, 0] },
  atmosphere: { pos: [8.5, 1.0, 12.5], target: [0, 4, 0] },
  gate: { pos: [1.8, 2.2, 3.0], target: [0, 1.55, 0] },
  liftoffWide: { pos: [9.5, 2.5, 15], target: [3.2, 3.2, 0] },
};

const FOCUS_DWELL_MS = 1700;
const COOLDOWN_MS = 2200;

/** Priority of a camera-worthy change; higher wins inside the cooldown window. */
const PRIORITY: Partial<Record<PoseId, number>> = { gate: 5, tank: 4, tilt: 3, altitude: 2, mass: 1 };

interface Snapshot {
  checks: CheckMap;
  armed: boolean;
  aborted: boolean;
}

/**
 * Owns the camera. Guided mode eases to the part whose check changed state
 * (~1.2 s), dwells, then drifts back to the overview. "Free camera" turns the
 * guidance off. With prefers-reduced-motion there are no camera moves or shake.
 */
export function CameraDirector({ reduced }: { reduced: boolean }) {
  const controls = useRef<CameraControls>(null);
  const freeCamera = useMission((s) => s.freeCamera);
  const commitStatus = useMission((s) => s.commit.status);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastFocus = useRef<{ at: number; priority: number }>({ at: 0, priority: 0 });
  const following = useRef(false);

  const go = (id: PoseId, animate = true) => {
    const c = controls.current;
    if (!c) return;
    const p = POSES[id];
    void c.setLookAt(...p.pos, ...p.target, animate && !reduced);
  };

  const focus = (id: PoseId) => {
    if (reduced || useMission.getState().freeCamera) return;
    const now = performance.now();
    const pr = PRIORITY[id] ?? 0;
    if (now - lastFocus.current.at < COOLDOWN_MS && pr < lastFocus.current.priority) return;
    lastFocus.current = { at: now, priority: pr };
    if (timer.current) clearTimeout(timer.current);
    go(id);
    timer.current = setTimeout(() => go('overview'), FOCUS_DWELL_MS);
  };

  // Initial framing.
  useEffect(() => {
    const c = controls.current;
    if (!c) return;
    c.smoothTime = 0.42; // ≈1.2 s to settle
    c.draggingSmoothTime = 0.1;
    go('overview', false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Turning guidance back on returns to the overview.
  useEffect(() => {
    if (!freeCamera) go('overview');
    else if (timer.current) clearTimeout(timer.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [freeCamera]);

  // Watch for camera-worthy state changes as the playhead moves forward.
  useEffect(() => {
    const snap = (s: ReturnType<typeof useMission.getState>): Snapshot => {
      const f = selectFrame(s);
      return { checks: deriveChecks(f), armed: f?.gate_status === 'ARMED', aborted: !!f?.is_abort };
    };
    let prev = snap(useMission.getState());
    let prevRun = useMission.getState().runId;
    const unsub = useMission.subscribe((s) => {
      const next = snap(s);
      const forward = s.playing && s.runId === prevRun;
      if (s.runId !== prevRun) {
        prevRun = s.runId;
        if (!reduced && !s.freeCamera) go('overview');
      }
      if (forward) {
        const a = prev.checks;
        const b = next.checks;
        // The leak gets a camera move and one shake.
        if (a.tank.state !== 'FAIL' && b.tank.state === 'FAIL') {
          if (!reduced) triggerShake();
          focus('tank');
        }
        if (!prev.armed && next.armed) focus('gate');
        if (a.altitude.state !== 'PASS' && b.altitude.state === 'PASS') focus('altitude');
        // The gondola swings in and out of the window all the way up; only it matters in the release band.
        if (b.altitude.state === 'PASS' && a.tilt.state !== b.tilt.state && a.tilt.state !== 'PENDING') focus('tilt');
        if (a.mass.state === 'MONITORING' && b.mass.state === 'HOLD') focus('mass');
      }
      prev = next;
    });
    return () => {
      unsub();
      if (timer.current) clearTimeout(timer.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reduced]);

  // Ignition: follow the rocket off the rail for ~2 s, then settle on a wide shot.
  useEffect(() => {
    if (commitStatus !== 'committed') {
      following.current = false;
      return;
    }
    if (timer.current) clearTimeout(timer.current);
    if (reduced || freeCamera) return;
    following.current = true;
    go('gate');
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [commitStatus]);

  useFrame(() => {
    if (!following.current || !controls.current) return;
    const e = ignitionElapsed(useMission.getState().commit);
    if (e < IGNITION.liftoff) return;
    if (e > IGNITION.followEnd) {
      following.current = false;
      go('liftoffWide');
      return;
    }
    const rise = riseAt(e);
    const x = driftAt(rise);
    const y = TANK_Y + rise;
    void controls.current.setLookAt(x + 2.8, y + 0.2, 4.6, x, y + 0.4, 0, true);
  });

  return (
    <CameraControls
      ref={controls}
      makeDefault
      minDistance={2.2}
      maxDistance={26}
      minPolarAngle={0.2}
      maxPolarAngle={Math.PI * 0.64}
      dollyToCursor={false}
    />
  );
}
