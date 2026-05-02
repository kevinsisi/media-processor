# Media Processor — ROADMAP（Phase 6–9）

> **單一定位**：個人 / 小團隊用的「拍完就上傳，AI 直接給可發佈影片」的工具。
> 目標 UX：手機優先、繁體中文、高級感、最少手動編輯。
> 目前版本：**0.16.1**（M9.1 — YOLO tracking + auto-reframe，含 reorder/render hardening）

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
| **M9.1** | **YOLO 物件追蹤 + auto-reframe 動態裁切** | ✅ done | **0.16.0 – 0.16.1** |
| M9.2 | 多專案批次 + 社群直接發布 | 🔮 future | 0.17.x+ |

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

## 🚧 Phase 7（M7）— Manual control（規劃中，0.13.x）

主題：**自動剪輯 70% 已 OK，再給使用者最後一哩的精修能力**。

定位：**手機優先、繁體中文、高級感**。所有 UI 在 6 吋螢幕上單手可操作。

### 7.1 時間軸拖拉排序

**前端**：
- 在 `ProjectDetail` 頁加一個橫向時間軸 component（縮圖 + 秒數 + 小型 motion icon）
- 手指拖拉可調整 cut 順序；放手後 0.5s debounce 觸發重排請求
- 使用 `@dnd-kit/core`（已驗證可用 + 手勢自然）；不要用任何 jQuery 系列

**後端**：
- 新 endpoint：`PATCH /projects/{id}/drafts/{draft_id}/order`，body: `{"cut_ids": [...]}`
- 寫入 `Draft.cut_plan` 後 enqueue render（force=true，跳過 plan 階段）
- 不重跑 Gemini — 重排只動 ffmpeg 階段

**驗收**：
- 拖完 < 90s 出新 mp4（單純重 mux + xfade，無 Gemini）
- 順序持久化（refresh 後保留）

### 7.2 字幕 inline 編輯器

**前端**：
- 字幕區塊每段一行：時間範圍（可點擊進入編輯）+ 文字（可長按編輯）
- 編輯狀態：tap 即進；blur / Enter 自動儲存（`PATCH /projects/{id}/drafts/{draft_id}/subtitles/{cue_id}`）
- 即時 preview 字數 / 行數（超 2 行紅字提示但不阻擋）

**後端**：
- 新 table `subtitle_cue`（draft_id, idx, start_ms, end_ms, text, edited_at）
- 字幕 stage 完跑後寫進 `subtitle_cue`，不再只保 SRT 檔
- render 時從 `subtitle_cue` 重生成 SRT，再走原本 drawtext burn-in path
- `PATCH` 後 enqueue render（force=true，跳過 STT）

**驗收**：
- 改一句話 → < 60s 出新 mp4（純字幕重燒）
- 多次改 / 撤銷不損壞時間軸

### 7.3 匯出格式 + 解析度

**前端**：
- 「匯出」按鈕展開 sheet：
  - 比例：9:16（直）/ 4:5（IG 動態）/ 1:1（方形）
  - 解析度：720p / 1080p / 1440p（依專案原始素材最大值動態 cap）
- 選完後 enqueue 一個 export job，獨立的檔案輸出（不覆蓋預設 16:9 mp4）

**後端**：
- 新 endpoint：`POST /projects/{id}/drafts/{draft_id}/export`，body: `{"aspect": "9:16", "height": 1080}`
- 新檔名：`v{N}-{aspect}-{height}p.mp4`
- ffmpeg：`scale + crop + pad`（依比例動態組）
- 對應 `EditStep.EXPORT` 加進 orchestrator（最後 stage、optional）

**驗收**：
- 9:16 直拍素材 → 9:16 輸出無黑邊
- 16:9 橫拍 → 1:1 中央裁切（不要硬縮）
- 解析度 cap 邏輯：原片只有 720p 不能輸出 1080p

### 7.x 後續可選

- 長按某段 cut → 顯示 alternative takes（從 `_AssetScore` 沒被選上的 best_span 候補）
- 整支影片預覽進度條（不只是 chip 狀態）

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

### 8.1.5 前端
- API 新 `EmotionTagsOut`（dominant + per-class ranges），`AssetAnalysisItem.emotion_tags` 在沒跑情緒時為 null。
- `i18n/tags.ts` 加 `EMOTION_TAG_LABELS / EMOTION_TAG_ICONS`（emoji + 繁中標籤）。
- `ProjectAnalysis.tsx` 在運鏡時間軸下方多一顆色票化情緒 chip（happy 暖黃 / surprised 紫 / serious 灰 / neutral 中性）。

---

## 🔜 Phase 8.2（M8.2）— BGM library + 動態元素（0.15.x）

### 8.2.1 內建 BGM library
- `bgm_library` table：name, mood (warm / energetic / calm / cinematic / pop), tempo, file_path
- 上傳合輯後可 tag 給每首；專案上 BGM 時可選「自動依片段情緒選曲」
- 自動選曲：用 STT transcript + scene tags + emotion 餵 Gemini → 回 mood key → 從 library 抽

### 8.2.2 動態元素（kinetic typography）
- 重點字幕高亮（粗體放大 + 短彈跳動畫，5 frames 內結束）
- AI 自己挑「值得放大的字」（Gemini 看 transcript 給 1–3 個關鍵字 per cue）
- 渲染走 ffmpeg 的 `drawtext` + `expr` enable 視窗

### 8.2.3 Hero shot 自動放慢
- 若某段 score > 90 且 dominant_motion=static + closeup → 自動 0.7x 慢速
- 配 BGM 重拍對齊（簡單啟發式：若 BGM tempo 已知，hero 進場對齊 beat）

---

## 🔮 Phase 9（M9）— 工作流規模化（0.15.x+）

### 9.1 批次專案
- 一次拉一整批（同主題、同 BGM、不同產品 / 不同型號）
- 共用 profile，每支獨立 plan

### 9.2 社群直接發布
- 完成後一鍵到 Instagram Reels / TikTok / YouTube Shorts（OAuth + Graph API / Data API）
- 搭 7.3 的比例 → 不同平台用不同檔（IG 用 9:16，TikTok 用 9:16，YT Shorts 用 9:16，IG 動態用 4:5）

### 9.3 AI 自動縮圖
- 從 `_AssetScore` score top-3 的 frame 抽圖
- 加 1–2 個關鍵字（同 8.2 的 kinetic typography 同源）
- 給 3 個版本選

---

## 跨 Phase 規範

- **版本號**：每個 Mx 結束 bump minor（0.12 → 0.13 → 0.14）；patch 內任何小修走 0.x.y+1
- **OpenSpec**：每個 phase 開頭寫 `openspec/changes/m{N}-{topic}/proposal.md` + `tasks.md`
- **記憶**：每個 phase 結束寫 `~/.claude/projects/D--GitClone--HomeProject-media-processor/memory/m{N}_*.md` 留 deploy / runtime 注意事項
- **驗收**：每個 phase 至少手機 6 吋實機跑一次 e2e
- **退場條件**：若某 phase 某 sub-task 試做後不符合 UX 期待，正式 abandon 並改寫 ROADMAP，不要硬上
