import { useCallback, useState } from "react";
import { ApiError, apiClient } from "../api/client";
import type { DraftExportResponse, ExportAspect } from "../api/types";
import "./ExportSheet.css";

interface ExportSheetProps {
  draftId: number;
  draftVersion: number;
  // When the draft is still rendering or in flight, hide the trigger.
  ready: boolean;
}

const ASPECTS: { value: ExportAspect; label: string; sub: string }[] = [
  { value: "9:16", label: "9:16", sub: "Reels / TikTok / Shorts" },
  { value: "4:5", label: "4:5", sub: "IG 動態" },
  { value: "1:1", label: "1:1", sub: "方形貼文" },
];

const HEIGHTS = [720, 1080, 1440] as const;

export default function ExportSheet({ draftId, draftVersion, ready }: ExportSheetProps) {
  const [open, setOpen] = useState(false);
  const [aspect, setAspect] = useState<ExportAspect>("9:16");
  const [height, setHeight] = useState<(typeof HEIGHTS)[number]>(1080);
  const [submitting, setSubmitting] = useState(false);
  const [latest, setLatest] = useState<DraftExportResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const submit = useCallback(async () => {
    setSubmitting(true);
    setError(null);
    try {
      const resp = await apiClient.exportDraft(draftId, { aspect, height });
      setLatest(resp);
    } catch (err) {
      const msg =
        err instanceof ApiError
          ? `${err.status}: ${err.message}`
          : err instanceof Error
            ? err.message
            : String(err);
      setError(`匯出失敗：${msg}`);
    } finally {
      setSubmitting(false);
    }
  }, [aspect, height, draftId]);

  if (!ready) return null;

  return (
    <div className="export-sheet">
      {!open ? (
        <button
          type="button"
          className="cta cta--quiet export-sheet__trigger"
          onClick={() => setOpen(true)}
          aria-label="匯出其他比例"
        >
          匯出其他比例
        </button>
      ) : (
        <div className="export-sheet__panel" aria-modal="false">
          <div className="export-sheet__head">
            <h3 className="export-sheet__title">匯出 v{draftVersion}</h3>
            <button
              type="button"
              className="export-sheet__close"
              onClick={() => setOpen(false)}
              aria-label="關閉"
            >
              ✕
            </button>
          </div>

          <fieldset className="export-sheet__group">
            <legend className="mono">比例</legend>
            <div className="export-sheet__chips">
              {ASPECTS.map((a) => (
                <button
                  key={a.value}
                  type="button"
                  className={`export-chip${aspect === a.value ? " export-chip--active" : ""}`}
                  onClick={() => setAspect(a.value)}
                  aria-pressed={aspect === a.value}
                >
                  <span className="export-chip__label">{a.label}</span>
                  <span className="export-chip__sub mono">{a.sub}</span>
                </button>
              ))}
            </div>
          </fieldset>

          <fieldset className="export-sheet__group">
            <legend className="mono">解析度</legend>
            <div className="export-sheet__chips">
              {HEIGHTS.map((h) => (
                <button
                  key={h}
                  type="button"
                  className={`export-chip${height === h ? " export-chip--active" : ""}`}
                  onClick={() => setHeight(h)}
                  aria-pressed={height === h}
                >
                  <span className="export-chip__label mono">{h}p</span>
                </button>
              ))}
            </div>
            <p className="export-sheet__hint mono">
              系統會依比例自動算寬度；超過原片尺寸會被自動降到上限。
            </p>
          </fieldset>

          {error && (
            <p className="export-sheet__error mono" role="alert">
              {error}
            </p>
          )}

          {latest && !error && (
            <p className="export-sheet__queued mono" aria-live="polite">
              已排入工作 #{latest.job_id.slice(0, 6)}…，完成後可從專案下載清單取得 {latest.output_filename}
            </p>
          )}

          <div className="export-sheet__actions">
            <button
              type="button"
              className="cta cta--quiet"
              onClick={() => setOpen(false)}
              disabled={submitting}
            >
              取消
            </button>
            <button
              type="button"
              className="cta cta--primary"
              onClick={() => void submit()}
              disabled={submitting}
            >
              {submitting ? "排隊中…" : `匯出 ${aspect} · ${height}p`}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
