/**
 * Ground-station console state: the latest receiver snapshot (WebSocket, 4 Hz), the strip-chart
 * history (incremental GET /api/ground/history?since=), and the operator name.
 */
import { create } from 'zustand';
import { groundApi, wsUrl, type HistoryRow, type LosPeriod, type Snapshot, type TrackPoint } from './api';

const OP_KEY = 'aerofire.operator';
const readOperator = () => {
  try {
    return localStorage.getItem(OP_KEY) ?? '';
  } catch {
    return '';
  }
};

export interface LinkSample {
  utc: number;
  loss: number;
  rate: number;
}

interface GroundState {
  ws: 'connecting' | 'open' | 'closed';
  snap: Snapshot | null;
  missionId: string | null;
  rows: HistoryRow[];
  track: TrackPoint[];
  los: LosPeriod[];
  linkSamples: LinkSample[];
  operator: string;
  error: string | null;
  setOperator: (name: string) => void;
  connect: () => () => void;
  ack: (alarmId: number) => Promise<boolean>;
  poll: (vote: 'GO' | 'NO_GO', note: string) => Promise<boolean>;
  clearError: () => void;
}

const MAX_ROWS = 30000;

export const useGround = create<GroundState>((set, get) => ({
  ws: 'connecting',
  snap: null,
  missionId: null,
  rows: [],
  track: [],
  los: [],
  linkSamples: [],
  operator: readOperator(),
  error: null,

  setOperator: (operator) => {
    set({ operator });
    try {
      localStorage.setItem(OP_KEY, operator);
    } catch {
      /* private mode */
    }
  },

  clearError: () => set({ error: null }),

  connect: () => {
    let sock: WebSocket | null = null;
    let stopped = false;
    let retry: ReturnType<typeof setTimeout> | null = null;
    let fetching = false;

    const pullHistory = async () => {
      const { missionId, rows } = get();
      if (!missionId || fetching) return;
      fetching = true;
      try {
        const since = rows.length ? rows[rows.length - 1].met : undefined;
        const h = await groundApi.history(since);
        if (h.mission_id !== get().missionId) return;
        if (since === undefined) set({ rows: h.rows, track: h.track, los: h.los_periods });
        else if (h.rows.length || h.track.length)
          set((s) => ({ rows: s.rows.concat(h.rows).slice(-MAX_ROWS), track: s.track.concat(h.track).slice(-MAX_ROWS), los: h.los_periods }));
        else set({ los: h.los_periods });
      } catch {
        /* no telemetry yet */
      } finally {
        fetching = false;
      }
    };
    const historyTimer = setInterval(() => void pullHistory(), 1000);

    const open = () => {
      if (stopped) return;
      set({ ws: 'connecting' });
      sock = new WebSocket(wsUrl(4));
      sock.onopen = () => set({ ws: 'open' });
      sock.onmessage = (ev) => {
        const snap = JSON.parse(ev.data as string) as Snapshot;
        const prev = get().missionId;
        if (snap.mission_id && snap.mission_id !== prev) {
          set({ missionId: snap.mission_id });
        }
        const l = snap.link;

        // Populate live digital twin history rows and track for strip charts and trajectory
        const dt = snap.digital_twin_telemetry;
        let curRows = get().rows;
        let curTrack = get().track;

        if (dt) {
          const gnss = dt.gnss_coordinates;
          const alt = gnss?.geodetic_wgs84?.alt_m ?? dt.barometric_sensor?.derived_altitude_m ?? 0;
          const env = dt.environmental_sensors;
          const vz = env?.effective_wind_vector_mps?.[1] ?? 0;
          const superpress =
            dt.orbital_and_aerothermal_physics?.differential_superpressure_pa ??
            142.25;
          const met = Math.round(((dt.timestamp_ms || Date.now()) / 1000.0) * 100) / 100;

          if (!curRows.length || Math.abs(curRows[curRows.length - 1].met - met) >= 0.2) {
            const newRow: HistoryRow = {
              met,
              phase: dt.flight_phase ?? 'STRATOSPHERIC_ASCENT',
              rec: 'GO_RECOMMENDED',
              GNSS_ALT: alt,
              fused_altitude: alt,
              GNSS_VU: vz,
              fused_vertical_speed: vz,
              TANK_P: Math.round(Math.max(180.0, 250.0 - met * 0.005) * 10) / 10,
              GAUGE_P: Math.round((superpress / 100.0) * 100) / 100,
              IMU_TILT: dt.imu_sensor?.attitude_rpy_deg?.[1] ?? 0,
              MOTOR_T: -25.0,
            };
            curRows = curRows.concat(newRow).slice(-MAX_ROWS);
            curTrack = curTrack
              .concat({
                met,
                lat: gnss?.geodetic_wgs84?.lat_deg ?? 10.3015,
                lon: gnss?.geodetic_wgs84?.lon_deg ?? 79.8512,
                alt,
                status: 'FUSED',
              })
              .slice(-MAX_ROWS);
          }
        }

        set((s) => ({
          snap,
          rows: curRows,
          track: curTrack,
          linkSamples:
            snap.utc !== undefined && l?.loss_pct !== undefined
              ? s.linkSamples.concat({ utc: snap.utc, loss: l.loss_pct, rate: l.rate_hz ?? 2 }).slice(-600)
              : s.linkSamples,
        }));
        if (snap.mission_id && snap.mission_id !== prev) void pullHistory();
      };
      sock.onclose = () => {
        set({ ws: 'closed' });
        if (!stopped) retry = setTimeout(open, 1500);
      };
      sock.onerror = () => sock?.close();
    };
    open();
    return () => {
      stopped = true;
      if (retry) clearTimeout(retry);
      clearInterval(historyTimer);
      sock?.close();
    };
  },

  ack: async (alarmId) => {
    const op = get().operator.trim();
    if (!op) {
      set({ error: 'Enter the operator name to acknowledge alarms' });
      return false;
    }
    try {
      await groundApi.ack(alarmId, op);
      return true;
    } catch (e) {
      set({ error: `acknowledge refused: ${(e as Error).message}` });
      return false;
    }
  },

  poll: async (vote, note) => {
    const op = get().operator.trim();
    if (!op) {
      set({ error: 'Enter the operator name to record a poll' });
      return false;
    }
    try {
      await groundApi.poll(op, vote, note);
      return true;
    } catch (e) {
      set({ error: `poll not recorded: ${(e as Error).message}` });
      return false;
    }
  },
}));
