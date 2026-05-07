---
name: gemini-prompts/smart-camera-focus
description: v0.30.0 — 用 Gemini Vision 對 cut span 內均勻取樣的 4 張 frame 找視覺重點區域 (focus_regions)，本機推導 zoom_in / zoom_out / pan / None directive。opt-in；預設關閉。
type: prompt
related_code: src/media_processor/services/smart_camera_planner.py:175 (`_VISION_PROMPT`)
---

# Smart Camera Focus Prompt（smart-camera-focus）

## 何時使用

- **僅當** `Project.smart_camera_enabled = True` AND 對應的 render flag 也為 true 時觸發
- plan generation 之後、render 之前的「smart camera」stage 內，每個 `CutPlanSegment` 各打一次
- 一個 cut 一次 Vision call（不是一個 frame 一次）：把 cut span 內均勻取樣的 4 張 JPEG 一次塞進同一個 multimodal request 給 Gemini Vision

## 為什麼要 opt-in

- **Gemini quota 成本**：plan generation 每打一次 smart-camera prompt 就多燒 N 個 tokens（4–6 frames + prompt × cut 數）
- **鏡頭穩定性 / 操作員偏好**：M8.1 emotion zoompan 上線時遇過「我要靜態鏡頭結果你給我推近」客訴，這次直接做 opt-in
- 預設 `False`，操作員在 ProjectEdit 進階區勾「AI 智慧運鏡（實驗性）」才生效

## 互斥規則（load-bearing）

- **vidstab on** → smart camera filter 跳過 + warning。理由：vidstab 改 `in_w`/`in_h`，疊 crop 會炸（v0.23.4 根因記錄）
- **auto-reframe on**（asset 有 tracking / point / custom_roi 且 `tracked_object_index ≠ -3`） → smart camera filter 跳過 + info。tracked subject 路徑優先
- **emotion zoompan**（M8.1：cut 的 `dominant_emotion ∈ {happy, surprised}` 且 motion-OR-face）+ smart camera 同開 → smart camera 勝。理由：focus_regions 是真正的視覺 saliency，比情緒推測準

## Prompt 模板

```text
你會看到一段影片中均勻取樣的數張畫面（依時間先後）。請對「每一張」畫面回傳該畫面中視覺上最重要的 1–3 個重點區域（人臉、動作主體、產品、文字等）。
座標以 0..1 之間的正規化值表示（左上角 0,0；右下角 1,1）。每個 bbox 須為矩形 (x, y, w, h)，其中 (x, y) 為左上角，(w, h) 為寬高，皆在 [0, 1] 之內。
請務必嚴格輸出 JSON：
{ "frames": [
   { "index": 0, "regions": [ {"x":0..1,"y":0..1,"w":0..1,"h":0..1,"salience":0..1}, ... ] },
   ...
] }
若該畫面沒有可辨識的重點，仍須輸出空 regions："regions": []。不要回傳框以外的文字、不要回傳描述、不要 markdown fence。
```

## 推導規則（_derive_directive）

把 frames 的 focus_regions 平鋪、greedy 單連結 cluster（`bbox IoU >= 0.10` 視為同一群）：

| 條件                                                  | 結果                                |
| ----------------------------------------------------- | ----------------------------------- |
| ≥ 2 個非重疊 cluster                                  | `pan` (第 1 → 最後一個依時間排序)   |
| 單一 cluster + `mean_area < 0.25`                     | `zoom_in` (1.0× → 1.4×)             |
| 單一 cluster + `mean_area > 0.60`                     | `zoom_out` (1.3× → 1.0×)            |
| 其他（mean_area 落在 0.25..0.60 中段）                | `directive = None`（不運鏡）        |

`ease`：`dominant_motion ∈ DYNAMIC_MOTIONS (pan/tilt/handheld)` → `exp`；其他 → `linear`。

`from_rect` / `to_rect` 都是 `(x_norm, y_norm, w_norm, h_norm)`，渲染端再轉 ffmpeg `crop=W:H:x:y` expression。

## 失敗模式（partial-success）

- Gemini 全部 key 429 / 5xx → `SmartCameraQuotaError` → 該 cut `smart_camera_json = None`，**不**阻塞整個 plan
- JSON parse 失敗 → `SmartCameraInvalidError` → 該 cut `smart_camera_json = None`
- 推導出 `directive = None` → 該 cut 跳過運鏡，stage 仍視為 success
- 渲染時單一 cut 的 smart camera filter 失敗 → catch + 退回原 cut（不讓單一 cut 整個 render fail）

## 變更紀錄

- v1（v0.30.0）— 初版 zoom_in / zoom_out / pan 三策略 + 4 frame per cut sampling + 互斥規則 vidstab > auto-reframe > smart camera > emotion zoompan
