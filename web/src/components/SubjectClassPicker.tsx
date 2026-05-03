// v0.21 — project-level "主角類別" (subject class) picker. Lists only
// classes that actually appear in this project's assets'
// ``tracking_json`` (sorted by total frame count desc), so the user
// picks from real footage rather than a hard-coded 80-class menu.
//
// When the project has no ``subject_class`` saved yet AND there's at
// least one detected class, the dropdown defaults visually to the
// most-frequent class and auto-PATCHes it to the project so the
// filter is on by default for projects that have run tracking.
// "(不限)" stays at the top as the explicit opt-out.

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, apiClient } from "../api/client";
import type { DetectedClassOut, ProjectDetail } from "../api/types";
import "./SubjectClassPicker.css";

interface SubjectClassPickerProps {
  project: ProjectDetail | null;
  onProjectUpdated: (next: ProjectDetail) => void;
  disabled?: boolean;
}

// Display labels for the historically high-signal COCO classes; the
// rest fall back to the raw English class name. Lookup-only — the
// server is the source of truth for which classes exist.
const ZH_LABEL: Record<string, string> = {
  person: "人物",
  bicycle: "腳踏車",
  car: "汽車",
  motorcycle: "機車",
  airplane: "飛機",
  bus: "公車",
  train: "火車",
  truck: "卡車",
  boat: "船",
  bird: "鳥",
  cat: "貓",
  dog: "狗",
  horse: "馬",
  sheep: "羊",
  cow: "牛",
  bear: "熊",
  elephant: "大象",
  zebra: "斑馬",
  giraffe: "長頸鹿",
  skateboard: "滑板",
  surfboard: "衝浪板",
  bottle: "瓶子",
  cup: "杯子",
  chair: "椅子",
  couch: "沙發",
  bed: "床",
  tv: "電視",
  laptop: "筆電",
  "cell phone": "手機",
  book: "書",
  "teddy bear": "玩偶",
  "potted plant": "盆栽",
  "dining table": "餐桌",
};

function labelFor(cls: string, totalFrames: number): string {
  const zh = ZH_LABEL[cls] ?? cls;
  const formatted = totalFrames.toLocaleString("zh-Hant");
  return `${zh}（${formatted} 幀）`;
}

export default function SubjectClassPicker({
  project,
  onProjectUpdated,
  disabled,
}: SubjectClassPickerProps) {
  const [detected, setDetected] = useState<DetectedClassOut[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const projectId = project?.id ?? null;

  useEffect(() => {
    if (projectId == null) return;
    let cancelled = false;
    setLoading(true);
    setLoadError(null);
    apiClient
      .fetchProjectDetectedClasses(projectId)
      .then((rows) => {
        if (cancelled) return;
        setDetected(rows);
      })
      .catch((err) => {
        if (cancelled) return;
        setLoadError(
          err instanceof ApiError
            ? `載入偵測類別失敗（${err.status}）：${err.message}`
            : err instanceof Error
              ? err.message
              : String(err),
        );
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  // Auto-PATCH the most-frequent class when the project has no
  // subject_class yet AND tracking has produced at least one class.
  // Runs at most once per component instance; if the user explicitly
  // picks "(不限)" later the saved value goes back to null and we
  // won't re-suggest until the next page mount with a still-null
  // project. ``autoApplied`` lives in a ref so re-renders triggered
  // by the PATCH-then-onProjectUpdated cycle don't loop.
  const autoApplied = useRef(false);
  useEffect(() => {
    if (autoApplied.current) return;
    if (!project) return;
    if (project.subject_class) {
      autoApplied.current = true;
      return;
    }
    if (!detected || detected.length === 0) return;
    autoApplied.current = true;
    const top = detected[0].cls_name;
    setSaving(true);
    apiClient
      .patchProjectSubjectClass(project.id, { subject_class: top })
      .then((next) => onProjectUpdated(next))
      .catch((err) => {
        setSaveError(
          err instanceof ApiError
            ? `預設選取失敗（${err.status}）：${err.message}`
            : err instanceof Error
              ? err.message
              : String(err),
        );
      })
      .finally(() => setSaving(false));
  }, [project, detected, onProjectUpdated]);

  const handleChange = useCallback(
    async (event: React.ChangeEvent<HTMLSelectElement>) => {
      if (!project) return;
      const raw = event.currentTarget.value;
      const next = raw === "" ? null : raw;
      setSaving(true);
      setSaveError(null);
      try {
        const updated = await apiClient.patchProjectSubjectClass(project.id, {
          subject_class: next,
        });
        onProjectUpdated(updated);
      } catch (err) {
        if (err instanceof ApiError) {
          setSaveError(`儲存失敗（${err.status}）：${err.message}`);
        } else {
          setSaveError(err instanceof Error ? err.message : String(err));
        }
      } finally {
        setSaving(false);
      }
    },
    [project, onProjectUpdated],
  );

  if (!project) {
    return (
      <section className="subject-class" aria-label="主角類別">
        <header className="subject-class__head">
          <h3 className="subject-class__title">主角類別</h3>
        </header>
        <p className="subject-class__hint mono">載入專案中…</p>
      </section>
    );
  }

  const isLocked = disabled || saving;
  const saved = project.subject_class ?? null;
  // Render the saved value as a stale option at the bottom if the
  // class no longer shows up in the detected list (happens when an
  // operator deletes the assets that contained the chosen class).
  const detectedList = detected ?? [];
  const savedIsStale =
    saved !== null && !detectedList.some((d) => d.cls_name === saved);

  return (
    <section className="subject-class" aria-label="主角類別">
      <header className="subject-class__head">
        <h3 className="subject-class__title">主角類別</h3>
        {saving && (
          <span className="subject-class__saving mono" aria-live="polite">
            儲存中…
          </span>
        )}
      </header>

      {loadError && (
        <p className="subject-class__error" role="alert">
          {loadError}
        </p>
      )}

      {loading && !loadError && (
        <p className="subject-class__hint mono">載入偵測類別中…</p>
      )}

      {!loading && !loadError && detectedList.length === 0 && (
        <p className="subject-class__hint mono">
          請先完成追蹤分析（偵測尚未產出任何類別）。
        </p>
      )}

      {!loading && !loadError && detectedList.length > 0 && (
        <>
          <div className="subject-class__row">
            <select
              className="subject-class__select"
              value={saved ?? ""}
              disabled={isLocked}
              onChange={handleChange}
              aria-label="主角類別選單"
            >
              <option value="">不限（使用所有素材）</option>
              {detectedList.map((d) => (
                <option key={d.cls_name} value={d.cls_name}>
                  {labelFor(d.cls_name, d.total_frames)}
                </option>
              ))}
              {savedIsStale && (
                <option key={`__stale_${saved}`} value={saved as string}>
                  {(ZH_LABEL[saved as string] ?? saved)}（已選但目前未偵測到）
                </option>
              )}
            </select>
          </div>

          <p className="subject-class__hint mono">
            選定後，自動剪輯只會用「主角出現的範圍 ± 0.5 秒」；完全沒偵測到主角的素材會被略過。
          </p>
        </>
      )}

      {saveError && (
        <p className="subject-class__error" role="alert">
          {saveError}
        </p>
      )}
    </section>
  );
}
