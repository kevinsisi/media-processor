// v0.24.0 — BGM tail-fade duration slider. Sits inside the "配樂"
// SettingsGroup right under <BgmSourcePicker>. Pulls the current
// value from ``project.bgm_fade_out_sec`` and PATCHes
// /projects/{id}/bgm-fade-out on commit. Default value (3.0 s)
// reflects the most-common operator preference; the slider is the
// affordance for "I want this to taper longer" or "I want the
// historical hard-cut" (= 0).
//
// Saves on commit (mouse-up / keyup) so a drag through the slider
// doesn't fire one PATCH per intermediate value. Local state mirrors
// the input so the slider feels responsive while the request is in
// flight.

import { useEffect, useState } from "react";
import { ApiError, apiClient } from "../api/client";
import type { ProjectDetail } from "../api/types";
import "./BgmFadeOutSlider.css";

interface BgmFadeOutSliderProps {
  project: ProjectDetail | null;
  onProjectUpdated: (next: ProjectDetail) => void;
  disabled?: boolean;
}

const FADE_MIN = 0;
const FADE_MAX = 5;
const FADE_STEP = 0.5;

export default function BgmFadeOutSlider({
  project,
  onProjectUpdated,
  disabled,
}: BgmFadeOutSliderProps) {
  const saved = project?.bgm_fade_out_sec ?? 3.0;
  const [draft, setDraft] = useState<number>(saved);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // When the parent refreshes ``project`` (e.g. after re-render), pick
  // up the new saved value unless the user is mid-drag (saving=true).
  useEffect(() => {
    if (!saving) {
      setDraft(saved);
    }
  }, [saved, saving]);

  const commit = async (value: number) => {
    if (!project || value === saved) return;
    setSaving(true);
    setError(null);
    try {
      const next = await apiClient.patchProjectBgmFadeOut(project.id, value);
      onProjectUpdated(next);
    } catch (exc) {
      const msg = exc instanceof ApiError ? exc.message : String(exc);
      setError(msg);
      // Revert local draft to the last known-saved value so the
      // slider reflects DB state when the request fails.
      setDraft(saved);
    } finally {
      setSaving(false);
    }
  };

  const label = draft === 0 ? "0 秒（直接切）" : `${draft.toFixed(1)} 秒`;

  return (
    <div className="bgm-fade-out">
      <div className="bgm-fade-out__head">
        <label className="bgm-fade-out__title" htmlFor="bgm-fade-out-input">
          配樂淡出
        </label>
        <span className="bgm-fade-out__value">{label}</span>
        {saving && <span className="bgm-fade-out__saving">儲存中…</span>}
      </div>
      <input
        id="bgm-fade-out-input"
        type="range"
        className="bgm-fade-out__slider"
        min={FADE_MIN}
        max={FADE_MAX}
        step={FADE_STEP}
        value={draft}
        disabled={disabled || saving}
        onChange={(e) => setDraft(Number(e.target.value))}
        onMouseUp={() => commit(draft)}
        onTouchEnd={() => commit(draft)}
        onKeyUp={() => commit(draft)}
      />
      <p className="bgm-fade-out__hint">
        影片結尾配樂淡出秒數。0 秒 = 不淡出（直接切斷，pre-0.24.0 行為）。
      </p>
      {error && <p className="bgm-fade-out__error">儲存失敗：{error}</p>}
    </div>
  );
}
