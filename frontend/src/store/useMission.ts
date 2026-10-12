/**
 * Mission store: frame buffer, playhead, run lifecycle, operator commit and
 * the append-only event log.
 *
 * Everything that is *shown about the vehicle* (check states, phase, gate) is
 * derived from (frames, playhead) by pure functions in lib/. The store only
 * keeps the inputs, so scrubbing backwards restores earlier states exactly.
 */
import { create } from 'zustand';
import { openStream, simulate, type SimulateSummary, type TelemetryFrame, type Verdict } from '../api/client';
import { fixtureFor } from '../data/fixtures';
import { deriveMissionEvents, deriveVerdict, type CheckId, type MissionEvent } from '../lib/checks';
import {
  advancePlayhead,
  clampPlayhead,
  endTimeOf,
  frameIndexAt,
  interpolateFrame,
  type PlaybackFrame,
  type Speed,
} from '../lib/playback';

export type Connection = 'checking' | 'online' | 'offline';
export type Mode = 'replay' | 'live';
export type RunSource = 'backend' | 'demo';
export type RunStatus = 'idle' | 'loading' | 'streaming' | 'ready' | 'error';
export type CommitStatus = 'none' | 'held' | 'committed';
export type LogLevel = 'info' | 'ok' | 'warn' | 'fail';

export interface LogEntry {
  id: number;
  /** Wall-clock time (ms since epoch). */
  wall: number;
  /** Mission time, when the entry is about the mission. */
  t: number | null;
  level: LogLevel;
  text: string;
}

export interface RunSummary {
  verdict: Verdict;
  abort_reason: string | null;
  /** True when the summary came from POST /api/simulate; false when derived in the frontend. */
  fromBackend: boolean;
  backend?: SimulateSummary;
}

export type HoverTarget = CheckId | 'gate';

interface MissionState {
  connection: Connection;
  mode: Mode;
  /** Scenario selected in the dock (µm). */
  scenarioUm: number;

  runId: number;
  runUm: number | null;
  source: RunSource | null;
  runStatus: RunStatus;
  error: string | null;
  frames: TelemetryFrame[];
  /** The run has ended (replay loaded, or the stream finished). */
  complete: boolean;
  summary: RunSummary | null;

  playhead: number;
  /**
   * The playhead as the 2D panels see it: follows `playhead` at ~15 Hz while
   * playing (and immediately on seek, stop, or a new backend frame). The 3D
   * scene reads `playhead` every frame; re-rendering every React panel at
   * 60 Hz saturated the main thread and dropped the frame rate.
   */
  uiPlayhead: number;
  playing: boolean;
  speed: Speed;

  commit: { status: CommitStatus; at: number };
  events: LogEntry[];
  /** Mission events already written to the log for this run. */
  loggedKeys: string[];

  freeCamera: boolean;
  hovered: HoverTarget | null;
  /** Where the hover started: a checklist card or a part in the 3D view. */
  hoverFrom: 'card' | 'scene' | null;

  setConnection: (online: boolean) => void;
  setMode: (mode: Mode) => void;
  setScenario: (um: number) => void;
  setSpeed: (speed: Speed) => void;
  setFreeCamera: (on: boolean) => void;
  setHovered: (target: HoverTarget | null, from?: 'card' | 'scene') => void;

  run: () => Promise<void>;
  restart: () => void;
  togglePlay: () => void;
  pause: () => void;
  seek: (t: number) => void;
  nudge: (dt: number) => void;
  tick: (dtMs: number) => void;

  commitIgnition: () => void;
  holdCommit: () => void;
  resumeCommit: () => void;
  log: (level: LogLevel, text: string, t?: number | null) => void;
}

let logSeq = 0;
/** Panel refresh interval while playing. */
const UI_INTERVAL_MS = 66;
let lastUiUpdate = 0;
let closeActiveStream: (() => void) | null = null;

const fmtUm = (um: number) => (um === 0 ? 'Sealed (0 µm)' : `${um} µm`);
export const fmtT = (t: number) => `T+${Math.round(t)} s`;

function eventLogLine(e: MissionEvent, frames: readonly TelemetryFrame[]): { level: LogLevel; text: string } {
  switch (e.kind) {
    case 'leak': {
      const f = frames.find((x) => x.leak_detected);
      const abort = f?.is_abort ? ' → ABORT' : '';
      return { level: 'fail', text: `LEAK detected ${f?.leak_status.replace('LEAK:', '') ?? ''}${abort}` };
    }
    case 'release':
      return { level: 'ok', text: 'Reached 25 km release band (crossing interpolated)' };
    case 'armed':
      return { level: 'ok', text: 'Gate ARMED · GO recommended, awaiting operator commit' };
    case 'abort':
      return { level: 'fail', text: 'MISSION ABORTED' };
    case 'terminated':
      return { level: 'warn', text: 'Flight terminated without arming' };
  }
}

export const useMission = create<MissionState>((set, get) => {
  /** Write mission events whose time lies in (from, to] to the log, once per run. */
  const logCrossings = (from: number, to: number) => {
    const { frames, complete, loggedKeys } = get();
    if (to <= from) return;
    const fresh: string[] = [];
    for (const e of deriveMissionEvents(frames, complete)) {
      const key = e.kind;
      if (loggedKeys.includes(key) || fresh.includes(key)) continue;
      if (e.t > from && e.t <= to) {
        // The leak line already says "→ ABORT" when both happen on one frame.
        if (e.kind === 'abort' && frames.find((f) => f.leak_detected)?.is_abort) {
          fresh.push(key);
          continue;
        }
        const line = eventLogLine(e, frames);
        get().log(line.level, line.text, e.t);
        fresh.push(key);
      }
    }
    if (fresh.length) set((s) => ({ loggedKeys: [...s.loggedKeys, ...fresh] }));
  };

  /** Operator commit only makes sense while the playhead is at/after ARMED. */
  const resetCommitIfBeforeArmed = (t: number) => {
    const { commit, frames } = get();
    if (commit.status === 'none') return;
    const armed = frames.find((f) => f.gate_status === 'ARMED');
    if (!armed || t < armed.time_s) {
      set({ commit: { status: 'none', at: 0 } });
      get().log('info', 'Operator commit cleared (playhead moved before ARMED)', t);
    }
  };

  const startDemo = (um: number, runId: number, why: string) => {
    const frames = fixtureFor(um);
    if (!frames) {
      set({ runStatus: 'error', error: 'Demo data exists only for Sealed (0 µm) and 100 µm.' });
      get().log('warn', `No demo data for ${fmtUm(um)}`);
      return;
    }
    if (get().runId !== runId) return;
    const derived = deriveVerdict(frames, true);
    set({
      source: 'demo',
      frames,
      complete: true,
      summary: derived && { ...derived, fromBackend: false },
      runStatus: 'ready',
      playing: true,
    });
    get().log('info', `Run ${fmtUm(um)} · demo fixture (${why})`);
  };

  return {
    connection: 'checking',
    mode: 'replay',
    scenarioUm: 0,

    runId: 0,
    runUm: null,
    source: null,
    runStatus: 'idle',
    error: null,
    frames: [],
    complete: false,
    summary: null,

    playhead: 0,
    uiPlayhead: 0,
    playing: false,
    speed: 1,

    commit: { status: 'none', at: 0 },
    events: [],
    loggedKeys: [],

    freeCamera: false,
    hovered: null,
    hoverFrom: null,

    setConnection: (online) => {
      const prev = get().connection;
      const next: Connection = online ? 'online' : 'offline';
      if (prev === next) return;
      set({ connection: next });
      if (prev !== 'checking') get().log(online ? 'ok' : 'warn', online ? 'Backend connected' : 'Backend offline · demo data');
      // Live stream needs the backend.
      if (!online && get().mode === 'live') set({ mode: 'replay' });
    },
    setMode: (mode) => set({ mode }),
    setScenario: (um) => set({ scenarioUm: Math.max(0, Math.min(1000, Math.round(um))) }),
    setSpeed: (speed) => set({ speed }),
    setFreeCamera: (on) => set({ freeCamera: on }),
    setHovered: (target, from = 'card') => {
      if (get().hovered !== target) set({ hovered: target, hoverFrom: target ? from : null });
    },

    run: async () => {
      closeActiveStream?.();
      closeActiveStream = null;
      const { scenarioUm: um, mode, connection } = get();
      const runId = get().runId + 1;
      set({
        runId,
        runUm: um,
        source: null,
        frames: [],
        complete: false,
        summary: null,
        playhead: 0,
        uiPlayhead: 0,
        playing: false,
        error: null,
        commit: { status: 'none', at: 0 },
        loggedKeys: [],
        runStatus: 'loading',
      });

      if (connection !== 'online') {
        startDemo(um, runId, 'backend offline');
        return;
      }

      if (mode === 'replay') {
        try {
          const res = await simulate(um);
          if (get().runId !== runId) return; // a newer run started meanwhile
          set({
            source: 'backend',
            frames: res.telemetry,
            complete: true,
            summary: {
              verdict: res.summary.verdict,
              abort_reason: res.summary.abort_reason,
              fromBackend: true,
              backend: res.summary,
            },
            runStatus: 'ready',
            playing: res.telemetry.length > 0,
          });
          get().log('info', `Run ${fmtUm(um)} · replay · ${res.summary.total_frames} frames from backend`);
        } catch (err) {
          if (get().runId !== runId) return;
          get().log('fail', `Simulate request failed: ${(err as Error).message}`);
          get().setConnection(false);
          startDemo(um, runId, 'request failed');
        }
        return;
      }

      // Live stream: frames are appended as they arrive; the playhead follows.
      set({ source: 'backend', runStatus: 'streaming', playing: true });
      get().log('info', `Run ${fmtUm(um)} · live stream opened`);
      closeActiveStream = openStream(um, {
        onFrame: (frame) => {
          if (get().runId !== runId) return;
          set((s) => {
            const last = s.frames[s.frames.length - 1];
            if (last && frame.time_s <= last.time_s) return {}; // ignore duplicates / replays
            return { frames: [...s.frames, frame] };
          });
        },
        onEnd: (reason) => {
          if (get().runId !== runId) return;
          closeActiveStream = null;
          const frames = get().frames;
          const derived = deriveVerdict(frames, true);
          set({ complete: true, runStatus: 'ready', summary: derived && { ...derived, fromBackend: false } });
          get().log(
            'info',
            reason === 'terminal'
              ? `Stream closed by console after terminal frame (${frames.length} frames)`
              : `Stream ended by server (${frames.length} frames)`,
          );
        },
        onError: (message) => {
          if (get().runId !== runId) return;
          closeActiveStream = null;
          set({ runStatus: 'error', error: message, playing: false });
          get().log('fail', `Live stream failed: ${message}`);
        },
      });
    },

    restart: () => {
      if (!get().frames.length) return;
      set({ playhead: 0, uiPlayhead: 0, playing: true });
      resetCommitIfBeforeArmed(0);
      get().log('info', 'Playback restarted', 0);
    },

    togglePlay: () => {
      const { playing, playhead, frames, complete } = get();
      if (!frames.length) return;
      // Pressing play at the end of a finished run replays from the start.
      if (!playing && complete && playhead >= endTimeOf(frames)) {
        get().restart();
        return;
      }
      set({ playing: !playing });
    },

    pause: () => set({ playing: false }),

    seek: (t) => {
      const { frames, playhead } = get();
      const next = clampPlayhead(t, endTimeOf(frames));
      logCrossings(playhead, next);
      set({ playhead: next, uiPlayhead: next });
      resetCommitIfBeforeArmed(next);
    },

    nudge: (dt) => get().seek(get().playhead + dt),

    tick: (dtMs) => {
      const { playing, frames, playhead, uiPlayhead, speed, complete } = get();
      if (!playing || !frames.length) return;
      const end = endTimeOf(frames);
      const next = advancePlayhead(playhead, dtMs, speed, end);
      logCrossings(playhead, next);
      // A finished run stops at its end; a live run waits at the buffer edge for more frames.
      const stillPlaying = !(complete && next >= end);
      const now = performance.now();
      // Panels refresh at ~15 Hz, but never miss a stop or a new backend frame (discrete fields).
      const uiDue =
        now - lastUiUpdate >= UI_INTERVAL_MS || !stillPlaying || frameIndexAt(frames, next) !== frameIndexAt(frames, uiPlayhead);
      if (uiDue) lastUiUpdate = now;
      set(uiDue ? { playhead: next, uiPlayhead: next, playing: stillPlaying } : { playhead: next, playing: stillPlaying });
    },

    commitIgnition: () => {
      set({ commit: { status: 'committed', at: performance.now() } });
      get().log('ok', 'OPERATOR COMMIT · ignition sequence (simulation)', get().playhead);
    },
    holdCommit: () => {
      set({ commit: { status: 'held', at: performance.now() } });
      get().log('warn', 'OPERATOR HOLD declared', get().playhead);
    },
    resumeCommit: () => {
      set({ commit: { status: 'none', at: 0 } });
      get().log('info', 'Operator hold released · awaiting commit', get().playhead);
    },

    log: (level, text, t = null) => {
      const entry: LogEntry = { id: ++logSeq, wall: Date.now(), t, level, text };
      set((s) => ({ events: [...s.events, entry].slice(-300) }));
    },
  };
});

// ---------------------------------------------------------------------------
// Derived selectors
// ---------------------------------------------------------------------------

let cache: { frames: TelemetryFrame[] | null; playhead: number; value: PlaybackFrame | null } = {
  frames: null,
  playhead: -1,
  value: null,
};

/** The interpolated frame at the playhead (memoised; safe to call every render / every 3D frame). */
export function selectFrame(s: Pick<MissionState, 'frames' | 'playhead'>): PlaybackFrame | null {
  if (cache.frames !== s.frames || cache.playhead !== s.playhead) {
    cache = { frames: s.frames, playhead: s.playhead, value: interpolateFrame(s.frames, s.playhead) };
  }
  return cache.value;
}

let uiCache: { frames: TelemetryFrame[] | null; playhead: number; value: PlaybackFrame | null } = {
  frames: null,
  playhead: -1,
  value: null,
};

/** The interpolated frame at the panel-rate playhead (see `uiPlayhead`). */
export function selectUiFrame(s: Pick<MissionState, 'frames' | 'uiPlayhead'>): PlaybackFrame | null {
  if (uiCache.frames !== s.frames || uiCache.playhead !== s.uiPlayhead) {
    uiCache = { frames: s.frames, playhead: s.uiPlayhead, value: interpolateFrame(s.frames, s.uiPlayhead) };
  }
  return uiCache.value;
}

/** For 2D panels: re-renders at the panel rate, not every animation frame. */
export const useCurrentFrame = () => useMission(selectUiFrame);

/** Non-reactive read for the 3D render loop. */
export const getCurrentFrame = () => selectFrame(useMission.getState());

export const selectEndTime = (s: Pick<MissionState, 'frames'>) => endTimeOf(s.frames);

export const selectAtEnd = (s: MissionState) => s.complete && s.frames.length > 0 && s.uiPlayhead >= endTimeOf(s.frames);
