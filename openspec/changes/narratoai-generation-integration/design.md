## Context

media-processor 目前的 pipeline：上傳素材 → Whisper 轉錄 → Gemini 場景標籤 → edit_planner 選片 → ffmpeg cut/concat → subtitle burn → MusicGen BGM mix。整個流程是「選片 + 剪輯」，沒有任何生成人聲或從畫面自動生成文案的能力。

NarratoAI 的核心是另一條路：從素材視頻或字幕出發，由 AI 生成解說稿，再合成人聲。它的技術棧是 moviepy + edge-tts；本整合把這些能力移植為 ffmpeg-native 的 worker 任務，與現有 RQ 架構對齊。

## Goals / Non-Goals

**Goals:**
- 移植 NarratoAI 的幀分析 pipeline（抽幀 → Vision LLM batch → 幀描述 JSON）
- 移植 narration script generation（幀描述 → Text LLM → 帶時間戳解說文案）
- 移植 SDP（SRT 字幕 → LLM 爆點識別 → 混剪腳本）
- 擴充既有 Story/Narrato TTS artifact 合成服務（edge-tts provider，輸出 durable narration artifact + measured duration）
- 新增 narration render 路徑（TTS 音訊軌 + ffmpeg amix）
- 新增 `documentary` / `drama_explain` edit_mode
- 所有新 worker 任務接入現有 RQ 佇列架構

**Non-Goals:**
- 不移植 moviepy（維持 ffmpeg-only 渲染）
- 不整合 IndexTTS2 / 語音克隆（edge-tts 免費 Neural TTS 已足夠 P0）
- 不整合 MusicGen 以外的 BGM 來源
- 不修改現有三種 edit_mode（standard / luxury_auto / viral_short）
- 不提供 WebUI 給 NarratoAI 的設定頁（使用 media-processor 現有設定 UI）

## Decisions

### D1：幀抽取用 ffmpeg，非 OpenCV

NarratoAI 用 `VideoProcessor`（OpenCV）抽幀。media-processor 的 worker-analysis 已依賴 ffmpeg，且 ffmpeg seek-then-decode 已在 smart_camera_planner 中驗證穩定。統一用 ffmpeg `select` filter 按秒間隔抽 JPEG，避免在 worker 鏡像中重複安裝兩套解碼器。

快取鍵：`sha256(video_path + mtime + interval)[:16]`，存在 `{MEDIA_STORAGE_DIR}/frame_cache/{key}/` 下。

### D2：Vision LLM 呼叫用現有 OpenCode provider / Gemini key pool fallback

NarratoAI 有自己的 `UnifiedLLMService`。media-processor 已有 `opencode_client.py`（OpenCode 主力）和 Gemini key pool fallback。新服務沿用此 fan-out 模式，不引入第三套 LLM 抽象層。凡是需要 OpenAI-compatible / provider-routed AI 的地方，優先透過 Settings 頁設定的 OpenCode provider/model/variant；Gemini API keys 只作 legacy fallback。

每批次（預設 10 幀）一次 Vision LLM 呼叫；批次間並發上限 = `GEMINI_CONCURRENCY`（現有常數）。

### D3：TTS 引擎選 edge-tts，作為 pip 依賴加入 worker runtime

`edge-tts` 是純 Python 非同步包，無需 GPU。TTS 由 StoryScript-backed render flow 在 editing worker 中觸發，並重用既有 `story_narration_assets` artifact 表、provider abstraction、duration probing 與 subtitle-only fallback。

TTS 輸出：`{STORY_NARRATION_DIR}/{project_id}/{story_script_id}/item_{order}_{hash}.m4a`。

### D4：解說文案時間戳對齊策略

NarratoAI 的解說文案條目帶有 `timestamp` 欄位（對應素材中的時間點）。在 `drama_explain` 模式下此欄位來自 SRT；在 `documentary` 模式下此欄位由 Text LLM 從幀分析推斷。兩種模式都使用相同的 `NarrationCue` dataclass（text / start_ms / end_ms / asset_id），由 narration_render 統一消費。

### D5：只新增幀分析 DB 欄位，旁白沿用 StoryScript artifacts

| 欄位 | 表 | 型別 | 預設 | 說明 |
|---|---|---|---|---|
| `frame_analysis_json` | assets | json nullable | NULL | 幀分析結果 JSON（每個 asset 獨立） |
| `frame_analysis_status` | assets | str | `not_started` | not_started / pending / running / done / failed |
| `frame_analysis_error` | assets | text nullable | NULL | 幀分析失敗原因 |

Project/Draft narration audio path columns were deliberately deferred. Runtime TTS configuration uses Settings UI / DB-backed `story_tts_provider`, `story_tts_voice`, `story_tts_model`, and `story_tts_timeout_s` with env fallback; generated narration files are tracked in `story_narration_assets` and referenced from CutPlan segments during render.

### D6：Orchestrator 整合

`edit_orchestrator.py` 加入新分支：

```
documentary   → frame_analysis_service → narration_script_generator → story_tts artifacts → render (narration path)
drama_explain → transcript StoryScript  → narration_script_generator → story_tts artifacts → render (narration path)
standard/luxury_auto/viral_short → 現有路徑不變
```

幀分析結果快取在 `asset.frame_analysis_json`，同一素材不重複分析。

## Risks / Trade-offs

- **edge-tts 依賴微軟 Azure 網路** → 本地無法測試時 fallback 為靜音（skip TTS，輸出無聲解說版）；`integration-robustness` skill 要求加重試 + 超時。
- **Vision LLM 批次成本高** → 每次幀分析可能消耗大量 token；加 `frame_analysis_status` 快取，同一素材只分析一次。
- **NarratoAI 的解說文案是簡體中文** → 移植時保留 OpenCC 可選轉繁體，但不強制。
- **narration 音訊與 BGM 混音複雜度** → 聲道優先順序：人聲解說 > 原聲（可選）> BGM；沿用 `bgm_mixer.py` 的 ffmpeg filter_complex 模式，新增 narration 聲道。
- **`drama_explain` 需要現有 Whisper 字幕** → 若 asset 無轉錄結果，pipeline 退化為 `standard` 模式並 log warning。

## Migration Plan

1. 新增 alembic migration（0035_asset_frame_analysis）
2. 新增 pip 依賴 `edge-tts` 至 `pyproject.toml`（worker-editing docker 重建）
3. 新 modes 仍由 `edit_mode` 顯式選擇；TTS 仍由 `story_narration` render flag 與 Settings UI / `story_tts_provider` runtime setting 控制，預設可安全字幕-only fallback
4. 生產部署：更新 API/worker/web image，最後跑 alembic upgrade
