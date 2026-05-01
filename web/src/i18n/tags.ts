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
  coverage: "對稿",
};

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
