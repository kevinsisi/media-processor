# Media Processor — ROADMAP（Phase 6–10）

> **單一定位**：個人 / 小團隊用的「拍完就上傳，AI 直接給可發佈影片」的工具。
> 目標 UX：手機優先、繁體中文、高級感、最少手動編輯。
> 目前版本：**0.21.4**（M9.6 — 轉場 flag 持久化 + 配樂自動觸發 + 主角類別 auto-trim）

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
| **M9.6** | **轉場 flag 持久化 + 配樂自動觸發 + 主角類別 auto-trim** | ✅ done | **0.21.0 – 0.21.4** |
| M10 | 多專案批次 + 社群直接發布 + AI 自動縮圖 | 🔮 future | 0.22.x+ |

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

## 🔮 Phase 10（M10）— 工作流規模化（0.22.x+）

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
