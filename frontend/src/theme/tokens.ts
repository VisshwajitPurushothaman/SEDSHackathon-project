/**
 * Design tokens: the single source of truth for colour (Tailwind reads `ui`, see tailwind.config.ts;
 * charts and maps import it directly). Status colour is always paired with a text tag.
 */
export const ui = {
  bg: '#05080F',
  panel: '#0B1220',
  panel2: '#0F1828',
  border: '#1A2436',
  text: '#DCE3EE',
  muted: '#7C8AA0',
  faint: '#7C8AA0',
  /** Nominal / within limits. */
  pass: '#3FD68A',
  /** Caution: yellow limit. */
  hold: '#F5C542',
  /** Alarm: red limit. */
  fail: '#FF4D4D',
  /** Predicted values and bands. */
  info: '#5CC8FF',
  /** Stale / LOS. */
  stale: '#7C8AA0',
  armed: '#3FD68A',
  white: '#F4F7FB',
} as const;

export type UiColor = keyof typeof ui;
