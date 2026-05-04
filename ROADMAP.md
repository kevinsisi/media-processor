# Media Processor — ROADMAP（Phase 6–10）

> **單一定位**：個人 / 小團隊用的「拍完就上傳，AI 直接給可發佈影片」的工具。
> 目標 UX：手機優先、繁體中文、高級感、最少手動編輯。
> 目前版本：**0.26.0**（M9.11 — 素材管理：單個 / 批次刪除 + analysis 列表上的長度・解析度・大小）

## Phase 進度速覽

| Phase | 主題 | 狀態 | 版本 |
| --- | --- | --- | --- |
| M2 | 資料模型 + 核心服務 | ✅ done | 0.2.x |
| M3 | 上傳 UI（手機優先） | ✅ done | 0.3.x |
| M4 | AI 分析 pipeline（Whisper + Vision + Coverage） | ✅ done | 0.4.x – 0.10.x |
| M4.6 | Asset thumbnail gallery | ✅ done | 0.10.x |
| M5 | Auto-edit MVP（cut planner + edit planner + LLM patcher） | ✅ done | 0.11.x |
| M6 | Rhythm + Transitions + BGM | ✅ done | 0.12.0 |
| M7 | Manual control（timeline / subtitle / export format） | ✅ done | 0.13.0 |
| M8.1 | 情緒分析 + 運鏡縮放（zoompan + emotion-shift transitions） | ✅ done | 0.14.0 – 0.14.2 |
| M8.3 | vidstab digital stabilization + subtitle/transition toggles + AI feedback | ✅ done | 0.14.3 – 0.14.9 |
| M9.0 | AI BGM 生成（MusicGen）+ curated music library | ✅ done | 0.15.0 – 0.15.3 |
| M9.1 | YOLO 物件追蹤 + auto-reframe 動態裁切 | ✅ done | 0.16.0 – 0.16.2 |
| M9.2 | 通用追蹤目標選擇 + 每片段音量控制 | ✅ done | 0.17.0 |
| M9.3 | 浮水印 / logo overlay | ✅ done | 0.18.0 |
| M9.4 | 字幕樣式 + 風格預設 + 雙語字幕 | ✅ done | 0.19.0 |
| M9.5 | 時間軸編輯器 Phase 1 + UX 收斂 | ✅ done | 0.20.0 – 0.20.3 |
| M9.6 | 轉場 flag 持久化 + 配樂自動觸發 + 主角類別 auto-trim | ✅ done | 0.21.0 – 0.21.6 |
| M9.7 | UI/UX 全面收斂（路由修正 + 標籤具體化 + 失敗收摺 + 假進度條移除） | ✅ done | 0.22.0 |
| M9.8 | 像素級單點追蹤（Lucas-Kanade）+ 全螢幕 modal + 5 處座標 / 渲染 / dispatcher / 旋轉根因修正 | ✅ done | 0.23.0 – 0.23.7 |
| M9.9 | 配樂淡出 + 轉場預設不勾 + voice_volume=0 silent-drop 根因修正 | ✅ done | 0.24.0 |
| M9.10 | RQ queue inspector + 排隊任務取消 + orphan-Draft 自動重新提交 watchdog | ✅ done | 0.25.0 – 0.25.1 |
| **M9.11** | **素材管理：單個 / 批次刪除 + analysis 列表 meta 行（長度・解析度・大小）** | ✅ done | **0.26.0** |
| M10 | 多專案批次 + 社群直接發布 + AI 自動縮圖 | 🔮 future | 0.27.x+ |

---

## ✅ Phase 6（M6）— 已完成（0.12.0）

主題：**讓自動剪輯不再像機器**，並把 Gemini 從瓶頸中救出來。

### 6.1 Per-asset Gemini fanout
- `services/edit_planner` 把單一大 prompt 拆成 14 隻並行小 prompt
- 每段 asset 獨立評分（score / position / best_span / source_kind / transition_to_next）
- key rotation + 並行 → 規劃時間從 90–180s 降到 ~12s；單隻壞 asset 不拖整 plan
- 抽成 skill：`skills/gemini-prompts/asset-score/SKILL.md`

### 6.2 Rhythm-aware cut ordering
- `_AssetScore.dominant_motion` 來自 best_span_ms 與 motion-tag time_ranges 的最大 overlap
- 排序時加分：與前段 motion 不同 +10、符合該 bucket 偏好 +15
- 軟約束 — 小型拍攝也保證有結果

### 6.3 xfade transitions
- `transition_to_next` 由 Gemini 提建議，預設 `dissolve`
- `concat_segments` 加 xfade chain path（offset cumulative + acrossfade 同步）
- `TRANSITION_DURATION_S = 0.5`，`VALID_TRANSITIONS` whitelist

### 6.4 BGM mix + voice ducking
- `services/bgm_mixer.py` — SRT cue ranges 直接當 voice presence，省一個 VAD
- `BGM_VOLUME_BASE = 0.55`、`BGM_VOLUME_DUCKED = 0.20`、`-shortest` 自動切短
- `Project.bgm_path` + `POST/DELETE /projects/{id}/bgm` 上傳 API
- BGM 失敗只標 `bgm` stage failed，subtitled mp4 仍可交付

---

## ✅ Phase 7（M7）— 手動控制（已完成 0.13.0）

主題：**自動剪輯 70% 已 OK，再給使用者最後一哩的精修能力**。手機優先。

### 7.1 時間軸拖拉排序
- `PATCH /drafts/{id}/order` 接 cut_id 順序，重新計算 on_timeline_*_ms
- skip-plan render fast path：跳過 Gemini，直接 cut→stabilize→concat→subtitles→bgm
- 拖完到出新 mp4 大約 30–90 秒（取決於 stabilize 是否開）

### 7.2 字幕 inline 編輯
- 新 `subtitle_cue` table（draft_id, idx, start_ms, end_ms, text, edited_at）
- 字幕 stage 完跑後寫進 `subtitle_cue`，不再只保 SRT
- `PATCH /drafts/{id}/subtitles/{idx}` 編輯後 `POST /drafts/{id}/rebuild-subtitles` 跳到字幕 burn-in 階段重燒

### 7.3 匯出格式 + 解析度
- 「匯出」sheet：9:16 / 4:5 / 1:1 比例 × 720p / 1080p / 1440p
- `POST /drafts/{id}/export` body `{aspect, height}` enqueue 獨立 export job
- 檔名 `v{N}-{aspect}-{height}p.mp4`，不覆蓋原檔
- 動態 cap：原素材只有 720p 不能輸出 1080p

---

## ✅ Phase 8.1（M8.1）— 情緒分析 + 運鏡縮放（已完成 0.14.0）

主題：**讓自動剪輯讀懂表情**，並順手清掉兩個 M6 留下的小 bug。

### 8.1.1 字幕時間 remap 修正（bug fix）
- `subtitles.build_cues` 沒扣掉 M6.3 xfade 的 0.5 s 重疊，所以 cut N 的字幕會晚 (N-1)*0.5 s 出。
- 修：把 `TRANSITION_OVERLAP_MS = 500` 鏡像到 subtitles，每段 cut（除了第一段）開始前先把 timeline_cursor 拉回 500 ms。

### 8.1.2 轉場停留在 dissolve（bug fix）
- 加 `circlecrop` 到 `VALID_TRANSITIONS` whitelist（renderer + planner 兩處）。
- 改 per-asset 評分 prompt：明確列出 fade/dissolve/wipeleft/slideright/circlecrop 五種、加上「避免整支片只用一種」指令。

### 8.1.3 MediaPipe 臉部情緒分析
- 新 `services/emotion.py`：MediaPipe Face Landmarker（Tasks API）以 2 fps 取樣，blendshapes 對應到 happy / surprised / serious / neutral，相鄰相同分類合併成 ranges。
- 模型 `.task` 檔首次分析時懶下載到 `${EMOTION_MODEL_DIR}/face_landmarker.task`（預設 `/app/media/emotion_models/`），離線部署可用 `EMOTION_MODEL_PATH` 指向預下載檔。
- `EMOTION_FAKE=1` 給 CI 用的確定性 stub。
- 新 `AnalysisStep.EMOTION` + `_run_emotion`；存進現有 `asset_tags` 表（無 schema migration）。

### 8.1.4 情緒驅動的剪輯決策
- 每段 `_AssetScore` / `CutPlanSegment` 帶 `dominant_emotion`（`serialise_plan` 也持久化）。
- `_assemble_plan` 在情緒桶（dynamic={happy, surprised} / static={serious, neutral}）跨界時把 `transition_to_next` 升級成 `circlecrop`。
- `video_renderer._cut_segment` 對 `dominant_emotion ∈ {happy, surprised}` 的 cut 在 aspect 切完之後串一段 `zoompan`（`1.0 → 1.15` 跨 cut 全長），靜態 / 中性 cut 維持原尺寸。

---

## ✅ Phase 9.2（M9.2）— 通用追蹤目標選擇 + 每片段音量（已完成 0.17.0）

主題：**讓 YOLO 不再只能看單一主體，並把音量交回給人**。

### 9.2.1 多物件追蹤 + 通用目標選擇
- `services/object_tracking.detect` 不再只回傳 dominant track；按 class 分群成 `tracks[]`，每個 track 有 `object_index`、`cls_name`、`area_score`、`frames`。
- COCO 80 類全開（不再只有 SUBJECT_CLASS_PRIORITY 10 類），但排序仍偏向 person/car/dog 等主流主體。
- 新欄位 `Asset.tracked_object_index`：null=自動 / >=0=指定物件 / -1=自訂 ROI / -2=固定構圖 / -3=不追蹤。
- 新欄位 `Asset.custom_roi_json`：CSRT 追蹤的使用者畫框（OpenCV `TrackerCSRT`）。
- API：
  - `GET /assets/{id}/tracking` 回傳每 track 的下采樣 bbox + 當前目標。
  - `PATCH /assets/{id}/tracking-target` body `{mode, object_index?, custom_roi?}`。
- 渲染：`auto_reframe.compute_crop_path(object_index=...)` + `compute_crop_path_from_custom_roi(...)` + `video_renderer._cut_segment(custom_roi=...)`，特殊 sentinel -2/-3 直接退回靜態置中。

### 9.2.2 每片段音量
- `DraftSegment.voice_volume`（float, default 1.0）+ `bgm_volume`（float, nullable, null=自動 ducking）。
- API：`PATCH /drafts/{id}/segments/{seg_id}/volume` body `{voice_volume?, bgm_volume?}`，partial 寫入；body 含 `bgm_volume: null` 顯式回 auto。
- `services/bgm_mixer.SegmentVolume` + `_build_voice_volume_expr` / `_build_bgm_volume_expr`：把每段 timeline 的 gain 表達式組成 nested `if(between(t,...))`，BGM 在沒設 override 的窗口內仍走 auto duck。
- `apply_voice_volume`：沒有 BGM 也能跑 voice-only re-encode，不強迫使用者上傳 BGM 才能改音量。

---

## ✅ Phase 9.3（M9.3）— 浮水印 / logo overlay（已完成 0.18.0）

主題：**品牌客戶要 logo 燒在每支片**。一次設好整個 project 通用。

詳細 OpenSpec：`openspec/changes/archive/2026-05-03-v0.18-watermark-overlay/`。

- `Project.watermark_path` / `_position` / `_scale` / `_opacity`（alembic 0014）— PNG 路徑 + 9-grid 錨點 + 邊長比例 + 不透明度
- `services/video_renderer.apply_watermark` 在字幕 burn-in 後、BGM 混音前燒入；`watermark_path IS NULL` 時無動作（向後相容）
- `POST/PATCH/DELETE /projects/{id}/watermark` 三隻 endpoint；PNG-only、≤ 5 MB
- `web/src/components/WatermarkPicker.tsx`：3×3 grid 位置 + 兩條 slider + 即時 preview，掛在 `視覺疊加` settings group

---

## ✅ Phase 9.4（M9.4）— 字幕樣式 + 風格預設 + 雙語字幕（已完成 0.19.0）

主題：**剪輯風格、字幕外觀、英文字幕一次解決**。三個 feature 並行開發，因為 alembic chain 有衝突最後合併成 0.19.0 release。

詳細 OpenSpec：`openspec/changes/archive/2026-05-03-v0.19-subtitle-style-i18n-presets/`。

### 9.4.1 字幕樣式自訂
- `Project.subtitle_*`（font / color / outline_color / position / size / outline_width，alembic 0015）
- `SubtitleStyle` dataclass 串進 `burn_subtitles`；`PATCH /projects/{id}/subtitle-style` 部分更新
- `SubtitleStyleEditor.tsx` 8-direction text-shadow 模擬 drawtext 即時 preview

### 9.4.2 Clip-style preset
- 五種 preset：fast / slow / commercial / artistic / custom
- 每個 preset 帶 `min_span_ms` / `max_span_ms` / `transition_allowlist` / `default_transition` / `bgm_hint` / `prompt_hint`
- `Draft.style_preset`（alembic 0016）snapshot 觸發時的選擇
- 重新引入 `fade` / `dissolve` / `fadeblack` / `fadewhite` 四個 xfade（v0.14.3 cleanup 拿掉的）給 slow / artistic / commercial 用
- `StylePresetPicker.tsx` 五卡 radio group

### 9.4.3 雙語字幕（英文 secondary track）
- `Asset.subtitle_secondary_lang` / `_segments_json` + `DraftSegment.subtitle_secondary_text`（alembic 0017）
- Whisper task=`translate` 跑出英文 SRT
- `burn_subtitles` 加 `secondary_srt_path`，第二行 drawtext 字幕在主字幕上方略小的位置

### 9.4.4 Alembic chain 修補
- 四個 0.18.0 PR 並行開發各自鑄 0014_*；merge 後 manual re-chain `down_revision`：
  ```
  0013 → 0014_project_watermark → 0015_project_subtitle_style → 0016_draft_style_preset → 0017_secondary_subtitles
  ```
- 教訓：parallel-branch merge 必須 manual re-chain，否則 `alembic upgrade head` 會 "Multiple head revisions"

---

## ✅ Phase 9.5（M9.5）— 時間軸編輯器 + UX 收斂（已完成 0.20.0 – 0.20.3）

主題：**自動剪輯給的草稿 95% 對，剩下 5% 用時間軸 1 秒精修**，順便把 ProjectEdit 的指示器收乾淨。

詳細 OpenSpec：`openspec/changes/archive/2026-05-03-v0.20-timeline-editor-ux-pass/`。

### 9.5.1 時間軸編輯器 Phase 1（0.20.0）
- 新 `TimelineEditor.tsx` 頁面 + `DraggableTimeline.tsx` component
- 三隻 segment-level endpoint：split / patch / delete（none auto-enqueue render）
- Apply 鈕走既有 `PATCH /drafts/{id}/order` 的 skip-plan render path
- `_reflow_segments_and_cut_plan(draft)` 共用 helper recurses on-timeline coords + regenerates `cut_plan_json`
- 量化拖拉到 100 ms

### 9.5.2 Mobile landscape patch（0.20.1）
- 拖拉熱區從 4 px 拓寬到 14 px（視覺軌仍 2 px）
- 鍵盤 ←/→ 微調聚焦邊緣 100 ms
- Mobile-landscape media query 收掉 playhead 標籤避免 overflow

### 9.5.3 UX 清晰化（0.20.2）
- ProjectEdit 三個 settings group 加 summary line（`60 秒 · 文青風 · 字幕中下方`）
- StylePreset → BGM 互動 banner：選非 custom 風格但 BGM 來源是 none/upload/library 時提示切「依風格預設自動生成」
- WatermarkPicker 樂觀 feedback（剛上傳的檔名顯示 2.5 s）
- ProjectAnalysis per-step status grid + retry icon

### 9.5.4 BGM 5-radio 簡化（0.20.3）
- 把舊的「建議 + 最終效果」兩層折成單一 5-radio 選擇器：none / preset / library / ai / upload
- 每個 radio 的 panel 直接是最終效果 — 沒有 suggestion banner
- Sticky `userChoseSourceRef` 防 auto-switch 蓋掉手動選擇

### 9.5.5 0.20.3 bug fix
- 修 `_project_detail` duplicate shadow（GET /projects/{id} 一直回 watermark_path=NULL）
- WatermarkPicker `createObjectURL` 即時 preview

---

## ✅ Phase 9.6（M9.6）— 轉場 flag 持久化 + 配樂自動觸發 + 主角類別（已完成 0.21.0 – 0.21.4）

主題：**修三個獨立 papercut**：reorder 後轉場默默打開 / BGM 切風格後播舊曲 / 沒法叫 LLM 專注一個主題。

詳細 OpenSpec：`openspec/changes/archive/2026-05-03-v0.21-transitions-bgm-subject-class/`。

### 9.6.1 主角類別 auto-trim（0.21.0）
- `Project.subject_class`（alembic 0018，nullable，COCO 80 之一）
- `_subject_presence_range_ms` + `_apply_subject_filter` 在 plan() / heuristic_fallback() 之間夾入 drop / clamp / snap
- A=drop（class 沒出現的素材直接踢掉）、B=snap（LLM 選到的 span 完全不重疊時 snap 到出現範圍 ±0.5s）
- `aggregate_detected_classes` 聚合所有 asset 的 tracking_json，產出按 frame 數降序的 class 清單
- `GET /projects/{id}/detected-classes` + `PATCH /projects/{id}/subject-class`
- `SubjectClassPicker.tsx` fetch 後動態渲染（不 hard-code 80 類），首次 mount auto-PATCH 出現最多的 class

### 9.6.2 Skip-plan re-render flag 持久化（0.21.1 → 0.21.3）
- 0.21.1：`Draft.render_flags_json`（alembic 0019）snapshot trigger 時的 4 個 flag；reorder / rebuild-subtitles 讀 snapshot
- 0.21.3：legacy NULL row 仍會 fallback all-True 是個漏洞 — 加 `RenderFlagsOverride` body schema 在兩個 endpoint，priority `body > snapshot > all-True`，並把 resolved flags backfill 寫回 Draft（legacy NULL 第一次 re-render 後 settle 到正確狀態）
- FE：`DraggableTimeline` + `SubtitleEditor` 加 `renderFlags` prop，`ProjectEdit` 把當前 toggle 灌進去

### 9.6.3 BGM preset UX 連續修正（0.21.2 → 0.21.4）
- 0.21.2：match 綠 banner（「✓ 已根據「文青風」生成配樂」）/ mismatch 橘 banner / 外部 BGM 中性提示；換一首改成小灰連結
- 0.21.3：mismatch banner 加大兩行（「**配樂尚未更新！目前播放的仍是舊配樂**」）+ CTA `--loud` 修飾（pulse 動畫）+ audio player 灰階 + 狀態行「🕘 舊版本」
- 0.21.4：自動觸發 — 切到 preset / 切換風格時 useEffect 自動 fire `handleGeneratePreset`，`autoTriggeredFor` ref latch 防 loop；按鈕改名「🔄 換一首」（auto-trigger 已涵蓋初次生成）

### 9.6.4 Subject_class merge collision
- 一個更早的並行分支用了不同設計（partition + soft demotion）已 push 到 main 為 `1fdba2e`
- 解法：`git revert 1fdba2e`（commit `c7d0399`）→ merge 新設計分支（merge commit `019d4c5`）
- 兩邊用同一個 alembic revision id `0018_project_subject_class` 且 column 形狀一致，prod DB 已 migrate 過的不需要任何 schema 動作

---

## ✅ Phase 9.7（M9.7）— UI/UX 全面收斂（已完成 0.22.0）

主題：**抹掉所有「不明所以」**。每個按鈕的結果可預測、每個狀態的措辭具體、不再對使用者撒謊。

### 9.7.1 ProjectList 路由修正
- 「剪輯就緒」/「成品就緒」原本連到 `/review`，但 Review.tsx 實際只渲染寫死的車身 SVG 佔位圖（不放真正的 mp4）— 是個被遺忘的死頁
- 改：drafted / approved 一律連到 `/edit`（會播放實際 mp4 的頁面），整列點擊與 CTA 點擊目的地一致
- Status 文案：「剪輯就緒」→「可預覽」「剪輯 vN 可預覽」+ CTA「預覽 / 下載 →」；「成品就緒」→「已採用」

### 9.7.2 進度條不再說謊
- 原 ProjectList 的「處理流程執行中」永遠寫死 55% — 移除
- 改用 `.progress-bar--indeterminate` 不定長 shimmer；真實進度在分析頁的步驟矩陣裡

### 9.7.3 Analysis 重新分析按鈕去歧義
- 「重新分析」→「重新分析（保留手改）」；「強制重跑」→「強制重跑（覆寫手改）」
- 兩顆按鈕加 `title` 解釋差別；批次工具列同樣處理
- 移除 AssetCard 的 `title="asset.status = analyzed"` debug-only 文字

### 9.7.4 Upload 下一步門檻
- 0 個素材時「進入素材分析 →」變成不可點的 dim 灰；title 解釋為什麼
- 還在上傳中（pendingUploadCount > 0）時改為警告色 +「進入素材分析（N 個還在上傳）→」

### 9.7.5 BgmSourcePicker AI 面板措辭
- 已存在配樂時的「重新生成」→「重新生成（覆寫舊配樂）」；初次生成「生成配樂」→「🎵 生成 30 秒配樂」

### 9.7.6 TimelineEditor Apply 顯示文字
- Apply 按鈕原本只有 🔄 emoji，操作員猜不到功能
- 改為 icon + 文字（「套用變更」/「已套用」/「套用中…」），<480px 折回 icon-only

### 9.7.7 ProjectEdit 失敗狀態收摺
- 失敗剪輯原直接吐 4–10 行 stack-trace 到畫面；改為先給一行 zh-Hant 解釋（「下方的進度條會標出失敗在哪一階段」），技術細節塞進 `<details>` 摺起來
- 配 `max-height: 320px; overflow-y: auto` 確保再長的 trace 也不撐爆畫面

---

## ✅ Phase 9.8（M9.8）— 像素級單點追蹤（已完成 0.23.0 – 0.23.4）

YOLO 物件追蹤對「我要追那個 logo」這種子-像素需求精度不夠（5 Hz / bbox 中心常落在物件邊上）；CSRT 自訂 ROI 又太重。M9.8 引進「點一下就追那個像素」工作流。

詳細 OpenSpec：`openspec/changes/archive/2026-05-04-v0.23-pixel-precise-point-tracking/`。

### 9.8.1 像素級單點追蹤管線（0.23.0）
- `services/point_tracking.track_point` — pyramidal Lucas-Kanade（`cv2.calcOpticalFlowPyrLK`），從 init 點往前 + 往後雙向追，每個 output frame 都有一筆 `{t_ms, x, y, lost}`；遇到 occlusion / 高 LK error 時凍結在 last good 並標 `lost=True`，Kalman 仍看得到連續測量。
- `Asset.point_tracking_json` + `Asset.point_tracking_origin` 新欄位（alembic 0021）；`tracked_object_index = -4` 是新 sentinel。
- API：`PATCH /assets/{id}/tracking-target` 增 `mode: "point"`，body `{norm_x, norm_y, frame_ms}`，後端乘以 `Asset.resolution` 轉像素再丟給 LK；validate norm_x / norm_y ∈ [0, 1]。
- `auto_reframe.compute_crop_path_from_point_track` — 把每個 LK frame 包成 1×1 bbox，沿用既有 `compute_crop_path` 的 Kalman + max-delta 平滑邏輯；renderer dispatch 順序 `point (-4) → custom_roi (-1) → YOLO`。
- 因為 sync 端點要呼 `cv2`，opencv-python-headless 加進 api 容器（之前只在 worker）。

### 9.8.2 全螢幕 PointPickerModal（0.23.1）
- 桌面那個小縮圖 + overlay 在手機完全不能用。改成全螢幕 modal，支援 wheel + pinch zoom + drag pan，centre-anchored transform。
- Backdrop click / Esc / cancel 不 commit；單擊（drag 距離 < threshold）才送 norm 座標。

### 9.8.3 座標換算 bug 修正（0.23.2 → 0.23.3）
- **0.23.2 modal commit 算式**：原本用 `imgRef.getBoundingClientRect()` 推 norm，遇到 `max-width: 100%; max-height: 100%; object-fit: contain` 的 layout edge case 會偏；換成 `visibleImageRect(stage, naturalWH, zoom, pan)` 從 state 直接算，跟 wheel/pinch 的 zoom-anchor 算式對齊。同時拿掉 `transition: transform 80ms`，避免點擊落在動畫中途取到 partway-through rect。
- **0.23.3 crosshair 顯示算式**：crosshair 原本用 `left: norm_x * 100%` 對 canvas div 定位，但 canvas 含 `object-fit:contain` 黑邊，norm 是相對影片內容的；改用 `norm_x * renderRect.renderedW + renderRect.offsetX`（跟既有的 bbox `cssBoxFor` 一樣的 px 算式）。

### 9.8.4 vidstab + 動態裁切衝突（0.23.4）
- 症狀：v0.23.3 之後 crosshair 顯示對了，但成片畫面仍偏：使用者點車標中央，渲染後車標卻在左 1/3。
- 根因：`stabilize_segments` 跑在 `cut_segments` 之後。動態裁切 sendcmd 已經把 LK 像素鎖在輸出中央，但 vidstab 看到背景在動（因為動態裁切「製造」了背景的相對運動 — 主角不動、背景跟著鏡頭走），就算成 camera shake 套個 translate 抵銷掉，剛剛 crop 拉到中央的主角又被推回邊緣。
- 修法：`_cut_segment` 回傳 bool 表示這段是否套了動態裁切；`cut_segments` 回傳 `(paths, reframed_flags)`；render 把 `{i for i, r in enumerate(reframed_flags) if r}` 當 `skip_indexes` 餵給 `stabilize_segments`，那些 segment 直接拿 cut 階段的輸出，不再二次 vidstab。靜態裁切的 segment 仍走完整 vidstab。

### 9.8.5 sendcmd duplicate-timestamp 規避（0.23.5）
- 症狀：v0.23.4 之後 vidstab 衝突修了，但長運鏡片段裡主角仍會「漂移」— 開頭 LK 像素在中央，後段慢慢偏到左 1/3。
- 根因：ffmpeg 4.4 的 sendcmd 在「同一個 start_time 有多個 directive」且總體 dispatch 速率 ≥ 30 Hz 時，會默默丟棄 second-and-onward 的 directive。`auto_reframe.write_sendcmd_file` 之前每個 frame 寫兩行（一行 x、一行 y，共用 timestamp），共 250 directives over 4 s — 解析 log 看每行都進 queue，但 runtime 只有第一個 x 被套到 crop filter，後面所有 update 都丟了，crop 凍結在 initial 值。
- 排查路徑：拆 chain 從 cut → vidstab → concat 一路驗證 — cut 階段的 seg_NNNN.mp4 單獨拿 ffmpeg 重 render 仍偏；同一 sendcmd 改成 1 Hz / 3 Hz / 10 Hz / 15 Hz 的 sparse 版本都正常；30 Hz 但只寫 x（不寫 y）也正常。確認 bug 在 duplicate-timestamp 的高頻 dispatch 而不是 rate 本身。
- 修法：`write_sendcmd_file` 改寫成每個 timestamp 一行，x 跟 y 用 `,` 分隔在同一個 directive 裡（`0.0000 crop@reframe x 264, crop@reframe y 436;`）。Dispatch rate 從 60 Hz 降到 30 Hz，避開 dispatcher quirk。format 仍合 ffmpeg sendcmd grammar（`,` 在 directive 內分隔 commands、`;` 結束 directive）。

### 9.8.6 bbox 中心 rounding（0.23.6）
- `compute_crop_path_from_point_track` 之前合成 `(int(x-0.5), int(y-0.5), w=1, h=1)` 的 1×1 bbox，希望讓 `compute_crop_path` 的 `cx = x + w//2` 算式落在 LK 像素上。但 `int(x-0.5)` 在浮點 LK output（如 864.3）上會 floor 成 863，後面 `+ 1//2 = 0` 沒有補正，centre 比真實 LK 像素左 1 px。在 1728-wide 來源 + crop_zoom=0.75 之下足以讓長 pan 看起來「差一點才中央」。
- 修法：改成 `(int(round(x)), int(round(y)), w=0, h=0)`，centre = round(x)，沒有任何系統性偏移。

### 9.8.7 旋轉素材 norm → pixel 用 cv2 維度（0.23.7）
- 症狀：v0.23.6 之後大部分素材都中央對齊，但用戶反饋「車頭片段偏左」一直存在 — 不是「過頭」，是同一個方向的固定偏差。
- 根因 — 等到追蹤每一步座標才發現的旋轉 metadata mismatch：asset 18（DJI 4K 直拍）的檔案存的是 3840×2160 landscape stream + `rotate=270` tag + `Display Matrix rotation=90` side data。ffprobe 看到 stream 寫 width=3840，所以 `Asset.resolution = "3840x2160"`。但縮圖（ffmpeg 自動套 rotate）跟 OpenCV 4.13（預設 `CAP_PROP_ORIENTATION_AUTO=1`）讀的都是旋轉後的 2160×3840 portrait。API 之前用 `_asset_native_resolution(asset)` → 3840 乘上 norm_x，把 0.481 算成 init_x=1848，clamp 到 cv2 的 2160 寬之後落在 86%（不是用戶想的 48%）。LK 從錯的像素開始追蹤，整段都偏。其他 9 個素材是原生 portrait（1728×3072，沒 rotation metadata），所以這個 bug 只在那一個旋轉素材上出現。
- 排查路徑：把整條座標鏈印出來 — origin (norm, x, y)、point_tracking_json.src_w/h、cv2 dims；發現 asset 18 唯一一個 `Asset.resolution.W ≠ point_tracking_json.src_w`（3840 vs 2160）。對 ffprobe 跑 `-show_streams` 看到 `TAG:rotate=270` 跟 `Display Matrix rotation=90`。
- 修法：重構 `services.point_tracking.track_point` 改吃 `init_norm_x` / `init_norm_y`，內部開 cv2 之後讀 `CAP_PROP_FRAME_WIDTH/HEIGHT`（旋轉後維度）才換成像素 — 整個 pipeline 對 source dimension 只有「cv2 看到什麼」這個唯一真理。API endpoint 把 norm 直接傳下去，再把回傳的 pixel 寫進 `Asset.point_tracking_origin` 給前端 crosshair 顯示用。
- Migration：之前的旋轉素材已存的 `point_tracking_json` row 因為 init_pixel 在錯的座標空間，必須在 UI 上重新 pick 一次。原生 portrait 素材不受影響，同一個 norm 在新舊兩種程式碼下都解析到同一像素。

---

## ✅ Phase 9.9（M9.9）— 配樂淡出 + 轉場預設不勾 + voice_volume root-cause（已完成 0.24.0）

操作體驗 bundle，三個小改動一起發佈：

詳細 OpenSpec：`openspec/changes/archive/2026-05-04-v0.24-bgm-fade-transitions-default-volume-bug/`。

### 9.9.1 配樂尾端淡出
- `Project.bgm_fade_out_sec`（alembic 0022，FLOAT NOT NULL DEFAULT 3.0），`services.bgm_mixer.mix_bgm` 多 `fade_out_sec` kwarg：> 0 時 ffprobe 影片長度後在 BGM 鏈尾追加 `afade=t=out:st=duration-N:d=N`。Probe 失敗時 silently skip，混音照樣出。前端在「配樂」SettingsGroup 內加滑桿（0..5 秒，step 0.5），commit on mouse-up 不每幀 PATCH。
- 0 秒 = pre-0.24.0 直接切，3 秒 = 預設新行為。

### 9.9.2 轉場特效預設不勾
- 操作者反饋：每個新專案打開都要先把轉場關掉。預設改成 `False`，要的人自己勾。Style preset 走 slow / artistic / commercial 還是會自己 re-enable。
- 一次改 6 處：`EditTriggerRequest` schema、`enqueue_project_edit`、`render_draft`、`run_render`、`video_renderer.render`、`ProjectEdit.tsx`。`_draft_render_flags` legacy fallback 也從「all-True」改成 per-flag dict，舊 `Draft.render_flags_json IS NULL` 的 row 重新渲染時也會 pick up 新的 `transitions=False`。

### 9.9.3 `voice_volume=0` silent-drop 根因修正
- 症狀：使用者把 11 個片段全部拉到 voice 0%，按重新渲染，聲音還在。
- 根因：`_load_segment_volumes` 用了 `float(getattr(r, "voice_volume", 1.0) or 1.0)`，但 Python 的 `0 or 1.0` 評估為 `1.0`（0 是 falsy），所以 voice_volume=0 被默默變回 1.0，mixer 跳過 override。同樣的 idiom 也出現在 GET draft 的 serialiser，所以前端 slider 顯示 100% 但 DB 存的是 0% — 兩邊互相確認對方是對的。
- 修法：`value if value is not None else default` 取代 `value or default`。兩處都修。
- Verified: re-render draft 42 (voice=0 全片) → audio mean 從 -26.9dB 降到 -27.9dB，max 從 -12.0dB 降到 -14.2dB（聲音真的靜音了，剩下的是 BGM）。
- **Codebase rule**: 任何 nullable numeric column，valid range 含 0 / 0.0 / False 的，必須用 `value if value is not None else default`，不能用 `value or default`。

---

## ✅ Phase 9.10（M9.10）— RQ queue inspector（已完成 0.25.0）

操作者反饋：剪輯卡在「排隊中…」時，UI 唯一給的訊息就是那個字串本身，看不到 worker 在忙什麼、不知道輪到自己要等多久、也沒有辦法取消前面的任務或自己的任務。

詳細 OpenSpec：`openspec/changes/archive/2026-05-04-v0.25-queue-inspector/`。

### 9.10.1 後端：`api/routers/queue.py`
- `GET /queue/status` 走 worker 的 listen 順序（analysis → editing → bgm）回 `{running, queued[]}`。每個 item 帶 `job_id`、`queue`、`kind`（server-side 從 RQ `func_name` 對映出 analyze / translate / render / export / bgm / unknown）、`state`、`position`、`enqueued_at`、`started_at`、`elapsed_s`，加上 best-effort 的 `project_id` / `project_name` / `asset_id` / `draft_id`，前端不需要再查就能直接 render 「{專案名} 的 {kind}」。Asset-bound jobs 用 batch query 從 `Asset.project_id` backfill；draft-bound 同理走 `Draft.project_id`。
- `DELETE /queue/jobs/{job_id}` 取消還沒開跑的任務。Running 的會 409（live render 的 ffmpeg / Whisper 子程序需要 domain-specific cancel — `POST /drafts/{id}/cancel`，不是這個 generic endpoint）；找不到 404；正確取消 204。底層走 `rq.Job.cancel()`。

### 9.10.2 前端：`<QueueStatusModal>` + `<QueueStatusBadge>`
- `<QueueStatusModal>` 是完整 modal：running job 用綠色 + 軟脈動 highlight，queued 列表帶 position 數字、enqueued waiting 時間、每行一個「取消」按鈕；caller 傳 `highlightDraftId` 讓「自己的任務」上一層琥珀 outline。打開時每 3 秒 poll、每 1 秒 tick 已等時間（不重新打 API）。取消是 optimistic（先在本地 drop，再 refresh 拿真實狀態）。
- `<QueueStatusBadge>` 是 header 上的小 chip，每 5 秒 poll 一次顯示「排隊 N」。Idle/queued/running 三種狀態各自配色，running 帶呼吸脈動。點擊開同一個 modal — 不管使用者在哪個頁，都能用同一個 view 看到 worker 狀態。
- ProjectEdit 的「排隊中…」卡片新增「查看排隊」按鈕，打開 modal 時帶 `highlightDraftId={selectedDraftId}`，使用者自己排在哪一位一眼可見。

### 9.10.3 為什麼是 single-worker 的限制
worker 容器是 single-process，listen `analysis editing bgm` 三個 queue，同時只有一個 job 在跑。response 的 `running` 因此最多一個 item；`queued[].position` 也因此能跟 worker 真正的 dispatch 順序對齊（不是各 queue 獨立計數）。當未來真要 scale 到多 worker，這個 invariants 會破，schema 不變但 `position` 的語意得改成「同 queue 內的位置」，FE 顯示也會跟著調。

### 9.10.4 Orphan-Draft watchdog（0.25.1）
使用者回報：專案 #6 的 Draft 卡在「排隊中」所有步驟都「等待」，但 `/queue/status` 卻回 `{running:null, queued:[]}`。RQ job 因為 worker crash / timeout / 手動 purge 不見了，Draft 卻還掛在 `pending` — FE 永遠 poll 一個鬼。

`api/watchdog.py` 在 FastAPI lifespan 啟動一個 background asyncio task：
- 啟動時掃一次 + 每 60 秒掃一次
- 找出 `status in ('pending', 'processing')` 的 Drafts
- 每個 draft 用 `services.queue.has_draft_render_job(draft.id)` 確認 RQ job 還在
- Job 不見 + `render_retry_count < 3`：用 snapshot flags 重新 enqueue（`skip_plan = bool(cut_plan_json)`、`subtitles_from_db` 跟 `style_preset` 都從 row 讀）並 ++retry_count
- 三振：把 row 改 `failed`，`prompt_feedback = "watchdog: retries exhausted ..."`，FE 跳真實的失敗卡片

Schema：`Draft.render_retry_count INTEGER NOT NULL DEFAULT 0`（alembic 0023）。每次使用者顯式重新觸發（trigger / re-render / reorder / rebuild-subtitles）都重設成 0，避免不相關的未來失敗繼承前一次的 retry budget。

`GET /drafts/{id}` 也加了 read-time fast-fail：`retry_count >= 3` 且 RQ job 不在 → 立刻在那次讀取 commit `failed`，不用等下一次 watchdog tick。Read-time 不會嘗試恢復（避免跟 watchdog 爭），watchdog 是 resubmit 的單一所有者。

FE 在 `ProjectEdit` 偵測 `prompt_feedback` 開頭是 `watchdog:`：標題改「任務已遺失」、按鈕改「重新提交」、跳過進度條（沒任何階段跑過，bar 空一片只會誤導）。

### 9.10.5 Queue inspector mobile 版面修正（0.25.1）
之前 modal 用 `max-height: 85vh` + `padding: 1rem` 在 iPhone Safari 直拍模式下會跑出可見區（`vh` 包到 URL bar 那一塊）。改成：
- backdrop 用 `env(safe-area-inset-*)` padding，避開 notch + home indicator
- modal 改 `max-height: 100%`（已經被 backdrop padding 限制過，不需要再 vh）
- header 改 sticky，捲動長 queue 不會把關閉鈕推出去
- `@media (max-width: 480px)`：phone 上去掉 padding + border-radius，直接全螢幕，把每一個垂直像素都讓給 queue 列表

---

## ✅ Phase 9.11（M9.11）— 素材管理：刪除 + meta 行（已完成 0.26.0）

使用者上傳錯素材要能刪掉；analysis 列表也要能一眼看出每段的長度、解析度、大小，才有辦法決定哪一段該丟。

詳細 OpenSpec：`openspec/changes/archive/2026-05-04-v0.26-asset-delete-and-meta/`。

### 9.11.1 後端：`DELETE /assets/{id}` + `DELETE /projects/{id}/assets/batch`
- `services/asset_management.py` 是兩個 endpoint 共用的服務層。`delete_asset(session, id)` 流程：
  - 找 `Draft.status in (pending, processing, ready_for_review, approved)` 還在用這個 asset 的 → 列出 `Draft.version` 拋 `AssetInUseError`，endpoint 翻成 409 + 「v3, v5 還在用，請先處理」訊息。
  - 失敗 / 拒絕的 draft 直接 cascade-delete（走 ORM `session.delete`，cascade 一路砍到 DraftSegment / DraftComment / Review），讓 `DraftSegment.asset_id ondelete=RESTRICT` 不會再卡 asset 的刪除。
  - 砍硬碟（source mp4 + thumbnails dir）走 best-effort，IO 失敗 log + swallow，但**先砍硬碟再刪 row** — 萬一硬碟錯誤，row 還在，使用者可以 retry；如果順序反過來，row 沒了就再也找不到那個檔案路徑。
  - DELETE `AssetTranscript` + `ScriptCoverage`（這兩個沒掛 Asset 的 relationship cascade，得手動 explicit DELETE）。
- batch endpoint 跑 per-asset 一個個試，回傳 `{deleted_count, blocked_count, results: [{asset_id, deleted, reason}]}`，partial-failure 不會擋住其他 row。Cross-project ids 在 endpoint 層先比對 `Asset.project_id == project_id` 過濾掉，避免從 request body 跨專案刪。
- gotcha：`select(Draft).distinct()` 在 PostgreSQL 上踩 `could not identify an equality operator for type json` — `Draft` 有 JSON 欄位 (`cut_plan_json` / `progress_steps_json` / `render_flags_json`)，DISTINCT 沒法去重。改成 `select(Draft.id, Draft.version).distinct()` 拿 tuple，要砍 row 的時候再 `select(Draft).where(Draft.id.in_(ids))` 第二次撈整個 row。

### 9.11.2 後端：`AssetAnalysisItem` 新增 resolution + file_size_bytes
- `resolution` 已經存在 `Asset.resolution`（upload-time ffprobe），直接 propagate。
- `file_size_bytes` 在每次 GET 時 `Path(asset.file_path).stat().st_size`。沒在 `Asset` 上開 column 是因為檔案的生命週期是 upload + delete 路徑擁有的；存 row 上會跟硬碟 stale，講不出真話。File missing → `None` → FE 顯示 `—`。

### 9.11.3 前端：素材卡 meta 行 + 批次刪除按鈕
- `AssetCard` 標題下面一行 mono：`05:38 · 1728×3072 · 67.6 MB`，每段缺值 fallback `—` 保持 layout 不跳動。
- `formatBytes(bytes)` 自動切 B / KB / MB / GB（小數一位）。
- 既有的「批次工具列」（重新分析所選 / 強制重跑）旁邊加「刪除所選（N）」紅色按鈕。`window.confirm` 提醒不可復原；partial-failure 用同一個 `triggerError` 面板列出每一個被拒的 row 跟原因。
- 選擇狀態跟 polling 都重用既有 batch flow 的 `selectedIds` set + `polling.refresh()`，沒拉新的 state。

---

## 🔮 Phase 10（M10）— 工作流規模化（0.27.x+）

### 10.1 批次專案
- 一次拉一整批（同主題、同 BGM、不同產品 / 不同型號）
- 共用 profile + style preset，每支獨立 plan + render

### 10.2 社群直接發布
- 完成後一鍵到 Instagram Reels / TikTok / YouTube Shorts（OAuth + Graph API / Data API）
- 搭 7.3 的比例 → 不同平台用不同檔（IG 用 9:16，TikTok 用 9:16，YT Shorts 用 9:16，IG 動態用 4:5）

### 10.3 AI 自動縮圖
- 從 `_AssetScore` score top-3 的 frame 抽圖
- 加 1–2 個關鍵字（kinetic typography）
- 給 3 個版本選

---

## 跨 Phase 規範

- **版本號**：每個 Mx 結束 bump minor（0.12 → 0.13 → 0.14）；patch 內任何小修走 0.x.y+1。0.19.0 是個例外 — 跳過 0.18.x patch 直接拉到 0.19.0 因為 alembic chain 重組。
- **OpenSpec**：每個 phase 開頭寫 `openspec/changes/m{N}-{topic}/proposal.md` + `tasks.md`；完成後搬到 `archive/YYYY-MM-DD-<slug>/`。
- **記憶**：每個 phase / 大 feature 結束寫 `~/.claude/projects/D--GitClone--HomeProject-media-processor/memory/<slug>.md` 留 deploy / runtime 注意事項。
- **驗收**：每個 phase 至少手機 6 吋實機跑一次 e2e。
- **退場條件**：若某 phase 某 sub-task 試做後不符合 UX 期待，正式 abandon 並改寫 ROADMAP，不要硬上。
- **Alembic**：parallel-branch 並行開發時必須 manual re-chain `down_revision`，否則 `alembic upgrade head` 會 "Multiple head revisions"（v0.19.0 教訓）。
