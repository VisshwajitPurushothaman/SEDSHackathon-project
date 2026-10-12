import { Outlines } from '@react-three/drei';
import type { ThreeEvent } from '@react-three/fiber';
import { Component, Suspense, type ReactNode } from 'react';
import { useMission, type HoverTarget } from '../store/useMission';
import { ui } from '../theme/tokens';

/** Pointer handlers that link a 3D part to its checklist card. */
export function hoverProps(id: HoverTarget) {
  return {
    onPointerOver: (e: ThreeEvent<PointerEvent>) => {
      e.stopPropagation();
      useMission.getState().setHovered(id, 'scene');
      document.body.style.cursor = 'pointer';
    },
    onPointerOut: (e: ThreeEvent<PointerEvent>) => {
      e.stopPropagation();
      if (useMission.getState().hovered === id) useMission.getState().setHovered(null);
      document.body.style.cursor = '';
    },
  };
}

export const useIsHovered = (id: HoverTarget) => useMission((s) => s.hovered === id);

/** Outline shown on a mesh while its check is hovered (card or scene). Place as a mesh child. */
export function Highlight({ id, thickness = 0.018 }: { id: HoverTarget; thickness?: number }) {
  const on = useIsHovered(id);
  return on ? <Outlines thickness={thickness} color={ui.info} screenspace={false} toneMapped={false} /> : null;
}

/** Renders nothing if a child throws (e.g. a font or HDR fails to download). */
export class Boundary extends Component<{ children: ReactNode; fallback?: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  componentDidCatch() {
    /* optional asset: fall back silently */
  }
  render() {
    return this.state.failed ? (this.props.fallback ?? null) : this.props.children;
  }
}

export function Optional({ children, fallback = null }: { children: ReactNode; fallback?: ReactNode }) {
  return (
    <Boundary fallback={fallback}>
      <Suspense fallback={fallback}>{children}</Suspense>
    </Boundary>
  );
}
