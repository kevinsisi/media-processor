import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  DndContext,
  type DragEndEvent,
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
  horizontalListSortingStrategy,
  sortableKeyboardCoordinates,
  useSortable,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { ApiError, apiClient } from "../api/client";
import type { DraftDetail, DraftSegmentOut } from "../api/types";
import { labelForCutSource } from "../i18n/tags";
import "./DraggableTimeline.css";

interface DraggableTimelineProps {
  draft: DraftDetail;
  videoRef: React.RefObject<HTMLVideoElement>;
  onReorderStart?: () => void;
  onReorderError?: (msg: string) => void;
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

interface DraggableCellProps {
  row: SegRowKey;
  totalMs: number;
  onTap: (seg: DraftSegmentOut) => void;
}

function DraggableCell({ row, totalMs, onTap }: DraggableCellProps) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: row.key });
  const style: React.CSSProperties = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.6 : 1,
  };
  const seg = row.segment;
  const cls =
    seg.source_kind === "scripted"
      ? "dt-cell--scripted"
      : "dt-cell--improv";
  const span = seg.on_timeline_end_ms - seg.on_timeline_start_ms;
  const pct = totalMs > 0 ? Math.round((span / totalMs) * 100) : 0;
  return (
    <li
      ref={setNodeRef}
      style={style}
      className={`dt-cell ${cls}${isDragging ? " dt-cell--dragging" : ""}`}
    >
      <div className="dt-cell__handle" {...attributes} {...listeners} aria-label="拖拉重新排序">
        ⋮⋮
      </div>
      <button
        type="button"
        className="dt-cell__btn"
        onClick={() => onTap(seg)}
      >
        <span className="dt-cell__order mono">#{seg.order + 1}</span>
        <span className="dt-cell__range mono">
          {formatTimecode(seg.on_timeline_start_ms)}
          {" → "}
          {formatTimecode(seg.on_timeline_end_ms)}
        </span>
        <span className="dt-cell__chip">{labelForCutSource(seg.source_kind)}</span>
        {seg.plan_reason && (
          <span className="dt-cell__reason" title={seg.plan_reason}>
            {seg.plan_reason}
          </span>
        )}
        <span className="dt-cell__total mono">{pct}%</span>
      </button>
    </li>
  );
}

/**
 * M7.1 — Drag-droppable timeline. The user re-orders cuts, the local state
 * updates immediately, and a debounced PATCH /drafts/{id}/order is sent.
 * On success the parent (`useDraftPolling`) will refresh and pick up the
 * new server state; until then we keep our optimistic order on screen.
 *
 * When the draft is in flight (pending / processing), drag is disabled so
 * the user can't reorder mid-render.
 */
export default function DraggableTimeline({
  draft,
  videoRef,
  onReorderStart,
  onReorderError,
}: DraggableTimelineProps) {
  const segments = useMemo(
    () => [...draft.segments].sort((a, b) => a.order - b.order),
    [draft.segments],
  );
  const [localRows, setLocalRows] = useState<SegRowKey[]>(() => segments.map(makeRow));
  // When upstream prop changes (poll refresh, force re-render), reseed
  // unless we just dragged — in that case we trust our optimistic state
  // until the PATCH lands.
  const dirtyRef = useRef(false);
  useEffect(() => {
    if (!dirtyRef.current) {
      setLocalRows(segments.map(makeRow));
    }
  }, [segments]);

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
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  const inFlight =
    draft.status === "pending" || draft.status === "processing";

  const submitReorder = useCallback(
    async (rows: SegRowKey[]) => {
      onReorderStart?.();
      try {
        const ids = rows.map((r) => r.segment.id);
        await apiClient.reorderDraftSegments(draft.id, { orders: ids });
      } catch (err) {
        const msg =
          err instanceof ApiError
            ? `${err.status}: ${err.message}`
            : err instanceof Error
              ? err.message
              : String(err);
        onReorderError?.(`重新排序失敗：${msg}`);
        dirtyRef.current = false;
        setLocalRows(segments.map(makeRow));
      }
    },
    [draft.id, onReorderStart, onReorderError, segments],
  );

  const handleDragEnd = useCallback(
    (ev: DragEndEvent) => {
      const { active, over } = ev;
      if (!over || active.id === over.id) return;
      setLocalRows((prev) => {
        const fromIdx = prev.findIndex((r) => r.key === active.id);
        const toIdx = prev.findIndex((r) => r.key === over.id);
        if (fromIdx < 0 || toIdx < 0) return prev;
        const next = arrayMove(prev, fromIdx, toIdx);
        dirtyRef.current = true;
        // Fire-and-forget. The parent decides whether to show a chip.
        void submitReorder(next);
        return next;
      });
    },
    [submitReorder],
  );

  const handleTap = useCallback(
    (seg: DraftSegmentOut) => {
      const v = videoRef.current;
      if (!v) return;
      v.currentTime = seg.on_timeline_start_ms / 1000;
      void v.play().catch(() => {});
    },
    [videoRef],
  );

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
      <DndContext
        sensors={sensors}
        collisionDetection={closestCenter}
        onDragEnd={inFlight ? undefined : handleDragEnd}
      >
        <SortableContext
          items={localRows.map((r) => r.key)}
          strategy={horizontalListSortingStrategy}
        >
          <ol className="dt-timeline__list" aria-label="剪輯時間軸">
            {localRows.map((r) => (
              <DraggableCell
                key={r.key}
                row={r}
                totalMs={totalMs}
                onTap={handleTap}
              />
            ))}
          </ol>
        </SortableContext>
      </DndContext>
    </div>
  );
}
