import type { SimulateResponse, TelemetryFrame } from './types';

export type { SimulateResponse, SimulateSummary, TelemetryFrame, Verdict, GateStatus } from './types';

export const API_URL: string = (import.meta.env.VITE_API_URL as string | undefined)?.replace(/\/$/, '') ?? 'http://localhost:8000';

/** GET /api/health. Resolves true only for the backend's exact status string. */
export async function checkHealth(timeoutMs = 2500): Promise<boolean> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(`${API_URL}/api/health`, { signal: ctrl.signal, cache: 'no-store' });
    if (!res.ok) return false;
    const body = (await res.json()) as { status?: string };
    return body.status === 'AERO-FIRE Backend Operational';
  } catch {
    return false;
  } finally {
    clearTimeout(timer);
  }
}

/** POST /api/simulate: the whole run in one response (Replay mode). */
export async function simulate(punctureUm: number, signal?: AbortSignal): Promise<SimulateResponse> {
  const res = await fetch(`${API_URL}/api/simulate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ puncture_um: punctureUm }),
    signal,
  });
  if (!res.ok) throw new Error(`simulate failed: HTTP ${res.status}`);
  return (await res.json()) as SimulateResponse;
}

export type StreamEnd =
  /** A frame with is_abort or is_ignition arrived; we closed the stream. */
  | 'terminal'
  /** The server closed the stream after at least one frame (e.g. a TERMINATED run). */
  | 'closed';

export interface StreamHandlers {
  onFrame: (frame: TelemetryFrame) => void;
  onEnd: (reason: StreamEnd) => void;
  /** The stream failed before any frame arrived. */
  onError: (message: string) => void;
}

/**
 * GET /api/stream (SSE), used by Live stream mode.
 *
 * EventSource reconnects on its own when the server closes the stream, which
 * would silently restart the simulation. So we close it ourselves:
 *  - as soon as a frame has is_abort or is_ignition, and
 *  - on the first `error` event after any frame was received (server closed).
 * An error before any frame is treated as a failure and also closed, so it
 * never retries in a loop.
 *
 * Returns a function that closes the stream (idempotent).
 */
export function openStream(punctureUm: number, handlers: StreamHandlers): () => void {
  const url = `${API_URL}/api/stream?puncture_um=${encodeURIComponent(String(punctureUm))}`;
  const source = new EventSource(url);
  let received = 0;
  let closed = false;

  const close = () => {
    if (closed) return;
    closed = true;
    source.close();
  };

  source.onmessage = (ev: MessageEvent<string>) => {
    if (closed) return;
    let frame: TelemetryFrame;
    try {
      frame = JSON.parse(ev.data) as TelemetryFrame;
    } catch {
      return; // ignore malformed events rather than killing the run
    }
    received += 1;
    handlers.onFrame(frame);
    if (frame.is_abort || frame.is_ignition) {
      close();
      handlers.onEnd('terminal');
    }
  };

  source.onerror = () => {
    if (closed) return;
    close();
    if (received > 0) handlers.onEnd('closed');
    else handlers.onError('Stream could not be opened');
  };

  return close;
}
