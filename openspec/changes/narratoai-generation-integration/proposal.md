## Why

media-processor 目前的 pipeline 是「創作驅動」——使用者需要先提供腳本，系統才能選片、剪輯、渲染。NarratoAI 的核心能力是「內容驅動」——從視頻本身的畫面或字幕出發，由 AI 自動生成解說稿、合成人聲、再混入影片。兩者整合後，media-processor 可以做到端到端的 AI 解說影片生成，不再要求使用者預先寫稿。

## What Changes

- **新增 `frame-analysis` 服務**：對素材視頻逐幀抽樣，批次呼叫 Vision LLM 生成每幀描述，輸出結構化的幀分析 JSON。（移植自 NarratoAI `DocumentaryFrameAnalysisService`）
- **新增 `narration-script-generation` 服務**：將幀分析 JSON 轉換為 Markdown 摘要，再由 Text LLM 生成帶時間戳的解說文案條目。（移植自 NarratoAI `generate_narration_script.py`）
- **新增 `tts-synthesis` 服務**：以 edge-tts 為主引擎，將解說文案逐條合成 MP3 音訊，附帶詞級時間戳，用於字幕對齊。支援 zh-TW / zh-CN / en-US 等多語種聲音。（移植自 NarratoAI `voice.py`）
- **新增 `drama-script-parsing` 服務**：讀取現有 SRT 字幕，由 LLM 分析劇情爆點並產出混剪腳本（時間戳 + 片段說明）。（移植自 NarratoAI SDP pipeline）
- **新增 `narration-render` 渲染路徑**：在現有 ffmpeg cut → concat → subtitle burn 流程後，加入 TTS 音訊軌混音步驟，產出帶人聲解說的最終影片。
- **新增 `edit_mode` 值**：
  - `documentary` — 幀分析 → 解說稿生成 → TTS → 解說渲染
  - `drama_explain` — SRT 字幕解析 → SDP 剪輯腳本 → TTS → 解說渲染
- **新增 Project / Draft 欄位**：`narration_voice`（TTS 聲音名稱）、`narration_speed`（語速 0.5–2.0）、`frame_interval_seconds`（抽幀間隔）

## Capabilities

### New Capabilities

- `frame-analysis`: 從素材視頻抽取關鍵幀、批次 Vision LLM 描述、快取幀分析 JSON
- `narration-script-generation`: 幀分析 → Text LLM → 帶時間戳解說文案 JSON
- `tts-synthesis`: 解說文案 → edge-tts MP3 + 詞級時間戳
- `drama-script-parsing`: SRT 字幕 → LLM 爆點識別 → 混剪腳本 JSON
- `narration-render`: TTS 音訊軌 + 現有剪輯輸出 → ffmpeg 混音 → 最終影片

### Modified Capabilities

- `edit-mode`: 新增 `documentary` 和 `drama_explain` 兩個 enum 值

## Impact

- **新 Python 依賴**：`edge-tts`（TTS 引擎）、`opencv-python`（幀抽取，worker-analysis 已有）
- **新 alembic migration**：Project 新增 `narration_voice` / `narration_speed` / `frame_interval_seconds`；Draft 新增 `narration_audio_path`
- **新 worker 工作量**：幀分析（Vision LLM fan-out，worker-analysis 佇列）+ TTS 合成（CPU，worker-editing 佇列）
- **新 API 端點**：`POST /projects/{id}/frame-analysis`（觸發幀分析）、`POST /drafts/{id}/synthesize-narration`（觸發 TTS）
- **現有 `bgm_mixer.py`**：需新增 narration 音訊軌混音路徑（voice-over duck BGM）
- **現有 `edit_orchestrator.py`**：新增 `documentary` / `drama_explain` 分支
- **不影響**：現有 `standard` / `luxury_auto` / `viral_short` 模式完全不變
