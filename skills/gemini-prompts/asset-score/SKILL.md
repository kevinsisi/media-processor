---
name: gemini-prompts/asset-score
description: 評估「一段」素材是否適合放進最終剪輯，回傳 score / position / best_span / source_kind / transition。M5+ 起 edit_planner 用 per-asset fanout 並行打多把鑰匙，避免一支壞 asset 拖垮整個 plan。
type: prompt
related_code: src/media_processor/services/edit_planner.py:249 (`_ASSET_SCORE_PROMPT`)
---

# Per-Asset 評分 Prompt（asset-score）

## 何時使用

- 規劃自動剪輯，每段 asset 單獨送一次 Gemini 取得「該不該用、放哪裡、轉場、一句話摘要」決策
- 想客製化評分維度時，**先複製這個檔案**，再修改副本，不要直接動 `_ASSET_SCORE_PROMPT`
- 新加欄位請同步更新 `ASSET_SCORE_SCHEMA_VERSION`（目前 `m5.asset-score.v2`）

## 為什麼是 per-asset，不是一次塞全部

- 14 隻素材一次塞一個 prompt → 1 次 Gemini ~90–180 s，又容易超 context
- per-asset 並行 → 14 個 ~12 s 並行 + key rotation，1 隻壞掉只丟 1 個 slot
- 規模放大到 30+ assets 時差距更明顯

## Prompt 模板（繁體中文）

```text
你是影片剪輯助手，正在評估「一段」素材是否適合放進最終剪輯。
你只需要看這段素材本身——其他素材會由其他助手獨立評估，最後由系統合併。

整支片要傳達的腳本：
{script_body}

這段素材：
- asset_id: {asset_id}
- 時長: {duration_s:.1f} 秒
- 場景標籤: {scene_tags}
- 運鏡: {motion}
- 逐字稿:
{transcript}
- 腳本對應: {coverage}

請評估：
 1. score (0-100)：這段對最終剪輯的相關度與品質
 2. position：這段適合放在 opening / middle / closing；若品質太低或與腳本完全無關回 skip
 3. best_span_ms：這段「最值得用」的 1.5–6 秒時間範圍 [start_ms, end_ms]，必須在 [0, {duration_ms}] 之內
 4. source_kind：scripted（照腳本講的部分）或 improv（自然發揮 / 情緒亮點）
 5. transition_to_next：這段播完後若銜接「下一段」適合的轉場效果，從 fade / dissolve / wipeleft / slideright / circlecrop 擇一
    （情緒延續用 dissolve；場景大跳用 wipeleft 或 slideright；段落收束用 fade；情緒大跳用 circlecrop）
 6. summary：用「一句話 (≤25 字繁中)」描述 best_span 內這段在講什麼（含主題與動作 / 主詞）。
    系統會用此欄位做去重，避免同樣的內容被多支素材重複塞進剪輯。請寫具體名詞，不要寫
    「介紹某事」「解釋某物」之類的空話。

嚴格輸出 JSON：
{
  "schema_version": "m5.asset-score.v2",
  "score": <0-100>,
  "position": "opening" | "middle" | "closing" | "skip",
  "best_span_ms": [<start_ms>, <end_ms>],
  "source_kind": "scripted" | "improv",
  "transition_to_next": "fade" | "dissolve" | "wipeleft" | "slideright" | "circlecrop",
  "summary": "<一句話 ≤25 字>",
  "reason": "<一句話原因>"
}
```

## 後處理規則（assembler 不靠 Gemini）

- 收到所有 `_AssetScore` 後在本機 `_assemble_plan`：
  - opening 桶取 score 最高 1 段；closing 桶取 score 最高 1 段；中間照 rhythm-aware 排序
  - rhythm-aware：dominant_motion 與前一段不同 +10；符合該位置偏好（opening 偏 pan/tilt/handheld，closing 偏 static）+15
  - **內容去重 (Phase 8.2)**：候選若與已選某段的 `span_transcript` 字元 3-gram Jaccard ≥ 0.5、或 `summary` ≥ 0.6 → 視為重複，丟掉。確保不會出現「蚊子館」連 4-5 段的退化案例。
  - **多樣性加分 (Phase 8.2)**：候選 top-3 場景 tag 若已出現於 chosen 列 → 每次重複扣 12；新 tag → 加 8。最多 3 段同一 tag。
  - **時長補齊 (Phase 8.2)**：bucket 走完若 `accumulated < target_duration_ms`，會繼續從剩餘候選池抽，直到 ≥ target 或無 dup-clean 候選。不再因為桶各取一段就早退（修正 60 s 請求只算出 30 s 的退化）。
  - `MIN_SEGMENT_DURATION_S = 1.5`、`MAX_SEGMENT_DURATION_S = 6.0`、`MIN_ASSET_COVERAGE_RATIO = 0.5`、`TARGET_IMPROV_SHARE = 0.4`、`MIN_SEGMENTS_FALLBACK = 6`
- 不要在 prompt 內談這些常數 — 模型不需要知道，避免讓他自己解全局最佳化

## 失敗模式

- 模型回 `position=skip` → 該 asset 整段丟棄
- best_span 超出 [0, duration_ms] → assembler clamp，並把 `reason` 記到 `cut.warnings`
- transition 不在 whitelist → 降回 `dissolve`（renderer 會 reject 非法值）
- JSON 解析失敗 → 該 asset 算 `quota_fails+=1`，不阻擋其他 asset

## 變更紀錄

- m5.asset-score.v1（M5）— 初版，無 transition_to_next
- m5.asset-score.v1（M6 內部欄位擴充）— 加 transition_to_next，schema_version 暫不 bump（向後相容）
- m5.asset-score.v2（M8.2 影片品質修復）— 加 `summary` 欄位，assembler 用它做去重；schema bump 起，舊 v1 缺欄位視為 summary="" 並 fall back 到 transcript-only 去重。
