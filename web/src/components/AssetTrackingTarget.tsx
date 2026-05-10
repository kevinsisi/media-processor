import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError, apiClient } from "../api/client";
import type {
  TrackingDetailOut,
  TrackingMode,
  TrackingTrackOut,
} from "../api/types";
import PointPickerModal from "./PointPickerModal";
import {
  labelForTrackingMode,
  labelForTrackingSubject,
} from "../i18n/tags";
import "./AssetTrackingTarget.css";

interface AssetTrackingTargetProps {
  assetId: number;
  // First (or any) thumbnail URL — drawn behind the bbox overlay so the
  // user can see the actual frame they're choosing on top of.
  thumbnailUrl: string | null;
}

interface RoiDraft {
  startX: number;
  startY: number;
  curX: number;
  curY: number;
}

const TRACKING_MODES: TrackingMode[] = [
  "auto",
  "object",
  "point",
  "custom",
  "fixed",
  "none",
];

function deriveActiveMode(detail: TrackingDetailOut | null): TrackingMode {
  if (!detail) return "auto";
  const idx = detail.tracked_object_index;
  if (idx == null) return "auto";
  if (idx === -1) return "custom";
  if (idx === -2) return "fixed";
  if (idx === -3) return "none";
  if (idx === -4) return "point";
  return "object";
}

function pickRepresentativeFrame(track: TrackingTrackOut): number[] | null {
  if (!track.sample_frames || track.sample_frames.length === 0) return null;
  // Middle frame so the bbox approximates "where the subject usually is".
  const mid = Math.floor(track.sample_frames.length / 2);
  return track.sample_frames[mid];
}

// Pre-v0.17 tracking_json had no `tracks` array. The backend synthesises
// a single track at object_index=0 from the legacy top-level `frames`,
// so we render exactly one bbox here. If `cls_name` is also empty
// (legacy subject_class was sometimes blank), fall back to a generic
// label rather than rendering an empty pill.
function displayTrackName(track: TrackingTrackOut): string {
  const labelled = labelForTrackingSubject(track.cls_name);
  if (labelled) return labelled;
  return "畫面主角";
}

function isLegacyTracking(detail: TrackingDetailOut): boolean {
  return (
    detail.tracks.length === 1 &&
    (!detail.tracks[0].cls_name || detail.tracks[0].area_score === 0)
  );
}

// v0.22.2 — drop noise tracks from the picker. Mirrors the backend's
// ``services.object_tracking.MIN_TRACK_FRAMES`` so the operator only
// sees tracks long enough to actually be useful as a tracking
// target. Defensive (the API filters too) — protects against
// legacy / partially-migrated rows that might still slip a stub
// track through.
const MIN_TRACK_FRAMES = 5;

export default function AssetTrackingTarget({
  assetId,
  thumbnailUrl,
}: AssetTrackingTargetProps) {
  const [detail, setDetail] = useState<TrackingDetailOut | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [activeMode, setActiveMode] = useState<TrackingMode>("auto");
  const [pendingMode, setPendingMode] = useState<TrackingMode | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const wrapRef = useRef<HTMLDivElement | null>(null);
  const [roiDraft, setRoiDraft] = useState<RoiDraft | null>(null);

  // v0.22.2 — visible-tracks list. Backend already filters but we
  // also strip locally so a legacy / cached payload can't surface a
  // 1-frame YOLO flicker as a selectable subject.
  const visibleTracks = useMemo(
    () =>
      (detail?.tracks ?? []).filter(
        (t) => t.frame_count >= MIN_TRACK_FRAMES,
      ),
    [detail],
  );

  const fetchDetail = useCallback(async () => {
    try {
      const d = await apiClient.fetchAssetTracking(assetId);
      setDetail(d);
      setActiveMode(deriveActiveMode(d));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoaded(true);
    }
  }, [assetId]);

  useEffect(() => {
    void fetchDetail();
  }, [fetchDetail]);

  const applyTarget = useCallback(
    async (
      mode: TrackingMode,
      objectIndex?: number,
      customRoi?: { x: number; y: number; w: number; h: number },
      point?: { norm_x: number; norm_y: number; frame_ms: number },
    ) => {
      setBusy(true);
      setError(null);
      try {
        const resp = await apiClient.patchAssetTrackingTarget(assetId, {
          mode,
          object_index: mode === "object" ? objectIndex : null,
          custom_roi: mode === "custom" && customRoi ? customRoi : null,
          point: mode === "point" && point ? point : null,
        });
        setDetail((prev) =>
          prev
            ? {
                ...prev,
                tracked_object_index: resp.tracked_object_index,
                has_custom_roi: resp.has_custom_roi,
                custom_roi_origin: resp.custom_roi_origin ?? null,
                has_point_track: resp.has_point_track,
                point_tracking_status: resp.point_tracking_status ?? null,
                point_tracking_error: null,
              }
            : prev,
        );
        // v0.28.0 — mode=point is now async on the worker. The
        // PATCH returns 202 + ``point_tracking_status="pending"``
        // and the polling effect (below) drives the rest of the
        // UX. For all OTHER modes we still want a single fetchDetail
        // to round-trip ``point_tracking_origin`` etc. — same as
        // pre-0.28.
        if (mode !== "point") {
          void fetchDetail();
        }
        setActiveMode(mode);
        setPendingMode(null);
      } catch (err) {
        const msg =
          err instanceof ApiError
            ? `${err.status}: ${err.message}`
            : err instanceof Error
              ? err.message
              : String(err);
        setError(msg);
      } finally {
        setBusy(false);
      }
    },
    [assetId, fetchDetail],
  );

  // v0.28.0 — poll while a point-tracking job is in flight. The PATCH
  // sets ``point_tracking_status="pending"`` and enqueues a worker
  // job; we re-fetch ``GET /tracking`` every 2 s until the status
  // flips to ``"done"`` (worker wrote the trace) or ``"failed"``
  // (worker raised). 2 s is the same cadence as the queue modal —
  // fast enough to feel responsive, slow enough not to spam an
  // already-loaded api on a long LK walk.
  const isPointTracking = detail?.point_tracking_status === "pending";
  useEffect(() => {
    if (!isPointTracking) return;
    const id = window.setInterval(() => {
      void fetchDetail();
    }, 2000);
    return () => window.clearInterval(id);
  }, [isPointTracking, fetchDetail]);

  // v0.28.0 — surface a worker-side failure as an error toast the
  // first time we see it. The polling effect above will keep
  // re-fetching while ``status === "pending"``; once the worker
  // flips it to ``"failed"``, this effect catches it on the next
  // detail refresh and copies the message into ``error``. Mirrors
  // the pre-0.28 sync error path so the FE state shape is unchanged.
  const failedError = detail?.point_tracking_status === "failed"
    ? detail.point_tracking_error ?? "point tracking failed"
    : null;
  useEffect(() => {
    if (failedError) setError(`跟住主角失敗：${failedError}`);
  }, [failedError]);

  const handleModeClick = useCallback(
    (mode: TrackingMode) => {
      if (busy) return;
      setError(null);
      if (mode === "auto" || mode === "fixed" || mode === "none") {
        void applyTarget(mode);
        return;
      }
      // For object / custom / point we wait for the user to actually
      // pick (click an object box, draw an ROI, or click the
      // tracking pixel).
      setPendingMode(mode);
    },
    [busy, applyTarget],
  );

  const handleObjectPick = useCallback(
    (objectIndex: number) => {
      if (busy) return;
      void applyTarget("object", objectIndex);
    },
    [busy, applyTarget],
  );

  const isCustomDrawing = activeMode === "custom" || pendingMode === "custom";
  // v0.23.1 — point-picking switched to a full-screen modal so the
  // operator can pinch-zoom precisely on a small phone screen. The
  // small inline canvas no longer accepts point clicks; the
  // modal's own ``getBoundingClientRect`` math handles the
  // coordinate mapping (transform-aware so it works post-zoom).
  const isPointPickerOpen = pendingMode === "point";

  const onPointerDown = useCallback(
    (ev: React.PointerEvent<HTMLDivElement>) => {
      if (!isCustomDrawing) return;
      const wrap = wrapRef.current;
      if (!wrap) return;
      const rect = wrap.getBoundingClientRect();
      const x = ev.clientX - rect.left;
      const y = ev.clientY - rect.top;
      setRoiDraft({ startX: x, startY: y, curX: x, curY: y });
      wrap.setPointerCapture(ev.pointerId);
    },
    [isCustomDrawing],
  );

  const onPointerMove = useCallback(
    (ev: React.PointerEvent<HTMLDivElement>) => {
      if (!roiDraft) return;
      const wrap = wrapRef.current;
      if (!wrap) return;
      const rect = wrap.getBoundingClientRect();
      const x = Math.max(0, Math.min(rect.width, ev.clientX - rect.left));
      const y = Math.max(0, Math.min(rect.height, ev.clientY - rect.top));
      setRoiDraft({ ...roiDraft, curX: x, curY: y });
    },
    [roiDraft],
  );

  const finishCustomRoi = useCallback(
    (ev: React.PointerEvent<HTMLDivElement>) => {
      if (!roiDraft) return;
      const wrap = wrapRef.current;
      if (!wrap || !detail) {
        setRoiDraft(null);
        return;
      }
      try {
        wrap.releasePointerCapture(ev.pointerId);
      } catch {
        /* not captured — ignore */
      }
      const rect = wrap.getBoundingClientRect();
      const xCss = Math.min(roiDraft.startX, roiDraft.curX);
      const yCss = Math.min(roiDraft.startY, roiDraft.curY);
      const wCss = Math.abs(roiDraft.curX - roiDraft.startX);
      const hCss = Math.abs(roiDraft.curY - roiDraft.startY);
      setRoiDraft(null);
      if (wCss < 12 || hCss < 12) {
        // Treat as click; nothing to commit.
        return;
      }
      // Map the CSS rect to the source-pixel rect using the rendered
      // image's contained box (object-fit: contain). The image fills
      // the wrap on the limiting axis and letterboxes the other.
      const srcW = detail.src_w || 1920;
      const srcH = detail.src_h || 1080;
      const wrapAspect = rect.width / rect.height;
      const srcAspect = srcW / srcH;
      let renderedW = rect.width;
      let renderedH = rect.height;
      let offsetX = 0;
      let offsetY = 0;
      if (srcAspect > wrapAspect) {
        renderedH = rect.width / srcAspect;
        offsetY = (rect.height - renderedH) / 2;
      } else {
        renderedW = rect.height * srcAspect;
        offsetX = (rect.width - renderedW) / 2;
      }
      const sx = Math.max(0, xCss - offsetX);
      const sy = Math.max(0, yCss - offsetY);
      const sxClamped = Math.min(renderedW, sx);
      const syClamped = Math.min(renderedH, sy);
      const swClamped = Math.min(renderedW - sxClamped, wCss);
      const shClamped = Math.min(renderedH - syClamped, hCss);
      if (swClamped < 8 || shClamped < 8) return;
      const scaleX = srcW / renderedW;
      const scaleY = srcH / renderedH;
      const roi = {
        x: Math.round(sxClamped * scaleX),
        y: Math.round(syClamped * scaleY),
        w: Math.round(swClamped * scaleX),
        h: Math.round(shClamped * scaleY),
      };
      void applyTarget("custom", undefined, roi);
    },
    [roiDraft, detail, applyTarget],
  );

  const onPointerUp = useCallback(
    (ev: React.PointerEvent<HTMLDivElement>) => {
      if (!roiDraft) return;
      finishCustomRoi(ev);
    },
    [roiDraft, finishCustomRoi],
  );

  // Convert detail src pixels to wrap CSS pixels for bbox overlays.
  const renderRect = useMemo(() => {
    if (!detail) return null;
    const wrap = wrapRef.current;
    if (!wrap) return null;
    const rect = wrap.getBoundingClientRect();
    const srcW = detail.src_w || 1920;
    const srcH = detail.src_h || 1080;
    const wrapAspect = rect.width / rect.height;
    const srcAspect = srcW / srcH;
    let renderedW = rect.width;
    let renderedH = rect.height;
    let offsetX = 0;
    let offsetY = 0;
    if (srcAspect > wrapAspect) {
      renderedH = rect.width / srcAspect;
      offsetY = (rect.height - renderedH) / 2;
    } else {
      renderedW = rect.height * srcAspect;
      offsetX = (rect.width - renderedW) / 2;
    }
    return { srcW, srcH, renderedW, renderedH, offsetX, offsetY };
  }, [detail, wrapRef.current?.clientWidth, wrapRef.current?.clientHeight]);

  // Also recompute on window resize so bboxes don't drift.
  const [, forceTick] = useState(0);
  useEffect(() => {
    const handle = () => forceTick((n) => n + 1);
    window.addEventListener("resize", handle);
    return () => window.removeEventListener("resize", handle);
  }, []);

  if (!loaded) {
    return (
      <div className="tracking-target">
        <p className="tracking-target__hint">畫面重點載入中…</p>
      </div>
    );
  }
  if (!detail) {
    return (
      <div className="tracking-target">
        <p className="tracking-target__hint">尚未完成畫面重點檢查。</p>
      </div>
    );
  }

  const cssBoxFor = (frame: number[] | null): React.CSSProperties | null => {
    if (!frame || !renderRect) return null;
    const [, fx, fy, fw, fh] = frame;
    const { srcW, srcH, renderedW, renderedH, offsetX, offsetY } = renderRect;
    return {
      left: `${(fx / srcW) * renderedW + offsetX}px`,
      top: `${(fy / srcH) * renderedH + offsetY}px`,
      width: `${(fw / srcW) * renderedW}px`,
      height: `${(fh / srcH) * renderedH}px`,
    };
  };

  const cssRoiFor = (
    roi: { x: number; y: number; w: number; h: number } | null | undefined,
  ): React.CSSProperties | null => {
    if (!roi || !renderRect) return null;
    const { srcW, srcH, renderedW, renderedH, offsetX, offsetY } = renderRect;
    return {
      left: `${(roi.x / srcW) * renderedW + offsetX}px`,
      top: `${(roi.y / srcH) * renderedH + offsetY}px`,
      width: `${(roi.w / srcW) * renderedW}px`,
      height: `${(roi.h / srcH) * renderedH}px`,
    };
  };

  const draftStyle = roiDraft
    ? {
        left: `${Math.min(roiDraft.startX, roiDraft.curX)}px`,
        top: `${Math.min(roiDraft.startY, roiDraft.curY)}px`,
        width: `${Math.abs(roiDraft.curX - roiDraft.startX)}px`,
        height: `${Math.abs(roiDraft.curY - roiDraft.startY)}px`,
      }
    : null;
  const savedCustomRoiStyle =
    activeMode === "custom" ? cssRoiFor(detail.custom_roi_origin) : null;

  return (
    <div className="tracking-target" aria-label="畫面要跟住誰">
      <div className="tracking-target__head">
        <h4 className="tracking-target__title">畫面要跟住誰</h4>
        <div className="tracking-target__mode" role="tablist">
          {TRACKING_MODES.map((mode) => {
            const active = activeMode === mode || pendingMode === mode;
            return (
              <button
                key={mode}
                type="button"
                role="tab"
                aria-selected={active}
                className={`tracking-target__mode-btn${active ? " tracking-target__mode-btn--active" : ""}`}
                disabled={busy}
                onClick={() => handleModeClick(mode)}
              >
                {labelForTrackingMode(mode)}
              </button>
            );
          })}
        </div>
      </div>

      <div
        className={`tracking-target__canvas-wrap${isCustomDrawing ? " tracking-target__canvas-wrap--draw" : ""}`}
        ref={wrapRef}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
      >
        {thumbnailUrl && (
          <img
            className="tracking-target__canvas-img"
            src={thumbnailUrl}
            alt="畫面重點縮圖"
            draggable={false}
          />
        )}
        {visibleTracks.map((track) => {
          const frame = pickRepresentativeFrame(track);
          const style = cssBoxFor(frame);
          if (!style) return null;
          const isActive =
            activeMode === "object" &&
            detail.tracked_object_index === track.object_index;
          return (
            <button
              key={track.object_index}
              type="button"
              className={`tracking-target__bbox${isActive ? " tracking-target__bbox--active" : ""}`}
              style={style}
              disabled={busy || isCustomDrawing}
              onClick={(ev) => {
                ev.stopPropagation();
                handleObjectPick(track.object_index);
              }}
              title={displayTrackName(track)}
            >
              <span className="tracking-target__bbox-label">
                {displayTrackName(track)}（
                {Math.round(track.confidence * 100)}%）
              </span>
            </button>
          );
        })}
        {savedCustomRoiStyle && !draftStyle && (
          <div
            className="tracking-target__custom-roi tracking-target__custom-roi--saved"
            style={savedCustomRoiStyle}
          />
        )}
        {draftStyle && (
          <div className="tracking-target__custom-roi" style={draftStyle} />
        )}
        {/* v0.23 — crosshair on the originally-clicked pixel (point
           tracking mode). Renders both as feedback after a click and
           on subsequent loads so the operator can see where the
           seed was. Position is the normalised origin × the
           rendered (object-fit:contain) display rect. */}
        {activeMode === "point" && detail.point_tracking_origin && renderRect && (
          <div
            className="tracking-target__crosshair"
            style={{
              left: `${detail.point_tracking_origin.norm_x * renderRect.renderedW + renderRect.offsetX}px`,
              top: `${detail.point_tracking_origin.norm_y * renderRect.renderedH + renderRect.offsetY}px`,
            }}
            aria-label="跟住的位置"
          />
        )}
      </div>

      {pendingMode === "custom" && !roiDraft && (
        <p className="tracking-target__hint">
          在縮圖上拖曳框出想保留的畫面範圍；放開手指即套用。
        </p>
      )}
      {/* v0.23.1 — full-screen modal handles point picking. The
         hint visible here while the modal is open is redundant
         (the modal has its own header copy), so we just show a
         brief "選擇中…" while it's mounted. */}
      {pendingMode === "point" && (
        <p className="tracking-target__hint">選擇要跟住的位置…</p>
      )}
      {activeMode === "point" && pendingMode !== "point" && detail.has_point_track && (
        <p className="tracking-target__hint">
          ✓ 已建立指定位置；按上方「點選位置」可重新選點。
        </p>
      )}
      {activeMode === "custom" && pendingMode !== "custom" && detail.has_custom_roi && (
        <p className="tracking-target__hint">
          ✓ 已建立框選區域；按上方「框選區域」可重新框選。
        </p>
      )}
      {activeMode === "object" && (
        <div className="tracking-target__list" role="group">
          {visibleTracks.map((track) => {
            const isActive =
              detail.tracked_object_index === track.object_index;
            return (
              <button
                key={track.object_index}
                type="button"
                className={`tracking-target__list-btn${isActive ? " tracking-target__list-btn--active" : ""}`}
                disabled={busy}
                onClick={() => handleObjectPick(track.object_index)}
              >
                <span className="tracking-target__list-btn-name">
                  {displayTrackName(track)}
                </span>
                <span className="tracking-target__list-btn-meta mono">
                  {Math.round(track.confidence * 100)}% · {track.frame_count} 幀
                </span>
              </button>
            );
          })}
        </div>
      )}
      {busy && <p className="tracking-target__busy">套用中…</p>}
      {/* v0.28.0 — async point-tracking status. ``busy`` only covers
          the single PATCH round-trip; once that returns 202 the
          operator has closed the picker but the worker is still
          chewing through the LK loop. This banner stays up until the
          polling effect sees the status flip to ``done`` / ``failed``. */}
      {!busy && detail?.point_tracking_status === "pending" && (
        <p className="tracking-target__busy">
          正在跟住你點的位置…較長或高解析度素材可能需要幾分鐘。
        </p>
      )}
      {error && (
        <p className="tracking-target__hint tracking-target__hint--err">
          無法套用：{error}
        </p>
      )}
      {visibleTracks.length === 0 && (
        <p className="tracking-target__hint">
          這段素材沒有找到明顯主角；可改用「框選區域」或「固定構圖」。
        </p>
      )}
      {visibleTracks.length > 0 && isLegacyTracking(detail) && (
        <p className="tracking-target__hint">
          這是舊版畫面重點資料（單一主角）；重新檢查可取得更多主角選擇。
        </p>
      )}

      {/* v0.23.1 — full-screen point picker. Renders nothing when
         the operator hasn't opened the point tab. ``thumbnailUrl``
         is the same image the inline canvas uses; the modal does
         its own zoom + pan on top. */}
      <PointPickerModal
        open={isPointPickerOpen}
        thumbnailUrl={thumbnailUrl}
        srcW={detail.src_w}
        srcH={detail.src_h}
        busy={busy}
        onCommit={({ norm_x, norm_y }) => {
          // Seed at t=0 — the source thumbnail is taken from the
          // first keyframe so the LK init frame matches what the
          // operator just clicked. ``applyTarget`` itself closes
          // the modal by clearing pendingMode on success.
          void applyTarget("point", undefined, undefined, {
            norm_x,
            norm_y,
            frame_ms: 0,
          });
        }}
        onCancel={() => setPendingMode(null)}
      />
    </div>
  );
}
