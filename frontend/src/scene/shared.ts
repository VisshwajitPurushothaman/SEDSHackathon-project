/**
 * Small mutable singletons shared between the 3D scene and the DOM overlay.
 * They change every frame, so they live outside React state on purpose.
 */
import type { Object3D } from 'three';
import type { HoverTarget } from '../store/useMission';

// --- Anchors: one invisible Object3D per check, used for leader lines and tooltips.

export const anchors = new Map<HoverTarget, Object3D>();

export function anchorRef(id: HoverTarget) {
  return (obj: Object3D | null) => {
    if (obj) anchors.set(id, obj);
    else anchors.delete(id);
  };
}

/** Screen position (client px) of the hovered part's anchor, written by the scene each frame. */
export const screenAnchor = { x: 0, y: 0, visible: false };

// --- One-shot camera/vehicle shake (the leak event).

export const shake = { start: -1 };
export function triggerShake() {
  shake.start = performance.now();
}

// --- Ignition sequence timeline (seconds after the operator commit).

export const IGNITION = {
  glowEnd: 0.8,
  plumeStart: 0.6,
  clampsOpen: 1.2,
  liftoff: 1.5,
  followEnd: 3.5,
  cardAt: 3.6,
  accel: 5.5,
} as const;

/** Seconds since commit, or -1 if not committed. */
export function ignitionElapsed(commit: { status: string; at: number }): number {
  return commit.status === 'committed' ? (performance.now() - commit.at) / 1000 : -1;
}

/** Rocket rise along its rail (scene units) at a time after commit. */
export function riseAt(e: number): number {
  const t = Math.max(0, e - IGNITION.liftoff);
  return 0.5 * IGNITION.accel * t * t;
}

/** Sideways drift after clearing the rail, so the vehicle departs clear of the balloon. */
export function driftAt(rise: number): number {
  return 0.22 * Math.pow(Math.max(0, rise - 0.6), 1.6);
}

/** Pitch (rad) along the departure path: the slope of driftAt. */
export function pitchAt(rise: number): number {
  const r = Math.max(0, rise - 0.6);
  return Math.atan(0.22 * 1.6 * Math.pow(r, 0.6));
}

// --- Geometry constants shared by several parts.

/** Suspension point (bottom of the balloon). The assembly swings about it. */
export const PIVOT_Y = 4.0;
export const ROCKET_R = 0.17;
export const TANK_Y = 0.07;

/** Altitude rail: 0 km at RAIL_Y0, 30 km at RAIL_Y1. */
export const RAIL_X = 2.7;
export const RAIL_Y0 = -1.7;
export const RAIL_Y1 = 4.3;
export const altToRailY = (m: number) => RAIL_Y0 + (Math.min(Math.max(m, 0), 30000) / 30000) * (RAIL_Y1 - RAIL_Y0);

export const smoothstep = (e0: number, e1: number, x: number) => {
  const t = Math.min(Math.max((x - e0) / (e1 - e0), 0), 1);
  return t * t * (3 - 2 * t);
};
