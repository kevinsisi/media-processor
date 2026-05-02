// zh-Hant display labels for the M4 fixed scene + motion tag enums.
// Single source of truth — pages import from here so the same English
// enum value renders the same Traditional-Chinese label everywhere.

export const SCENE_TAG_LABELS: Record<string, string> = {
  indoor: "室內",
  outdoor: "室外",
  studio: "棚拍",
  closeup: "特寫",
  medium_shot: "中景",
  wide: "全景",
  dynamic: "動態",
  static: "靜態",
  bright: "明亮",
  dim: "昏暗",
  mixed_light: "混合光",
};

export const MOTION_TAG_LABELS: Record<string, string> = {
  pan: "橫移",
  tilt: "俯仰",
  zoom: "推拉",
  static: "固定",
  handheld: "手持",
};

export const ANALYSIS_STEP_LABELS: Record<string, string> = {
  stt: "轉錄",
  scene: "場景",
  motion: "運鏡",
  emotion: "情緒",
  coverage: "對稿",
};

// Phase 8.1 — face emotion classes returned by services/emotion.py.
export const EMOTION_TAG_LABELS: Record<string, string> = {
  happy: "開心",
  surprised: "驚喜",
  serious: "嚴肅",
  neutral: "平靜",
};

// Emoji glyphs used as the leading icon on the emotion chip; kept here
// (not in styles) so swapping copy stays a one-file change.
export const EMOTION_TAG_ICONS: Record<string, string> = {
  happy: "😄",
  surprised: "😮",
  serious: "😐",
  neutral: "🙂",
};

export function labelForEmotionTag(name: string): string {
  return EMOTION_TAG_LABELS[name] ?? name;
}

export function iconForEmotionTag(name: string): string {
  return EMOTION_TAG_ICONS[name] ?? "";
}

// M5 — auto-edit pipeline stage labels. Used by ProjectEdit.tsx.
// M6.4 added the bgm stage; it no-ops (auto "done") when the project has
// no uploaded BGM track, so the chip just confirms the stage ran.
export const EDIT_STEP_LABELS: Record<string, string> = {
  plan: "規劃",
  cut: "切片",
  concat: "拼接",
  subtitles: "字幕",
  bgm: "配樂",
};

export const DRAFT_STATUS_LABELS: Record<string, string> = {
  pending: "排隊中",
  processing: "剪輯中",
  ready_for_review: "完成",
  approved: "已採用",
  rejected: "已退回",
  failed: "失敗",
};

export const CUT_SOURCE_LABELS: Record<string, string> = {
  scripted: "照稿",
  improv: "即興",
};

export function labelForEditStep(name: string): string {
  return EDIT_STEP_LABELS[name] ?? name;
}

export function labelForDraftStatus(value: string): string {
  return DRAFT_STATUS_LABELS[value] ?? value;
}

export function labelForCutSource(value: string | null | undefined): string {
  if (!value) return "—";
  return CUT_SOURCE_LABELS[value] ?? value;
}

// Top-level asset status pills.
export const ASSET_STATUS_LABELS: Record<string, string> = {
  pending: "待分析",
  analyzing: "分析中",
  analyzed: "已分析",
  analysis_failed: "分析失敗",
};

// Per-step status pills (the values stored in analysis_steps_json).
export const STEP_STATE_LABELS: Record<string, string> = {
  pending: "等待",
  running: "進行中",
  done: "完成",
};

// Failure-class labels. The raw token is `failed:{reason}`; we render the
// reason via a partial-match map so unknown reasons fall back to a plain
// "失敗" pill.
export const FAILURE_REASON_LABELS: Record<string, string> = {
  "gpu-unavailable": "GPU 不可用",
  "quota-exhausted": "配額耗盡",
  "missing-script": "缺少腳本",
  timeout: "逾時",
  "disk-error": "儲存錯誤",
  "model-error": "模型錯誤",
};

// Skip-reason labels. Steps that legitimately can't run (e.g. coverage
// against a project with no script) report ``skipped:{reason}`` rather
// than ``failed:{reason}`` so the UI can render a calm chip instead of
// a red error pill.
export const SKIP_REASON_LABELS: Record<string, string> = {
  "no-script": "略過（無腳本）",
  "no-transcript": "略過（無語音）",
};

export function labelForStepState(value: string | undefined): string {
  if (!value) return "等待";
  if (value.startsWith("failed:")) {
    // failed:gpu-unavailable / failed:model-error:Foo / failed:disk-error:bar
    const reason = value.slice("failed:".length);
    for (const key of Object.keys(FAILURE_REASON_LABELS)) {
      if (reason === key || reason.startsWith(`${key}:`)) {
        return FAILURE_REASON_LABELS[key];
      }
    }
    return "失敗";
  }
  if (value.startsWith("skipped:")) {
    const reason = value.slice("skipped:".length);
    return SKIP_REASON_LABELS[reason] ?? "略過";
  }
  return STEP_STATE_LABELS[value] ?? value;
}

export function labelForSceneTag(name: string): string {
  return SCENE_TAG_LABELS[name] ?? name;
}

export function labelForMotionType(name: string): string {
  return MOTION_TAG_LABELS[name] ?? name;
}

export function labelForAssetStatus(value: string): string {
  return ASSET_STATUS_LABELS[value] ?? value;
}
