import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError, apiClient } from "../api/client";
import type {
  AssetAnalysisItem,
  TranscriptOut,
  TranscriptSegmentIn,
  TranscriptSegmentOut,
} from "../api/types";
import { useAssetPolling } from "../hooks/useAssetPolling";
import {
  ANALYSIS_STEP_LABELS,
  labelForAssetStatus,
  labelForMotionType,
  labelForSceneTag,
  labelForStepState,
} from "../i18n/tags";
import "./ProjectAnalysis.css";

const ANALYSIS_STEP_ORDER: ("stt" | "scene" | "motion" | "coverage")[] = [
  "stt",
  "scene",
  "motion",
  "coverage",
];

const TRANSCRIPT_DEBOUNCE_MS = 1500;

function formatDuration(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

function formatPercent(ratio: number): string {
  return `${Math.round(ratio * 1000) / 10}%`;
}

function classifyStepState(value: string | undefined): string {
  if (!value) return "pending";
  if (value.startsWith("failed:")) return "failed";
  return value;
}

interface AnalysisStepChipsProps {
  steps: Record<string, string> | null | undefined;
}

function AnalysisStepChips({ steps }: AnalysisStepChipsProps) {
  return (
    <div className="step-chips">
      {ANALYSIS_STEP_ORDER.map((step) => {
        const raw = steps?.[step];
        const cls = classifyStepState(raw);
        return (
          <span
            key={step}
            className={`step-chip step-chip--${cls}`}
            title={raw ?? "pending"}
          >
            <span className="step-chip__name">{ANALYSIS_STEP_LABELS[step]}</span>
            <span className="step-chip__state">{labelForStepState(raw)}</span>
          </span>
        );
      })}
    </div>
  );
}

interface MotionTimelineProps {
  totalMs: number;
  segments: AssetAnalysisItem["motion_segments"];
}

function MotionTimeline({ totalMs, segments }: MotionTimelineProps) {
  if (totalMs <= 0 || segments.length === 0) {
    return <div className="motion-timeline motion-timeline--empty">無運鏡資訊</div>;
  }
  return (
    <div className="motion-timeline" aria-label="運鏡時間軸">
      {segments.map((seg, i) => {
        const left = (seg.start_ms / totalMs) * 100;
        const width = Math.max(1, ((seg.end_ms - seg.start_ms) / totalMs) * 100);
        return (
          <span
            key={`${seg.motion_type}-${seg.start_ms}-${i}`}
            className={`motion-bar motion-bar--${seg.motion_type}`}
            style={{ left: `${left}%`, width: `${width}%` }}
            title={`${labelForMotionType(seg.motion_type)} ${formatDuration(seg.start_ms)} → ${formatDuration(seg.end_ms)}`}
          />
        );
      })}
    </div>
  );
}

interface CoverageCardProps {
  summary: AssetAnalysisItem["coverage_summary"];
}

function CoverageCard({ summary }: CoverageCardProps) {
  if (!summary) return null;
  const scriptedPct = summary.coverage_ratio_by_duration_ms;
  const improvisedPct = Math.max(0, 1 - scriptedPct);
  return (
    <div className="coverage-card">
      <div className="coverage-card__head">
        <span className="coverage-card__label">腳本覆蓋率</span>
        <span className="coverage-card__nums">
          照稿 {formatPercent(scriptedPct)} · 即興 {formatPercent(improvisedPct)}
        </span>
      </div>
      <div className="coverage-bar">
        <span
          className="coverage-bar__scripted"
          style={{ width: `${Math.round(scriptedPct * 100)}%` }}
          title={`照稿 ${summary.scripted_segment_count} / ${summary.total_segment_count} 段`}
        />
        <span
          className="coverage-bar__improvised"
          style={{ width: `${Math.round(improvisedPct * 100)}%` }}
        />
      </div>
    </div>
  );
}

type SaveState = "idle" | "saving" | "saved" | "error";

interface TranscriptEditorProps {
  assetId: number;
}

function TranscriptEditor({ assetId }: TranscriptEditorProps) {
  const [transcript, setTranscript] = useState<TranscriptOut | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [saveState, setSaveState] = useState<SaveState>("idle");
  const [saveError, setSaveError] = useState<string | null>(null);
  const [savedAt, setSavedAt] = useState<Date | null>(null);

  const segmentsRef = useRef<TranscriptSegmentOut[]>([]);
  const debounceRef = useRef<number | null>(null);
  const retryRef = useRef<{ attempt: number }>({ attempt: 0 });

  // Initial fetch.
  useEffect(() => {
    let cancelled = false;
    apiClient
      .fetchTranscript(assetId)
      .then((tx) => {
        if (cancelled) return;
        if (tx) {
          setTranscript(tx);
          segmentsRef.current = tx.segments;
        } else {
          setTranscript(null);
        }
        setLoaded(true);
      })
      .catch((err) => {
        if (cancelled) return;
        setSaveError(err instanceof Error ? err.message : String(err));
        setLoaded(true);
      });
    return () => {
      cancelled = true;
    };
  }, [assetId]);

  const performSave = useCallback(
    async (segments: TranscriptSegmentOut[]) => {
      setSaveState("saving");
      const payload: TranscriptSegmentIn[] = segments.map((s) => ({
        start_ms: s.start_ms,
        end_ms: s.end_ms,
        text: s.text,
      }));
      try {
        const updated = await apiClient.putTranscript(assetId, { segments: payload });
        setTranscript(updated);
        segmentsRef.current = updated.segments;
        setSaveState("saved");
        setSavedAt(new Date());
        setSaveError(null);
        retryRef.current.attempt = 0;
      } catch (err) {
        const msg =
          err instanceof ApiError
            ? `儲存失敗 (${err.status})`
            : err instanceof Error
              ? err.message
              : String(err);
        setSaveError(msg);
        setSaveState("error");
        const attempt = ++retryRef.current.attempt;
        const backoff = [1000, 3000, 10_000][Math.min(attempt - 1, 2)];
        if (attempt <= 3) {
          window.setTimeout(() => {
            void performSave(segmentsRef.current);
          }, backoff);
        }
      }
    },
    [assetId],
  );

  const handleSegmentChange = useCallback(
    (idx: number, value: string) => {
      const next = segmentsRef.current.map((s) =>
        s.idx === idx ? { ...s, text: value } : s,
      );
      segmentsRef.current = next;
      // Re-render so the textarea displays the new value.
      setTranscript((prev) => (prev ? { ...prev, segments: next } : prev));
      if (debounceRef.current !== null) {
        window.clearTimeout(debounceRef.current);
      }
      debounceRef.current = window.setTimeout(() => {
        void performSave(segmentsRef.current);
      }, TRANSCRIPT_DEBOUNCE_MS);
    },
    [performSave],
  );

  if (!loaded) {
    return <div className="transcript-empty">逐字稿載入中…</div>;
  }
  if (!transcript) {
    return <div className="transcript-empty">尚未產生逐字稿。</div>;
  }
  return (
    <div className="transcript-editor">
      <div className="transcript-status">
        {saveState === "saving" && <span className="save-pill">儲存中…</span>}
        {saveState === "saved" && savedAt && (
          <span className="save-pill save-pill--ok">
            已儲存{" "}
            {savedAt.getHours().toString().padStart(2, "0")}:
            {savedAt.getMinutes().toString().padStart(2, "0")}
          </span>
        )}
        {saveState === "error" && (
          <span className="save-pill save-pill--err">
            {saveError ?? "儲存失敗，重試中"}
          </span>
        )}
        {transcript.edited && saveState !== "saving" && (
          <span className="save-pill save-pill--edited">已編輯</span>
        )}
      </div>
      <ol className="transcript-list">
        {transcript.segments.map((seg) => (
          <li key={seg.idx} className="transcript-item">
            <div className="transcript-item__time">
              {formatDuration(seg.start_ms)} → {formatDuration(seg.end_ms)}
            </div>
            <textarea
              className="transcript-item__text"
              value={seg.text}
              rows={Math.min(4, Math.max(1, Math.ceil(seg.text.length / 24)))}
              onChange={(e) => handleSegmentChange(seg.idx, e.currentTarget.value)}
              spellCheck={false}
            />
          </li>
        ))}
      </ol>
    </div>
  );
}

interface AssetCardProps {
  asset: AssetAnalysisItem;
  onAnalyze: (assetId: number, force: boolean) => void;
}

function AssetCard({ asset, onAnalyze }: AssetCardProps) {
  const [expanded, setExpanded] = useState(false);
  return (
    <article className="asset-card" data-status={asset.status}>
      <header className="asset-card__head">
        <div className="asset-card__title">
          <h3 className="asset-card__filename">{asset.filename}</h3>
          <span className="asset-card__duration mono">{formatDuration(asset.duration_ms)}</span>
        </div>
        <span
          className={`asset-status asset-status--${asset.status}`}
          title={`asset.status = ${asset.status}`}
        >
          {labelForAssetStatus(asset.status)}
        </span>
      </header>

      <AnalysisStepChips steps={asset.analysis_steps} />

      {asset.scene_tags.length > 0 && (
        <div className="scene-tag-row" aria-label="場景標籤">
          {asset.scene_tags.map((t) => (
            <span key={t.name} className="scene-chip" title={`${t.confidence.toFixed(2)}`}>
              {labelForSceneTag(t.name)}
            </span>
          ))}
        </div>
      )}

      <MotionTimeline totalMs={asset.duration_ms} segments={asset.motion_segments} />

      <CoverageCard summary={asset.coverage_summary} />

      <div className="asset-card__transcript-toggle">
        <button
          type="button"
          className="cta cta--quiet"
          onClick={() => setExpanded((e) => !e)}
        >
          {expanded ? "收合逐字稿" : "展開逐字稿"}
        </button>
        {asset.transcript_summary && (
          <span className="transcript-summary mono">
            {asset.transcript_summary.segment_count} 段
            {asset.transcript_summary.edited && " · 已編輯"}
          </span>
        )}
      </div>

      {expanded && <TranscriptEditor assetId={asset.id} />}

      <footer className="asset-card__actions">
        <button
          type="button"
          className="cta cta--quiet"
          onClick={() => onAnalyze(asset.id, false)}
        >
          重新分析
        </button>
        <button
          type="button"
          className="cta"
          onClick={() => {
            if (window.confirm("強制重跑會覆蓋手動編輯過的逐字稿。確定要繼續？")) {
              onAnalyze(asset.id, true);
            }
          }}
        >
          強制重跑
        </button>
      </footer>
    </article>
  );
}

export default function ProjectAnalysis() {
  const params = useParams<{ id: string }>();
  const projectId = params.id ? Number(params.id) : NaN;
  const validProjectId = Number.isFinite(projectId) ? projectId : null;
  const polling = useAssetPolling(validProjectId);
  const [triggerError, setTriggerError] = useState<string | null>(null);

  const handleAnalyze = useCallback(
    async (assetId: number, force: boolean) => {
      try {
        await apiClient.triggerAnalyze(assetId, { force });
        polling.refresh();
      } catch (err) {
        setTriggerError(err instanceof Error ? err.message : String(err));
      }
    },
    [polling],
  );

  const project = polling.data?.project;
  const assets = polling.data?.assets ?? [];

  const overallStatus = useMemo(() => {
    if (assets.length === 0) return "尚無素材";
    const counts: Record<string, number> = {};
    for (const a of assets) counts[a.status] = (counts[a.status] ?? 0) + 1;
    const parts = Object.entries(counts).map(
      ([k, v]) => `${labelForAssetStatus(k)} ${v}`,
    );
    return parts.join(" · ");
  }, [assets]);

  return (
    <main className="page project-analysis">
      <header className="analysis-hero">
        <div className="analysis-hero__kicker">素材分析</div>
        <h1 className="analysis-hero__title">
          {project ? project.name : "載入中…"}
        </h1>
        <p className="analysis-hero__lede mono">
          {overallStatus}
          {polling.isPolling && (
            <span className="polling-indicator" aria-live="polite">
              {" · 更新中"}
            </span>
          )}
        </p>
        <div className="analysis-hero__actions">
          <Link to={`/projects/${validProjectId}/upload`} className="cta cta--quiet">
            ← 回到上傳
          </Link>
          <Link to="/" className="cta cta--quiet">
            專案清單
          </Link>
        </div>
        {!polling.data?.has_script && project && (
          <p className="analysis-hint">
            尚未設定腳本 — 對稿步驟會自動跳過。
            <Link to={`/projects/${validProjectId}/upload`}>前往設定 →</Link>
          </p>
        )}
        {triggerError && (
          <p className="analysis-error" role="alert">
            觸發分析失敗：{triggerError}
          </p>
        )}
        {polling.error && (
          <p className="analysis-error" role="alert">
            載入失敗：{polling.error.message}
          </p>
        )}
      </header>

      <section className="asset-list">
        {polling.loading && !polling.data && (
          <div className="board__notice mono">載入中…</div>
        )}
        {polling.data && assets.length === 0 && (
          <div className="board__notice mono">這個專案尚未上傳素材。</div>
        )}
        {assets.map((asset) => (
          <AssetCard key={asset.id} asset={asset} onAnalyze={handleAnalyze} />
        ))}
      </section>
    </main>
  );
}
