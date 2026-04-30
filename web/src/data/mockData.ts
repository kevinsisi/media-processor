/**
 * Mock data for the design preview. Mirrors the entity shape from the
 * Phase α design spec (§4) but lives only in the browser — no backend
 * required. Replace with API calls once the worker pipeline lands.
 */

export type ProjectStatus =
  | "ingesting"
  | "analyzing"
  | "drafted"
  | "approved";

export interface MockProject {
  id: string;
  number: string; // editorial issue number (zero-padded)
  client: "carsmeet" | "freelance";
  name: string;
  profileName: "carsmeet-luxury" | "universal";
  assetCount: number;
  status: ProjectStatus;
  draftVersion?: number;
  pendingReview?: number;
  pipelineStage?: { stage: number; total: number; label: string };
  createdAt: string; // ISO-ish for display
}

export type SegmentTag =
  | "logo_close_up"
  | "integral_hero_shot"
  | "body_line_pan"
  | "wheel_caliper"
  | "interior_leather"
  | "dashboard"
  | "exhaust_pipe"
  | "stranger_face";

export const TAG_DISPLAY: Record<
  SegmentTag,
  { short: string; label: string; tone: "gold" | "hero" | "wheel" | "body" | "interior" | "warn" }
> = {
  logo_close_up: { short: "L", label: "Logo 特寫", tone: "gold" },
  integral_hero_shot: { short: "Hr", label: "整車 Hero", tone: "hero" },
  body_line_pan: { short: "Bd", label: "車身線條", tone: "body" },
  wheel_caliper: { short: "W", label: "輪框/卡鉗", tone: "wheel" },
  interior_leather: { short: "I", label: "皮椅內裝", tone: "interior" },
  dashboard: { short: "D", label: "儀表板", tone: "interior" },
  exhaust_pipe: { short: "E", label: "排氣管", tone: "wheel" },
  stranger_face: { short: "F", label: "陌生人臉", tone: "warn" },
};

export interface MockSegment {
  order: number;
  startMs: number;
  endMs: number;
  tag: SegmentTag;
  score: number;
  assetName: string;
  reasons: string[];
  beat?: number; // beat index this segment is aligned to
}

export interface MockDraft {
  id: string;
  projectId: string;
  version: number;
  aiScore: number;
  segments: MockSegment[];
  durationMs: number;
  beatGridCount: number;
  intel: {
    counts: Partial<Record<SegmentTag, number>>;
    captionsLines: number;
    bpmAlignedCuts: number;
    strangerFacesNotInBlurList: number;
  };
}

// ───────────────────────────────────────────────────────────────────

export const MOCK_PROJECTS: MockProject[] = [
  {
    id: "p_003",
    number: "003",
    client: "carsmeet",
    name: "Phantom 隕石長軸 0428",
    profileName: "carsmeet-luxury",
    assetCount: 150,
    status: "drafted",
    draftVersion: 1,
    pendingReview: 1,
    createdAt: "2026·04·30 · 14:22",
  },
  {
    id: "p_002",
    number: "002",
    client: "freelance",
    name: "王先生婚禮速剪 0501",
    profileName: "universal",
    assetCount: 87,
    status: "analyzing",
    pipelineStage: { stage: 5, total: 8, label: "reframe" },
    createdAt: "2026·04·30 · 11:08",
  },
  {
    id: "p_001",
    number: "001",
    client: "carsmeet",
    name: "Bentley GTC V8 Mansory 0427",
    profileName: "carsmeet-luxury",
    assetCount: 200,
    status: "approved",
    draftVersion: 2,
    createdAt: "2026·04·27 · 16:50",
  },
];

// ───────────────────────────────────────────────────────────────────

export const MOCK_DRAFT: MockDraft = {
  id: "d_003_v1",
  projectId: "p_003",
  version: 1,
  aiScore: 8.4,
  durationMs: 30000,
  beatGridCount: 30,
  segments: [
    {
      order: 0,
      startMs: 0,
      endMs: 1200,
      tag: "integral_hero_shot",
      score: 9.2,
      assetName: "IMG_3401.MOV",
      beat: 0,
      reasons: [
        "整車 hero shot · 45° 側角",
        "畫面穩定（無抖動）",
        "profile 加權 +1.4（carsmeet 偏好開場 hero）",
        "對齊重拍 #1",
      ],
    },
    { order: 1, startMs: 1200, endMs: 1700, tag: "logo_close_up", score: 8.7, assetName: "IMG_3421.MOV", beat: 2, reasons: ["Logo 特寫（飛天女神）信心 92%", "對齊重拍 #2"] },
    { order: 2, startMs: 1700, endMs: 2300, tag: "wheel_caliper", score: 7.4, assetName: "IMG_3502.MOV", beat: 3, reasons: ["輪框 + 紅色卡鉗特寫", "曝光適中"] },
    { order: 3, startMs: 2300, endMs: 2900, tag: "body_line_pan", score: 8.1, assetName: "IMG_3454.MOV", beat: 4, reasons: ["車身線條 slow pan", "光線反射飽滿"] },
    { order: 4, startMs: 2900, endMs: 3500, tag: "logo_close_up", score: 8.2, assetName: "IMG_3422.MOV", beat: 5, reasons: ["Logo 特寫（B 字徽）", "對齊重拍 #5"] },
    { order: 5, startMs: 3500, endMs: 4100, tag: "interior_leather", score: 7.6, assetName: "IMG_3611.MOV", beat: 6, reasons: ["菱格紋皮椅", "金線縫線清楚"] },
    { order: 6, startMs: 4100, endMs: 4700, tag: "dashboard", score: 7.1, assetName: "IMG_3618.MOV", beat: 7, reasons: ["儀表板 + 方向盤", "對齊重拍 #7"] },
    { order: 7, startMs: 4700, endMs: 5500, tag: "integral_hero_shot", score: 8.9, assetName: "IMG_3403.MOV", beat: 8, reasons: ["整車 hero · 車頭正面低角度", "profile 加權 +1.4"] },
    { order: 8, startMs: 5500, endMs: 6100, tag: "wheel_caliper", score: 7.3, assetName: "IMG_3505.MOV", beat: 9, reasons: ["輪框正面 · 22 吋", "對齊重拍 #9"] },
    { order: 9, startMs: 6100, endMs: 6700, tag: "logo_close_up", score: 8.5, assetName: "IMG_3423.MOV", beat: 10, reasons: ["Logo 特寫（飛天女神 · 側角）", "光線反射"] },
    { order: 10, startMs: 6700, endMs: 7400, tag: "body_line_pan", score: 8.0, assetName: "IMG_3457.MOV", beat: 11, reasons: ["車尾流線", "對齊重拍 #11"] },
    { order: 11, startMs: 7400, endMs: 8000, tag: "exhaust_pipe", score: 7.0, assetName: "IMG_3702.MOV", beat: 12, reasons: ["排氣管尾段特寫", "對齊重拍 #12"] },
    { order: 12, startMs: 8000, endMs: 8600, tag: "interior_leather", score: 7.8, assetName: "IMG_3613.MOV", beat: 13, reasons: ["後座皮椅 + 折疊餐桌", "燈光柔和"] },
    { order: 13, startMs: 8600, endMs: 9300, tag: "logo_close_up", score: 8.6, assetName: "IMG_3424.MOV", beat: 14, reasons: ["Logo 特寫（飛天女神）信心 94%", "對齊重拍 #14"] },
    { order: 14, startMs: 9300, endMs: 10000, tag: "wheel_caliper", score: 7.2, assetName: "IMG_3506.MOV", beat: 15, reasons: ["輪框 + 卡鉗", "略晃 — 可接受"] },
    { order: 15, startMs: 10000, endMs: 10700, tag: "body_line_pan", score: 7.9, assetName: "IMG_3460.MOV", beat: 16, reasons: ["引擎蓋 slow pan", "對齊重拍 #16"] },
    { order: 16, startMs: 10700, endMs: 11400, tag: "dashboard", score: 7.0, assetName: "IMG_3621.MOV", beat: 17, reasons: ["儀表 close-up", "對齊重拍 #17"] },
    { order: 17, startMs: 11400, endMs: 12100, tag: "logo_close_up", score: 8.3, assetName: "IMG_3425.MOV", beat: 18, reasons: ["Logo 特寫", "對齊重拍 #18"] },
    { order: 18, startMs: 12100, endMs: 12800, tag: "interior_leather", score: 7.7, assetName: "IMG_3614.MOV", beat: 19, reasons: ["皮椅菱格", "對齊重拍 #19"] },
    { order: 19, startMs: 12800, endMs: 13500, tag: "integral_hero_shot", score: 8.7, assetName: "IMG_3404.MOV", beat: 20, reasons: ["整車 · 車側 90°", "profile 加權 +1.4"] },
    { order: 20, startMs: 13500, endMs: 14200, tag: "wheel_caliper", score: 7.1, assetName: "IMG_3508.MOV", beat: 21, reasons: ["輪框", "對齊重拍 #21"] },
    { order: 21, startMs: 14200, endMs: 14900, tag: "body_line_pan", score: 7.8, assetName: "IMG_3463.MOV", beat: 22, reasons: ["車身線條", "光反射"] },
    { order: 22, startMs: 14900, endMs: 15600, tag: "logo_close_up", score: 8.4, assetName: "IMG_3426.MOV", beat: 23, reasons: ["Logo 特寫", "對齊重拍 #23"] },
    { order: 23, startMs: 15600, endMs: 16300, tag: "exhaust_pipe", score: 6.9, assetName: "IMG_3704.MOV", beat: 24, reasons: ["排氣管", "略過曝 — 仍可用"] },
    { order: 24, startMs: 16300, endMs: 17000, tag: "interior_leather", score: 7.5, assetName: "IMG_3615.MOV", beat: 25, reasons: ["皮椅 + 紅色縫線", "對齊重拍 #25"] },
    { order: 25, startMs: 17000, endMs: 17700, tag: "logo_close_up", score: 8.6, assetName: "IMG_3427.MOV", beat: 26, reasons: ["Logo · 角度切換", "對齊重拍 #26"] },
    { order: 26, startMs: 17700, endMs: 18400, tag: "wheel_caliper", score: 7.0, assetName: "IMG_3509.MOV", beat: 27, reasons: ["輪框", "對齊重拍 #27"] },
    {
      order: 27,
      startMs: 18400,
      endMs: 30000,
      tag: "integral_hero_shot",
      score: 9.5,
      assetName: "IMG_3405.MOV",
      beat: 28,
      reasons: [
        "整車收尾 hero · 車頭 45° 低角度",
        "燈光最佳幀",
        "profile 加權 +1.4（carsmeet 偏好收尾 hero）",
        "對齊重拍 #28（含尾拍）",
      ],
    },
  ],
  intel: {
    counts: {
      logo_close_up: 9,
      integral_hero_shot: 4,
      wheel_caliper: 6,
      body_line_pan: 4,
      interior_leather: 4,
      dashboard: 2,
      exhaust_pipe: 2,
    },
    captionsLines: 12,
    bpmAlignedCuts: 28,
    strangerFacesNotInBlurList: 2,
  },
};

export function findProject(id: string): MockProject | undefined {
  return MOCK_PROJECTS.find((p) => p.id === id);
}
