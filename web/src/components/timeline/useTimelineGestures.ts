// Wheel + pinch zoom hook for the timeline canvas. Returns a `pxPerSec`
// scalar and an event handler the canvas component spreads onto its
// scroll/zoom container.

import { useCallback, useEffect, useRef, useState } from "react";

const PX_PER_SEC_MIN = 1;
const PX_PER_SEC_MAX = 50;
const PX_PER_SEC_DEFAULT = 8;
// Wheel + pinch step the zoom by ~10% per tick — fine enough that
// overshooting is rare, coarse enough that getting from 1 to 50 takes
// a reasonable number of strokes.
const ZOOM_STEP = 1.1;

export interface TimelineGestures {
  pxPerSec: number;
  setPxPerSec: (v: number) => void;
  /** Spread onto the canvas scroll container. */
  bind: {
    onWheel: (e: React.WheelEvent<HTMLElement>) => void;
    onTouchStart: (e: React.TouchEvent<HTMLElement>) => void;
    onTouchMove: (e: React.TouchEvent<HTMLElement>) => void;
    onTouchEnd: () => void;
  };
}

export function useTimelineGestures(
  initial: number = PX_PER_SEC_DEFAULT,
): TimelineGestures {
  const [pxPerSec, setPxPerSecRaw] = useState<number>(clamp(initial));
  const pinchRef = useRef<{ baseDist: number; basePxPerSec: number } | null>(
    null,
  );

  const setPxPerSec = useCallback((v: number) => {
    setPxPerSecRaw(clamp(v));
  }, []);

  const onWheel = useCallback((e: React.WheelEvent<HTMLElement>) => {
    // Only zoom when Ctrl / Meta is held — bare wheel scrolls the
    // canvas like the user expects. deltaY > 0 = scroll down = zoom out.
    if (!e.ctrlKey && !e.metaKey) return;
    e.preventDefault();
    setPxPerSecRaw((prev) =>
      clamp(e.deltaY > 0 ? prev / ZOOM_STEP : prev * ZOOM_STEP),
    );
  }, []);

  const onTouchStart = useCallback((e: React.TouchEvent<HTMLElement>) => {
    if (e.touches.length === 2) {
      const [a, b] = [e.touches[0], e.touches[1]];
      const dist = Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
      pinchRef.current = { baseDist: dist, basePxPerSec: pxPerSec };
    }
  }, [pxPerSec]);

  const onTouchMove = useCallback((e: React.TouchEvent<HTMLElement>) => {
    if (e.touches.length !== 2 || pinchRef.current === null) return;
    const [a, b] = [e.touches[0], e.touches[1]];
    const dist = Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
    const ratio = dist / pinchRef.current.baseDist;
    setPxPerSecRaw(clamp(pinchRef.current.basePxPerSec * ratio));
  }, []);

  const onTouchEnd = useCallback(() => {
    pinchRef.current = null;
  }, []);

  // Resync clamp on initial-value change (e.g. user opens a different
  // draft). Cheap so no useMemo.
  useEffect(() => {
    setPxPerSecRaw(clamp(initial));
  }, [initial]);

  return {
    pxPerSec,
    setPxPerSec,
    bind: { onWheel, onTouchStart, onTouchMove, onTouchEnd },
  };
}

function clamp(v: number): number {
  if (Number.isNaN(v)) return PX_PER_SEC_DEFAULT;
  return Math.max(PX_PER_SEC_MIN, Math.min(PX_PER_SEC_MAX, v));
}

export const PX_PER_SEC_BOUNDS = {
  min: PX_PER_SEC_MIN,
  max: PX_PER_SEC_MAX,
  default: PX_PER_SEC_DEFAULT,
};
