/**
 * Flight window state: the attached mission, every ground frame received so far, and a cursor.
 * Live mode follows the newest frame; replay mode scrubs/plays through what has arrived.
 * Operator commands are only allowed while live (you cannot commit from a replayed past).
 */
import { create } from 'zustand';
import { API_URL } from '../api/client';
import { missionsApi, streamMission, type Alert, type EventLogEntry, type GroundFrame, type MissionSummary } from './api';

export interface PublicConfig {
  site: { lat_deg: number; lon_deg: number; elevation_m: number; name: string };
  gates: { release_band_m: [number, number]; approved_area: [number, number][]; swing_max_amplitude_deg: number; float_max_vz_m_s: number };
  motor: { T_min_qualified_c: number };
  rocket: { rail_azimuth_deg: number; label: string };
  balloon: { V_max_m3: number };
  tank: { P0_bar: number };
  api: { default_time_scale: number; truth_enabled: boolean };
}

const OP_KEY = 'aerofire.operator';
const readOperator = () => {
  try {
    return localStorage.getItem(OP_KEY) ?? '';
  } catch {
    return '';
  }
};

let closeStream: (() => void) | null = null;
let eventsTimer: ReturnType<typeof setInterval> | null = null;

interface FlightState {
  config: PublicConfig | null;
  missionId: string | null;
  summary: MissionSummary | null;
  frames: GroundFrame[];
  cursor: number; // index into frames
  live: boolean;
  playing: boolean;
  speed: number; // frames per tick in replay
  ended: boolean;
  streamNote: string | null;
  error: string | null;
  busy: boolean;
  operator: string;
  events: EventLogEntry[];
  /** Server-confirmed acknowledgements: shown even when no later frame arrives (e.g. after landing). */
  acks: Record<number, Alert>;

  loadConfig: () => Promise<void>;
  setOperator: (name: string) => void;
  attach: (id: string) => void;
  detach: () => void;
  goLive: () => void;
  seek: (i: number) => void;
  togglePlay: () => void;
  setSpeed: (s: number) => void;
  tick: () => void;
  command: (kind: 'commit' | 'hold' | 'release' | 'terminate') => Promise<void>;
  ack: (alertId: number) => Promise<void>;
}

export const useFlight = create<FlightState>((set, get) => ({
  config: null,
  missionId: null,
  summary: null,
  frames: [],
  cursor: 0,
  live: true,
  playing: false,
  speed: 1,
  ended: false,
  streamNote: null,
  error: null,
  busy: false,
  operator: readOperator(),
  events: [],
  acks: {},

  loadConfig: async () => {
    try {
      const res = await fetch(`${API_URL}/api/config`);
      if (res.ok) set({ config: (await res.json()) as PublicConfig });
    } catch {
      /* offline: the flight view needs the backend; shown as an error state */
    }
  },
  setOperator: (name) => {
    set({ operator: name });
    try {
      localStorage.setItem(OP_KEY, name);
    } catch {
      /* private mode */
    }
  },


  attach: (id) => {
    get().detach();
    set({ missionId: id, frames: [], cursor: 0, live: true, playing: false, ended: false, streamNote: null, events: [], acks: {} });
    // Frames can arrive faster than React should render: buffer and apply once per animation frame.
    let pending: GroundFrame[] = [];
    let scheduled = false;
    const flush = () => {
      scheduled = false;
      const batch = pending;
      pending = [];
      set((s) => {
        let lastId = s.frames.length ? s.frames[s.frames.length - 1].id : 0;
        const fresh = batch.filter((f) => (f.id > lastId ? ((lastId = f.id), true) : false)); // drop duplicates after a reconnect
        if (!fresh.length) return {};
        const frames = s.frames.concat(fresh);
        return { frames, cursor: s.live ? frames.length - 1 : s.cursor, streamNote: null };
      });
    };
    closeStream = streamMission(id, 0, {
      onFrame: (f) => {
        pending.push(f);
        if (!scheduled) {
          scheduled = true;
          requestAnimationFrame(flush);
        }
      },
      onEnd: () => set({ ended: true, streamNote: null }),
      onError: (m) => set({ streamNote: m }),
    });
    const poll = async () => {
      try {
        const [events, summary] = await Promise.all([missionsApi.events(id), missionsApi.summary(id)]);
        set({ events, summary });
      } catch {
        /* transient */
      }
    };
    void poll();
    eventsTimer = setInterval(poll, 2000);
  },

  detach: () => {
    closeStream?.();
    closeStream = null;
    if (eventsTimer) clearInterval(eventsTimer);
    eventsTimer = null;
  },

  goLive: () => set((s) => ({ live: true, playing: false, cursor: Math.max(0, s.frames.length - 1) })),
  seek: (i) => set((s) => ({ cursor: Math.min(Math.max(0, i), Math.max(0, s.frames.length - 1)), live: i >= s.frames.length - 1 })),
  togglePlay: () => set((s) => ({ playing: !s.playing, live: false })),
  setSpeed: (speed) => set({ speed }),
  tick: () =>
    set((s) => {
      if (!s.playing || s.live) return {};
      const next = s.cursor + s.speed;
      if (next >= s.frames.length - 1) return { cursor: s.frames.length - 1, playing: false, live: true };
      return { cursor: next };
    }),

  command: async (kind) => {
    const { missionId, operator, live } = get();
    if (!missionId) return;
    if (!live) {
      set({ error: 'Go live first: commands act on the current state, not on a replayed past' });
      return;
    }
    if (!operator.trim()) {
      set({ error: 'Enter the operator name first' });
      return;
    }
    set({ busy: true, error: null });
    try {
      if (kind === 'commit') await missionsApi.commit(missionId, operator);
      else if (kind === 'hold') await missionsApi.hold(missionId, operator, false);
      else if (kind === 'release') await missionsApi.hold(missionId, operator, true);
      else await missionsApi.terminate(missionId, operator);
    } catch (e) {
      set({ error: `${kind} refused: ${(e as Error).message}` });
    } finally {
      set({ busy: false });
    }
  },

  ack: async (alertId) => {
    const { missionId, operator } = get();
    if (!missionId) return;
    if (!operator.trim()) {
      set({ error: 'Enter the operator name to acknowledge alerts' });
      return;
    }
    try {
      const acked = await missionsApi.ack(missionId, alertId, operator);
      if (get().missionId === missionId) set((st) => ({ acks: { ...st.acks, [alertId]: acked } }));
    } catch (e) {
      set({ error: `acknowledge refused: ${(e as Error).message}` });
    }
  },
}));

export const selectFrame = (s: FlightState): GroundFrame | null => s.frames[s.cursor] ?? null;
