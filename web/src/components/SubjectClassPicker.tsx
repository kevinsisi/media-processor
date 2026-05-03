// v0.21 — project-level subject class picker. The user picks one of the
// COCO-80 classes (or 不限) and the planner trims each chosen segment's
// asset_start_ms / asset_end_ms to where the subject actually appears,
// plus demotes assets that don't contain the class to last-resort
// priority. Sits next to the style-preset picker inside the basic
// settings group; PATCHes /projects/{id}/subject-class on each change.

import { useCallback, useState } from "react";
import { ApiError, apiClient } from "../api/client";
import type { ProjectDetail } from "../api/types";
import { TRACKING_SUBJECT_LABELS } from "../i18n/tags";
import "./SubjectClassPicker.css";

interface SubjectClassPickerProps {
  project: ProjectDetail | null;
  onProjectUpdated: (next: ProjectDetail) => void;
  disabled?: boolean;
}

// Canonical COCO-80 vocabulary, ordered to match
// services.object_tracking.COCO80_CLASSES so the dropdown order is
// reproducible across reloads. Hand-mirrored — keep in sync with the
// Python tuple if it ever changes.
const COCO80_CLASSES: readonly string[] = [
  "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train",
  "truck", "boat", "traffic light", "fire hydrant", "stop sign",
  "parking meter", "bench", "bird", "cat", "dog", "horse", "sheep",
  "cow", "elephant", "bear", "zebra", "giraffe", "backpack", "umbrella",
  "handbag", "tie", "suitcase", "frisbee", "skis", "snowboard",
  "sports ball", "kite", "baseball bat", "baseball glove", "skateboard",
  "surfboard", "tennis racket", "bottle", "wine glass", "cup", "fork",
  "knife", "spoon", "bowl", "banana", "apple", "sandwich", "orange",
  "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair",
  "couch", "potted plant", "bed", "dining table", "toilet", "tv",
  "laptop", "mouse", "remote", "keyboard", "cell phone", "microwave",
  "oven", "toaster", "sink", "refrigerator", "book", "clock", "vase",
  "scissors", "teddy bear", "hair drier", "toothbrush",
] as const;

const NO_LIMIT_VALUE = "__none__";

export default function SubjectClassPicker({
  project,
  onProjectUpdated,
  disabled,
}: SubjectClassPickerProps) {
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleChange = useCallback(
    async (raw: string) => {
      if (!project) return;
      const next: string | null = raw === NO_LIMIT_VALUE ? null : raw;
      setSaving(true);
      setError(null);
      try {
        const updated = await apiClient.patchProjectSubjectClass(project.id, {
          subject_class: next,
        });
        onProjectUpdated(updated);
      } catch (err) {
        if (err instanceof ApiError) {
          setError(`儲存失敗（${err.status}）：${err.message}`);
        } else {
          setError(err instanceof Error ? err.message : String(err));
        }
      } finally {
        setSaving(false);
      }
    },
    [project, onProjectUpdated],
  );

  const current = project?.subject_class ?? null;
  const selectValue = current ?? NO_LIMIT_VALUE;

  return (
    <fieldset className="subject-class-picker" disabled={disabled || saving}>
      <legend className="subject-class-picker__legend">主角類別</legend>
      <p className="subject-class-picker__hint mono">
        指定後，剪輯規劃會優先選擇含此主角的素材，並將片段裁切到主角實際出現的範圍（±0.5 秒）。
      </p>
      <label className="subject-class-picker__field">
        <span className="subject-class-picker__label">主角</span>
        <select
          className="subject-class-picker__select"
          value={selectValue}
          onChange={(e) => void handleChange(e.currentTarget.value)}
          disabled={disabled || saving}
          aria-label="主角類別"
        >
          <option value={NO_LIMIT_VALUE}>不限（傳統行為）</option>
          {COCO80_CLASSES.map((cls) => {
            const label = TRACKING_SUBJECT_LABELS[cls] ?? cls;
            return (
              <option key={cls} value={cls}>
                {label}（{cls}）
              </option>
            );
          })}
        </select>
        {saving && (
          <span className="subject-class-picker__saving mono" aria-live="polite">
            儲存中…
          </span>
        )}
      </label>
      {error && (
        <p className="subject-class-picker__error" role="alert">
          {error}
        </p>
      )}
    </fieldset>
  );
}
