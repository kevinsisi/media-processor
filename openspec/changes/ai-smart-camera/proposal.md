# AI Smart Camera（AI 智慧運鏡）— Gemini Vision 重點區域 + 動態縮放/平移 + 節拍同步

ROADMAP: M9.15 / target version 0.30.0
Status: 🚧 planned（**proposal-only**，尚未實作）
預設：**關閉**，使用者於專案設定頁勾選「AI 智慧運鏡（實驗性）」才生效。

## Why

從 v0.14.1（M8.1 emotion + zoompan）到 v0.23（pixel-precise point tracking），
我們把「自動剪輯如何運鏡」推到了 subject-following 等級。但**靜態 cut**（沒
有追蹤目標、emotion 也是 neutral 的素材）目前還是平鋪直敘 — 渲染管線就只是
「切一段 → 套 aspect crop → 接下一段」。觀眾視線沒有被引導，敘事節奏沒被強化。

實機剪了三批 IG / FB Reels 後最常聽到的兩個 feedback：
1. 「人臉太小看不到細節，能不能 zoom in？」
2. 「全景太久，能不能 pan 一下動起來？」

這兩個都是運鏡語言（cinematography），不是情緒分析能解的問題。Gemini Vision
在 plan generation 階段已經幫每個素材看過 frames 一次（asset-score / scene-tag），
重新利用同一份輸入再多打一隻 prompt 拿「視覺重點區域」幾乎零額外人力成本，
也沒有 Whisper / YOLO 那種 GPU bottleneck。

但**這個 feature 預設必須關閉**，原因有兩個：

- **Gemini quota**：plan generation 每打一次 smart-camera prompt 就多燒 N 個
  tokens（N ≈ 4–6 frames + prompt × cut 數）。小白工作流通常一支片 5–8 cuts，
  5 支 = 25–40 cuts/天，沒 opt-in 的話會默默吃掉 free tier。
- **鏡頭穩定性**：操作員可能不喜歡 zoom / pan 結果（推錯重點、跟既有 vidstab
  打架）。M8.1 emotion zoompan 上線時就遇過「我要靜態鏡頭結果你給我推近」的
  客訴 — 這次直接做成 opt-in。

## What changes

### 1. 新 service：`services/smart_camera_planner.py`

- 對每個 `CutPlan` 內的 segment 額外發一隻 Gemini Vision call。
- 輸入：`best_span_ms` 範圍內均勻取樣的 4–6 張 PIL frame（沿用既有的
  `services/asset_management.extract_frame` helper；不重新解碼整支素材）。
- 輸出：每張 frame 的視覺重點 bbox 列表，shape：

  ```python
  focus_regions: list[FocusRegion]
  # FocusRegion = {t_norm: float, x_norm: float, y_norm: float,
  #                w_norm: float, h_norm: float, salience: float}
  ```

- Prompt 抽成 skill：`skills/gemini-prompts/smart-camera-focus/SKILL.md`
  作為 canonical reference（仿 asset-score / scene-tag 模式）。
- LLM key pool 共用既有的 `LLM_API_KEYS` / `LLM_MODEL`；retry + per-item
  timeout 沿用 `skills/integration-robustness/SKILL.md` 的標準。
- 任何 cut 的 smart-camera call 失敗 → 該 segment 的
  `smart_camera_json = None`，**不**阻塞整個 plan。

### 2. CutPlan schema 擴充

- `CutPlanSegment.smart_camera_json: dict | None`：序列化 focus_regions
  + 推導出來的 directive（`kind` / `from_rect` / `to_rect` / `ease`）。
- `_serialise_plan` / `_deserialise_plan` 雙向加新欄位；舊 plan 反序列化
  時 default 為 `None`，向後相容。
- 不需要 Draft 層 alembic — 既有 `cut_plan_json` 是 free-form JSON。

### 3. Project 層 toggle + alembic 0027

- `Project.smart_camera_enabled: bool`，default `False`，nullable=False。
- alembic 0027：`add_column` 走 batch mode 給 SQLite test backend；Postgres
  直接 add 即可，nullable=False + server_default `'0'` 保證舊 row 不爆。
- `ProjectDetail.smart_camera_enabled` 透過 `_project_detail` 一起回傳。
- `PATCH /projects/{id}/smart-camera` body `{enabled: bool}`，沿用既有
  `subject_class` / `crop_region` 那種 partial-patch 風格。

### 4. Render flag 持久化（沿用 v0.21.1 模式）

- `EditTriggerRequest.smart_camera: bool | None` — `None` ≡ 沿用 project
  toggle；明確 `True/False` 覆寫一次。
- 觸發時 snapshot 進 `Draft.render_flags_json["smart_camera"]`，skip-plan
  re-render 走 `_draft_render_flags(draft, override)` 解析（priority body >
  snapshot > project toggle > false）。
- 操作員中途取消勾選後再次 re-render 看不到運鏡效果 — render flag 為主。

### 5. 動態縮放決策（in `smart_camera_planner._derive_directive`）

把 focus_regions 平均面積 + count 套規則：

- `mean_area < 0.25` 且 only one disjoint cluster → `zoom_in`（1.0×→1.4×，
  目標 = focus 中心）
- `mean_area > 0.60` 且 only one cluster → `zoom_out`（1.3×→1.0×，起點 =
  focus 中心）
- `≥ 2 disjoint clusters` → `pan`（from_rect = 第 1 個 cluster bbox，
  to_rect = 最後 1 個 cluster bbox，ease=linear）
- 都不符合 → `directive = None`（segment 不運鏡）

cluster disjoint 判定：bbox IoU < 0.10 視為不同 cluster。
directive 內的 `from_rect` / `to_rect` 都是 `(x_norm, y_norm, w_norm,
h_norm)`，渲染端再轉 ffmpeg expression。

### 6. 節拍同步（in `_derive_directive`）

讀 `CutPlanSegment.dominant_motion`（M6.2 已存）：

- `energetic` → `ease="exp"`（推得急）
- `calm` / `neutral` → `ease="linear"`

zoom curve 起終點對齊 cut 入出點各 50 ms 內縮（避開 xfade overlap，仿
v0.14.1 的 `TRANSITION_OVERLAP_MS` 邏輯）。

### 7. Renderer 套用（in `services/video_renderer._cut_segment`）

新 `_smart_camera_filter(directive, segment_duration_s)`：

- `zoom_in` / `zoom_out` → 用 `crop=W:H:x:y` + 時間驅動的 expr（仿 v0.16
  sendcmd 但這次是純 expr，不需要 sendcmd file）。
- `pan` → sendcmd file 串 dynamic crop（**直接 reuse**
  `auto_reframe.write_sendcmd_file` 的 dual-axis directive — v0.23.5 的
  sendcmd-bug 修正延伸到這裡）。

互斥規則（**load-bearing**）：

- **vidstab 開** → smart camera filter 跳過 + log warning
  「vidstab 與 AI 智慧運鏡互斥；本 cut 維持穩定不運鏡」。理由：vidstab 已經
  改 in_w/in_h，疊 crop 會炸（v0.23.4 的根因紀錄裡寫過）。
- **auto-reframe 已啟用**（asset 有 `tracked_object_index ≠ -3` 或 point
  tracking）→ smart camera filter 跳過 + log info。tracked subject 路徑優先。
- **emotion zoompan 已套用**（M8.1，cut 的 dominant_emotion ∈ {happy,
  surprised}）→ smart camera filter override emotion zoompan + log info。
  理由：smart camera 拿到的是真正的視覺 saliency，比情緒推測準。
- filter 失敗（ffmpeg expr 解析錯誤等）→ catch + log error + 渲染原 cut 不
  運鏡，**不**讓單一 cut 把整個 render fail。

### 8. 前端 toggle + 進階說明

- `ProjectEdit.tsx` 進階剪輯區新增一個 checkbox「AI 智慧運鏡（實驗性）」。
- hover tip：「啟用後在每次重新產生時會多打一次 Gemini 規劃鏡頭運動。
  可能蓋過情緒縮放；與穩定畫面、跟住主角同時開啟時會自動退讓。」
- `EditSettingsBlock` 把 `smart_camera` flag 串進 `EditTriggerRequest`。
- `web/src/api/types.ts`：`ProjectDetail.smart_camera_enabled`
  + `EditTriggerRequest.smart_camera`。
- `client.ts`：`patchProjectSmartCamera(id, {enabled})`。

## Non-goals

- **per-cut smart-camera override**：v0.30.0 是 project-level toggle + 自動推
  導 directive。操作員不能在時間軸上手動指定「這個 cut 要 pan、那個要 zoom
  out」。如果實機 feedback 強烈想要才上 v0.30.x。
- **AI 推導之外的手動運鏡 directive**：時間軸 UI 上不加運鏡編輯器。
- **重新跑 scene-tag / asset-score**：smart-camera prompt 是**新的**第三隻
  Gemini，不重打前兩隻。
- **針對沒有 plan 的素材直接運鏡**：smart camera 走 plan generation 階段；
  skip-plan re-render（v0.14 timeline edit）不會新增 directive，只會套用 plan
  裡已經算好的。
- **與 transitions 的整合**：v0.30.0 不動 xfade chain；運鏡 directive 在
  `_cut_segment` 內生效，concat / xfade 邏輯不變。
- **第 4 種策略**：先把 zoom_in / zoom_out / pan 三種跑穩。tilt / dolly-zoom
  之類等實機 feedback 出現再加。

## Migration / back-compat

- 舊 plan（`cut_plan_json` 沒有 `smart_camera_json` 欄位）→ deserialise 時
  default `None` → renderer 走原路徑，無變化。
- 舊 Draft（`render_flags_json` 沒有 `smart_camera` key）→ flag resolver
  default `False` → renderer 不套運鏡，無變化。
- alembic 0027 是 `add_column nullable=False + server_default='0'`；既有 row
  寫成 false，無破壞性。
- 舊 frontend 沒帶 `smart_camera_enabled` POST → 新 backend default false
  入庫，安全。新 frontend 對舊 backend → `ProjectDetail.smart_camera_enabled`
  是 undefined，UI 把 checkbox render 成 unchecked，操作員勾起來會 PATCH 到
  舊 endpoint（404）→ FE 拿到 404 顯示「此版本後端不支援 AI 智慧運鏡」toast。
  可接受 — 我們 BE/FE 永遠一起 deploy。
- **退場**：若實機跑下來 smart camera 經常推錯重點 / 跟 vidstab 打架太多，
  整個 phase 可從 ROADMAP 退場（仿 M8.1 退場條件）。code-side：留 toggle
  default false、service 留著，但從 ProjectEdit UI 拔掉 checkbox + 文件改
  「實驗性 / 已退場」。不需要破壞性 migration。

## Out-of-scope rule sources to honour

- `skills/plan-before-build/SKILL.md`：本 proposal 必須先過使用者確認再進實作。
- `skills/integration-robustness/SKILL.md`：Gemini call 必須 retry + per-item
  timeout，部分失敗不阻塞整個 plan。
- `skills/key-pool-standard/SKILL.md`：smart-camera prompt 共用既有 LLM 池，
  不開新池。
- `memory/v023_point_tracking.md`：sendcmd directive 必須 x/y 合併成一條，
  不要重蹈 v0.23.5 覆轍。
- `memory/v024_bgm_fade_transitions_volume_bug.md`：nullable bool flag 解析
  時禁用 `value or default`，要寫 `value if value is not None else default`。
