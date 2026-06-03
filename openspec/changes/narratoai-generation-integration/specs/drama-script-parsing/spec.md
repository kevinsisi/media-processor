# Capability: drama-script-parsing

從現有 Whisper 轉錄字幕（SRT 格式）出發，由 LLM 識別劇情爆點並產出混剪腳本，替代 `edit_planner` 的選片邏輯。用於 `drama_explain` edit_mode。

## Behaviour

1. **輸入**：`project` 所有 assets 的 `transcript`（Whisper 輸出）+ `project.script`（可為空，作為主題 brief）+ `custom_clips`（目標片段數，預設 5）。
2. **字幕組裝**：從各 asset 的 `transcript` 取 Whisper SRT 格式（現有 `subtitles.py` 已有此輸出）；多 asset 時按上傳順序串接，時間戳累加偏移。
3. **LLM 分析**：移植 NarratoAI `analyze_subtitle`（`step1_subtitle_analyzer_openai.py`）；Prompt 要求輸出 JSON：
   ```json
   {
     "plot_summary": "劇情梗概",
     "plot_points": [
       {
         "timestamp": "00:01:23,000",
         "picture": "片段描述",
         "hook_score": 8,
         "duration_ms": 4000
       }
     ]
   }
   ```
4. **腳本映射**：`plot_points` 中的 `timestamp` 對應 asset 中的絕對時間，轉換為 `CutPlanSegment(asset_id, start_ms, end_ms)`；與 `edit_planner` 的 `CutPlanSegment` 格式一致，讓後續 render 路徑不變。
5. **退化**：若 asset 無 transcript（尚未轉錄）→ 退化為 `standard` 模式並在 draft 上寫 `prompt_feedback="字幕未就緒，已退化為一般剪輯"`。

## Constraints

- LLM 呼叫：`opencode_client`（primary）+ `GeminiClient`（fallback）
- Timeout：90 s
- `hook_score < 5` 的 plot_point 預設過濾（可由 `custom_clips` 寬鬆）
- worker-analysis 佇列
- 容錯解析：移植 NarratoAI `_repair_narration_payload` 的 JSON 修復邏輯

## Integration Point

`edit_orchestrator._plan_stage`：當 `edit_mode == "drama_explain"` 時，呼叫 `drama_script_parser.parse(project)` 取代 `edit_planner.plan(project)`；結果為相同的 `CutPlan`，後續 render 路徑完全不變。
