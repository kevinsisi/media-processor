---
name: gemini-prompts/scene-tag
description: 用 Gemini Vision 給單一 frame 標籤，限定 11 個 tag 的 closed vocabulary，每幀回 1–4 個 tag + confidence。每段 asset 取樣多幀後在本機聚合。
type: prompt
related_code: src/media_processor/services/scene_tagging.py:50 (`_VISION_PROMPT`)
---

# Scene Tagging Prompt（scene-tag）

## 何時使用

- 每隻新上傳的 asset 跑 Vision 標籤（M4 analysis stage）
- 取樣 frame → 每 frame 一次 Vision call → 結果聚合到 `AssetSceneTags`
- 上限：每隻 asset 60 frames（`MAX_FRAMES_PER_ASSET`），自動拉長間隔

## Closed Vocabulary（11 tags）

只能回這 11 個 tag，其他全部丟棄：

```
indoor, outdoor, studio,
closeup, medium_shot, wide,
dynamic, static,
bright, dim, mixed_light
```

加新 tag → 同步更新 `SCENE_TAGS` tuple + `_VISION_PROMPT` + 觸發重跑（已標的 asset 不會回填）。

## Prompt 模板

```text
你會看到一張影片擷取的畫面。請從以下標籤集中挑選 1–4 個最貼切的場景描述，其餘忽略。
只回傳 JSON：
{ "tags": [{"name": "<tag>", "confidence": 0..1}, ...] }
允許的 tag：indoor, outdoor, studio, closeup, medium_shot, wide, dynamic, static, bright, dim, mixed_light
```

## 聚合（FrameTagging → AssetSceneTags）

- `FIRE_RATIO_THRESHOLD = 0.30`：tag 在多少比例的 frame 中出現算 fire
- `HIGH_CONFIDENCE_THRESHOLD = 0.80`：個別 frame confidence ≥ 0.80 時 fire ratio 門檻折半
- 一段 asset 最後吐出去的 tag = 通過 fire 篩選的 (tag, mean_confidence) tuple，依 confidence 降序

## 失敗模式

- 模型回非 closed vocabulary 的 tag → drop，不要硬塞回 enum
- JSON parse 失敗 → 該 frame 標籤為空，不影響其他 frame
- `SceneQuotaExhaustedError` → 該 asset 整段標 `failed:scene-quota`，後續 stage（edit_planner）以空 tags 處理

## 變更紀錄

- v1（M4）— 初版 11 tags + 30% fire / 80% confidence 雙門檻
