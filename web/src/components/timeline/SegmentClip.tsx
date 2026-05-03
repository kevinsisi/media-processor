import { useEffect, useRef, useState } from "react";
import type { AssetDetail, DraftSegmentOut } from "../../api/types";
import "./SegmentClip.css";

// One clip block on the video track. Three drag affordances:
//   - body: reorder (NOT implemented in the canvas — Phase 1 keeps
//     reorder in the basic-edit DraggableTimeline view; here it's
//     a click-to-select target only). Phase 2 will add canvas reorder.
//   - left edge: drag = trim asset_start_ms
//   - right edge: drag = trim asset_end_ms
// On pointerup, fires the parent callback with the new asset window;
// the parent issues the PATCH and re-renders with the server's reflowed
// segments.

const HANDLE_WIDTH_PX = 8;
const MIN_CLIP_DURATION_MS = 200; // hard floor so trim doesn't produce <200ms cuts

export interface SegmentClipProps {
  segment: DraftSegmentOut;
  asset?: AssetDetail;
  pxPerSec: number;
  selected: boolean;
  onSelect: () => void;
  onTrimCommit: (next: { asset_start_ms?: number; asset_end_ms?: number }) => void;
}

export default function SegmentClip({
  segment,
  asset,
  pxPerSec,
  selected,
  onSelect,
  onTrimCommit,
}: SegmentClipProps) {
  const widthPx =
    ((segment.on_timeline_end_ms - segment.on_timeline_start_ms) * pxPerSec) /
    1000;
  const leftPx = (segment.on_timeline_start_ms * pxPerSec) / 1000;

  const [trimDelta, setTrimDelta] = useState<number>(0);
  const [trimSide, setTrimSide] = useState<"left" | "right" | null>(null);
  const dragStartRef = useRef<{ x: number; side: "left" | "right" } | null>(
    null,
  );

  useEffect(() => {
    if (!trimSide) return;
    const onMove = (e: PointerEvent) => {
      if (!dragStartRef.current) return;
      const dxPx = e.clientX - dragStartRef.current.x;
      setTrimDelta((dxPx * 1000) / pxPerSec);
    };
    const onUp = () => {
      if (dragStartRef.current && segment.asset_start_ms != null && segment.asset_end_ms != null) {
        const side = dragStartRef.current.side;
        const assetStart = segment.asset_start_ms;
        const assetEnd = segment.asset_end_ms;
        const trimMsRaw = trimDelta;
        if (Math.abs(trimMsRaw) > 30) {
          if (side === "left") {
            const proposed = clamp(
              assetStart + Math.round(trimMsRaw),
              0,
              assetEnd - MIN_CLIP_DURATION_MS,
            );
            if (proposed !== assetStart) {
              onTrimCommit({ asset_start_ms: proposed });
            }
          } else {
            const maxEnd = asset?.duration_ms ?? Number.MAX_SAFE_INTEGER;
            const proposed = clamp(
              assetEnd + Math.round(trimMsRaw),
              assetStart + MIN_CLIP_DURATION_MS,
              maxEnd,
            );
            if (proposed !== assetEnd) {
              onTrimCommit({ asset_end_ms: proposed });
            }
          }
        }
      }
      setTrimDelta(0);
      setTrimSide(null);
      dragStartRef.current = null;
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
  }, [trimSide, trimDelta, pxPerSec, segment, asset, onTrimCommit]);

  const startTrim = (e: React.PointerEvent, side: "left" | "right") => {
    e.stopPropagation();
    dragStartRef.current = { x: e.clientX, side };
    setTrimSide(side);
    setTrimDelta(0);
  };

  // Visual width adjustment while dragging (snappy feedback even
  // before the server returns the reflowed plan).
  const dragLeftAdj = trimSide === "left" ? (trimDelta * pxPerSec) / 1000 : 0;
  const dragRightAdj = trimSide === "right" ? (trimDelta * pxPerSec) / 1000 : 0;

  return (
    <div
      className={`seg-clip ${selected ? "seg-clip--selected" : ""}`}
      style={{
        left: leftPx + dragLeftAdj,
        width: Math.max(HANDLE_WIDTH_PX * 2 + 4, widthPx - dragLeftAdj + dragRightAdj),
      }}
      onClick={onSelect}
    >
      <div
        className="seg-clip__handle seg-clip__handle--left"
        onPointerDown={(e) => startTrim(e, "left")}
        title="拖拉裁切起點"
      />
      <div className="seg-clip__body">
        <span className="seg-clip__label mono">#{segment.order + 1}</span>
        {segment.transition && (
          <span className="seg-clip__transition mono">
            {segment.transition}
          </span>
        )}
      </div>
      <div
        className="seg-clip__handle seg-clip__handle--right"
        onPointerDown={(e) => startTrim(e, "right")}
        title="拖拉裁切終點"
      />
    </div>
  );
}

function clamp(v: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, v));
}
