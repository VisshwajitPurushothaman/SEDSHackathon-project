import { useEffect, useRef, useState } from 'react';
import { checkHealth } from '../api/client';
import { useMission } from '../store/useMission';

/** Drives the mission clock from requestAnimationFrame (pauses with the tab). */
export function usePlaybackLoop() {
  useEffect(() => {
    let raf = 0;
    let last = performance.now();
    const loop = (now: number) => {
      // Clamp so a background tab or a long GC pause never jumps the playhead.
      const dt = Math.min(now - last, 250);
      last = now;
      useMission.getState().tick(dt);
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, []);
}

/** Polls GET /api/health every 5 s. Starts the first run once the backend's state is known. */
export function useHealthPoll() {
  const started = useRef(false);
  useEffect(() => {
    let alive = true;
    const poll = async () => {
      const ok = await checkHealth();
      if (!alive) return;
      useMission.getState().setConnection(ok);
      if (!started.current) {
        started.current = true;
        void useMission.getState().run();
      }
    };
    void poll();
    const id = setInterval(poll, 5000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);
}

/** Space play/pause, ←/→ scrub (Shift = bigger steps), R restart. */
export function useKeyboardShortcuts() {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const el = e.target as HTMLElement | null;
      const tag = el?.tagName;
      // Let focused controls keep their native keys (buttons click on Space, sliders use arrows).
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || tag === 'BUTTON' || el?.isContentEditable) return;
      const s = useMission.getState();
      if (e.code === 'Space') {
        e.preventDefault();
        s.togglePlay();
      } else if (e.key === 'ArrowRight') {
        e.preventDefault();
        s.nudge(e.shiftKey ? 25 : 5);
      } else if (e.key === 'ArrowLeft') {
        e.preventDefault();
        s.nudge(e.shiftKey ? -25 : -5);
      } else if (e.key === 'r' || e.key === 'R') {
        s.restart();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);
}

const reducedQuery = '(prefers-reduced-motion: reduce)';

export function prefersReducedMotion(): boolean {
  return typeof window !== 'undefined' && window.matchMedia?.(reducedQuery).matches === true;
}

export function useReducedMotionPref(): boolean {
  const [reduced, setReduced] = useState(prefersReducedMotion);
  useEffect(() => {
    const mq = window.matchMedia(reducedQuery);
    const on = () => setReduced(mq.matches);
    mq.addEventListener('change', on);
    return () => mq.removeEventListener('change', on);
  }, []);
  return reduced;
}

/**
 * The playhead sampled at most every `ms` milliseconds. Charts use this so
 * recharts does not re-render at 60 fps.
 */
export function useThrottledPlayhead(ms = 120): number {
  const [value, setValue] = useState(() => useMission.getState().uiPlayhead);
  useEffect(() => {
    let lastEmit = 0;
    let pending: ReturnType<typeof setTimeout> | null = null;
    const unsub = useMission.subscribe((s, prev) => {
      if (s.uiPlayhead === prev.uiPlayhead && s.frames === prev.frames) return;
      const now = performance.now();
      if (now - lastEmit >= ms) {
        lastEmit = now;
        setValue(s.uiPlayhead);
      } else if (!pending) {
        // Trailing update so the chart lands on the final value when playback stops.
        pending = setTimeout(() => {
          pending = null;
          lastEmit = performance.now();
          setValue(useMission.getState().uiPlayhead);
        }, ms);
      }
    });
    return () => {
      unsub();
      if (pending) clearTimeout(pending);
    };
  }, [ms]);
  return value;
}

/** document.visibilityState as React state. */
export function usePageVisible(): boolean {
  const [visible, setVisible] = useState(() => typeof document === 'undefined' || document.visibilityState === 'visible');
  useEffect(() => {
    const on = () => setVisible(document.visibilityState === 'visible');
    document.addEventListener('visibilitychange', on);
    return () => document.removeEventListener('visibilitychange', on);
  }, []);
  return visible;
}
