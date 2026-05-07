# Tasks — AI Smart Camera（M9.15 / v0.30.0）

## Pre-flight

- [ ] T0 — 與 user 確認 `proposal.md` 後再進實作（plan-before-build 標準）
- [ ] T0.1 — Brainstorm 一次（`.claude/skills/superpowers/brainstorming/SKILL.md`）：
  在動 service code 之前先用 4–5 張代表性 cut 手動跑 Gemini Vision，確認
  focus_regions 推得出來的東西對得上人類期待，再決定要不要繼續
- [ ] T0.2 — 開 worktree（`.claude/skills/superpowers/using-git-worktrees/SKILL.md`）

## Backend — schema + plan

- [ ] T1 — `models/project.py`：`smart_camera_enabled: bool` default `False`
- [ ] T2 — alembic 0027：`add_column` 走 batch mode（SQLite test 後端）+ direct
  （Postgres prod）；nullable=False + server_default `'0'`
- [ ] T3 — `services/cut_plan.py`（或 plan dataclass 所在處）：
  `CutPlanSegment.smart_camera_json: dict | None` + `_serialise_plan` /
  `_deserialise_plan` 雙向加新欄位（舊 plan 反序列化 default `None`）
- [ ] T4 — `services/smart_camera_planner.py`：
  - [ ] `FocusRegion` / `Directive` dataclass
  - [ ] `analyse_focus_regions(asset, span_ms)` Gemini call
    （key pool + retry + per-item timeout）
  - [ ] `_derive_directive(focus_regions, segment_duration_s, dominant_motion)`
    推導 zoom_in / zoom_out / pan / None
  - [ ] disjoint cluster 判定（IoU < 0.10）
  - [ ] ease 選擇（energetic→exp / 其他→linear）
  - [ ] 入出點各內縮 50 ms 對齊 xfade overlap
- [ ] T5 — `skills/gemini-prompts/smart-camera-focus/SKILL.md`：prompt canonical
  reference + `CLAUDE.md` 的 Skill Activation Rules 加入

## Backend — orchestration + render

- [ ] T6 — `services/edit_orchestrator.py`：
  - [ ] smart-camera stage（在 plan generation 之後、render 之前）
  - [ ] 讀 `Project.smart_camera_enabled` + `Draft.render_flags_json["smart_camera"]`
  - [ ] 對每個 segment 跑 `smart_camera_planner.analyse_focus_regions`，
    寫回 `cut_plan_json`
  - [ ] 任何 cut fail → 該 segment `smart_camera_json=None`，stage 整體仍
    視為 success（partial-success 語意，仿 BGM stage）
- [ ] T7 — `services/video_renderer.py`：
  - [ ] `_smart_camera_filter(directive, duration_s)` → ffmpeg filter / sendcmd 字串
  - [ ] `_cut_segment` 整合：vidstab / auto-reframe / emotion-zoompan 的
    互斥邏輯 + warning / info log
  - [ ] filter 失敗 → catch + 退回原 cut（不讓單一 cut 整個 render fail）
- [ ] T8 — render flag 持久化沿用 v0.21.1：
  - [ ] `EditTriggerRequest.smart_camera: bool | None`
  - [ ] `_draft_render_flags(draft, override)` 加 `smart_camera` key：
    priority body > snapshot > project toggle > false
  - [ ] re-render endpoints（`reorder` / `rebuild-subtitles`）的
    `RenderFlagsOverride` 加新 key
  - [ ] 解析時用 `value if value is not None else default`，**不**用
    `value or default`（v0.24.0 voice_volume=0 silent-drop 教訓）

## Backend — API

- [ ] T9 — `api/routers/projects.py`：
  - [ ] `_project_detail` 投影 `smart_camera_enabled`
  - [ ] `PATCH /projects/{id}/smart-camera` body `{enabled: bool}`
- [ ] T10 — `api/schemas.py`：
  - [ ] `ProjectDetail.smart_camera_enabled: bool`
  - [ ] `SmartCameraPatch` body schema
  - [ ] `EditTriggerRequest.smart_camera: bool | None`

## Frontend

- [ ] T11 — `web/src/api/types.ts`：`ProjectDetail.smart_camera_enabled`
  + `EditTriggerRequest.smart_camera`
- [ ] T12 — `web/src/api/client.ts`：`patchProjectSmartCamera(id, {enabled})`
- [ ] T13 — `ProjectEdit.tsx` 進階剪輯區：
  - [ ] checkbox「AI 智慧運鏡（實驗性）」
  - [ ] hover tip：「啟用後重新產生時會多打一次 Gemini 規劃鏡頭運動。
    可能蓋過情緒縮放；與穩定畫面、跟住主角同時開啟時會自動退讓。」
  - [ ] 切換時 PATCH `/projects/{id}/smart-camera`
- [ ] T14 — `EditSettingsBlock`（兩處 mount 點）把 `smart_camera` flag 串進
  `EditTriggerRequest`（同 transitions / vidstab 模式）

## Tests

- [ ] T15 — `tests/unit/test_smart_camera_planner.py`：
  - [ ] focus_regions → directive 三種 case（zoom_in / zoom_out / pan）
  - [ ] mixed coverage → directive=None
  - [ ] energetic motion → ease=exp
  - [ ] Gemini fail → directive=None，不阻塞
- [ ] T16 — `tests/unit/test_video_renderer.py`：
  - [ ] smart camera filter 對 zoom_in / zoom_out / pan 各產出對的 ffmpeg expr
  - [ ] vidstab on → smart camera 跳過
  - [ ] auto-reframe on → smart camera 跳過
  - [ ] emotion zoompan + smart camera → smart camera 勝
  - [ ] filter fail → 退回原 cut（assert render 不 raise）
- [ ] T17 — `tests/unit/test_routers.py`：
  - [ ] `PATCH /projects/{id}/smart-camera` round-trip
  - [ ] `EditTriggerRequest.smart_camera` 進 `Draft.render_flags_json`
  - [ ] re-render 時 priority body > snapshot > project toggle 解析正確
  - [ ] `smart_camera=False` 帶 body 不會被 `value or default` 吃掉
- [ ] T18 — `tests/unit/test_edit_orchestrator.py`：
  - [ ] smart_camera_enabled=False → 不打 Gemini（mock 計數）
  - [ ] enabled=True → 每個 segment 各打一次
  - [ ] 部分 segment Gemini fail → stage success，failed segment
    smart_camera_json=None

## Verification

- [ ] T19 — Lint / typecheck / tests
  - [ ] `ruff check src tests` → All checks passed
  - [ ] `ruff format src tests --check` → no diff
  - [ ] `tsc -b --noEmit` → exit 0
  - [ ] `pytest tests/unit` → 全綠
- [ ] T20 — 實機 e2e（手機 6 吋）
  - [ ] 預設關閉：新建專案 + 舊專案載入時 checkbox unchecked、render 不運鏡
  - [ ] 勾選後重新產生 → plan generation 多打 Gemini（從 worker log 看 token
    usage）
  - [ ] 三種策略樣片：zoom_in / zoom_out / pan 各拍一支跑完
  - [ ] vidstab + smart camera 同開 → vidstab 勝、log 有 warning
  - [ ] auto-reframe（YOLO tracking） + smart camera 同開 → tracking 勝、
    log 有 info
  - [ ] emotion happy + smart camera → smart camera directive 勝
  - [ ] 取消勾選後 skip-plan re-render → 鏡頭回穩定、不運鏡

## Closing

- [ ] T21 — 更新 memory：
  - [ ] `v030_ai_smart_camera_planning.md` 從「規劃中」改成「shipped + 實機 quirks」
  - [ ] 更新 `MEMORY.md` 索引行
- [ ] T22 — 更新 ROADMAP.md：M9.15 從 🚧 planned → ✅ done + 寫實際版本號
- [ ] T23 — 更新 CLAUDE.md：Project Architecture Pointers 加
  `smart_camera_planner.py` + alembic 0027
- [ ] T24 — `pyproject.toml` + `web/package.json` bump 0.30.0
- [ ] T25 — Commit + push
- [ ] T26 — 搬 `openspec/changes/ai-smart-camera/` →
  `openspec/changes/archive/YYYY-MM-DD-ai-smart-camera/`
