// v0.29.0 — static-crop anchor picker.
//
// Mounts on ProjectEdit when the project's `target_aspect_ratio`
// disagrees with the orientation of its analysed assets (so the
// renderer's static aspect-crop has to drop part of the source).
// The auto-reframe path (YOLO / point / custom_roi) ignores this
// anchor — it already centres on a tracked subject — so the picker
// is purely for the static fallback.
//
// 9:16 source → 16:9 target: vertical crop, presets are top /
// middle / bottom. 16:9 source → 9:16 target: horizontal crop,
// presets are left / center / right. Same source + target
// orientation: parent hides the picker entirely.

import { useCallback, useState } from "react";
import { ApiError, apiClient } from "../api/client";
import type { CropRegion, ProjectDetail } from "../api/types";
import "./CropRegionPicker.css";

export type CropDirection = "vertical" | "horizontal";

interface CropPreset {
  key: string;
  label: string;
  hint: string;
  region: CropRegion;
}

const VERTICAL_PRESETS: readonly CropPreset[] = [
  {
    key: "top",
    label: "保留上半",
    hint: "捨棄下半畫面",
    region: { x_norm: 0.5, y_norm: 0.0 },
  },
  {
    key: "middle",
    label: "保留中間",
    hint: "預設值，上下對稱裁切",
    region: { x_norm: 0.5, y_norm: 0.5 },
  },
  {
    key: "bottom",
    label: "保留下半",
    hint: "捨棄上半畫面",
    region: { x_norm: 0.5, y_norm: 1.0 },
  },
];

const HORIZONTAL_PRESETS: readonly CropPreset[] = [
  {
    key: "left",
    label: "保留左側",
    hint: "捨棄右側畫面",
    region: { x_norm: 0.0, y_norm: 0.5 },
  },
  {
    key: "center",
    label: "保留中間",
    hint: "預設值，左右對稱裁切",
    region: { x_norm: 0.5, y_norm: 0.5 },
  },
  {
    key: "right",
    label: "保留右側",
    hint: "捨棄左側畫面",
    region: { x_norm: 1.0, y_norm: 0.5 },
  },
];

const PRESET_TOLERANCE = 0.0001;

function regionsEqual(a: CropRegion, b: CropRegion): boolean {
  return (
    Math.abs(a.x_norm - b.x_norm) < PRESET_TOLERANCE
    && Math.abs(a.y_norm - b.y_norm) < PRESET_TOLERANCE
  );
}

interface CropRegionPickerProps {
  project: ProjectDetail | null;
  // ``vertical`` = source is portrait, target is landscape (operator
  // picks top / middle / bottom). ``horizontal`` = source is
  // landscape, target is portrait (operator picks left / center /
  // right). Parent computes this from analysed-asset resolutions.
  direction: CropDirection;
  onProjectUpdated: (next: ProjectDetail) => void;
  disabled?: boolean;
}

export default function CropRegionPicker({
  project,
  direction,
  onProjectUpdated,
  disabled,
}: CropRegionPickerProps) {
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const presets = direction === "vertical" ? VERTICAL_PRESETS : HORIZONTAL_PRESETS;

  // ``null`` ≡ centre per the API contract; treat it that way for
  // the highlighted preset so the default state reads as "middle"
  // rather than nothing-selected (which would let the user think
  // they had to pick before triggering a render).
  const currentRegion: CropRegion = project?.crop_region ?? {
    x_norm: 0.5,
    y_norm: 0.5,
  };

  const onPick = useCallback(
    async (region: CropRegion) => {
      if (!project) return;
      setSaving(true);
      setError(null);
      try {
        // Centre is stored as NULL on the server (compact + the
        // renderer shortcut). Send {x_norm: null, y_norm: null} for
        // the middle preset; otherwise send the explicit values.
        const isCentre =
          Math.abs(region.x_norm - 0.5) < PRESET_TOLERANCE
          && Math.abs(region.y_norm - 0.5) < PRESET_TOLERANCE;
        const next = await apiClient.patchProjectCropRegion(project.id, {
          x_norm: isCentre ? null : region.x_norm,
          y_norm: isCentre ? null : region.y_norm,
        });
        onProjectUpdated(next);
      } catch (err) {
        const msg =
          err instanceof ApiError
            ? `${err.status}: ${err.message}`
            : err instanceof Error
              ? err.message
              : String(err);
        setError(`儲存失敗：${msg}`);
      } finally {
        setSaving(false);
      }
    },
    [project, onProjectUpdated],
  );

  return (
    <fieldset className="crop-region-picker" disabled={disabled || saving}>
      <legend className="crop-region-picker__legend">
        裁切區域（來源 ≠ 輸出比例）
      </legend>
      <p className="crop-region-picker__hint mono">
        {direction === "vertical"
          ? "影片是直式但要輸出橫向，需要裁掉部分高度。挑你想保留的位置；沒選會用「中間」。"
          : "影片是橫向但要輸出直式，需要裁掉部分寬度。挑你想保留的位置；沒選會用「中間」。"}
      </p>
      <div className="crop-region-picker__grid" role="radiogroup">
        {presets.map((preset) => {
          const selected = regionsEqual(preset.region, currentRegion);
          return (
            <button
              key={preset.key}
              type="button"
              role="radio"
              aria-checked={selected}
              className={
                "crop-region-card"
                + (selected ? " crop-region-card--selected" : "")
              }
              disabled={disabled || saving}
              onClick={() => void onPick(preset.region)}
            >
              <span
                className={
                  "crop-region-card__viz crop-region-card__viz--"
                  + direction
                  + " crop-region-card__viz--"
                  + preset.key
                }
                aria-hidden
              />
              <span className="crop-region-card__label">{preset.label}</span>
              <span className="crop-region-card__hint mono">{preset.hint}</span>
            </button>
          );
        })}
      </div>
      {error && (
        <p className="crop-region-picker__error mono" role="alert">
          {error}
        </p>
      )}
    </fieldset>
  );
}
