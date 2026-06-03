# Capability: narration-script-generation

將幀分析 JSON 轉換為 Markdown 摘要後，由 Text LLM 生成帶時間戳的解說文案，輸出 `NarrationCue` 列表。

## Behaviour

1. **輸入**：`asset.frame_analysis_json`（必須 `frame_analysis_status == done`）+ `project.script` 作為主題/創作方向 brief（可為空）。
2. **Markdown 轉換**：將 `batches[].overall_activity_summary` 與 `frame_observations` 串接為純文字摘要，移植自 NarratoAI `parse_frame_analysis_to_markdown`。
3. **Text LLM 呼叫**：Prompt 描述解說風格（依 `edit_mode` 選擇風格詞），輸出 JSON 陣列：
   ```json
   {
     "items": [
       {
         "timestamp": "00:00:03,000",
         "narration": "解說文案文字",
         "duration_hint_ms": 3500
       }
     ]
   }
   ```
4. **容錯解析**：移植 NarratoAI `_repair_narration_payload`：支援 markdown fence 包裹、單引號 key、trailing comma、prose-wrapped JSON 等 LLM 輸出亂象。
5. **輸出映射**：每個 item 映射為 `NarrationCue(text, start_ms, end_ms, asset_id)`；`start_ms` 從 `timestamp` 解析，`end_ms = start_ms + duration_hint_ms`（LLM 未給則預估 = 字數 × 200ms）。
6. **儲存**：`draft.narration_cues_json`（新欄位，JSON）儲存 `NarrationCue` 列表；每次 TTS 觸發前可重新生成。

## Constraints

- Text LLM 呼叫走 `opencode_client`（primary）+ `GeminiClient`（fallback）
- Timeout：120 s（解說稿生成比單次標籤慢）
- 輸出 item 數不得超過 `ceil(video_duration_s / frame_interval_seconds) × 1.5`；超出則截斷並 log warning
- 若 LLM 失敗或解析失敗，`draft` 不 fail；orchestrator 跳過 TTS 步驟，輸出無聲解說版
- 繁體/簡體：依 `project.subtitle_language`（現有欄位）決定；預設 `zh-TW`

## Worker

worker-analysis 佇列（因需呼叫 LLM）
