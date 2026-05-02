import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  DndContext,
  type DragEndEvent,
  DragOverlay,
  type DragStartEvent,
  KeyboardSensor,
  PointerSensor,
  TouchSensor,
  closestCenter,
  useSensor,
  useSensors,
} from "@dnd-kit/core";
import {
  SortableContext,
  arrayMove,
  verticalListSortingStrategy,
  sortableKeyboardCoordinates,
  useSortable,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { ApiError, apiClient } from "../api/client";
import type { DraftDetail, DraftSegmentOut } from "../api/types";
import { labelForCutSource } from "../i18n/tags";
import "./DraggableTimeline.css";

interface AssetThumbInfo {
  duration_ms: number;
  thumbnail_urls: string[];
}

interface DraggableTimelineProps {
  draft: DraftDetail;
  videoRef: React.RefObject<HTMLVideoElement>;
  // v0.14.7 — per-asset keyframe gallery (asset_id → frames + duration).
  // Used by each cell to pick the frame whose timestamp is closest to
  // the cut's mid-point. Empty map when the analysis hasn't run yet
  // — cells just render without a thumbnail.
  assetThumbs?: Map<number, AssetThumbInfo>;
  onReorderStart?: () => void;
  onReorderError?: (msg: string) => void;
}

// Mirror of services.thumbnails.FRAME_PERCENTAGES so we can map a frame
// index in ``thumbnail_urls`` back to its real asset timestamp. If the
// backend ever changes the schedule, update both sides.
const FRAME_PERCENTAGES = [0.1, 0.3, 0.5, 0.7, 0.9];

function pickThumbnailForSpan(
  info: AssetThumbInfo | undefined,
  startMs: number | null,
  endMs: number | null,
): string | null {
  if (!info || info.thumbnail_urls.length === 0) return null;
  if (startMs == null || endMs == null || endMs <= startMs) {
    // Fall back to the middle keyframe so we still show something.
    const mid = Math.floor(info.thumbnail_urls.length / 2);
    return info.thumbnail_urls[mid] ?? null;
  }
  const targetMs = (startMs + endMs) / 2;
  const dur = info.duration_ms || endMs;
  let bestIdx = 0;
  let bestDelta = Infinity;
  info.thumbnail_urls.forEach((_, i) => {
    const pct = FRAME_PERCENTAGES[i] ?? (i + 0.5) / info.thumbnail_urls.length;
    const tsMs = pct * dur;
    const delta = Math.abs(tsMs - targetMs);
    if (delta < bestDelta) {
      bestDelta = delta;
      bestIdx = i;
    }
  });
  return info.thumbnail_urls[bestIdx] ?? null;
}

interface SegRowKey {
  // The backend reorder API takes a permutation of DraftSegment row ids.
  // Schema now exposes `id` so we use it directly as the dnd-kit sortable
  // key — no composite-key gymnastics needed.
  key: string;
  segment: DraftSegmentOut;
}

function formatTimecode(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

function makeRow(seg: DraftSegmentOut): SegRowKey {
  return { key: `seg-${seg.id}`, segment: seg };
}

interface CellContentProps {
  row: SegRowKey;
  totalMs: number;
  thumbnailUrl?: string | null;
  onTap?: (seg: DraftSegmentOut) => void;
  // Pass-through for the drag handle's listeners + a11y attributes when
  // useSortable is wired up. The DragOverlay clone renders without
  // these so we accept undefined.
  handleAttrs?: React.HTMLAttributes<HTMLDivElement>;
}

function CellContent({
  row,
  totalMs,
  thumbnailUrl,
  onTap,
  handleAttrs,
}: CellContentProps) {
  const seg = row.segment;
  const cls =
    seg.source_kind === "scripted" ? "dt-cell--scripted" : "dt-cell--improv";
  const span = seg.on_timeline_end_ms - seg.on_timeline_start_ms;
  const pct = totalMs > 0 ? Math.round((span / totalMs) * 100) : 0;
  return (
    <div className={`dt-cell-inner ${cls}`}>
      <div className="dt-cell__handle" {...handleAttrs} aria-label="拖拉重新排序">
        ⋮⋮
      </div>
      {thumbnailUrl && (
        <div className="dt-cell__thumb">
          <img src={thumbnailUrl} alt="" loading="lazy" />
        </div>
      )}
      <button
        type="button"
        className="dt-cell__btn"
        onClick={onTap ? () => onTap(seg) : undefined}
        disabled={!onTap}
      >
        <div className="dt-cell__top-row">
          <span className="dt-cell__order mono">#{seg.order + 1}</span>
          <span className="dt-cell__range mono">
            {formatTimecode(seg.on_timeline_start_ms)}
            {" → "}
            {formatTimecode(seg.on_timeline_end_ms)}
          </span>
          <span className="dt-cell__chip">
            {labelForCutSource(seg.source_kind)}
          </span>
          <span className="dt-cell__total mono">{pct}%</span>
        </div>
        {seg.plan_reason && (
          <p className="dt-cell__reason">{seg.plan_reason}</p>
        )}
      </button>
    </div>
  );
}

interface DraggableCellProps {
  row: SegRowKey;
  totalMs: number;
  thumbnailUrl: string | null;
  onTap: (seg: DraftSegmentOut) => void;
}

function DraggableCell({
  row,
  totalMs,
  thumbnailUrl,
  onTap,
}: DraggableCellProps) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: row.key });
  // The cell's *original* slot stays in the DOM during drag. We dim it
  // (DragOverlay renders a high-z-index clone above the list so users
  // see the actual moving card on top of everything else).
  const style: React.CSSProperties = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.25 : 1,
  };
  return (
    <li
      ref={setNodeRef}
      style={style}
      className={`dt-cell${isDragging ? " dt-cell--ghost" : ""}`}
    >
      <CellContent
        row={row}
        totalMs={totalMs}
        thumbnailUrl={thumbnailUrl}
        onTap={onTap}
        handleAttrs={{ ...attributes, ...listeners }}
      />
    </li>
  );
}

/**
 * M7.1 — Drag-droppable timeline. v0.14.5 reworked the commit flow:
 * dragging only updates local state, with a "順序已調整" banner offering
 * the user an explicit "以此順序重新生成" button. Without that click the
 * backend never sees the new ordering, so accidental drags don't kick
 * off a 4-minute re-render. The button calls
 * PATCH /drafts/{id}/order, which on the backend renumbers segments,
 * rewrites cut_plan_json, and enqueues a skip-plan render.
 *
 * When the draft is in flight (pending / processing) drag is disabled
 * so the user can't reorder mid-render.
 */
export default function DraggableTimeline({
  draft,
  videoRef,
  assetThumbs,
  onReorderStart,
  onReorderError,
}: DraggableTimelineProps) {
  const segments = useMemo(
    () => [...draft.segments].sort((a, b) => a.order - b.order),
    [draft.segments],
  );
  const [localRows, setLocalRows] = useState<SegRowKey[]>(() =>
    segments.map(makeRow),
  );
  // Snapshot of the segment ids in their server order. We compare this
  // against the local order to decide whether the "commit" banner shows.
  const [serverIds, setServerIds] = useState<number[]>(() =>
    segments.map((s) => s.id),
  );
  // Active dragged-cell key — fed into <DragOverlay> so we can render a
  // high-contrast floating clone of the cell instead of relying on the
  // CSS z-index gymnastics that mis-stacked under tall siblings.
  const [activeKey, setActiveKey] = useState<string | null>(null);
  // True while the commit PATCH is in flight; prevents double-tap.
  const [committing, setCommitting] = useState<boolean>(false);

  // Re-seed local rows when the upstream draft changes — e.g. poll
  // refresh after a successful re-render. We trust the server ordering
  // when it differs from our last-known snapshot, which is the only
  // safe way to avoid stuck-banner state after a successful commit.
  const segmentSig = segments.map((s) => s.id).join(",");
  useEffect(() => {
    setLocalRows(segments.map(makeRow));
    setServerIds(segments.map((s) => s.id));
  }, [segmentSig]);

  const totalMs = useMemo(
    () =>
      localRows.length === 0
        ? 0
        : Math.max(...localRows.map((r) => r.segment.on_timeline_end_ms)),
    [localRows],
  );

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
    useSensor(TouchSensor, {
      activationConstraint: { delay: 150, tolerance: 8 },
    }),
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
    }),
  );

  const inFlight =
    draft.status === "pending" || draft.status === "processing";

  // Dirty when local order differs from the server snapshot.
  const dirty = useMemo(() => {
    if (localRows.length !== serverIds.length) return true;
    for (let i = 0; i < localRows.length; i += 1) {
      if (localRows[i].segment.id !== serverIds[i]) return true;
    }
    return false;
  }, [localRows, serverIds]);

  const handleDragStart = useCallback((ev: DragStartEvent) => {
    setActiveKey(String(ev.active.id));
  }, []);

  const handleDragEnd = useCallback((ev: DragEndEvent) => {
    setActiveKey(null);
    const { active, over } = ev;
    if (!over || active.id === over.id) return;
    setLocalRows((prev) => {
      const fromIdx = prev.findIndex((r) => r.key === active.id);
      const toIdx = prev.findIndex((r) => r.key === over.id);
      if (fromIdx < 0 || toIdx < 0) return prev;
      return arrayMove(prev, fromIdx, toIdx);
    });
  }, []);

  const handleDragCancel = useCallback(() => {
    setActiveKey(null);
  }, []);

  const handleCommit = useCallback(async () => {
    if (!dirty || committing || inFlight) return;
    setCommitting(true);
    onReorderStart?.();
    try {
      const ids = localRows.map((r) => r.segment.id);
      await apiClient.reorderDraftSegments(draft.id, { orders: ids });
      // Locally reflect the commit by updating the server snapshot —
      // when the parent re-fetches the new draft (with status=pending)
      // the snapshot syncs again via the segments effect above.
      setServerIds(ids);
    } catch (err) {
      const msg =
        err instanceof ApiError
          ? `${err.status}: ${err.message}`
          : err instanceof Error
            ? err.message
            : String(err);
      onReorderError?.(`重新排序失敗：${msg}`);
    } finally {
      setCommitting(false);
    }
  }, [
    dirty,
    committing,
    inFlight,
    localRows,
    draft.id,
    onReorderStart,
    onReorderError,
  ]);

  const handleRevert = useCallback(() => {
    // Drop the local order, snap back to whatever the server sent.
    setLocalRows(segments.map(makeRow));
  }, [segments]);

  const handleTap = useCallback(
    (seg: DraftSegmentOut) => {
      const v = videoRef.current;
      if (!v) return;
      v.currentTime = seg.on_timeline_start_ms / 1000;
      void v.play().catch(() => {});
    },
    [videoRef],
  );

  // Resolve the actively-dragged row for the overlay clone. ``null``
  // when nothing is being dragged so DragOverlay renders nothing.
  const activeRow = useRef<SegRowKey | null>(null);
  if (activeKey) {
    activeRow.current =
      localRows.find((r) => r.key === activeKey) ?? activeRow.current;
  }

  if (segments.length === 0) {
    return <div className="dt-timeline dt-timeline--empty mono">尚無片段</div>;
  }

  return (
    <div className={`dt-timeline${inFlight ? " dt-timeline--locked" : ""}`}>
      <p className="dt-timeline__hint mono">
        {inFlight
          ? "剪輯進行中，完成後可拖拉排序"
          : "長按 ⋮⋮ 或拖拉卡片可調整片段順序"}
      </p>
      {dirty && !inFlight && (
        <div className="dt-timeline__commit" role="status">
          <span className="dt-timeline__commit-label">
            順序已調整。新順序只會在點下方按鈕後才套用並重新渲染。
          </span>
          <div className="dt-timeline__commit-actions">
            <button
              type="button"
              className="cta cta--quiet"
              onClick={handleRevert}
              disabled={committing}
            >
              還原
            </button>
            <button
              type="button"
              className="cta cta--primary"
              onClick={() => void handleCommit()}
              disabled={committing}
            >
              {committing ? "排隊中…" : "以此順序重新生成"}
            </button>
          </div>
        </div>
      )}
      <DndContext
        sensors={sensors}
        collisionDetection={closestCenter}
        onDragStart={inFlight ? undefined : handleDragStart}
        onDragEnd={inFlight ? undefined : handleDragEnd}
        onDragCancel={inFlight ? undefined : handleDragCancel}
      >
        <SortableContext
          items={localRows.map((r) => r.key)}
          strategy={verticalListSortingStrategy}
        >
          <ol className="dt-timeline__list" aria-label="剪輯時間軸">
            {localRows.map((r) => {
              const info =
                r.segment.asset_id != null
                  ? assetThumbs?.get(r.segment.asset_id)
                  : undefined;
              const thumbnailUrl = pickThumbnailForSpan(
                info,
                r.segment.asset_start_ms,
                r.segment.asset_end_ms,
              );
              return (
                <DraggableCell
                  key={r.key}
                  row={r}
                  totalMs={totalMs}
                  thumbnailUrl={thumbnailUrl}
                  onTap={handleTap}
                />
              );
            })}
          </ol>
        </SortableContext>
        {/* Floating clone of the dragged card; rendered in a portal at
            the document root so no parent stacking context can hide it
            behind sibling cells. Only the visible card; no listeners. */}
        <DragOverlay dropAnimation={null}>
          {activeKey && activeRow.current ? (
            <div className="dt-cell dt-cell--floating">
              <CellContent
                row={activeRow.current}
                totalMs={totalMs}
                thumbnailUrl={(() => {
                  const seg = activeRow.current.segment;
                  const info =
                    seg.asset_id != null
                      ? assetThumbs?.get(seg.asset_id)
                      : undefined;
                  return pickThumbnailForSpan(
                    info,
                    seg.asset_start_ms,
                    seg.asset_end_ms,
                  );
                })()}
              />
            </div>
          ) : null}
        </DragOverlay>
      </DndContext>
    </div>
  );
}
