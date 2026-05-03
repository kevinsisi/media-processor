import { useEffect, useRef } from "react";
import "./PlayheadCursor.css";

// A draggable red vertical line marking the on-timeline playhead. The
// drag handle sits on top of the ruler row so it's always accessible
// even when clips fill the entire video track.

export interface PlayheadCursorProps {
  playheadMs: number;
  pxPerSec: number;
  totalMs: number;
  onPlayheadMsChange: (ms: number) => void;
  /** Height in px; used so the line spans ruler + tracks. */
  height: number;
}

export default function PlayheadCursor({
  playheadMs,
  pxPerSec,
  totalMs,
  onPlayheadMsChange,
  height,
}: PlayheadCursorProps) {
  const draggingRef = useRef(false);

  useEffect(() => {
    const onMove = (e: PointerEvent) => {
      if (!draggingRef.current) return;
      const target = (e.target as HTMLElement).closest(
        ".timeline-canvas__scroll",
      ) as HTMLElement | null;
      if (!target) return;
      const rect = target.getBoundingClientRect();
      const xWithinTrack =
        e.clientX - rect.left + (target.scrollLeft ?? 0);
      const ms = Math.max(
        0,
        Math.min(totalMs, (xWithinTrack * 1000) / pxPerSec),
      );
      onPlayheadMsChange(ms);
    };
    const onUp = () => {
      draggingRef.current = false;
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
  }, [pxPerSec, totalMs, onPlayheadMsChange]);

  const leftPx = (playheadMs * pxPerSec) / 1000;

  return (
    <div
      className="playhead"
      style={{ left: leftPx, height }}
      onPointerDown={(e) => {
        e.preventDefault();
        draggingRef.current = true;
      }}
    >
      <div className="playhead__handle" />
      <div className="playhead__line" />
    </div>
  );
}
