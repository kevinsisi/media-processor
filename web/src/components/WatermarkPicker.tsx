import { useCallback, useMemo, useRef, useState } from "react";
import { ApiError, apiClient } from "../api/client";
import type { ProjectDetail, WatermarkPosition } from "../api/types";
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
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const watermarkUrl = project?.watermark_url ?? null;
  const filename = useMemo(
    () => watermarkFilename(project?.watermark_path),
    [project?.watermark_path],
  );
  const position: WatermarkPosition =
    project?.watermark_position ?? "bottom-right";
  // Backend stores fractions; the sliders work in percent for legibility.
  const scalePct = Math.round((project?.watermark_scale ?? 0.1) * 100);
  const opacityPct = Math.round((project?.watermark_opacity ?? 1.0) * 100);

  const handleUpload = useCallback(
    async (file: File) => {
      setError(null);
      setBusy(true);
      try {
        const updated = await apiClient.uploadProjectWatermark(
          projectId,
          file,
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

  const interactive = !disabled && !busy;

  return (
    <section className="watermark-picker" aria-busy={busy}>
      <header className="watermark-picker__head">
        <h3 className="watermark-picker__title">浮水印 / LOGO</h3>
        {filename ? (
          <span className="watermark-picker__current" title={filename}>
            目前：{filename}
          </span>
        ) : (
          <span className="watermark-picker__current">尚未上傳</span>
        )}
      </header>

      <div className="watermark-picker__upload-row">
        <label className="watermark-picker__upload">
          <input
            ref={fileInputRef}
            type="file"
            accept="image/png"
            disabled={!interactive}
            onChange={handleFile}
          />
          <span>{watermarkUrl ? "更換 PNG" : "上傳 PNG"}</span>
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
          aria-label="浮水印預覽"
          role="img"
        >
          {watermarkUrl ? (
            <img
              src={watermarkUrl}
              alt="watermark preview"
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
          aria-label="浮水印位置"
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
        下次剪輯時會將 PNG 烙印到成片角落（{POSITION_LABEL[position]}），
        大小與透明度即時生效。
      </p>
    </section>
  );
}
