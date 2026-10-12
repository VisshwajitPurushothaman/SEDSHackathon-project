/**
 * Design tokens: the single source of truth for colour.
 * Tailwind reads `ui` (see tailwind.config.ts); the three.js scene imports
 * `ui` and `scene` directly, so no component ever carries a raw hex value.
 */

export const ui = {
  bg: '#070B14',
  panel: '#0D1424',
  panel2: '#111A2E',
  border: '#1C2740',
  text: '#E6EDF7',
  muted: '#8A97AB',
  faint: '#7A879C',
  pass: '#34C38F',
  hold: '#F2A93B',
  fail: '#FF5D5D',
  info: '#6AA8FF',
  armed: '#3DF5A7',
} as const;

/** Colours used only inside the 3D scene (materials, sky, particles). */
export const scene = {
  liveryWhite: '#E9EDF2',
  graphite: '#2A303A',
  darkMetal: '#3A3F47',
  accentStripe: '#6AA8FF',
  gondolaFrame: '#9AA6B8',
  balloon: '#DCE6F2',
  balloonRim: '#A9C8FF',
  loadLine: '#B8C2D0',
  rail: '#5B6678',
  keyLight: '#FFF4E6',
  rimLight: '#7FA8FF',
  // Sky gradient endpoints at ground level and at the 30 km ceiling.
  skyLowTop: '#1A3156',
  skyLowHorizon: '#3B5A80',
  skyHighTop: '#010207',
  skyHighHorizon: '#0B1630',
  haze: '#A9BCD6',
  star: '#F4F7FF',
  // Motor casing colour map: warm amber at 20 °C, icy blue below 0 °C.
  motorWarm: '#F2A93B',
  motorCold: '#6FC3FF',
  plumeCore: '#FFF1C9',
  plumeEdge: '#FF8A3D',
  vent: '#D8E8FF',
} as const;

export type UiColor = keyof typeof ui;
