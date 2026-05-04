## Why

YOLO 物件追蹤 (v0.16) 對「我要追那個 logo / 那顆輪框 / 車牌中央那個字」這種子-像素需求精度不夠：

- 取樣只有 5 Hz，主角快速移動時 crop 會明顯卡頓。
- bbox 中心常落在物件邊上，不在使用者真正想盯的那個點上。
- 自訂 ROI（v0.17 CSRT）解了第二點，但要求使用者畫一個矩形，對手機觸控不友善，而且 CSRT 在低紋理 / 高動態場景容易跑飛。

操作者實際的心理模型其實就是 After Effects 的 point tracker：「我在某一格上點一下這個像素，後面每一格自動跟著它走」。M9.8 把這個工作流帶進來。

整個 0.23.x 包含 6 次 release：

- **0.23.0 — pipeline**：Lucas-Kanade 像素追蹤 + auto_reframe 串接 + tracked_object_index = -4 sentinel。
- **0.23.1 — modal**：手機上桌面那個小縮圖根本沒法精準點，全螢幕 PointPickerModal + pinch / wheel zoom + pan。同時把 opencv-python-headless 加進 api 容器（之前只在 worker），因為 `tracking-target` 端點要 sync 跑 LK。
- **0.23.2 — modal commit math**：使用者點車標中央，crosshair 卻偏左，rendered 影片也只看到右半邊。發現 modal 用 `imgRef.getBoundingClientRect()` 在 transformed image 上取 rect 不可靠（layout edge case + CSS `transition: transform` 動畫競態），改用從 state 算的 `visibleImageRect(stage, naturalWH, zoom, pan)`，並拿掉 transition。
- **0.23.3 — crosshair display math**：crosshair 顯示用 `left: norm_x * 100%` 對 canvas div 定位，但 canvas 含 `object-fit:contain` 黑邊。改成 `norm_x * renderRect.renderedW + renderRect.offsetX`（跟 bbox `cssBoxFor` 一樣）。
- **0.23.4 — auto-reframe + vidstab 衝突**：crosshair 顯示對了之後，rendered 影片仍偏右；車標應該在中央卻跑到左邊。發現是 vidstab 跑在動態裁切之後 — 動態裁切已經把 LK 像素鎖在中央，但 vidstab 看到背景在動，當作 camera shake 套了個 translate 把（剛剛被裁中央的）主角推回邊緣。修法是 per-segment skip：套了動態裁切的 segment 就不再二次 vidstab。
- **0.23.5 — sendcmd duplicate-timestamp dispatcher quirk**：vidstab 衝突修了之後，長運鏡片段的成片仍漂移：開頭主角在中央，後半段慢慢偏到左 1/3。拆 chain 一路驗到 cut 階段就已經漂了；同一個 sendcmd 改成 sparse（1 / 3 / 10 / 15 Hz）都正常，只有 30 Hz 失敗。再進一步發現 30 Hz 但只寫 x（不寫 y）也正常 — 真正的問題是「同一個 start_time 兩個 directive」這個高頻 dispatch pattern。`auto_reframe.write_sendcmd_file` 之前每個 frame 寫兩行（x 跟 y 共用 timestamp），總體 60 Hz dispatch，ffmpeg 4.4 的 sendcmd dispatcher 把 second-and-onward directive 默默吃掉，crop window 凍結在 initial 值。修法：把 x 跟 y 合進同一行 directive，用 `,` 分隔（`0.0000 crop@reframe x N, crop@reframe y M;`）。

## What Changes

### 1. 像素追蹤管線（0.23.0）

#### Schema
- `Asset.point_tracking_json: Mapped[Any] | None`（JSON column）
  - 形狀：`{src_w, src_h, fps, init_t_ms, init: {x, y}, frames: [{t_ms, x, y, lost}], sampled_frames}`
  - x / y 是 SOURCE 像素座標（不是 norm，不是 thumbnail 像素）。
- `Asset.point_tracking_origin: Mapped[Any] | None` — verbatim 使用者點擊：`{x, y, frame_ms, norm_x, norm_y}`，前端用來畫 crosshair 而不需要再呼一次 API。
- alembic 0021_asset_point_tracking。
- `tracked_object_index = -4` 是新的 sentinel（既有：null=auto / ≥0=YOLO track / -1=custom_roi / -2=fixed / -3=off）。

#### Service
- `services/point_tracking.track_point(media_path, init_x, init_y, init_t_ms, duration_ms)` — pyramidal Lucas-Kanade（`cv2.calcOpticalFlowPyrLK`）：
  - `LK_WIN_SIZE = (21, 21)`、`LK_MAX_LEVEL = 3`、`LK_MAX_ERR = 50.0`。
  - 雙向 pass：從 init frame 往前到結尾、再從 init frame 往後到 0；`backward.reverse() + forward` 拼起來的 frames 自然按 t_ms 升冪。
  - LK fail（`status == 0` 或 `err > LK_MAX_ERR`）時凍結在 last good 並標 `lost: True`，Kalman 後面拿到的還是連續測量。
  - `TRACKING_FAKE=1` 跳過 cv2，回 deterministic stub。

#### Auto-reframe 整合
- `auto_reframe.compute_crop_path_from_point_track(point_track, *, target_aspect, asset_start_ms, asset_end_ms)`：
  - 把每個 LK frame 包成 `{x: int(x-0.5), y: int(y-0.5), w: 1, h: 1}` 的 1×1 bbox。
  - 包成 wrapped tracking dict 餵給既有的 `compute_crop_path`，centre-of-bbox math + Kalman + max-delta clamp 全部不變。
- `services/video_renderer._cut_segment` dispatch：`point_track → custom_roi → YOLO`，與 `tracked_object_index` sentinel 順序對應。
- `services/edit_orchestrator` 新增 `point_track_by_asset` dict 並丟給 renderer，與既有的 `tracking_by_asset / custom_roi_by_asset` 平行。

#### API
- `PATCH /assets/{id}/tracking-target` 增 `mode: "point"`，body `{point: {norm_x, norm_y, frame_ms}}`。
- norm_x / norm_y 必須在 [0, 1]；後端用 `Asset.resolution` 乘出像素座標再丟 LK，呼叫端不需要知道原始解析度。
- 同步呼叫（`asyncio.to_thread`），10 秒內可解；不丟 RQ queue。
- 因此 opencv-python-headless 必須出現在 BOTH api 和 worker 容器（之前只在 worker）— api Dockerfile 加 install line。

#### 前端
- `components/AssetTrackingTarget.tsx` 新增 `point` mode 在 picker 上；按下打開 PointPickerModal。
- 渲染 crosshair on the post-commit thumbnail：使用 `point_tracking_origin.norm_x / norm_y`。

### 2. PointPickerModal — 全螢幕點選（0.23.1）

桌面那個小縮圖加 overlay 在手機完全不能用（縮圖 320 px 寬、車標在裡面只有 15 px）。改成：

- `components/PointPickerModal.tsx`：fixed-position fullscreen modal，grid `auto 1fr auto`（header / stage / footer）。
- Stage 的 `<img>` 沒有指定寬高，靠 `max-width: 100%; max-height: 100%; object-fit: contain` 自然 fit。
- Pinch zoom（兩指距離比例）+ wheel zoom（focal-point-anchored，圍繞滑鼠位置 zoom）+ drag pan，centre-anchored CSS `transform: translate(panX, panY) scale(zoom)`。
- Drag-vs-click discrimination：`DRAG_THRESHOLD_PX = 4`；`pointermove` 累計位移超過閾值才算 drag。
- Backdrop click / Esc / cancel button 不 commit；只有 single click 才 emit norm 座標。
- API 改為非同步：`onConfirm: (norm) => Promise<void>`，modal 顯示 spinner 直到 promise resolve / reject。

### 3. Modal commit math（0.23.2）

#### 症狀
使用者全螢幕 zoom 進去點車標中央，但 crosshair 顯示偏左、rendered 也只剩右半。

#### 根因
1. `imgRef.current.getBoundingClientRect()` 對 transform 過的 `<img>` 不可靠：`max-width: 100%; max-height: 100%; object-fit: contain` 在內容自然尺寸 < container 時根本不 fill container，跟 wheel/pinch 的 transform-origin 假設兜不起來。
2. CSS `transition: transform 80ms ease-out`：點擊落在 wheel-zoom 動畫中途時 `getBoundingClientRect()` 拿到 partway-through rect，不是最終 rect。

#### 修法
- 拿掉 `transition: transform`（保留 `will-change: transform` 給 compositor）。
- 新增 `fittedImageSize(natW, natH, containerW, containerH)` — 模擬瀏覽器對 contain 的行為（natural ≤ container 時不縮，否則保 aspect 縮）。
- 新增 `visibleImageRect(stage, natW, natH, zoom, pan)` — 從 state 直接算螢幕上的 image rect：`stage_centre - (base * zoom) / 2 + pan`。
- `onPointerUp` 改用 `visibleImageRect()` 算 click rect，不再呼 `getBoundingClientRect`。useCallback deps 加 `zoom, pan`。

### 4. Crosshair display math（0.23.3）

#### 症狀
0.23.2 修完 modal commit 之後，crosshair on the thumbnail 仍偏：使用者點中央，crosshair 卻往左偏。

#### 根因
`AssetTrackingTarget.tsx` 用 `left: norm_x * 100%` 對 canvas `<div>` 定位，但 canvas 包了 `object-fit:contain` 的 `<img>`，含 letterbox 黑邊。norm_x 是相對影片內容的；`% 100%` 是相對 canvas 總寬（含黑邊）。bbox overlay 早就用 `renderRect`（`renderedW/H + offsetX/Y`）正確 map，crosshair 是漏網之魚。

#### 修法
- crosshair `style.left = norm_x * renderRect.renderedW + renderRect.offsetX` (px)；`top` 同理。
- guard 條件加 `&& renderRect`，避免第一次 render 時 canvas 還沒量到尺寸就嘗試 divide。

### 5. vidstab + 動態裁切衝突（0.23.4）

#### 症狀
0.23.3 之後 crosshair 顯示對了，但 rendered mp4 仍偏：車標應該在中央，rendered 卻在左 1/3。draft.render_flags_json 有 `"stabilize": true`。

#### 診斷
- 拿 LK frames 跑 `compute_crop_path_from_point_track`：crop_x 一路追到 LK 像素 ✓。
- 手動跑 ffmpeg cut（dynamic crop chain，不過 vidstab）：badge 中央 ✓。
- 完整 pipeline 跑出來：badge 偏左。
- 對 cut 階段的 seg_NNNN.mp4 單獨跑 vidstab：偏移開始出現。

#### 根因
動態裁切 sendcmd 在每個 output frame 把 LK 像素鎖在 crop 中央 — 主角不動、背景跟著鏡頭走。vidstab 看到背景在動，演算當作 camera shake 套個 translate 抵銷掉，剛被 crop 拉到中央的主角又被推回邊緣。架構上是兩個 stabilizer 在打架：dynamic crop 把主角當錨點、vidstab 把背景當錨點。

#### 修法
- `_cut_segment` 改回傳 `bool`：`True` 代表這段套了 dynamic `crop@reframe` chain，`False` 代表用 static aspect crop。
- `cut_segments` 改回傳 `(list[Path], list[bool])`。
- `stabilize_segments` 新增 `skip_indexes: set[int] | None` kwarg：set 內的 index 直接拿 cut 階段輸出當下一階段輸入，不跑 vidstab。
- `render()` 把 `{i for i, r in enumerate(reframed_flags) if r}` 餵給 `stabilize_segments`。
- 靜態裁切的 segment（沒 tracking、沒 custom_roi、沒 point_track，或 `tracked_object_index in (-2, -3)`）仍走完整 vidstab。

### 6. Memory + docs

- `memory/v023_point_tracking.md` 新增三條 gotchas：opencv 必須也在 api image、modal commit 用 manual rect 而不是 `getBoundingClientRect`、overlay 用 renderRect 而不是 `% 100%`。0.23.4 加上：dynamic crop + vidstab 不能同時套。
- `MEMORY.md` index 更新。
- `ROADMAP.md`：M9.8 條目 + 0.23.x sub-task。
- `CLAUDE.md`：openspec/archive 列表 + render pipeline + asset model 條目補上。

## Impact

- **Schema**：alembic 0021 新增 `point_tracking_json` + `point_tracking_origin` 兩欄，兩者皆 nullable，legacy assets 行為不變。
- **API contract**：`PATCH /assets/{id}/tracking-target` mode 列舉多一個 `"point"`；body 多一個 optional `point` field。`tracked_object_index = -4` 是新 sentinel。
- **Renderer signature**：`_cut_segment` 從 `None` 改回傳 `bool`、`cut_segments` 從 `list[Path]` 改回傳 `(list[Path], list[bool])`、`stabilize_segments` 多 `skip_indexes` kwarg。內部呼叫鏈全部 updated；外部唯一呼叫 `cut_segments` 的地方是 `tests/unit/test_video_renderer.py`，已 update。
- **Container image**：api Dockerfile 增 opencv-python-headless install — 鏡像體積 + 約 70 MB。
- **Backwards compat**：`point_tracking_json IS NULL` 走原有 dispatch 順序（YOLO / custom_roi）；render flag `stabilize=True` 對沒設 tracking-target 的 segment 行為不變。
