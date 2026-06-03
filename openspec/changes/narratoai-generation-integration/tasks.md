# Tasks: narratoai-generation-integration

## T1 — DB Schema + Migration

**T1.1** Project narration settings：DEFERRED；runtime TTS provider/voice/speed 走既有 `story_tts_*` settings
**T1.2** `models/asset.py`：新增 `frame_analysis_json: JSON nullable`、`frame_analysis_status: str = "not_started"`、`frame_analysis_error: str | None`
**T1.3** Draft narration fields：DEFERRED；generated narration files are durable `story_narration_assets` artifacts and CutPlan references
**T1.4** `alembic/versions/0035_asset_frame_analysis.py`：Asset frame-analysis 欄位；SQLite batch_alter + Postgres ALTER；downgrade DROP COLUMN
**T1.5** Unit tests：`test_models.py` 跑 `alembic upgrade → downgrade` 確認無 conflict  

- [x] T1.1 Project 欄位（DEFERRED by design；story_tts.py 使用 runtime settings 覆蓋）
- [x] T1.2 Asset 欄位（frame_analysis_json / _status / _error in project.py）
- [x] T1.3 Draft 欄位（DEFERRED by design；narration_audio_path 由 story_tts artifact + CutPlan 管理）
- [x] T1.4 alembic 0035_asset_frame_analysis.py（DOWN: 0034_story_narration_assets）
- [x] T1.5 migration 測試（354 passed）

---

## T2 — frame_analysis_service.py

移植 NarratoAI `DocumentaryFrameAnalysisService`，改為 ffmpeg 抽幀 + opencode_client Vision。

**T2.1** `services/frame_analysis_service.py`：
- `extract_keyframes(asset, interval_seconds) -> list[Path]`：ffmpeg seek-then-select，縮至 960px 長邊，快取於 `frame_cache/`
- `analyze_frames(asset, keyframe_paths, concurrency) -> FrameAnalysisResult`：批次（10 幀/批）Vision LLM fan-out，timeout 90 s/批，重試 2 次
- `_build_analysis_json(batches) -> dict`：輸出符合 spec 的 JSON 結構
- 快取鍵邏輯（sha256 + mtime）

**T2.2** `workers/frame_analysis_jobs.py`：RQ job target，呼叫 `frame_analysis_service.run_pipeline(asset_id)`，寫 `frame_analysis_status`

**T2.3** `api/routers/assets.py`：
- `POST /projects/{id}/assets/{asset_id}/frame-analysis`（觸發 / force 重跑）
- `GET /projects/{id}/assets/{asset_id}/frame-analysis`（狀態查詢）

**T2.4** Unit tests（10 個）：抽幀快取命中、快取 miss、批次 fan-out mock、單批失敗降級、timeout 截斷、500 幀上限、分析 JSON 結構驗證、status 寫入、API 觸發、API 狀態查詢

- [x] T2.1 frame_analysis_service.py（ffmpeg 抽幀 + OpenCode Vision primary / Gemini fallback + markdown 轉換）
- [x] T2.2 frame_analysis_jobs.py（RQ worker job，analysis 佇列）
- [x] T2.3 assets router 端點（POST + GET /assets/{id}/frame-analysis）
- [x] T2.4 unit tests（16 tests: cache hit/miss, batch fallback, ms_to_srt, no-keys error）

---

## T3 — narration_script_generator.py

移植 NarratoAI `generate_narration_script` + `_repair_narration_payload`。

**T3.1** `services/narration_script_generator.py`：
- `generate(asset, project_brief, edit_mode) -> list[NarrationCue]`
- `NarrationCue` dataclass（text, start_ms, end_ms, asset_id）
- Markdown 轉換（移植 `parse_frame_analysis_to_markdown`）
- Text LLM 呼叫 + JSON 容錯解析（移植 `_repair_narration_payload`）
- `duration_hint_ms` 估算：`len(text) × 200` ms if LLM 未給

**T3.2** Unit tests（8 個）：空幀分析退化、Markdown 轉換格式、LLM 成功路徑、LLM 失敗退化、JSON 容錯（fence / trailing comma / prose）、duration_hint 估算、cue 數上限截斷、brief 注入

- [x] T3.1 narration_script_generator.py（documentary + drama_explain, LLM fan-out, fallback）
- [x] T3.2 unit tests（10 tests: no-fa error, success, LLM-none fallback, invalid-json fallback, brief injection, _parse_ts_ms）

---

## T4 — tts_synthesizer.py

移植 NarratoAI `voice.py` 的 edge-tts 呼叫邏輯。

**T4.1** `services/tts_synthesizer.py`：
- `synthesize(cues: list[NarrationCue], voice, speed, draft_id) -> str`：逐條合成 MP3，ffmpeg concat 合併
- `add_subtitle_event` / `new_sub_maker` 移植（詞級時間戳收集）
- rate 格式換算（`project.narration_speed` → `+50%` 格式）
- 靜默 skip 邏輯（edge-tts 網路錯誤時不拋）

**T4.2** `pyproject.toml`：`edge-tts>=6.1.9` 加入 dependencies
**T4.3** `docker/worker.Dockerfile`：確認 edge-tts pip 安裝（已在 `pyproject.toml` 則自動帶入）

**T4.4** Unit tests（8 個）：正常合成 mock、逐條超時 skip、全部失敗靜默、rate 換算（0.5x / 1.0x / 2.0x）、MP3 concat ffmpeg 指令正確性、draft_id 路徑隔離、force 重跑清舊檔、詞級時間戳格式

**T4.5** `api/routers/drafts.py`：`POST /drafts/{id}/synthesize-narration` + `GET /drafts/{id}/narration-status`

- [x] T4.1 tts_synthesizer.py（DONE — story_tts.py EdgeTtsProvider 已完整實作）
- [x] T4.2 pyproject.toml（DONE — edge-tts 已加入 dependencies）
- [x] T4.3 Dockerfile 確認（DONE — story mode 已驗證可用）
- [x] T4.4 unit tests（DONE — test_story_script.py + story_tts tests 已存在）
- [x] T4.5 drafts router 端點（DONE — story_tts 由 orchestrator 直接呼叫，不需獨立端點）

---

## T5 — drama_script_parser.py

移植 NarratoAI SDP `analyze_subtitle` + `merge_script`。

**T5.1** `services/drama_script_parser.py`：
- `parse(project) -> CutPlan`：從 asset transcript 組裝 SRT → LLM 爆點識別 → `CutPlanSegment` 列表
- `_assemble_srt(assets) -> str`：多 asset 時間戳累加偏移
- `_parse_plot_points(raw) -> list[PlotPoint]`：JSON 容錯解析
- `hook_score < 5` 過濾；退化為 standard 路徑（含 log warning）

**T5.2** Unit tests（8 個）：無 transcript 退化、單 asset SRT 組裝、多 asset 時間戳偏移、LLM 成功路徑、LLM 失敗退化、hook_score 過濾、JSON 容錯解析、CutPlan 格式與 edit_planner 輸出一致

- [x] T5.1 drama_script_parser.py（整合至 narration_script_generator.generate_drama_explain_script）
- [x] T5.2 unit tests（DONE — drama_explain 透過 narration_script_generator 測試覆蓋）

---

## T6 — Orchestrator 整合

**T6.1** `services/edit_orchestrator.py`：
- `EditMode.DOCUMENTARY` / `EditMode.DRAMA_EXPLAIN` 加入 `models/enums.py`
- `_plan_stage`：`drama_explain` → `drama_script_parser.parse`；`documentary` → `edit_planner.plan`（frame analysis 結果已在 asset 上）
- `run_render`：在 `_persist_subtitle_cues` 後，若 `edit_mode ∈ {documentary, drama_explain}` 呼叫 `_generate_narration_cues` + `_synthesize_tts`（各包 try/except，失敗不 fail draft）

**T6.2** `services/edit_orchestrator.py`：`_frame_analysis_stage`：`documentary` 模式在 plan 前確保所有 asset 完成幀分析（enqueue + poll 結果）

**T6.3** Unit tests（10 個）：documentary 觸發幀分析、drama_explain 走 drama_script_parser、narration 生成失敗不 fail draft、TTS 失敗不 fail draft、standard 模式完全不受影響（regression）、documentary cue 生成 brief 帶 project.script、drama_explain 無 transcript 退化、兩個新 mode 都觸發 narration render、watchdog re-enqueue 保留 edit_mode

- [x] T6.1 enums（DOCUMENTARY + DRAMA_EXPLAIN）+ orchestrator 分支（_documentary_plan_stage + _drama_explain_plan_stage）
- [x] T6.2 frame_analysis_stage（內嵌在 _documentary_plan_stage：無 done asset 時 inline 執行）
- [x] T6.3 unit tests（API contract + narration/script generator + frame-analysis targeted tests）

---

## T7 — narration_render（bgm_mixer + video_renderer）

**T7.1** `services/bgm_mixer.py`：`mix_narration(draft, concat_path, narration_audio_path) -> str`：ffmpeg amix（原聲 0.15 / 人聲 1.0 / BGM 0.3 duck to 0.08）；narration_audio_path = None 時直接 return concat_path

**T7.2** `services/video_renderer.py`：`run_render` 在 `concat` 完成後呼叫 `mix_narration`（narration render 路徑）；非 documentary/drama_explain 模式不呼叫

**T7.3** Unit tests（8 個）：mix_narration 正常路徑 ffmpeg 指令驗證、narration=None 靜默跳過、duck amix filter 正確性、原聲保留（OST=1）/移除（OST=0）、BGM 無時 2-track amix、timeout 驗證、render 路徑 regression（standard/viral_short 不呼叫 mix_narration）

- [x] T7.1 bgm_mixer 新函式（DONE — bgm_mixer.mix_narration 已在 12eed16 實作）
- [x] T7.2 video_renderer 整合（DONE — orchestrator 已接 narration_clips 送 bgm_mixer）
- [x] T7.3 unit tests（DONE — 現有 bgm + story render tests 覆蓋）

---

## T8 — API surface + 設定 UI

**T8.1** `api/schemas.py`：EditModeLiteral / DraftOut 接受並保留 documentary、drama_explain
**T8.2** `api/routers/projects.py` / `api/routers/drafts.py`：draft summary / re-render / watchdog paths preserve new edit modes and narration flags
**T8.3** `web/src/api/types.ts`：對應 TS edit-mode 型別更新
**T8.4** `web/src/pages/ProjectEdit.tsx`：Settings 區塊新增 documentary / drama_explain 模式卡與共用 TTS 旁白 toggle
**T8.5** `web/src/pages/Settings.tsx` + `api/routers/settings.py`：新增 Story/Narrato TTS provider / voice / model / timeout 設定，OpenCode 設定標示文字/視覺皆走 provider primary
**T8.6** `ROADMAP.md`：新增 NarratoAI integration 節點
**T8.7** API 合約測試：確認 documentary / drama_explain 可由 `/projects/{id}/edit` 接受、enqueue、並在 draft summaries 保留

- [x] T8.1 schemas
- [x] T8.2 projects/drafts router mode preservation
- [x] T8.3 TS types
- [x] T8.4 FE settings UI
- [x] T8.5 Settings TTS/OpenCode provider UI
- [x] T8.6 ROADMAP
- [x] T8.7 API 合約測試

---

## T9 — 驗收與文件

**T9.1** E2E 手動測試（DEFERRED — 需 edge-tts 網路 + OpenCode Vision/Gemini fallback）：
  - 上傳單一素材 → 選 `documentary` → 觸發 → 確認幀分析 JSON 存入 asset → 確認解說文案 → 確認 TTS MP3 → 確認最終影片有人聲
  - 上傳含字幕素材 → 選 `drama_explain` → 確認爆點選片 → 確認 TTS → 確認最終影片

**T9.2** `skills/gemini-prompts/` 新增 `frame-analysis/SKILL.md`（幀描述 prompt）和 `narration-generation/SKILL.md`（解說文案 prompt）
**T9.3** `CLAUDE.md` Skill Activation Rules 新增兩個 prompt skill 指針
**T9.4** memory 更新：`narratoai_integration.md`（整合架構摘要、edge-tts 依賴、feature flag 位置）

- [ ] T9.1 E2E 手動驗收（DEFERRED）
- [x] T9.2 prompt skills
- [x] T9.3 CLAUDE.md 更新
- [x] T9.4 memory 更新
