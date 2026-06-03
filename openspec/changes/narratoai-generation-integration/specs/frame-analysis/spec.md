# Capability: frame-analysis

從素材視頻抽取關鍵幀並以 Vision LLM 批次描述，輸出結構化幀分析 JSON，供後續解說文案生成使用。

## Behaviour

1. **觸發**：`POST /projects/{id}/assets/{asset_id}/frame-analysis`（或 `documentary` edit_mode 的 orchestrator 自動觸發）
2. **幀抽取**：用 ffmpeg `select=not(mod(n\,fps*interval))` 按 `frame_interval_seconds`（預設 3.0）間隔抽 JPEG；解析度縮小至最長邊 960px。
3. **快取**：快取鍵 = `sha256(asset.file_path + asset.mtime + interval)[:16]`，存 `{MEDIA_STORAGE_DIR}/frame_cache/{key}/`；若快取命中直接跳抽幀。
4. **批次分析**：每批 `vision_batch_size`（預設 10）幀，一次 Vision LLM 呼叫；並發上限 `GEMINI_CONCURRENCY`。Prompt 移植自 NarratoAI `DocumentaryFrameAnalysisService.PROMPT_TEMPLATE`（繁體中文版）。
5. **輸出**：`asset.frame_analysis_json`（JSON column）存以下結構：
   ```json
   {
     "interval_seconds": 3.0,
     "batches": [
       {
         "batch_index": 0,
         "time_range": "00:00:00,000-00:00:30,000",
         "frame_observations": [
           {"timestamp": "00:00:00,000", "observation": "..."}
         ],
         "overall_activity_summary": "..."
       }
     ]
   }
   ```
6. **狀態欄位**：`asset.frame_analysis_status` ∈ `{not_started, pending, running, done, failed}`；失敗時寫 `asset.frame_analysis_error`。
7. **幂等**：若 `frame_analysis_status == done` 且快取仍有效，API 回傳 200 + 現有結果，不重新分析；傳 `force=true` 強制重跑。

## Constraints

- Vision LLM 單批次 timeout：90 s（較 smart_camera_planner 的 60 s 寬鬆，因幀數更多）
- 單個 asset 最多 500 幀（超過時按間隔自動跳過早期幀）
- worker-analysis 佇列執行（同 Gemini Vision 呼叫）
- 失敗後 orchestrator 不停止 draft；退化為無幀分析的 standard 路徑

## API

```
POST /projects/{id}/assets/{asset_id}/frame-analysis
  Body: { "interval_seconds": 3.0, "force": false }
  Response: { "status": "pending", "asset_id": "..." }

GET /projects/{id}/assets/{asset_id}/frame-analysis
  Response: { "status": "done", "batch_count": 12, "frame_count": 120 }
```
