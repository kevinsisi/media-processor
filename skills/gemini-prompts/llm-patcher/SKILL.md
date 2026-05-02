---
name: gemini-prompts/llm-patcher
description: M5 重剪 Stage 4.5 — 使用者用自然語言描述想改什麼，模型回傳 profile 的 tag_weight_deltas + required_segments_overrides，後端套到下一輪重剪。輸出嚴格受限於兩個欄位群。
type: prompt
related_code: src/media_processor/services/llm_patcher.py:82 (`_SYSTEM_PROMPT`)
---

# LLM Patcher Prompt（llm-patcher）

## 何時使用

- 使用者在前端按「重剪」並輸入自然語言反饋（例如：「太多近景、開頭太弱、希望多用 outdoor」）
- 系統把 profile + 當前 draft segments + 反饋一起送 Gemini
- 結果是一段 ProfilePatch，不是新的 plan — patch 套上去後重新跑 cut_planner / edit_planner

## 為什麼限制輸出形狀

- profile 的可調整面只有 `tag_weights` + `required_segments`
- 模型若回 segment_overrides、新增 hero_tag、或亂改 editing_rules → 直接 reject
- 用 `responseMimeType: application/json` + system_instruction + temperature=0.2 鎖死

## Prompt 模板（system_instruction）

```text
你是影片剪輯助手。使用者會提供當前 profile 摘要、目前草稿的片段清單，以及一段自然語言反饋。
請輸出一段嚴格的 JSON（不要 Markdown code fence、不要解釋文字），形狀為：
{
  "tag_weight_deltas": { "<tag>": <float, 可正可負>, ... },
  "required_segments_overrides": {
    "opening_hero": <bool, optional>,
    "closing_hero": <bool, optional>,
    "hero_tag": <string, optional>
  }
}
規則：
- 只能調整 tag_weights 與 required_segments；不要回傳其他欄位。
- 若無調整需求，對應欄位請給空物件 {}。
- delta 通常落在 [-1.0, 1.0] 區間，避免大幅震盪。
```

## User prompt 結構（伺服器組）

```python
{
  "profile_summary": {"name", "tag_weights", "required_segments"},
  "draft_segments": [{"asset_id", "scene_tags", "duration_ms", "position", "source_kind"}, ...],
  "user_feedback": "<自然語言反饋>"
}
```

## Key Pool + Retry

- `GeminiKeyPoolConfig.api_keys` rotate on 429 / 5xx
- 全 keys 耗盡 → `LLMQuotaExhaustedError`，前端顯示「配額用盡，稍後再試」
- 單 key 4xx（除了 429）→ 直接 `LLMPatchError` 立即冒泡（典型是 prompt 超 token）

## 失敗模式

- 模型回 `tag_weight_deltas: {"some_tag": 5.0}` 超出 [-1, 1] → server 直接 clamp，不 reject
- 模型回 `editing_rules: {...}` 等不認的欄位 → 整筆 patch reject，回前端「請改寫反饋」
- 模型 fence 包 JSON → server 端不 strip，因為已用 `responseMimeType: application/json`，回 fence 一律算 schema 違規

## 變更紀錄

- v1（M5）— 初版，限定 tag_weights + required_segments 兩塊
