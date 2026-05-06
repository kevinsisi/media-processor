import { useCallback, useEffect, useMemo, useState } from "react";
import { ApiError, apiClient } from "../api/client";
import type {
  DraftExportArtifact,
  DraftExportResponse,
  DraftExportStatus,
  ExportAspect,
} from "../api/types";
import "./ExportSheet.css";

interface ExportSheetProps {
  draftId: number;
  draftVersion: number;
  // When the draft is still rendering or in flight, hide the trigger.
  ready: boolean;
}

const ASPECTS: { value: ExportAspect; label: string; sub: string }[] = [
  { value: "9:16", label: "9:16", sub: "直式短影音" },
  { value: "4:5", label: "4:5", sub: "IG / FB 直式貼文" },
  { value: "1:1", label: "1:1", sub: "方形貼文" },
];

const HEIGHTS = [720, 1080, 1440] as const;
const EXPORT_POLL_MS = 3000;

interface SocialExportPreset {
  id: string;
  platform: string;
  title: string;
  hint: string;
  aspect: ExportAspect;
  height: (typeof HEIGHTS)[number];
}

const SOCIAL_EXPORT_PRESETS: SocialExportPreset[] = [
  {
    id: "ig-fb-reels",
    platform: "IG / FB",
    title: "Reels 直式短影音",
    hint: "最適合全螢幕滑動觀看，可同時用在 IG 和 FB。",
    aspect: "9:16",
    height: 1080,
  },
  {
    id: "feed-portrait",
    platform: "IG / FB",
    title: "直式貼文版",
    hint: "保留更多畫面，適合貼文牆瀏覽。",
    aspect: "4:5",
    height: 1080,
  },
  {
    id: "square-post",
    platform: "IG / FB",
    title: "方形貼文版",
    hint: "適合商品、活動或需要整齊版面的貼文。",
    aspect: "1:1",
    height: 1080,
  },
];

const STATUS_LABEL: Record<DraftExportStatus, string> = {
  queued: "等待建立",
  running: "建立中",
  done: "可下載",
  failed: "建立失敗",
};

function exportKey(aspect: string, height: number): string {
  return `${aspect}-${height}`;
}

export default function ExportSheet({ draftId, draftVersion, ready }: ExportSheetProps) {
  const [open, setOpen] = useState(false);
  const [aspect, setAspect] = useState<ExportAspect>("9:16");
  const [height, setHeight] = useState<(typeof HEIGHTS)[number]>(1080);
  const [submittingId, setSubmittingId] = useState<string | null>(null);
  const [latest, setLatest] = useState<DraftExportResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [artifacts, setArtifacts] = useState<DraftExportArtifact[]>([]);
  const submitting = submittingId !== null;

  const activeArtifactsByKey = useMemo(() => {
    const map = new Map<string, DraftExportArtifact>();
    for (const item of artifacts) {
      if (item.status !== "queued" && item.status !== "running") continue;
      map.set(exportKey(item.aspect, item.height), item);
    }
    return map;
  }, [artifacts]);

  const customActiveArtifact = activeArtifactsByKey.get(
    exportKey(aspect, height),
  );

  const fetchArtifacts = useCallback(
    () => apiClient.fetchDraftExports(draftId),
    [draftId],
  );

  const refreshArtifacts = useCallback(async () => {
    const list = await fetchArtifacts();
    setArtifacts(list);
    setLoadError(null);
    return list;
  }, [fetchArtifacts]);

  const submit = useCallback(async (options?: {
    aspect?: ExportAspect;
    height?: (typeof HEIGHTS)[number];
    submitId?: string;
  }) => {
    const requestAspect = options?.aspect ?? aspect;
    const requestHeight = options?.height ?? height;
    const submitId = options?.submitId ?? "custom";
    setSubmittingId(submitId);
    setError(null);
    try {
      setAspect(requestAspect);
      setHeight(requestHeight);
      const resp = await apiClient.exportDraft(draftId, {
        aspect: requestAspect,
        height: requestHeight,
      });
      setLatest(resp);
      setArtifacts((prev) => [
        resp,
        ...prev.filter((item) => item.export_id !== resp.export_id),
      ]);
      void refreshArtifacts().catch(() => {});
    } catch (err) {
      const msg =
        err instanceof ApiError
          ? `${err.status}: ${err.message}`
          : err instanceof Error
            ? err.message
            : String(err);
      setError(`建立下載版本失敗：${msg}`);
    } finally {
      setSubmittingId(null);
    }
  }, [aspect, height, draftId, refreshArtifacts]);

  useEffect(() => {
    setArtifacts([]);
    setLatest(null);
    setLoadError(null);
  }, [draftId]);

  useEffect(() => {
    if (!open || !ready) return;
    let cancelled = false;
    let timer: number | null = null;

    const tick = async () => {
      try {
        const list = await fetchArtifacts();
        if (cancelled) return;
        setArtifacts(list);
        setLoadError(null);
        if (list.some((item) => item.status === "queued" || item.status === "running")) {
          timer = window.setTimeout(() => void tick(), EXPORT_POLL_MS);
        }
      } catch (err) {
        if (cancelled) return;
        setLoadError(err instanceof Error ? err.message : String(err));
      }
    };

    void tick();
    return () => {
      cancelled = true;
      if (timer !== null) window.clearTimeout(timer);
    };
  }, [fetchArtifacts, latest?.export_id, open, ready]);

  if (!ready) return null;

  return (
    <div className="export-sheet">
      {!open ? (
        <button
          type="button"
          className="cta cta--quiet export-sheet__trigger"
          onClick={() => setOpen(true)}
          aria-label="建立 IG / FB 版本"
        >
          建立 IG / FB 版本
        </button>
      ) : (
        <div className="export-sheet__panel" aria-modal="false">
          <div className="export-sheet__head">
            <div>
              <h3 className="export-sheet__title">建立 IG / FB 短影音版本</h3>
              <p className="export-sheet__subtitle">
                選要發佈的平台，系統會用適合的尺寸建立 v{draftVersion} 下載檔。
              </p>
            </div>
            <button
              type="button"
              className="export-sheet__close"
              onClick={() => setOpen(false)}
              aria-label="關閉"
            >
              ✕
            </button>
          </div>

          <div className="export-preset-grid" aria-label="社群平台版本預設">
            {SOCIAL_EXPORT_PRESETS.map((preset) => {
              const existingActive = activeArtifactsByKey.get(
                exportKey(preset.aspect, preset.height),
              );
              return (
                <button
                  key={preset.id}
                  type="button"
                  className="export-preset-card"
                  onClick={() => void submit({
                    aspect: preset.aspect,
                    height: preset.height,
                    submitId: preset.id,
                  })}
                  disabled={submitting || Boolean(existingActive)}
                >
                  <span className="export-preset-card__platform mono">
                    {preset.platform}
                  </span>
                  <span className="export-preset-card__title">{preset.title}</span>
                  <span className="export-preset-card__hint">{preset.hint}</span>
                  <span className="export-preset-card__meta mono">
                    {preset.aspect} · {preset.height}p
                  </span>
                  <span className="export-preset-card__action">
                    {submittingId === preset.id
                      ? "建立中…"
                      : existingActive
                        ? "建立中，下方會更新"
                        : "建立這個版本"}
                  </span>
                </button>
              );
            })}
          </div>

          <details className="export-sheet__advanced">
            <summary>進階：自訂社群比例與清晰度</summary>
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
                    disabled={submitting}
                  >
                    <span className="export-chip__label">{a.label}</span>
                    <span className="export-chip__sub mono">{a.sub}</span>
                  </button>
                ))}
              </div>
            </fieldset>

            <fieldset className="export-sheet__group">
              <legend className="mono">清晰度</legend>
              <div className="export-sheet__chips">
                {HEIGHTS.map((h) => (
                  <button
                    key={h}
                    type="button"
                    className={`export-chip${height === h ? " export-chip--active" : ""}`}
                    onClick={() => setHeight(h)}
                    aria-pressed={height === h}
                    disabled={submitting}
                  >
                    <span className="export-chip__label mono">{h}p</span>
                  </button>
                ))}
              </div>
              <p className="export-sheet__hint mono">
                系統會依比例自動計算寬度；若超過原片尺寸，會自動降到可用上限。
              </p>
              <button
                type="button"
                className="cta cta--primary export-sheet__custom-submit"
                onClick={() => void submit()}
                disabled={submitting || Boolean(customActiveArtifact)}
              >
                {submittingId === "custom"
                  ? "建立中…"
                  : customActiveArtifact
                    ? "建立中，下方會更新"
                    : `建立 ${aspect} · ${height}p`}
              </button>
            </fieldset>
          </details>

          {error && (
            <p className="export-sheet__error mono" role="alert">
              {error}
            </p>
          )}

          {loadError && (
            <p className="export-sheet__error mono" role="alert">
              版本清單讀取失敗：{loadError}
            </p>
          )}

          {latest && !error && (
            <p className="export-sheet__queued mono" aria-live="polite">
              已開始建立 #{latest.job_id.slice(0, 6)}…，完成後下方會出現下載鈕。
            </p>
          )}

          <div className="export-sheet__list" aria-live="polite">
            <h4 className="export-sheet__list-title">已建立的下載版本</h4>
            {artifacts.length === 0 ? (
              <p className="export-sheet__empty mono">尚未建立其他比例。</p>
            ) : (
              <ul className="export-sheet__items">
                {artifacts.map((item) => (
                  <li
                    key={item.export_id}
                    className={`export-sheet__item export-sheet__item--${item.status}`}
                  >
                    <div className="export-sheet__item-main">
                      <span className="export-sheet__item-label">
                        {item.aspect} · {item.height}p
                      </span>
                      <span className="export-sheet__item-meta mono">
                        {STATUS_LABEL[item.status]} · {item.output_filename}
                      </span>
                      {item.status === "failed" && item.error && (
                        <span className="export-sheet__item-error mono">
                          {item.error}
                        </span>
                      )}
                    </div>
                    {item.status === "done" && item.download_url ? (
                      <a
                        className="cta cta--primary export-sheet__download"
                        href={item.download_url}
                        download={item.output_filename}
                      >
                        下載
                      </a>
                    ) : (
                      <span className="export-sheet__item-state mono">
                        {STATUS_LABEL[item.status]}
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div className="export-sheet__actions">
            <button
              type="button"
              className="cta cta--quiet"
              onClick={() => setOpen(false)}
              disabled={submitting}
            >
              關閉
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
