import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError, apiClient } from "../api/client";
import type {
  AnalysisStep,
  AssetAnalysisItem,
  TranscriptOut,
  TranscriptSegmentIn,
  TranscriptSegmentOut,
} from "../api/types";
import AssetTrackingTarget from "../components/AssetTrackingTarget";
import { useAssetPolling } from "../hooks/useAssetPolling";
import {
  ANALYSIS_STEP_LABELS,
  iconForEmotionTag,
  labelForAssetStatus,
  labelForEmotionTag,
  labelForMotionType,
  labelForSceneTag,
  labelForStepState,
  labelForTrackingSubject,
} from "../i18n/tags";
import "./ProjectAnalysis.css";

const ANALYSIS_STEP_ORDER: AnalysisStep[] = [
  "stt",
  "scene",
  "motion",
  "emotion",
  "tracking",
  "coverage",
];

// v0.20.2 — emoji-leading icons for each step. Helps the step-card row
// scan as 6 distinct things at a glance instead of a wall of identical
// chips. Pure visual aid; the localized name still drives semantics.
const ANALYSIS_STEP_ICONS: Record<AnalysisStep, string> = {
  stt: "🗣",
  scene: "🏞",
  motion: "🎥",
  emotion: "😊",
  tracking: "🎯",
  coverage: "📜",
};

const TRANSCRIPT_DEBOUNCE_MS = 1500;

function formatDuration(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

// v0.26.0 — bytes → "45.2 MB" / "1.3 GB" / "240 KB". Falls back to a
// dash for null / unknown so the asset card line stays uniform-width.
function formatBytes(bytes: number | null | undefined): string {
  if (bytes == null) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

function formatPercent(ratio: number): string {
  return `${Math.round(ratio * 1000) / 10}%`;
}

function classifyStepState(value: string | undefined): string {
  if (!value) return "pending";
  if (value.startsWith("failed:")) return "failed";
  if (value.startsWith("skipped:")) return "skipped";
  return value;
}

// v0.20.2 — step-summary text. Pulls a short, meaningful description
// out of the asset's analysis output so the user can tell what each
// analysis step *produced* without expanding the asset.
function summaryForStep(
  step: AnalysisStep,
  asset: AssetAnalysisItem,
): string | null {
  const raw = asset.analysis_steps?.[step];
  // Only show summaries for steps that actually finished. Other states
  // are conveyed by the state pill itself.
  if (raw !== "done") return null;

  switch (step) {
    case "stt": {
      const tx = asset.transcript_summary;
      if (!tx) return null;
      const editedTag = tx.edited ? " · 已編輯" : "";
      return `${tx.segment_count} 段字幕${editedTag}`;
    }
    case "scene": {
      const tags = asset.scene_tags;
      if (tags.length === 0) return "未找到明顯場景";
      // Top tag + count — tags are already sorted by confidence by the
      // analysis pipeline.
      const top = tags[0];
      const moreCount = Math.max(0, tags.length - 1);
      const moreText = moreCount > 0 ? ` 等 ${tags.length} 標` : "";
      return `${labelForSceneTag(top.name)}${moreText}`;
    }
    case "motion": {
      const segs = asset.motion_segments;
      if (segs.length === 0) return "未找到明顯畫面動態";
      // Bucket by motion_type, get the dominant by total ms.
      const bucket: Record<string, number> = {};
      for (const s of segs) {
        const dur = Math.max(0, s.end_ms - s.start_ms);
        bucket[s.motion_type] = (bucket[s.motion_type] ?? 0) + dur;
      }
      const total = Object.values(bucket).reduce((a, b) => a + b, 0);
      if (total <= 0) return `${segs.length} 段`;
      const sorted = Object.entries(bucket).sort((a, b) => b[1] - a[1]);
      const [topName, topDur] = sorted[0];
      const pct = Math.round((topDur / total) * 100);
      return `${labelForMotionType(topName)} ${pct}%`;
    }
    case "emotion": {
      const e = asset.emotion_tags;
      if (!e) return "未找到明顯情緒";
      const dom = labelForEmotionTag(e.dominant);
      const ranges = e.ranges.length;
      return ranges > 1 ? `${dom} 等 ${ranges} 段` : dom;
    }
    case "tracking": {
      const t = asset.tracking_summary;
      if (!t || !t.subject_class) return "未找到明顯主角";
      const conf = Math.round((t.confidence ?? 0) * 100);
      return `${labelForTrackingSubject(t.subject_class)} ${conf}%`;
    }
    case "coverage": {
      const c = asset.coverage_summary;
      if (!c) return null;
      const pct = Math.round(c.coverage_ratio_by_duration_ms * 100);
      return `照稿 ${pct}%`;
    }
  }
}

interface AnalysisStepStatusGridProps {
  asset: AssetAnalysisItem;
  onRetryStep: (assetId: number, step: AnalysisStep) => void;
  retryingStep: AnalysisStep | null;
}

// v0.20.2 — per-step status cards (replaces the old <AnalysisStepChips>).
// Each step gets:
//  * a coloured state pill (4 states: pending / running / done / failed,
//    plus skipped),
//  * a one-line summary built from the analysis output (e.g. "32 段字幕"),
//  * an icon-only retry button on done / failed / skipped states.
//
// The retry button calls the regular /assets/{id}/analyze endpoint with
// ``steps: [<this step>]`` so we don't re-run the whole pipeline.
function AnalysisStepStatusGrid({
  asset,
  onRetryStep,
  retryingStep,
}: AnalysisStepStatusGridProps) {
  return (
    <div className="step-grid" role="list" aria-label="素材檢查項目狀態">
      {ANALYSIS_STEP_ORDER.map((step) => {
        const raw = asset.analysis_steps?.[step];
        const cls = classifyStepState(raw);
        const summary = summaryForStep(step, asset);
        // Retry makes sense once the step has *settled* — running/pending
        // is wasted work because the worker is already on it (or will be).
        const canRetry = cls === "done" || cls === "failed" || cls === "skipped";
        const busy = retryingStep === step;
        // v0.22.0 — show the friendly mapped reason (e.g.「GPU 不可用」)
        // when we recognise the token; fall back to the raw tail for
        // unmapped errors so debugging info isn't fully hidden.
        const failureDetail =
          raw && raw.startsWith("failed:")
            ? labelForStepState(raw) === "失敗"
              ? raw.slice("failed:".length)
              : labelForStepState(raw)
            : null;
        return (
          <div
            key={step}
            className={`step-card step-card--${cls}`}
            role="listitem"
          >
            <div className="step-card__head">
              <span className="step-card__icon" aria-hidden>
                {ANALYSIS_STEP_ICONS[step]}
              </span>
              <span className="step-card__name">
                {ANALYSIS_STEP_LABELS[step]}
              </span>
              <span className="step-card__pill mono" title={raw ?? "pending"}>
                {labelForStepState(raw)}
              </span>
              {canRetry && (
                <button
                  type="button"
                  className={
                    "step-card__retry"
                    + (cls === "failed" ? " step-card__retry--prominent" : "")
                  }
                  aria-label={`重新檢查「${ANALYSIS_STEP_LABELS[step]}」`}
                  title={`重新檢查「${ANALYSIS_STEP_LABELS[step]}」`}
                  disabled={busy}
                  onClick={() => onRetryStep(asset.id, step)}
                >
                  <span className="step-card__retry-icon" aria-hidden>
                    {busy ? "⋯" : "↻"}
                  </span>
                  <span className="step-card__retry-label">
                    {busy ? "重試中" : "重試"}
                  </span>
                </button>
              )}
            </div>
            {summary && (
              <div className="step-card__summary mono" title={summary}>
                {summary}
              </div>
            )}
            {cls === "failed" && failureDetail && (
              <div
                className="step-card__error mono"
                title={failureDetail}
                aria-label="失敗原因"
              >
                {failureDetail}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

// v0.20.2 — top-of-page roll-up. Counts every (asset × step) cell so
// the user knows whether the whole project is analysed or there are
// stragglers, without having to scan each card. ``mode`` toggles the
// granularity between "by asset" (each asset's overall status) and
// "by step" (cells of the analysis matrix).
interface AnalysisProgressSummaryProps {
  assets: AssetAnalysisItem[];
}

function AnalysisProgressSummary({ assets }: AnalysisProgressSummaryProps) {
  const counts = useMemo(() => {
    const out = {
      assetTotal: assets.length,
      assetDone: 0,
      assetRunning: 0,
      assetFailed: 0,
      assetPending: 0,
      stepTotal: 0,
      stepDone: 0,
      stepRunning: 0,
      stepFailed: 0,
      stepPending: 0,
      stepSkipped: 0,
    };
    for (const a of assets) {
      // Per-asset status from the asset-level pill (already aggregated
      // by the analyser).
      switch (a.status) {
        case "analyzed":
          out.assetDone += 1;
          break;
        case "analyzing":
          out.assetRunning += 1;
          break;
        case "analysis_failed":
          out.assetFailed += 1;
          break;
        default:
          out.assetPending += 1;
      }
      const steps = a.analysis_steps ?? {};
      for (const s of ANALYSIS_STEP_ORDER) {
        out.stepTotal += 1;
        const cls = classifyStepState(steps[s]);
        if (cls === "done") out.stepDone += 1;
        else if (cls === "running") out.stepRunning += 1;
        else if (cls === "failed") out.stepFailed += 1;
        else if (cls === "skipped") out.stepSkipped += 1;
        else out.stepPending += 1;
      }
    }
    return out;
  }, [assets]);

  if (counts.assetTotal === 0) return null;

  const stepPctDone =
    counts.stepTotal > 0
      ? Math.round((counts.stepDone / counts.stepTotal) * 100)
      : 0;

  return (
    <section
      className="analysis-progress-summary"
      aria-label="素材檢查進度"
    >
      <div className="analysis-progress-summary__head">
        <span className="analysis-progress-summary__title">
          素材檢查進度
        </span>
        <span className="analysis-progress-summary__nums mono">
          {counts.stepDone} / {counts.stepTotal} 項完成（{stepPctDone}%）
        </span>
      </div>
      <div
        className="analysis-progress-summary__bar"
        role="progressbar"
        aria-valuenow={stepPctDone}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <span
          className="analysis-progress-summary__bar-fill"
          style={{ width: `${stepPctDone}%` }}
        />
      </div>
      <div className="analysis-progress-summary__breakdown mono">
        <span className="aps-pill aps-pill--done">
          ✓ 完成 {counts.stepDone}
        </span>
        {counts.stepRunning > 0 && (
          <span className="aps-pill aps-pill--running">
            ⏵ 進行中 {counts.stepRunning}
          </span>
        )}
        {counts.stepPending > 0 && (
          <span className="aps-pill aps-pill--pending">
            · 等待檢查 {counts.stepPending}
          </span>
        )}
        {counts.stepFailed > 0 && (
          <span className="aps-pill aps-pill--failed">
            ✗ 失敗 {counts.stepFailed}
          </span>
        )}
        {counts.stepSkipped > 0 && (
          <span className="aps-pill aps-pill--skipped">
            – 略過 {counts.stepSkipped}
          </span>
        )}
      </div>
    </section>
  );
}

interface MotionTimelineProps {
  totalMs: number;
  segments: AssetAnalysisItem["motion_segments"];
}

function MotionTimeline({ totalMs, segments }: MotionTimelineProps) {
  if (totalMs <= 0 || segments.length === 0) {
    return <div className="motion-timeline motion-timeline--empty">無畫面動態資訊</div>;
  }
  return (
    <div className="motion-timeline" aria-label="畫面動態時間軸">
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

interface AutoResizeTextareaProps {
  value: string;
  onChange: (value: string) => void;
  className?: string;
  minRows?: number;
}

function AutoResizeTextarea({
  value,
  onChange,
  className,
  minRows = 4,
}: AutoResizeTextareaProps) {
  const ref = useRef<HTMLTextAreaElement | null>(null);

  // Grow the textarea so the entire transcript segment is visible without
  // an inner scrollbar. Resets to "auto" first so the box can shrink when
  // text is deleted.
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }, [value]);

  return (
    <textarea
      ref={ref}
      className={className}
      value={value}
      rows={minRows}
      onChange={(e) => onChange(e.currentTarget.value)}
      spellCheck={false}
    />
  );
}

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
            <AutoResizeTextarea
              className="transcript-item__text"
              value={seg.text}
              onChange={(value) => handleSegmentChange(seg.idx, value)}
              minRows={4}
            />
          </li>
        ))}
      </ol>
    </div>
  );
}

function isAssetUnanalyzed(asset: AssetAnalysisItem): boolean {
  // "Unanalyzed" = at least one of the 4 pipeline steps hasn't reached "done".
  // Failed / pending / missing all qualify so the user can sweep them up in
  // a single batch.
  const steps = asset.analysis_steps ?? {};
  return ANALYSIS_STEP_ORDER.some((s) => steps[s] !== "done");
}

interface ThumbnailGalleryProps {
  assetId: number;
  filename: string;
  thumbnails: string[];
}

function ThumbnailGallery({ assetId, filename, thumbnails }: ThumbnailGalleryProps) {
  if (thumbnails.length === 0) {
    return (
      <div
        className="thumb-gallery thumb-gallery--empty"
        aria-label={`${filename} 縮圖尚未產生`}
      >
        <span className="thumb-gallery__placeholder mono">尚未產生縮圖</span>
      </div>
    );
  }
  return (
    <div
      className="thumb-gallery"
      role="group"
      aria-label={`${filename} 縮圖`}
    >
      {thumbnails.map((url, i) => (
        <img
          key={`${assetId}-${i}-${url}`}
          src={url}
          alt={`${filename} 第 ${i + 1} 幀`}
          className="thumb-gallery__frame"
          loading="lazy"
          decoding="async"
          draggable={false}
        />
      ))}
    </div>
  );
}

interface AssetCardProps {
  asset: AssetAnalysisItem;
  onAnalyze: (assetId: number, force: boolean) => void;
  onRetryStep: (assetId: number, step: AnalysisStep) => void;
  retryingStep: AnalysisStep | null;
  onTranslate: (assetId: number) => void;
  translating: boolean;
  selected: boolean;
  onToggleSelect: (assetId: number, next: boolean) => void;
}

interface SecondarySubtitleToggleProps {
  asset: AssetAnalysisItem;
  onTranslate: (assetId: number) => void;
  translating: boolean;
}

// v0.18 — analysis-page chip + button for the optional second-language
// subtitle. Disabled until the primary STT step has finished (translate
// uses the same audio path; running both in parallel just hammers the
// GPU). The chip appears once a translation has been generated.
function SecondarySubtitleToggle({
  asset,
  onTranslate,
  translating,
}: SecondarySubtitleToggleProps) {
  const sttDone = asset.analysis_steps?.stt === "done";
  const summary = asset.secondary_subtitle_summary ?? null;
  const buttonLabel = (() => {
    if (translating) return "翻譯中…";
    if (summary) return "重新翻譯英文";
    return "產生英文字幕";
  })();
  const disabled = translating || !sttDone;
  return (
    <div className="secondary-subtitle-toggle">
      {summary && (
        <span
          className="secondary-subtitle-chip mono"
          title={`${summary.lang.toUpperCase()} ${summary.segment_count} 段`}
        >
          {summary.lang.toUpperCase()} · {summary.segment_count} 段
        </span>
      )}
      <button
        type="button"
        className="cta cta--quiet"
        onClick={() => onTranslate(asset.id)}
        disabled={disabled}
        title={
          sttDone
            ? "把這個素材翻成英文字幕（可疊在主字幕之上）"
            : "請先完成語音文字檢查，才能翻譯"
        }
      >
        {buttonLabel}
      </button>
    </div>
  );
}

function AssetCard({
  asset,
  onAnalyze,
  onRetryStep,
  retryingStep,
  onTranslate,
  translating,
  selected,
  onToggleSelect,
}: AssetCardProps) {
  const [expanded, setExpanded] = useState(false);
  return (
    <article className="asset-card" data-status={asset.status}>
      <ThumbnailGallery
        assetId={asset.id}
        filename={asset.filename}
        thumbnails={asset.thumbnail_urls}
      />
      <header className="asset-card__head">
        <label
          className="asset-card__select"
          aria-label={`選擇素材 ${asset.filename}`}
          onClick={(e) => e.stopPropagation()}
        >
          <input
            type="checkbox"
            checked={selected}
            onChange={(e) => onToggleSelect(asset.id, e.currentTarget.checked)}
          />
        </label>
        <div className="asset-card__title">
          <h3 className="asset-card__filename">{asset.filename}</h3>
          {/* v0.26.0 — single-line spec: duration · resolution · size.
              Each segment falls back to a dash when the underlying
              value is null so the line keeps a stable shape. */}
          <span className="asset-card__meta mono">
            {formatDuration(asset.duration_ms)}
            {asset.resolution ? ` · ${asset.resolution}` : ""}
            {" · "}
            {formatBytes(asset.file_size_bytes)}
          </span>
        </div>
        <span
          className={`asset-status asset-status--${asset.status}`}
          title={labelForAssetStatus(asset.status)}
        >
          {labelForAssetStatus(asset.status)}
        </span>
      </header>

      {asset.tracking_summary && (
        <div
          className={`tracking-chip tracking-chip--${
            asset.tracking_summary.subject_class || "none"
          }`}
          aria-label={
            asset.tracking_summary.subject_class
              ? `畫面主角：${labelForTrackingSubject(
                  asset.tracking_summary.subject_class,
                )}`
              : "未找到明顯主角"
          }
          title={
            asset.tracking_summary.subject_class
              ? `${asset.tracking_summary.frame_count} 幀（共取樣 ${asset.tracking_summary.sampled_frames}），平均信心 ${(
                  asset.tracking_summary.confidence * 100
                ).toFixed(0)}%`
              : "本片段沒有找到可跟住的主角"
          }
        >
          <span className="tracking-chip__icon" aria-hidden>
            🎯
          </span>
          <span className="tracking-chip__label">
            {asset.tracking_summary.subject_class
              ? `主角：${labelForTrackingSubject(asset.tracking_summary.subject_class)}（${(
                  asset.tracking_summary.confidence * 100
                ).toFixed(0)}%）`
              : "主角：未找到"}
          </span>
        </div>
      )}

      {asset.tracking_summary && (
        <AssetTrackingTarget
          assetId={asset.id}
          thumbnailUrl={asset.thumbnail_urls[0] ?? null}
        />
      )}

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

      <SecondarySubtitleToggle
        asset={asset}
        onTranslate={onTranslate}
        translating={translating}
      />

      <AnalysisStepStatusGrid
        asset={asset}
        onRetryStep={onRetryStep}
        retryingStep={retryingStep}
      />

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

      {asset.emotion_tags && (
        <div
          className={`emotion-chip emotion-chip--${asset.emotion_tags.dominant}`}
          aria-label={`主要情緒：${labelForEmotionTag(asset.emotion_tags.dominant)}`}
          title={`${asset.emotion_tags.ranges.length} 段情緒（${asset.emotion_tags.ranges
            .map((r) => labelForEmotionTag(r.emotion))
            .join(" / ")}）`}
        >
          <span className="emotion-chip__icon" aria-hidden>
            {iconForEmotionTag(asset.emotion_tags.dominant)}
          </span>
          <span className="emotion-chip__label">
            {labelForEmotionTag(asset.emotion_tags.dominant)}
          </span>
        </div>
      )}

      <CoverageCard summary={asset.coverage_summary} />

      <footer className="asset-card__actions">
        <button
          type="button"
          className="cta cta--quiet"
          onClick={() => onAnalyze(asset.id, false)}
          title="只重新檢查尚未完成或失敗的項目；手動編輯過的字幕會保留。"
        >
          重新檢查（保留手改）
        </button>
        <button
          type="button"
          className="cta"
          onClick={() => {
            if (
              window.confirm(
                "重新檢查全部會覆蓋手動編輯過的逐字稿與字幕。確定要繼續？",
              )
            ) {
              onAnalyze(asset.id, true);
            }
          }}
          title="所有檢查項目全部重跑，並覆寫手動編輯過的字幕。"
        >
          重新檢查全部（覆寫手改）
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
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [batchRunning, setBatchRunning] = useState(false);
  // v0.18 — track per-asset translate-button busy state. The job runs
  // on the worker (poll picks up the new ``secondary_subtitle_summary``
  // when done); the local set just keeps the button disabled long
  // enough to communicate "queued" to the user.
  const [translatingIds, setTranslatingIds] = useState<Set<number>>(new Set());
  // v0.20.2 — track per-asset per-step retry. Map assetId -> step name
  // currently being retried. Lets the AssetCard show the spinner on
  // exactly the right step button without leaking state across cards.
  const [retryingMap, setRetryingMap] = useState<
    Record<number, AnalysisStep | null>
  >({});

  const handleRetryStep = useCallback(
    async (assetId: number, step: AnalysisStep) => {
      setRetryingMap((prev) => ({ ...prev, [assetId]: step }));
      setStatusMessage(null);
      try {
        await apiClient.triggerAnalyze(assetId, { steps: [step], force: true });
        polling.refresh();
      } catch (err) {
        setTriggerError(
          err instanceof Error
            ? `重新檢查「${ANALYSIS_STEP_LABELS[step]}」失敗：${err.message}`
            : String(err),
        );
      } finally {
        // Brief delay so the spinner is visible even if the queue
        // accepted instantly. Polling will then flip the chip.
        window.setTimeout(() => {
          setRetryingMap((prev) => {
            if (prev[assetId] !== step) return prev;
            const next = { ...prev };
            next[assetId] = null;
            return next;
          });
        }, 1500);
      }
    },
    [polling],
  );

  const handleAnalyze = useCallback(
    async (assetId: number, force: boolean) => {
      setStatusMessage(null);
      try {
        await apiClient.triggerAnalyze(assetId, { force });
        polling.refresh();
      } catch (err) {
        setTriggerError(err instanceof Error ? err.message : String(err));
      }
    },
    [polling],
  );

  const handleTranslate = useCallback(
    async (assetId: number) => {
      setStatusMessage(null);
      setTranslatingIds((prev) => {
        const out = new Set(prev);
        out.add(assetId);
        return out;
      });
      try {
        await apiClient.triggerSubtitleTranslate(assetId, { lang: "en" });
        polling.refresh();
      } catch (err) {
        setTriggerError(
          err instanceof Error
            ? `英文字幕建立失敗：${err.message}`
            : String(err),
        );
      } finally {
        // The worker job runs async; clear the local busy flag after
        // a short delay so the user sees "翻譯中…" briefly even when
        // the queue accepts instantly. Polling will swap the button
        // label to "重新翻譯英文" once the secondary segments land.
        window.setTimeout(() => {
          setTranslatingIds((prev) => {
            if (!prev.has(assetId)) return prev;
            const out = new Set(prev);
            out.delete(assetId);
            return out;
          });
        }, 2000);
      }
    },
    [polling],
  );

  const project = polling.data?.project;
  const assets = polling.data?.assets ?? [];

  const unanalyzedIds = useMemo(
    () => assets.filter(isAssetUnanalyzed).map((a) => a.id),
    [assets],
  );

  // Drop selection IDs that no longer exist in the current asset list (e.g.
  // after deletion / refresh) so "全選未分析" stays consistent.
  useEffect(() => {
    const valid = new Set(assets.map((a) => a.id));
    setSelectedIds((prev) => {
      let changed = false;
      const next = new Set<number>();
      for (const id of prev) {
        if (valid.has(id)) next.add(id);
        else changed = true;
      }
      return changed ? next : prev;
    });
  }, [assets]);

  const allUnanalyzedSelected =
    unanalyzedIds.length > 0 &&
    unanalyzedIds.every((id) => selectedIds.has(id));

  const toggleSelect = useCallback((assetId: number, next: boolean) => {
    setSelectedIds((prev) => {
      const out = new Set(prev);
      if (next) out.add(assetId);
      else out.delete(assetId);
      return out;
    });
  }, []);

  const toggleSelectAllUnanalyzed = useCallback(() => {
    setSelectedIds((prev) => {
      if (unanalyzedIds.every((id) => prev.has(id))) {
        const out = new Set(prev);
        for (const id of unanalyzedIds) out.delete(id);
        return out;
      }
      const out = new Set(prev);
      for (const id of unanalyzedIds) out.add(id);
      return out;
    });
  }, [unanalyzedIds]);

  const clearSelection = useCallback(() => setSelectedIds(new Set()), []);

  const runBatchAnalyze = useCallback(
    async (force: boolean) => {
      if (selectedIds.size === 0) return;
      if (force) {
        if (
          !window.confirm(
            `將重新檢查 ${selectedIds.size} 個素材的全部項目，會覆蓋手動編輯的逐字稿。確定？`,
          )
        ) {
          return;
        }
      }
      setBatchRunning(true);
      setTriggerError(null);
      setStatusMessage(null);
      const ids = Array.from(selectedIds);
      let failed = 0;
      // Sequential trigger — each call hits the API/queue cheaply, and
      // sequential posts make any 5xx easier to attribute to one asset.
      for (const id of ids) {
        try {
          await apiClient.triggerAnalyze(id, { force });
        } catch (err) {
          failed += 1;
          setTriggerError(
            `素材 #${id} 送出檢查失敗：${
              err instanceof Error ? err.message : String(err)
            }`,
          );
        }
      }
      setBatchRunning(false);
      if (failed === 0) clearSelection();
      polling.refresh();
    },
    [selectedIds, polling, clearSelection],
  );

  // v0.26.0 / v0.27.1 — bulk asset delete. Two-phase flow:
  //
  //   1. First call with force=false. Backend deletes assets that
  //      have no active drafts referencing them, and returns
  //      ``deleted=false`` + ``affected_drafts`` for the rest.
  //   2. If any rows came back as needs-force, surface a grouped
  //      confirm listing every blocked row + the active draft
  //      versions referencing it.
  //   3. On confirm, re-issue the SAME id list with force=true.
  //      Backend wipes their draft segments; drafts that lose every
  //      segment flip to status=failed + prompt_feedback="素材已
  //      被刪除". Asset is then deleted normally.
  //
  // The pre-0.27.1 path threw a 409 and made the operator manually
  // reject each draft first — way too many clicks for a bulk-clean.
  const runBatchDelete = useCallback(async () => {
    if (selectedIds.size === 0 || validProjectId == null) return;
    if (
      !window.confirm(
        `確定要刪除 ${selectedIds.size} 個素材？檔案、縮圖、畫面重點資料都會一起清掉，無法復原。`,
      )
    ) {
      return;
    }
    setBatchRunning(true);
    setTriggerError(null);
    const ids = Array.from(selectedIds);
    try {
      let summary = await apiClient.batchDeleteAssets(validProjectId, ids);
      if (summary.needs_force_count > 0) {
        const lines = summary.results
          .filter((r) => !r.deleted && r.affected_drafts.length > 0)
          .map((r) => {
            const versions = r.affected_drafts
              .map((d) => `v${d.version}`)
              .join("、");
            return `素材 #${r.asset_id} 被 ${versions} 使用中`;
          });
        const confirmed = window.confirm(
          [
            `${summary.needs_force_count} 個素材正被使用中：`,
            ...lines,
            "",
            "刪除後上述版本將被標為「失敗（素材已被刪除）」。確定刪除？",
          ].join("\n"),
        );
        if (confirmed) {
          summary = await apiClient.batchDeleteAssets(
            validProjectId,
            ids,
            { force: true },
          );
        }
      }
      const errorLines = summary.results
        .filter(
          (r) => !r.deleted && r.affected_drafts.length === 0 && r.reason,
        )
        .map((r) => `素材 #${r.asset_id}：${r.reason}`);
      const invalidatedLines = summary.results
        .filter((r) => r.deleted && r.invalidated_versions.length > 0)
        .map(
          (r) =>
            `素材 #${r.asset_id}：連帶將 ${r.invalidated_versions
              .map((v) => `v${v}`)
              .join("、")} 標為失敗`,
        );
      const statusLines: string[] = [];
      if (summary.deleted_count > 0) {
        statusLines.push(`刪除完成 ${summary.deleted_count} 個。`);
      }
      if (invalidatedLines.length > 0) {
        statusLines.push(...invalidatedLines);
      }
      if (errorLines.length > 0) {
        setTriggerError([`部分刪除失敗：`, ...errorLines].join("\n"));
      }
      if (statusLines.length > 0) {
        setStatusMessage(statusLines.join("\n"));
      }
    } catch (err) {
      setTriggerError(
        `批次刪除失敗：${err instanceof Error ? err.message : String(err)}`,
      );
    }
    setBatchRunning(false);
    clearSelection();
    polling.refresh();
  }, [selectedIds, validProjectId, polling, clearSelection]);

  const overallStatus = useMemo(() => {
    if (assets.length === 0) return "尚無素材";
    const counts: Record<string, number> = {};
    for (const a of assets) counts[a.status] = (counts[a.status] ?? 0) + 1;
    const parts = Object.entries(counts).map(
      ([k, v]) => `${labelForAssetStatus(k)} ${v}`,
    );
    return parts.join(" · ");
  }, [assets]);

  // M5 — 開始剪輯 / 預覽剪輯 CTA state.
  const latestDraft = polling.data?.latest_draft ?? null;
  const allAssetsTerminal = useMemo(() => {
    if (assets.length === 0) return false;
    return assets.every(
      (a) => a.status === "analyzed" || a.status === "analysis_failed",
    );
  }, [assets]);
  const editLabel = useMemo(() => {
    if (!latestDraft) return "產生短影音";
    if (latestDraft.status === "processing" || latestDraft.status === "pending") {
      return "查看製作進度";
    }
    if (latestDraft.status === "ready_for_review") return "預覽成品";
    if (latestDraft.status === "failed") return "重新產生";
    return "產生短影音";
  }, [latestDraft]);

  return (
    <main className="page project-analysis">
      <header className="analysis-hero">
        <div className="analysis-hero__kicker">素材檢查</div>
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
          {assets.length > 0 && (
            <Link
              to={`/projects/${validProjectId}/edit`}
              className="cta cta--primary"
              title={
                !allAssetsTerminal && !latestDraft
                  ? "前往剪輯頁；尚未檢查完成的素材會在剪輯頁顯示提示"
                  : undefined
              }
            >
              {editLabel}
            </Link>
          )}
        </div>
        {!polling.data?.has_script && project && (
          <p className="analysis-hint">
            尚未設定腳本 — 腳本對照會自動略過。
            <Link to={`/projects/${validProjectId}/upload`}>前往設定 →</Link>
          </p>
        )}
        {triggerError && (
          <p className="analysis-error" role="alert">
            操作失敗：{triggerError}
          </p>
        )}
        {statusMessage && (
          <p className="analysis-status" role="status">
            {statusMessage}
          </p>
        )}
        {polling.error && (
          <p className="analysis-error" role="alert">
            載入失敗：{polling.error.message}
          </p>
        )}
      </header>

      <AnalysisProgressSummary assets={assets} />

      {assets.length > 0 && (
        <div className="batch-toolbar" role="toolbar" aria-label="批次素材檢查">
          <label className="batch-toolbar__select-all">
            <input
              type="checkbox"
              checked={allUnanalyzedSelected}
              onChange={toggleSelectAllUnanalyzed}
              disabled={unanalyzedIds.length === 0}
            />
            <span>
              全選需檢查
              <span className="batch-toolbar__count mono">
                {" "}
                ({unanalyzedIds.length})
              </span>
            </span>
          </label>
          <span className="batch-toolbar__selected mono" aria-live="polite">
            已選 {selectedIds.size} / {assets.length}
          </span>
          <div className="batch-toolbar__actions">
            <button
              type="button"
              className="cta cta--quiet"
              onClick={clearSelection}
              disabled={selectedIds.size === 0 || batchRunning}
            >
              取消選取
            </button>
            <button
              type="button"
              className="cta cta--primary"
              onClick={() => void runBatchAnalyze(false)}
              disabled={selectedIds.size === 0 || batchRunning}
              title="只檢查未完成或失敗的項目，手改字幕會保留。"
            >
              {batchRunning
                ? "送出中…"
                : `重新檢查所選（${selectedIds.size}）`}
            </button>
            <button
              type="button"
              className="cta"
              onClick={() => void runBatchAnalyze(true)}
              disabled={selectedIds.size === 0 || batchRunning}
              title="所有檢查項目全部重跑，會覆寫手改字幕。"
            >
              重新檢查全部（覆寫手改）
            </button>
            {/* v0.26.0 / v0.27.1 — bulk delete. v0.27.1 stops
                hard-blocking on active drafts; instead a second
                confirm appears listing which drafts will be
                marked failed (素材已被刪除). */}
            <button
              type="button"
              className="cta cta--danger"
              onClick={() => void runBatchDelete()}
              disabled={selectedIds.size === 0 || batchRunning}
              title="刪除所選素材（檔案、縮圖、畫面重點資料）。被使用中的版本引用時會出現二次確認，確定後該版本會被標為失敗。"
            >
              刪除所選（{selectedIds.size}）
            </button>
          </div>
        </div>
      )}

      <section className="asset-list">
        {polling.loading && !polling.data && (
          <div className="board__notice mono">載入中…</div>
        )}
        {polling.data && assets.length === 0 && (
          <div className="board__notice mono">這個專案尚未上傳素材。</div>
        )}
        {assets.map((asset) => (
          <AssetCard
            key={asset.id}
            asset={asset}
            onAnalyze={handleAnalyze}
            onRetryStep={handleRetryStep}
            retryingStep={retryingMap[asset.id] ?? null}
            onTranslate={handleTranslate}
            translating={translatingIds.has(asset.id)}
            selected={selectedIds.has(asset.id)}
            onToggleSelect={toggleSelect}
          />
        ))}
      </section>
    </main>
  );
}
