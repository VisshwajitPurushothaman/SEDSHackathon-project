import { getCurrentFrame } from '../store/useMission';
import { parseLeakUm } from '../lib/checks';
import { scene } from '../theme/tokens';
import { ParticleField, type Spawn } from './Particles';
import { ROCKET_R, TANK_Y } from './shared';

/** Angle around the tank where the puncture vents (front-right, facing the default camera). */
const VENT_ANGLE = 0.55;
const NX = Math.cos(VENT_ANGLE);
const NZ = Math.sin(VENT_ANGLE);

/**
 * 0..1 jet strength from the backend's leak estimate in `leak_status`
 * (log-scaled: 10 µm small, 1000 µm large). 0 while the frame is sealed, so
 * scrubbing back before the leak frame stops the jet.
 */
function ventStrength(): number {
  const f = getCurrentFrame();
  if (!f?.leak_detected) return 0;
  const um = parseLeakUm(f.leak_status) ?? 100;
  return Math.min(1, Math.max(0.2, Math.log10(Math.max(um, 1)) / 3));
}

/** Helium venting from a single point on the pressurant tank. */
export function VentJet() {
  const emit = (s: Spawn) => {
    const k = ventStrength();
    const speed = 0.8 + 2.6 * k;
    const spread = 0.12 + 0.35 * k;
    s.pos.set(NX * (ROCKET_R + 0.005), TANK_Y + 0.12, NZ * (ROCKET_R + 0.005));
    s.vel.set(
      NX * speed + (Math.random() - 0.5) * spread * speed,
      (Math.random() - 0.3) * spread * speed,
      NZ * speed + (Math.random() - 0.5) * spread * speed,
    );
    s.life = 0.6 + Math.random() * 0.6;
  };

  return (
    <ParticleField
      count={450}
      rate={() => {
        const k = ventStrength();
        return k > 0 ? 160 + 520 * k : 0;
      }}
      emit={emit}
      colorA={scene.vent}
      colorB={scene.vent}
      intensity={0.55}
      size={() => 0.07 + 0.08 * ventStrength()}
      drag={0.25}
      gravity={-0.15}
    />
  );
}
