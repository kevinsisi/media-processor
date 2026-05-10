import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError, apiClient } from "../api/client";
import { useConfirmDialog } from "./ConfirmDialog";
import type {
  ProjectDetail,
  WatermarkPosition,
  WatermarkPresetOut,
} from "../api/types";
import "./WatermarkPicker.css";

// 3x3 grid order for the position picker — matches the on-screen layout.
const POSITIONS: WatermarkPosition[] = [
  "top-left",
  "top-center",
  "top-right",
  "middle-left",
  "middle-center",
  "middle-right",
  "bottom-left",
  "bottom-center",
  "bottom-right",
];

const POSITION_LABEL: Record<WatermarkPosition, string> = {
  "top-left": "左上",
  "top-center": "上中",
  "top-right": "右上",
  "middle-left": "左中",
  "middle-center": "中央",
  "middle-right": "右中",
  "bottom-left": "左下",
  "bottom-center": "下中",
  "bottom-right": "右下",
};

const SCALE_MIN = 2; // %
const SCALE_MAX = 50; // %
const OPACITY_MIN = 0; // %
const OPACITY_MAX = 100; // %

interface WatermarkPickerProps {
  projectId: number;
  project: ProjectDetail | null;
  onProjectUpdated: (project: ProjectDetail) => void;
  disabled?: boolean;
}

function watermarkFilename(path: string | null | undefined): string | null {
  if (!path) return null;
  const sep = path.lastIndexOf("/");
  return sep >= 0 ? path.slice(sep + 1) : path;
}

export default function WatermarkPicker({
  projectId,
  project,
  onProjectUpdated,
  disabled,
}: WatermarkPickerProps) {
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const { confirm, confirmDialog } = useConfirmDialog();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // v0.20.2 — immediate-feedback state. Holds the just-uploaded
  // filename for ~2.5 seconds so the green ✓ banner is visible even
  // before the parent's project state finishes updating. Used in
  // tandem with ``filename`` (from project.watermark_path) so that
  // even on flaky parent state the user can confirm the upload
  // succeeded. Cleared by a timer so the banner fades back to the
  // standard "目前：" label once the parent state catches up.
  const [justUploadedName, setJustUploadedName] = useState<string | null>(
    null,
  );
  const successTimerRef = useRef<number | null>(null);
  // v0.20.3 — local object-URL for the file the user just picked. Used
  // as a preview source while the POST + re-fetch are still in flight,
  // so the canvas pops to the new logo instantly instead of staying on
  // the "無預覽" placeholder for a beat. Cleared once the parent's
  // project.watermark_url lands (server URL is authoritative + carries
  // the cache-bust query). Also revoked on unmount to avoid leaking.
  const [localPreviewUrl, setLocalPreviewUrl] = useState<string | null>(null);
  // v0.21.6 — saved preset gallery. Loaded once on mount + after every
  // save / delete so the list stays current. ``null`` until the first
  // fetch resolves so we can distinguish "loading" from "empty".
  const [presets, setPresets] = useState<WatermarkPresetOut[] | null>(null);
  const [presetBusy, setPresetBusy] = useState<number | "saving" | null>(null);

  useEffect(() => {
    return () => {
      if (successTimerRef.current !== null) {
        window.clearTimeout(successTimerRef.current);
      }
    };
  }, []);

  // Revoke the previous object-URL whenever a new one replaces it, and
  // on unmount. createObjectURL leaks the underlying blob until you
  // revoke, so this keeps memory bounded even after several re-uploads.
  useEffect(() => {
    return () => {
      if (localPreviewUrl) URL.revokeObjectURL(localPreviewUrl);
    };
  }, [localPreviewUrl]);

  const watermarkUrl = project?.watermark_url ?? null;
  const filename = useMemo(
    () => watermarkFilename(project?.watermark_path),
    [project?.watermark_path],
  );

  // Preview-source priority: server URL (authoritative + cache-bust)
  // wins as soon as it lands; the local object-URL fills the gap
  // between "user picked a file" and "API roundtrip + re-fetch
  // returned the new watermark_url".
  const previewSrc = watermarkUrl ?? localPreviewUrl;

  // Once the server's watermark_url is in scope the local preview is
  // redundant — drop it (and revoke the blob) so the canvas only
  // tracks one source of truth.
  useEffect(() => {
    if (watermarkUrl && localPreviewUrl) {
      URL.revokeObjectURL(localPreviewUrl);
      setLocalPreviewUrl(null);
    }
  }, [watermarkUrl, localPreviewUrl]);

  // v0.20.2 — display priority: justUploadedName (right after upload)
  // → filename (parent state). Either one indicates "we have a
  // watermark." When both are set we still prefer justUploadedName
  // because the user just touched it — its provenance is unambiguous.
  const displayFilename = justUploadedName ?? filename;
  const showSuccessFlash = justUploadedName !== null;
  const position: WatermarkPosition =
    project?.watermark_position ?? "bottom-right";
  // Backend stores fractions; the sliders work in percent for legibility.
  const scalePct = Math.round((project?.watermark_scale ?? 0.1) * 100);
  const opacityPct = Math.round((project?.watermark_opacity ?? 1.0) * 100);

  const handleUpload = useCallback(
    async (file: File) => {
      setError(null);
      setBusy(true);
      // v0.20.3 — kick off a local object-URL preview before the POST
      // round-trips, so the canvas pops to the new logo as soon as the
      // user picks the file. Replaces any previous local preview;
      // ``URL.revokeObjectURL`` runs via the cleanup effect.
      const blobUrl = URL.createObjectURL(file);
      setLocalPreviewUrl((prev) => {
        if (prev) URL.revokeObjectURL(prev);
        return blobUrl;
      });
      try {
        const updated = await apiClient.uploadProjectWatermark(
          projectId,
          file,
        );
        onProjectUpdated(updated);
        // v0.20.2 — immediate-feedback path. Shows the file the user
        // just picked even if onProjectUpdated -> setProject hasn't
        // finished propagating, so the user is never left staring at
        // "尚未上傳" after a successful upload. Clears after 2.5 s,
        // by which point ``project.watermark_path`` is authoritative.
        setJustUploadedName(file.name);
        if (successTimerRef.current !== null) {
          window.clearTimeout(successTimerRef.current);
        }
        successTimerRef.current = window.setTimeout(() => {
          setJustUploadedName(null);
          successTimerRef.current = null;
        }, 2500);
        // Defensive belt-and-suspenders: re-fetch the project after
        // the upload to catch any case where the POST response was
        // missing watermark_path (shouldn't happen, but cheap to
        // guard against). Failure is non-fatal — the optimistic
        // setJustUploadedName covers the gap.
        try {
          const refreshed = await apiClient.fetchProject(projectId);
          onProjectUpdated(refreshed);
        } catch {
          /* ignore — primary update already landed */
        }
      } catch (err) {
        setError(
          err instanceof ApiError
            ? `${err.status}: ${err.message}`
            : err instanceof Error
              ? err.message
              : String(err),
        );
      } finally {
        setBusy(false);
        if (fileInputRef.current) fileInputRef.current.value = "";
      }
    },
    [projectId, onProjectUpdated],
  );

  const handleFile = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const f = e.target.files?.[0];
      if (!f) return;
      void handleUpload(f);
    },
    [handleUpload],
  );

  const patchSettings = useCallback(
    async (patch: {
      position?: WatermarkPosition;
      scale?: number;
      opacity?: number;
    }) => {
      setError(null);
      setBusy(true);
      try {
        const updated = await apiClient.updateProjectWatermark(
          projectId,
          patch,
        );
        onProjectUpdated(updated);
      } catch (err) {
        setError(
          err instanceof ApiError
            ? `${err.status}: ${err.message}`
            : err instanceof Error
              ? err.message
              : String(err),
        );
      } finally {
        setBusy(false);
      }
    },
    [projectId, onProjectUpdated],
  );

  const handlePosition = useCallback(
    (p: WatermarkPosition) => {
      if (p === position) return;
      void patchSettings({ position: p });
    },
    [position, patchSettings],
  );

  const handleScale = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const pct = Number(e.target.value);
      void patchSettings({ scale: Math.max(SCALE_MIN, pct) / 100 });
    },
    [patchSettings],
  );

  const handleOpacity = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const pct = Number(e.target.value);
      void patchSettings({ opacity: pct / 100 });
    },
    [patchSettings],
  );

  const handleDelete = useCallback(async () => {
    setError(null);
    setBusy(true);
    try {
      await apiClient.deleteProjectWatermark(projectId);
      // The DELETE returns 204; pull a fresh detail so the picker
      // shows the upload prompt again.
      const refreshed = await apiClient.fetchProject(projectId);
      onProjectUpdated(refreshed);
    } catch (err) {
      setError(
        err instanceof ApiError
          ? `${err.status}: ${err.message}`
          : err instanceof Error
            ? err.message
            : String(err),
      );
    } finally {
      setBusy(false);
    }
  }, [projectId, onProjectUpdated]);

  // v0.21.6 — preset gallery handlers.
  const refreshPresets = useCallback(async () => {
    try {
      const list = await apiClient.fetchWatermarkPresets();
      setPresets(list);
    } catch (err) {
      // Don't surface as a top-level error — presets are an aux
      // feature, the main upload flow keeps working without them.
      // eslint-disable-next-line no-console
      console.warn("watermark presets fetch failed:", err);
      setPresets([]);
    }
  }, []);

  useEffect(() => {
    void refreshPresets();
  }, [refreshPresets]);

  const handleApplyPreset = useCallback(
    async (preset: WatermarkPresetOut) => {
      setError(null);
      setPresetBusy(preset.id);
      try {
        const updated = await apiClient.applyWatermarkPreset(projectId, {
          preset_id: preset.id,
        });
        onProjectUpdated(updated);
      } catch (err) {
        setError(
          err instanceof ApiError
            ? `${err.status}: ${err.message}`
            : err instanceof Error
              ? err.message
              : String(err),
        );
      } finally {
        setPresetBusy(null);
      }
    },
    [projectId, onProjectUpdated],
  );

  const handleSavePreset = useCallback(async () => {
    if (!filename) return;
    const raw = window.prompt(
      "輸入這組品牌標誌設定的名稱（之後可以套用到其他專案）",
      `預設 ${(presets?.length ?? 0) + 1}`,
    );
    if (raw === null) return; // user cancelled
    const name = raw.trim();
    if (!name) {
      setError("品牌標誌名稱不可空白");
      return;
    }
    setError(null);
    setPresetBusy("saving");
    try {
      await apiClient.saveWatermarkPreset({ project_id: projectId, name });
      await refreshPresets();
    } catch (err) {
      setError(
        err instanceof ApiError
          ? `${err.status}: ${err.message}`
          : err instanceof Error
            ? err.message
            : String(err),
      );
    } finally {
      setPresetBusy(null);
    }
  }, [filename, projectId, presets, refreshPresets]);

  const handleDeletePreset = useCallback(
    async (preset: WatermarkPresetOut, ev: React.MouseEvent) => {
      ev.stopPropagation();
      const ok = await confirm({
        title: "刪除品牌標誌預設？",
        message: `刪除預設「${preset.name}」？已套用到專案的標誌不會被移除。`,
        confirmLabel: "刪除預設",
        tone: "danger",
      });
      if (!ok) return;
      setPresetBusy(preset.id);
      try {
        await apiClient.deleteWatermarkPreset(preset.id);
        await refreshPresets();
      } catch (err) {
        setError(
          err instanceof ApiError
            ? `${err.status}: ${err.message}`
            : err instanceof Error
              ? err.message
              : String(err),
        );
      } finally {
        setPresetBusy(null);
      }
    },
    [refreshPresets, confirm],
  );

  const interactive = !disabled && !busy;

  return (
    <section className="watermark-picker" aria-busy={busy}>
      <header className="watermark-picker__head">
        <h3 className="watermark-picker__title">品牌標誌</h3>
        {displayFilename ? (
          <span
            className={
              "watermark-picker__current" +
              (showSuccessFlash
                ? " watermark-picker__current--just-uploaded"
                : " watermark-picker__current--ok")
            }
            title={displayFilename}
            aria-live="polite"
          >
            <span className="watermark-picker__check" aria-hidden>
              ✓
            </span>
            {showSuccessFlash ? "上傳成功：" : "目前："}
            {displayFilename}
          </span>
        ) : (
          <span className="watermark-picker__current">尚未上傳</span>
        )}
      </header>

      {/* v0.21.6 — saved preset gallery. Click a thumbnail to apply
         that preset's PNG + position / scale / opacity to this
         project; "💾 儲存目前設定為預設" snapshots the current
         project's watermark for later reuse. */}
      {presets !== null && (presets.length > 0 || filename) ? (
        <section
          className="watermark-presets"
          aria-label="已儲存的品牌標誌預設"
        >
          <header className="watermark-presets__head">
            <span className="watermark-presets__title">
              已儲存的品牌標誌（{presets.length}）
            </span>
            {filename && presetBusy !== "saving" ? (
              <button
                type="button"
                className="watermark-presets__save"
                onClick={() => void handleSavePreset()}
                disabled={!interactive || presetBusy !== null}
                title="把目前的品牌標誌、位置、大小與透明度存成可重用的預設"
              >
                💾 儲存目前設定為預設
              </button>
            ) : null}
            {presetBusy === "saving" ? (
              <span className="watermark-presets__saving">儲存中…</span>
            ) : null}
          </header>
          {presets.length > 0 ? (
            <ul className="watermark-presets__list">
              {presets.map((p) => {
                const applying = presetBusy === p.id;
                return (
                  <li key={p.id} className="watermark-presets__item">
                    <button
                      type="button"
                      className="watermark-presets__card"
                      onClick={() => void handleApplyPreset(p)}
                      disabled={
                        !interactive || presetBusy !== null
                      }
                      title={`套用「${p.name}」（${POSITION_LABEL[p.position]}、${Math.round(p.scale * 100)}%、不透明度 ${Math.round(p.opacity * 100)}%）`}
                    >
                      <span className="watermark-presets__thumb">
                        {p.preview_url ? (
                          <img src={p.preview_url} alt={p.name} />
                        ) : (
                          <span className="watermark-presets__thumb-blank">
                            ?
                          </span>
                        )}
                      </span>
                      <span className="watermark-presets__name">
                        {applying ? "套用中…" : p.name}
                      </span>
                    </button>
                    <button
                      type="button"
                      className="watermark-presets__remove"
                      aria-label={`刪除預設「${p.name}」`}
                      onClick={(ev) => void handleDeletePreset(p, ev)}
                      disabled={presetBusy !== null}
                    >
                      ×
                    </button>
                  </li>
                );
              })}
            </ul>
          ) : (
            <p className="watermark-presets__empty">
              還沒有預設。上傳品牌標誌後可按上方「💾 儲存目前設定為預設」存成可重用的版本。
            </p>
          )}
        </section>
      ) : null}

      <div className="watermark-picker__upload-row">
        <label className="watermark-picker__upload">
          <input
            ref={fileInputRef}
            type="file"
            accept="image/png"
            disabled={!interactive}
            onChange={handleFile}
          />
          <span>{watermarkUrl ? "更換標誌圖" : "上傳標誌圖"}</span>
        </label>
        {watermarkUrl ? (
          <button
            type="button"
            className="watermark-picker__delete"
            disabled={!interactive}
            onClick={handleDelete}
          >
            移除
          </button>
        ) : null}
      </div>

      <div className="watermark-picker__preview-row">
        <div
          className="watermark-picker__canvas"
          aria-label="品牌標誌預覽"
          role="img"
        >
          {previewSrc ? (
            <img
              src={previewSrc}
              alt="品牌標誌預覽"
              className={`watermark-picker__logo watermark-picker__logo--${position}`}
              style={{
                width: `${Math.max(SCALE_MIN, Math.min(SCALE_MAX, scalePct))}%`,
                opacity: opacityPct / 100,
              }}
            />
          ) : (
            <div className="watermark-picker__placeholder">無預覽</div>
          )}
        </div>

        <div
          className="watermark-picker__grid"
          role="radiogroup"
          aria-label="品牌標誌位置"
        >
          {POSITIONS.map((p) => (
            <button
              key={p}
              type="button"
              role="radio"
              aria-checked={p === position}
              aria-label={POSITION_LABEL[p]}
              className={
                "watermark-picker__cell" +
                (p === position ? " watermark-picker__cell--active" : "")
              }
              disabled={!interactive}
              onClick={() => handlePosition(p)}
            >
              <span className="watermark-picker__dot" />
            </button>
          ))}
        </div>
      </div>

      <div className="watermark-picker__sliders">
        <label className="watermark-picker__slider">
          <span>大小</span>
          <input
            type="range"
            min={SCALE_MIN}
            max={SCALE_MAX}
            step={1}
            value={scalePct}
            disabled={!interactive}
            onChange={handleScale}
          />
          <span className="watermark-picker__value">{scalePct}%</span>
        </label>
        <label className="watermark-picker__slider">
          <span>透明度</span>
          <input
            type="range"
            min={OPACITY_MIN}
            max={OPACITY_MAX}
            step={5}
            value={opacityPct}
            disabled={!interactive}
            onChange={handleOpacity}
          />
          <span className="watermark-picker__value">{opacityPct}%</span>
        </label>
      </div>

      {error ? (
        <p className="watermark-picker__error" role="alert">
          {error}
        </p>
      ) : null}
      <p className="watermark-picker__hint">
        下次產生成品時會將 PNG 標誌放到成片角落（{POSITION_LABEL[position]}），
        大小與透明度即時生效。
      </p>
      {confirmDialog}
    </section>
  );
}
