import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ApiError, apiClient } from "../api/client";
import PreviewPane from "../components/timeline/PreviewPane";
import RotateHint from "../components/timeline/RotateHint";
import SegmentInspector from "../components/timeline/SegmentInspector";
import TimelineCanvas from "../components/timeline/TimelineCanvas";
import type {
  AssetDetail,
  DraftDetail,
  ProjectDetail,
} from "../api/types";
import "./TimelineEditor.css";

// Phase 1 timeline editor — opt-in "進階編輯" view layered on top of
// the existing draft. All edits mutate DraftSegment rows; rendering
// stays paused until the operator hits Apply, which fires the existing
// PATCH /drafts/{id}/order endpoint with the current order list (that
// endpoint already does the skip-plan re-render enqueue).

const PWA_HINT_DISMISS_KEY = "timeline.pwaHintDismissed";
const QUICK_SPEEDS = [0.5, 1.0, 2.0] as const;

export default function TimelineEditor() {
  const params = useParams();
  const navigate = useNavigate();
  const projectId = Number.parseInt(params.projectId ?? "", 10);
  const draftId = Number.parseInt(params.draftId ?? "", 10);

  const [project, setProject] = useState<ProjectDetail | null>(null);
  const [draft, setDraft] = useState<DraftDetail | null>(null);
  const [assets, setAssets] = useState<AssetDetail[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const [applying, setApplying] = useState(false);

  const [selectedSegmentId, setSelectedSegmentId] = useState<number | null>(
    null,
  );
  const [playheadMs, setPlayheadMs] = useState<number>(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const [speed, setSpeed] = useState<number>(1.0);

  const [isPortrait, setIsPortrait] = useState<boolean>(() =>
    detectPortrait(),
  );
  const [moreOpen, setMoreOpen] = useState(false);
  const [showPwaHint, setShowPwaHint] = useState<boolean>(() =>
    !localStorage.getItem(PWA_HINT_DISMISS_KEY) && isMobileViewport(),
  );

  const moreMenuRef = useRef<HTMLDivElement | null>(null);

  // Body class so global CSS (AppHeader hide on mobile landscape) and
  // page-level fullscreen styling can target this route specifically.
  useEffect(() => {
    document.body.classList.add("timeline-editor-active");
    return () => document.body.classList.remove("timeline-editor-active");
  }, []);

  useEffect(() => {
    const mq = window.matchMedia(
      "(orientation: portrait) and (max-width: 1023px)",
    );
    const listener = () => setIsPortrait(detectPortrait());
    mq.addEventListener?.("change", listener);
    return () => mq.removeEventListener?.("change", listener);
  }, []);

  // Click-outside to close more-options menu.
  useEffect(() => {
    if (!moreOpen) return;
    const onDoc = (e: MouseEvent) => {
      if (
        moreMenuRef.current &&
        !moreMenuRef.current.contains(e.target as Node)
      ) {
        setMoreOpen(false);
      }
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [moreOpen]);

  // Initial load.
  useEffect(() => {
    if (!Number.isFinite(projectId) || !Number.isFinite(draftId)) {
      setLoadError("網址缺少專案或版本資訊");
      return;
    }
    let cancelled = false;
    void (async () => {
      try {
        const [proj, drft] = await Promise.all([
          apiClient.fetchProject(projectId),
          apiClient.fetchDraft(draftId),
        ]);
        if (cancelled) return;
        setProject(proj);
        setDraft(drft);

        // Pull every asset referenced by the draft. The API has no
        // /projects/{id}/assets-detail bulk endpoint with file_path so
        // we fan out per-asset; usually <12 calls per draft.
        const ids = Array.from(
          new Set(
            drft.segments.map((s) => s.asset_id).filter((x): x is number => x != null),
          ),
        );
        const fetched = await Promise.all(
          ids.map((id) => apiClient.fetchAsset(id).catch(() => null)),
        );
        if (cancelled) return;
        setAssets(fetched.filter((a): a is AssetDetail => a !== null));
      } catch (err) {
        if (!cancelled)
          setLoadError(
            err instanceof Error ? err.message : "載入失敗",
          );
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [projectId, draftId]);

  const assetsById = useMemo<Record<number, AssetDetail>>(() => {
    const m: Record<number, AssetDetail> = {};
    for (const a of assets) m[a.id] = a;
    return m;
  }, [assets]);

  const totalMs = useMemo(() => {
    if (!draft) return 0;
    return draft.segments.reduce(
      (max, s) => Math.max(max, s.on_timeline_end_ms),
      0,
    );
  }, [draft]);

  const selectedSegment = useMemo(
    () =>
      draft?.segments.find((s) => s.id === selectedSegmentId) ?? null,
    [draft, selectedSegmentId],
  );

  const selectedAsset =
    selectedSegment?.asset_id != null
      ? assetsById[selectedSegment.asset_id] ?? null
      : null;

  const applyDraftResponse = useCallback((next: DraftDetail) => {
    setDraft(next);
    setDirty(true);
    setActionError(null);
  }, []);

  const handleTrimCommit = useCallback(
    async (
      segId: number,
      patch: { asset_start_ms?: number; asset_end_ms?: number },
    ) => {
      setBusy(true);
      try {
        const next = await apiClient.patchDraftSegment(draftId, segId, patch);
        applyDraftResponse(next);
      } catch (err) {
        setActionError(extractError(err));
      } finally {
        setBusy(false);
      }
    },
    [draftId, applyDraftResponse],
  );

  const handlePatch = useCallback(
    async (patch: Parameters<typeof apiClient.patchDraftSegment>[2]) => {
      if (selectedSegmentId == null) return;
      setBusy(true);
      try {
        const next = await apiClient.patchDraftSegment(
          draftId,
          selectedSegmentId,
          patch,
        );
        applyDraftResponse(next);
      } catch (err) {
        setActionError(extractError(err));
      } finally {
        setBusy(false);
      }
    },
    [draftId, selectedSegmentId, applyDraftResponse],
  );

  const handleSplit = useCallback(async () => {
    if (selectedSegmentId == null || !selectedSegment) return;
    if (
      playheadMs <= selectedSegment.on_timeline_start_ms ||
      playheadMs >= selectedSegment.on_timeline_end_ms
    ) {
      setActionError("播放頭不在這段內，無法分割");
      return;
    }
    setBusy(true);
    try {
      const next = await apiClient.splitDraftSegment(
        draftId,
        selectedSegmentId,
        { at_ms: Math.round(playheadMs) },
      );
      applyDraftResponse(next);
    } catch (err) {
      setActionError(extractError(err));
    } finally {
      setBusy(false);
    }
  }, [draftId, selectedSegmentId, selectedSegment, playheadMs, applyDraftResponse]);

  const handleDelete = useCallback(async () => {
    if (selectedSegmentId == null) return;
    if (!window.confirm("刪除這個片段？這會立即更新時間軸。")) return;
    setBusy(true);
    try {
      await apiClient.deleteDraftSegment(draftId, selectedSegmentId);
      // DELETE returns 204; refetch the draft to get the reflowed shape.
      const next = await apiClient.fetchDraft(draftId);
      setSelectedSegmentId(null);
      applyDraftResponse(next);
    } catch (err) {
      setActionError(extractError(err));
    } finally {
      setBusy(false);
    }
  }, [draftId, selectedSegmentId, applyDraftResponse]);

  const handleApply = useCallback(async () => {
    if (!draft) return;
    setApplying(true);
    setActionError(null);
    try {
      const orders = [...draft.segments]
        .sort((a, b) => a.order - b.order)
        .map((s) => s.id);
      const next = await apiClient.reorderDraftSegments(draftId, { orders });
      setDraft(next);
      setDirty(false);
    } catch (err) {
      setActionError(extractError(err));
    } finally {
      setApplying(false);
    }
  }, [draft, draftId]);

  const dismissPwaHint = useCallback(() => {
    localStorage.setItem(PWA_HINT_DISMISS_KEY, "1");
    setShowPwaHint(false);
  }, []);

  if (loadError) {
    return (
      <main className="timeline-editor timeline-editor--error">
        <p>載入失敗：{loadError}</p>
        <Link to={`/projects/${projectId}/edit`} className="cta cta--secondary">
          ← 返回成品頁
        </Link>
      </main>
    );
  }

  if (!project || !draft) {
    return (
      <main className="timeline-editor timeline-editor--loading">
        <p>載入中…</p>
      </main>
    );
  }

  if (isPortrait) {
    return (
      <main className="timeline-editor">
        <header className="timeline-editor__header timeline-editor__header--mobile">
          <Link
            to={`/projects/${projectId}/edit`}
            className="timeline-editor__icon-btn"
            aria-label="返回成品頁"
            title="返回"
          >
            ←
          </Link>
          <span className="timeline-editor__title">
            {project.name} · v{draft.version}
          </span>
        </header>
        <RotateHint />
      </main>
    );
  }

  const applyTitle = applying
    ? "等待開始…"
    : dirty
      ? "套用變更並重新產生成品"
      : "尚無待套用的變更";
  // v0.22 — short label shown next to the icon so the button reads as
  // "套用 ↻" instead of just an emoji. ``applying`` keeps the dots
  // visible for spinner intent.
  const applyLabel = applying ? "套用中…" : dirty ? "套用變更" : "已套用";

  return (
    <main className="timeline-editor">
      <header className="timeline-editor__header">
        <Link
          to={`/projects/${projectId}/edit`}
          className="timeline-editor__icon-btn"
          aria-label="返回成品頁"
          title="返回成品頁"
        >
          ←
        </Link>
        <span className="timeline-editor__title">
          {project.name}
          <span className="timeline-editor__version mono">
            v{draft.version}
          </span>
          {dirty && (
            <span
              className="timeline-editor__dirty"
              aria-label="有未套用的變更"
              title="有未套用的變更"
            >
              ●
            </span>
          )}
        </span>
        <div className="timeline-editor__actions">
          <button
            type="button"
            className={`timeline-editor__apply-btn ${
              dirty && !applying ? "is-active" : ""
            }`}
            onClick={() => void handleApply()}
            disabled={!dirty || applying || busy}
            title={applyTitle}
            aria-label={applyTitle}
          >
            <span className="timeline-editor__apply-icon" aria-hidden>
              {applying ? "…" : "🔄"}
            </span>
            <span className="timeline-editor__apply-label">{applyLabel}</span>
          </button>
          <div
            className="timeline-editor__more"
            ref={moreMenuRef}
          >
            <button
              type="button"
              className="timeline-editor__icon-btn"
              onClick={() => setMoreOpen((v) => !v)}
              aria-label="更多選項"
              aria-expanded={moreOpen}
              title="更多選項"
            >
              ⋯
            </button>
            {moreOpen && (
              <div
                className="timeline-editor__more-menu"
                role="menu"
              >
                <div className="timeline-editor__more-section">
                  <span className="timeline-editor__more-label">倍速</span>
                  <div className="timeline-editor__speed-row">
                    {QUICK_SPEEDS.map((s) => (
                      <button
                        key={s}
                        type="button"
                        className={`timeline-editor__speed-btn ${
                          speed === s ? "is-active" : ""
                        }`}
                        onClick={() => {
                          setSpeed(s);
                          setMoreOpen(false);
                        }}
                      >
                        {s}×
                      </button>
                    ))}
                  </div>
                </div>
                <div className="timeline-editor__more-divider" />
                <button
                  type="button"
                  className="timeline-editor__more-item"
                  role="menuitem"
                  onClick={() => {
                    setMoreOpen(false);
                    navigate(`/projects/${projectId}/edit`);
                  }}
                >
                  回到成品頁
                </button>
              </div>
            )}
          </div>
        </div>
      </header>
      {actionError && (
        <div className="timeline-editor__error">{actionError}</div>
      )}
      <div className="timeline-editor__main">
        <div className="timeline-editor__preview">
          <PreviewPane
            segments={draft.segments}
            assetsById={assetsById}
            playheadMs={playheadMs}
            onPlayheadMsChange={setPlayheadMs}
            totalMs={totalMs}
            isPlaying={isPlaying}
            onIsPlayingChange={setIsPlaying}
            speed={speed}
            onSpeedChange={setSpeed}
          />
        </div>
        <div className="timeline-editor__canvas">
          <TimelineCanvas
            segments={draft.segments}
            assetsById={assetsById}
            project={project}
            totalMs={totalMs}
            playheadMs={playheadMs}
            onPlayheadMsChange={setPlayheadMs}
            selectedSegmentId={selectedSegmentId}
            onSelectSegment={setSelectedSegmentId}
            onTrimCommit={handleTrimCommit}
          />
        </div>
        <div className="timeline-editor__inspector">
          <SegmentInspector
            segment={selectedSegment}
            asset={selectedAsset}
            playheadMs={playheadMs}
            busy={busy}
            error={actionError}
            onPatch={handlePatch}
            onSplit={handleSplit}
            onDelete={handleDelete}
          />
        </div>
      </div>
      {showPwaHint && (
        <div className="timeline-editor__pwa-hint" role="status">
          <span className="timeline-editor__pwa-hint-text">
            💡 加到主畫面可獲得全螢幕體驗（隱藏瀏覽器工具列）
          </span>
          <button
            type="button"
            className="timeline-editor__pwa-hint-dismiss"
            onClick={dismissPwaHint}
            aria-label="關閉提示"
          >
            ✕
          </button>
        </div>
      )}
    </main>
  );
}

function detectPortrait(): boolean {
  if (typeof window === "undefined") return false;
  const mq = window.matchMedia(
    "(orientation: portrait) and (max-width: 1023px)",
  );
  return mq.matches;
}

function isMobileViewport(): boolean {
  if (typeof window === "undefined") return false;
  return window.matchMedia("(max-width: 1023px)").matches;
}

function extractError(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return String(err);
}
