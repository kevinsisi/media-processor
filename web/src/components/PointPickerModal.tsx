// v0.23.1 — full-screen modal for picking a single source pixel as
// the LK tracking seed. Replaces the small inline canvas click which
// was too cramped to hit a wheel hub or eye reliably on a 6-inch
// phone.
//
// Interaction model:
//   * Mouse wheel zooms in/out around the cursor (desktop)
//   * Two-finger pinch zooms around the pinch midpoint (mobile)
//   * Single-finger / mouse drag pans (when zoomed)
//   * Click / tap commits the point at the clicked pixel — the
//     drag-vs-click disambiguation uses a small movement threshold
//     so a small jitter during tap doesn't misfire as drag
//   * Backdrop click / Esc / cancel button closes without committing
//
// Coordinate math (v0.23.2 — manual computation):
//
// We DON'T use ``imgRef.getBoundingClientRect()`` because:
//
//   1. With ``max-width: 100%; max-height: 100%`` the <img> only
//      fills the stage when its intrinsic dimensions exceed the
//      container; for small thumbnails (e.g. 640×360 in an 1200×800
//      stage) the element renders at its natural pixel size, which
//      is harmless on its own but stacks badly with the transform
//      origin assumptions in the wheel/pinch handlers.
//   2. A CSS ``transition: transform 80ms`` on the image meant a
//      click landing in the middle of a wheel-zoom transition saw
//      the partway-through rect, not the final one. Even after
//      removing the transition (v0.23.2), relying on the browser's
//      bounding rect for transformed elements is more variable than
//      computing it ourselves.
//
// Instead, we recompute the visible image rect from known state on
// every click: stage rect + image natural dimensions + zoom + pan.
// The wheel + pinch handlers anchor on the same model, so the
// click→commit math + the zoom-anchor math always agree.

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import "./PointPickerModal.css";

interface PointPickerModalProps {
  open: boolean;
  thumbnailUrl: string | null | undefined;
  // Source pixel dimensions; only used for the "click outside image"
  // letterbox-rejection check + the on-screen size hint. The
  // normalised coordinates we emit don't depend on this.
  srcW: number;
  srcH: number;
  onCommit: (norm: { norm_x: number; norm_y: number }) => void;
  onCancel: () => void;
  // Optional disabled flag — the picker stays open but reject clicks
  // (e.g. while a previous commit is still in flight).
  busy?: boolean;
}

const MIN_ZOOM = 1.0;
const MAX_ZOOM = 8.0;
const WHEEL_ZOOM_STEP = 1.18; // ~18% per notch
const DRAG_THRESHOLD_PX = 4;

interface Pan {
  x: number;
  y: number;
}

// v0.23.2 — fitted base size of the image inside the stage at zoom=1,
// pan=(0,0). Mirrors the browser's behaviour for an <img> with
// ``max-width: 100%; max-height: 100%`` and no explicit width/height:
// keep the natural size unless either dimension exceeds the
// container, in which case scale down preserving aspect.
function fittedImageSize(
  naturalW: number,
  naturalH: number,
  containerW: number,
  containerH: number,
): { w: number; h: number } {
  if (naturalW <= 0 || naturalH <= 0 || containerW <= 0 || containerH <= 0) {
    return { w: 0, h: 0 };
  }
  if (naturalW <= containerW && naturalH <= containerH) {
    return { w: naturalW, h: naturalH };
  }
  const aspect = naturalW / naturalH;
  const containerAspect = containerW / containerH;
  if (aspect > containerAspect) {
    return { w: containerW, h: containerW / aspect };
  }
  return { h: containerH, w: containerH * aspect };
}

// v0.23.2 — visible image rect on-screen given stage + transform
// state. Same model the wheel/pinch handlers anchor on:
//   1. Image base size = fittedImageSize within the stage
//   2. Image is centred in the stage (flex centring)
//   3. transform: translate(panX, panY) scale(zoom)
//      — CSS reads right-to-left: scale FIRST around centre, THEN
//      translate. The centre of the image is the stage centre, so
//      the post-transform top-left is
//        stage_centre - (base * zoom) / 2 + pan
function visibleImageRect(
  stage: DOMRect,
  natW: number,
  natH: number,
  zoom: number,
  pan: Pan,
): { left: number; top: number; width: number; height: number } | null {
  const base = fittedImageSize(natW, natH, stage.width, stage.height);
  if (base.w === 0 || base.h === 0) return null;
  const w = base.w * zoom;
  const h = base.h * zoom;
  const cx = stage.left + stage.width / 2;
  const cy = stage.top + stage.height / 2;
  return {
    left: cx - w / 2 + pan.x,
    top: cy - h / 2 + pan.y,
    width: w,
    height: h,
  };
}

export default function PointPickerModal({
  open,
  thumbnailUrl,
  srcW,
  srcH,
  onCommit,
  onCancel,
  busy,
}: PointPickerModalProps) {
  const imgRef = useRef<HTMLImageElement | null>(null);
  const stageRef = useRef<HTMLDivElement | null>(null);

  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState<Pan>({ x: 0, y: 0 });

  // Drag bookkeeping. We track the active pointer id + start position
  // so the same handler can disambiguate drag (movement > threshold)
  // from click (no movement on release).
  const dragRef = useRef<{
    pointerId: number;
    startX: number;
    startY: number;
    startPanX: number;
    startPanY: number;
    moved: boolean;
  } | null>(null);

  // Pinch bookkeeping. Two simultaneous touches → track the distance
  // between them and the midpoint; deltas drive zoom + pan together.
  const pinchRef = useRef<{
    initialDist: number;
    initialZoom: number;
    midX: number;
    midY: number;
    initialPanX: number;
    initialPanY: number;
  } | null>(null);

  // Reset zoom / pan whenever the modal closes so the next open
  // starts fit-to-screen.
  useEffect(() => {
    if (!open) {
      setZoom(1);
      setPan({ x: 0, y: 0 });
      dragRef.current = null;
      pinchRef.current = null;
    }
  }, [open]);

  // Esc to cancel.
  useEffect(() => {
    if (!open) return;
    const onKey = (ev: KeyboardEvent) => {
      if (ev.key === "Escape") onCancel();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onCancel]);

  // Lock body scroll while open so a phone in landscape doesn't
  // bounce the page when the user pinches.
  useEffect(() => {
    if (!open) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, [open]);

  const transformStyle = useMemo(
    () => ({
      transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`,
      transformOrigin: "center center",
    }),
    [zoom, pan],
  );

  const clampZoom = (z: number) => Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, z));

  // v0.23.1 — wheel zoom centred on the cursor: keep the image
  // pixel under the cursor anchored as the zoom changes by adjusting
  // pan to compensate for the focal-point shift.
  const handleWheel = useCallback(
    (ev: React.WheelEvent<HTMLDivElement>) => {
      ev.preventDefault();
      const stage = stageRef.current;
      if (!stage) return;
      const stageRect = stage.getBoundingClientRect();
      const cx = ev.clientX - stageRect.left - stageRect.width / 2;
      const cy = ev.clientY - stageRect.top - stageRect.height / 2;

      const factor = ev.deltaY < 0 ? WHEEL_ZOOM_STEP : 1 / WHEEL_ZOOM_STEP;
      const nextZoom = clampZoom(zoom * factor);
      if (nextZoom === zoom) return;
      // Shift pan so the cursor's image-space anchor stays put.
      const ratio = nextZoom / zoom;
      setPan((p) => ({
        x: cx - (cx - p.x) * ratio,
        y: cy - (cy - p.y) * ratio,
      }));
      setZoom(nextZoom);
    },
    [zoom],
  );

  // ---- Pointer (mouse + single-touch) drag-or-click ----
  const onPointerDown = useCallback(
    (ev: React.PointerEvent<HTMLDivElement>) => {
      if (busy) return;
      // Two-or-more-touch case is handled by the touch listeners
      // (pinch); ignore secondary pointers here.
      if (
        ev.pointerType === "touch"
        && pinchRef.current !== null
      ) {
        return;
      }
      dragRef.current = {
        pointerId: ev.pointerId,
        startX: ev.clientX,
        startY: ev.clientY,
        startPanX: pan.x,
        startPanY: pan.y,
        moved: false,
      };
      const stage = stageRef.current;
      stage?.setPointerCapture(ev.pointerId);
    },
    [busy, pan],
  );

  const onPointerMove = useCallback(
    (ev: React.PointerEvent<HTMLDivElement>) => {
      const drag = dragRef.current;
      if (!drag || drag.pointerId !== ev.pointerId) return;
      const dx = ev.clientX - drag.startX;
      const dy = ev.clientY - drag.startY;
      if (
        !drag.moved
        && Math.hypot(dx, dy) > DRAG_THRESHOLD_PX
      ) {
        drag.moved = true;
      }
      if (drag.moved) {
        setPan({ x: drag.startPanX + dx, y: drag.startPanY + dy });
      }
    },
    [],
  );

  const onPointerUp = useCallback(
    (ev: React.PointerEvent<HTMLDivElement>) => {
      const drag = dragRef.current;
      if (!drag || drag.pointerId !== ev.pointerId) return;
      const stage = stageRef.current;
      try {
        stage?.releasePointerCapture(ev.pointerId);
      } catch {
        /* not captured — ignore */
      }
      const wasClick = !drag.moved;
      dragRef.current = null;
      if (!wasClick || busy) return;

      // v0.23.2 — manual rect from state. ``imgRef.naturalWidth /
      // naturalHeight`` is the thumbnail's intrinsic resolution
      // (e.g. 640×360 for the asset's keyframe gallery JPEG); the
      // normalised coords we emit are scale-invariant — the
      // backend multiplies them by the source asset's full
      // resolution, not the thumbnail's. As long as the thumbnail
      // is the same crop / aspect as the source (which it is —
      // ``services.thumbnails`` extracts from the source frame),
      // norm at the thumbnail = norm at the source.
      const img = imgRef.current;
      if (!stage || !img) return;
      const natW = img.naturalWidth;
      const natH = img.naturalHeight;
      if (!natW || !natH) return;
      const rect = visibleImageRect(
        stage.getBoundingClientRect(),
        natW,
        natH,
        zoom,
        pan,
      );
      if (!rect) return;
      const xCss = ev.clientX - rect.left;
      const yCss = ev.clientY - rect.top;
      if (xCss < 0 || yCss < 0 || xCss > rect.width || yCss > rect.height) {
        // Click landed outside the image (letterbox / backdrop).
        return;
      }
      onCommit({
        norm_x: Math.max(0, Math.min(1, xCss / rect.width)),
        norm_y: Math.max(0, Math.min(1, yCss / rect.height)),
      });
    },
    [busy, onCommit, zoom, pan],
  );

  // ---- Touch pinch ----
  const onTouchStart = useCallback(
    (ev: React.TouchEvent<HTMLDivElement>) => {
      if (ev.touches.length !== 2) return;
      const t1 = ev.touches[0];
      const t2 = ev.touches[1];
      const stage = stageRef.current;
      if (!stage) return;
      const stageRect = stage.getBoundingClientRect();
      pinchRef.current = {
        initialDist: Math.hypot(
          t2.clientX - t1.clientX,
          t2.clientY - t1.clientY,
        ),
        initialZoom: zoom,
        midX: (t1.clientX + t2.clientX) / 2 - stageRect.left - stageRect.width / 2,
        midY: (t1.clientY + t2.clientY) / 2 - stageRect.top - stageRect.height / 2,
        initialPanX: pan.x,
        initialPanY: pan.y,
      };
      // Cancel any in-flight single-finger drag so the click-on-end
      // logic doesn't misfire on the pinch finger lift.
      dragRef.current = null;
    },
    [zoom, pan],
  );

  const onTouchMove = useCallback(
    (ev: React.TouchEvent<HTMLDivElement>) => {
      const pinch = pinchRef.current;
      if (!pinch || ev.touches.length !== 2) return;
      ev.preventDefault();
      const t1 = ev.touches[0];
      const t2 = ev.touches[1];
      const dist = Math.hypot(
        t2.clientX - t1.clientX,
        t2.clientY - t1.clientY,
      );
      const nextZoom = clampZoom(
        pinch.initialZoom * (dist / pinch.initialDist),
      );
      const ratio = nextZoom / pinch.initialZoom;
      // Anchor the pinch midpoint in image space so the gesture
      // feels like grabbing the underlying pixels.
      setZoom(nextZoom);
      setPan({
        x: pinch.midX - (pinch.midX - pinch.initialPanX) * ratio,
        y: pinch.midY - (pinch.midY - pinch.initialPanY) * ratio,
      });
    },
    [],
  );

  const onTouchEnd = useCallback(
    (ev: React.TouchEvent<HTMLDivElement>) => {
      if (ev.touches.length < 2) {
        pinchRef.current = null;
      }
    },
    [],
  );

  if (!open) return null;

  const zoomPct = Math.round(zoom * 100);

  return (
    <div
      className="point-picker-modal"
      role="dialog"
      aria-modal="true"
      aria-label="點選畫面重點"
      onClick={(ev) => {
        if (ev.target === ev.currentTarget) onCancel();
      }}
    >
      <header className="point-picker-modal__head">
        <span className="point-picker-modal__title">點一下要跟住的位置</span>
        <span className="point-picker-modal__hint">
          滑鼠滾輪 / 雙指縮放放大；拖曳平移；單擊送出。
        </span>
        <span className="point-picker-modal__zoom mono">{zoomPct}%</span>
        <button
          type="button"
          className="point-picker-modal__close"
          onClick={onCancel}
          aria-label="取消"
        >
          ✕
        </button>
      </header>

      <div
        ref={stageRef}
        className="point-picker-modal__stage"
        onWheel={handleWheel}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        onTouchStart={onTouchStart}
        onTouchMove={onTouchMove}
        onTouchEnd={onTouchEnd}
      >
        {thumbnailUrl ? (
          <img
            ref={imgRef}
            className="point-picker-modal__img"
            src={thumbnailUrl}
            alt={`${srcW}×${srcH} 縮圖`}
            style={transformStyle}
            draggable={false}
          />
        ) : (
          <div className="point-picker-modal__no-thumb">縮圖不可用</div>
        )}
      </div>

      <footer className="point-picker-modal__foot">
        <button
          type="button"
          className="point-picker-modal__btn point-picker-modal__btn--quiet"
          onClick={() => {
            setZoom(1);
            setPan({ x: 0, y: 0 });
          }}
          disabled={zoom === 1 && pan.x === 0 && pan.y === 0}
        >
          重置縮放
        </button>
        <button
          type="button"
          className="point-picker-modal__btn"
          onClick={onCancel}
        >
          取消
        </button>
        {busy ? (
          <span className="point-picker-modal__busy">建立中…</span>
        ) : null}
      </footer>
    </div>
  );
}
