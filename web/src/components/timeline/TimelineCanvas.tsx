import type { AssetDetail, DraftSegmentOut, ProjectDetail } from "../../api/types";
import PlayheadCursor from "./PlayheadCursor";
import SegmentClip from "./SegmentClip";
import { useTimelineGestures } from "./useTimelineGestures";
import "./TimelineCanvas.css";

// The right 2/3 panel: ruler + video track + BGM track + playhead.
// Owns the px/sec zoom state via useTimelineGestures.

export interface TimelineCanvasProps {
  segments: DraftSegmentOut[];
  assetsById: Record<number, AssetDetail>;
  project: ProjectDetail;
  totalMs: number;
  playheadMs: number;
  onPlayheadMsChange: (ms: number) => void;
  selectedSegmentId: number | null;
  onSelectSegment: (id: number | null) => void;
  onTrimCommit: (segId: number, next: { asset_start_ms?: number; asset_end_ms?: number }) => void;
}

const RULER_HEIGHT_PX = 24;
const TRACK_HEIGHT_PX = 56;
const TRACK_GAP_PX = 6;

export default function TimelineCanvas({
  segments,
  assetsById,
  project,
  totalMs,
  playheadMs,
  onPlayheadMsChange,
  selectedSegmentId,
  onSelectSegment,
  onTrimCommit,
}: TimelineCanvasProps) {
  const { pxPerSec, bind } = useTimelineGestures(8);

  const trackWidthPx = Math.max(400, (totalMs * pxPerSec) / 1000 + 40);
  const totalHeightPx =
    RULER_HEIGHT_PX + TRACK_HEIGHT_PX * 2 + TRACK_GAP_PX * 2;

  const orderedSegments = [...segments].sort((a, b) => a.order - b.order);

  return (
    <div className="timeline-canvas">
      <div
        className="timeline-canvas__scroll"
        onWheel={bind.onWheel}
        onTouchStart={bind.onTouchStart}
        onTouchMove={bind.onTouchMove}
        onTouchEnd={bind.onTouchEnd}
        onClick={(e) => {
          // Click on empty area = jump playhead AND deselect.
          const target = e.currentTarget;
          const rect = target.getBoundingClientRect();
          const x = e.clientX - rect.left + target.scrollLeft;
          const ms = Math.max(0, Math.min(totalMs, (x * 1000) / pxPerSec));
          onPlayheadMsChange(ms);
          onSelectSegment(null);
        }}
      >
        <div
          className="timeline-canvas__inner"
          style={{ width: trackWidthPx, height: totalHeightPx }}
        >
          <Ruler
            totalMs={totalMs}
            pxPerSec={pxPerSec}
            height={RULER_HEIGHT_PX}
          />
          <div
            className="timeline-canvas__track timeline-canvas__track--video"
            style={{
              top: RULER_HEIGHT_PX + TRACK_GAP_PX,
              height: TRACK_HEIGHT_PX,
            }}
            onClick={(e) => e.stopPropagation()}
          >
            {orderedSegments.map((seg) => (
              <SegmentClip
                key={seg.id}
                segment={seg}
                asset={
                  seg.asset_id != null ? assetsById[seg.asset_id] : undefined
                }
                pxPerSec={pxPerSec}
                selected={selectedSegmentId === seg.id}
                onSelect={() => onSelectSegment(seg.id)}
                onTrimCommit={(next) => onTrimCommit(seg.id, next)}
              />
            ))}
          </div>
          <div
            className="timeline-canvas__track timeline-canvas__track--bgm"
            style={{
              top:
                RULER_HEIGHT_PX +
                TRACK_GAP_PX +
                TRACK_HEIGHT_PX +
                TRACK_GAP_PX,
              height: TRACK_HEIGHT_PX,
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <BgmTrackBar project={project} totalMs={totalMs} pxPerSec={pxPerSec} />
          </div>
          <PlayheadCursor
            playheadMs={playheadMs}
            pxPerSec={pxPerSec}
            totalMs={totalMs}
            onPlayheadMsChange={onPlayheadMsChange}
            height={totalHeightPx}
          />
        </div>
      </div>
    </div>
  );
}

function Ruler({
  totalMs,
  pxPerSec,
  height,
}: {
  totalMs: number;
  pxPerSec: number;
  height: number;
}) {
  // Tick density adapts to zoom: aim for ~80 px between major ticks.
  const targetPxBetweenMajor = 80;
  const candidateSeconds = [0.5, 1, 2, 5, 10, 30, 60, 120];
  const majorEverySec =
    candidateSeconds.find((s) => s * pxPerSec >= targetPxBetweenMajor) ??
    candidateSeconds[candidateSeconds.length - 1];

  const ticks: { ms: number; label: string }[] = [];
  for (let s = 0; s * 1000 <= totalMs + majorEverySec * 1000; s += majorEverySec) {
    const ms = s * 1000;
    if (ms > totalMs + 5_000) break;
    const m = Math.floor(s / 60);
    const sec = Math.floor(s % 60);
    ticks.push({
      ms,
      label: m > 0 ? `${m}:${String(sec).padStart(2, "0")}` : `${sec}s`,
    });
  }

  return (
    <div className="timeline-canvas__ruler" style={{ height }}>
      {ticks.map((t) => (
        <div
          key={t.ms}
          className="timeline-canvas__tick"
          style={{ left: (t.ms * pxPerSec) / 1000 }}
        >
          <span className="timeline-canvas__tick-label mono">{t.label}</span>
        </div>
      ))}
    </div>
  );
}

function BgmTrackBar({
  project,
  totalMs,
  pxPerSec,
}: {
  project: ProjectDetail;
  totalMs: number;
  pxPerSec: number;
}) {
  if (!project.bgm_path) {
    return (
      <div className="timeline-canvas__bgm-empty">
        無 BGM（在基本編輯設定 BGM）
      </div>
    );
  }
  const widthPx = (totalMs * pxPerSec) / 1000;
  const filename = project.bgm_path.split(/[\\/]/).pop() ?? "BGM";
  return (
    <div
      className="timeline-canvas__bgm-bar"
      style={{ width: Math.max(60, widthPx) }}
      title={filename}
    >
      <span className="timeline-canvas__bgm-label">🎵 {filename}</span>
    </div>
  );
}
