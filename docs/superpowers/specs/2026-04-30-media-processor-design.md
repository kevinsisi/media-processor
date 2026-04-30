# Media Processor — Phase α 設計文件

| 欄位 | 內容 |
|------|------|
| 日期 | 2026-04-30 |
| 狀態 | Draft（待使用者過稿） |
| 範圍 | Phase α MVP — carsmeet 單客戶產線 |
| 目標 | 一週穩定產出 10 支 reels，使用者女朋友從剪輯者轉為審片者 |
| 預估工期 | 全職 約 6 個月 / 業餘 ~1–1.5 年（細項見 §14） |

---

## 1. 問題與願景

### 1.1 終端使用者

開發者的女朋友，身分有兩個：

1. **carsmeet.tw 小編**：台北內湖豪車經銷商「遇見好車 CarsMeet」的 IG 內容運營（Rolls-Royce、Bentley、Lamborghini Urus、Porsche GT3 等二手豪車）
2. **接案剪輯師**：個人接案，案件主題不一（婚禮、餐飲、人物、商業…）

### 1.2 當前痛點

每支 30 秒成片需要從約 150 個原始素材手動挑選、排序、卡點，使用剪映/CapCut Pro Mac 桌面版完成。**剪輯本身吃光她大部分時間**。

### 1.3 願景：內容產線

不是「AI 剪片助手」，是**內容產線（content factory）**。差別：

| | AI 剪片助手 | 內容產線 |
|---|------------|---------|
| 目標 | 一支片剪得快 | 持續產出，不停機 |
| 瓶頸 | 單支耗時 | 案件吞吐量 |
| 重點 | 剪輯演算法 | pipeline + 多案件管理 + 品牌模板 + 排程發布 |
| 她的角色 | 單支微調 | 審片人 / 品保 |

她的工作從「坐在時間軸上拖拽」變成「**選片 / 拒片**」，每支只看 30 秒就決定要不要過。

### 1.4 非目標（明確排除）

- 取代剪映/CapCut 作為完整 NLE 編輯器
- 商業化 SaaS / 對外通用方案（個人使用為主，未來可能再考慮）
- 自動發布到社群平台（Phase γ 才談，本 spec 不含）

---

## 2. 階段切分

| Phase | 範圍 | 目標 |
|-------|------|------|
| **α**（本 spec） | 單客戶（carsmeet）產線 + 兩個 profile | 一週穩定 10 支 reels |
| **β** | 多客戶分租 + 品牌模板 + 進階審片功能 | 開始接案 + 同時撐 carsmeet |
| **γ** | 階段 4-5 自動化（品牌套版自動 + 排程發布） | 從審片轉為產出官，產量再翻倍 |

本文件僅為 Phase α MVP 設計。

---

## 3. 系統架構

### 3.1 部署拓撲

```
                    家用 LAN（或 Tailscale VPN）
        ┌──────────────────────────────────────────────┐
        │                                                │
   [她 Mac]                              [開發者 Windows + RTX 2070]
                                         │
   - Finder 掛 SMB                       │  Docker Desktop + WSL2
     拖素材進 assets/                    │  + NVIDIA Container Toolkit
                                         │  ┌──────────────────────┐
   - Chrome / Edge 開                    │  │ docker-compose:       │
     http://server:8080                  │  │   - api (FastAPI)     │
     review、過稿、自動同步草稿          │  │   - web (React/Nginx) │
                                         │  │   - worker (AI, GPU)  │
   - 剪映/CapCut Pro                     │  │   - watcher (Ingest)  │
     直接看到草稿做最後微調              │  │   - postgres           │
                                         │  │   - redis              │
                                         │  └──────────────────────┘
                                         │
                                         │  C:\MediaProcessor\
                                         │   ├─ assets/  ← SMB 共享
                                         │   ├─ drafts/
                                         │   └─ db/
```

**關鍵約束**：

- 開發者 Windows host 的 RTX 2070 (8GB VRAM) 是 AI worker 的主要運算資源
- 她 Mac 與 Windows server 同網段（家用 LAN），SMB 共享存取素材
- 跨網段或外點存取走 Tailscale
- 所有素材、模型、推論、LLM 文字輸入都本地處理；**僅有「加指令再剪」的 LLM 文字 prompt 會送外部 Anthropic API**，不傳影像或音訊

### 3.2 模組總覽

| 模組 | 角色 | 跑在哪 |
|------|------|-------|
| Ingest Watcher | 監看素材資料夾、抽 metadata、觸發 pipeline | Python 背景常駐 |
| Asset DB (Postgres) | 素材 metadata、tag、segment、draft、review | Docker 容器 |
| AI Pipeline Workers | 8.5 個 stage 的核心 AI 邏輯 | Python，吃 Redis 佇列 |
| Profile 系統 | YAML 規則檔 | git 版控的 `profiles/` 目錄 |
| Draft Store | AI 產出的剪映草稿包 + mp4 預覽 | 本地 volume |
| Review Inbox UI | 她操作的 web app | Electron 不採用，改用瀏覽器 |
| Caption Module | 字幕生成（faster-whisper） | Python worker |
| Face Blur Module | 選擇性人臉遮蔽（InsightFace） | Python worker |
| LLM Patcher | 「加指令再剪」呼叫 Claude API | Python worker |
| Browser FS Sync | File System Access API 自動寫入剪映目錄 | 瀏覽器 JavaScript |

### 3.3 工作流程

```
[她] 拍攝完丟素材到 Mac 上的 NAS/SMB 共享資料夾
   │
   ▼
[Ingest Watcher] 偵測 → 建 Project + Asset 記錄 → 派任務
   │
   ▼
[AI Pipeline] 跑 8.5 個 stage → 產出草稿 + mp4 預覽
   │
   ▼
[Notify] WebSocket 推播 → 她瀏覽器收到通知 + 自動下載 zip
   │
   ▼
[她] 在 web app 審片（30 秒看完）
   ├─ 過稿 → 自動同步到剪映目錄 → 開剪映微調發布
   ├─ 退回 → AI 重抽（不串 LLM）
   ├─ 加指令再剪 → LLM 解讀 → AI 重剪 v2
   └─ 直接下載 zip → 手動處理（保留逃生口）
```

---

## 4. 資料模型

### 4.1 實體一覽（9 個）

| 群組 | Entity | 主要欄位 |
|------|--------|---------|
| **A. 案件素材** | Project | id, name, client, profile_name, source_dir, status, created_at |
| | Asset | id, project_id, file_path, duration_ms, resolution, fps, codec, sha256, thumbnail_path, status |
| | AssetTag | asset_id, tag_type, tag_name, confidence, source_model, time_ranges_ms |
| | AssetSegment | id, asset_id, start_ms, end_ms, score, used_in_draft |
| **B. 規則** | Profile | YAML 檔，DB 只存 profile_name 引用 |
| **C. 草稿** | Draft | id, project_id, profile_name, version, status, output_zip_path, mp4_preview_path, ai_score, prompt_feedback |
| | DraftSegment | draft_id, order, asset_segment_id, on_timeline_start_ms, on_timeline_end_ms, reframe_keyframes (jsonb), transition, blurred_source_path |
| | BGM | id, file_path, name, bpm, beat_grid_json |
| **D. 審片** | Review | id, draft_id, reviewer, action, prompt_feedback, reviewed_at |

### 4.2 ER 概覽

```
Project ──── 1:N ──── Asset ──── 1:N ──── AssetSegment
   │                    │                       │
   │                    └── 1:N ── AssetTag    │
   │                                            │
   └─ 1:N ─ Draft ─ 1:N ─ DraftSegment ─ N:1 ──┘
              │
              └─ 1:N ─ Review

Profile (YAML)  ──── 多個 Project 引用同一個
BGM             ──── 多個 Draft 引用
```

### 4.3 設計決定

- **Asset vs AssetSegment 分開**：一支 5–10 秒 clip 內可能多個精選片段，打分打在 Segment level
- **Profile 用 YAML 不入 DB**：規則應被 git 版控、code review、人類可讀
- **Project 多版本 Draft**：v1 / v2 / v3 保留歷史，方便比較與 fallback
- **DraftSegment.blurred_source_path**：face blur 不修改原檔，產生新片段檔，draft 引用此版本
- **Review 表預留 reviewer 欄位**：MVP 永遠寫 hardcode `"alice"`，Phase β 再加多人

### 4.4 為什麼選 Postgres 不選 SQLite

- 多 service 並發寫（api / worker / watcher）
- 大量 AssetTag insert（YOLO 對每 asset 寫幾十個 tag）
- jsonb 欄位（reframe_keyframes、beat_grid_json）原生索引
- Phase β multi-tenant 自然延伸（row-level security 等）

---

## 5. 風格檔（Profile）系統

Profile 是「該案件套哪一組打分規則」的依據。**MVP 內建兩個**：

- `carsmeet-luxury.yaml`：豪車展間 cinematic 風
- `universal.yaml`：通用，無特定主題偏好

### 5.1 carsmeet-luxury.yaml 範例

```yaml
name: carsmeet-luxury
description: 豪車經銷展間 cinematic 風（Rolls-Royce / Bentley / Lambo / Porsche）

tag_weights:
  # 高分（一定要留）
  logo_close_up: 1.5
  integral_hero_shot: 1.4
  body_line_pan: 1.2
  light_reflection: 1.1
  # 中分
  wheel_caliper: 0.8
  interior_leather: 0.8
  dashboard: 0.7
  star_ceiling: 0.9
  exhaust_pipe: 0.7
  # 扣分
  stranger_face: -0.8
  parking_lot_other_car: -0.6
  blur: -1.0
  overexposed: -0.7

filters:
  min_quality_score: 0.5
  max_blur: 0.4
  min_segment_duration_ms: 200
  max_segment_duration_ms: 2000

editing_rules:
  target_duration_ms: 30000
  min_cuts: 25
  max_cuts: 50
  diversity_penalty:
    same_tag_consecutive: 0.3   # 連續同 tag 候選分數 *0.3
  required_segments:
    opening_hero: true           # 開頭必須是 hero shot
    closing_hero: true           # 結尾必須是 hero shot

reframe:
  subject_class: car
  subject_padding_pct: 15
  smoothing_window_frames: 30
  fallback: center_crop

captions:
  enabled: true
  language: zh
  font: PingFangTC-Regular
  font_size: 48
  position: bottom_center
  outline: true
  outline_color: "#000000"

face_blur:
  mode: selective
  blur_identities_dir: ./profiles/carsmeet-luxury/blur_faces/
  blur_style: gaussian
  blur_strength: 25
```

### 5.2 universal.yaml 範例

```yaml
name: universal
description: 通用，畫面品質為主，無特定主題偏好

tag_weights:
  face_clear: 0.5
  composition_centered: 0.3
  motion_smooth: 0.4
  blur: -1.0
  overexposed: -0.7
  underexposed: -0.6

filters:
  min_quality_score: 0.5
  max_blur: 0.4
  min_segment_duration_ms: 300
  max_segment_duration_ms: 3000

editing_rules:
  target_duration_ms: 30000
  min_cuts: 15
  max_cuts: 40

reframe:
  subject_class: auto    # 取每幀最大 confidence 物件
  subject_padding_pct: 20
  smoothing_window_frames: 30
  fallback: center_crop

captions:
  enabled: true
  language: zh

face_blur:
  mode: off
```

### 5.3 Profile 演進路徑

- **MVP**：兩個 YAML 檔，使用者編輯靠手動
- **Phase β**：支援自訂 profile（新增 / 編輯 UI），prompt-driven 微調（用 Claude API 把中文描述轉 weights）
- **Phase γ**：reference-driven（給一支「想做成這樣」的成片，AI 自動推導 weights）

---

## 6. AI Pipeline 詳細設計

### 6.1 8.5 個 Stage

```
[0. Ingest Probe]
        │
        ▼
[1. Per-asset Analysis (YOLO + CLIP + OpenCV + VAD)]    GPU
        │
        ▼
[2. Segment Selection]
        │
[3. Music Analysis (BPM)]
        │
        ▼
[4. Cut Planning (Greedy + Diversity)]
        │
        ▼
[5. Reframe Planning (ByteTrack)]                        GPU

  (初次跑 pipeline 不經過 4.5；當使用者於審片畫面點「加指令再剪」時，
   觸發 4.5 → 重跑 stage 2 + stage 4 → 重跑 5/6/7.5/7 產 Draft v2)

       [4.5 Prompt Patch (Claude API)]                   外部
        │
        ▼
[6. Caption (faster-whisper)]                            GPU
        │
        ▼
[7.5 Face Blur (InsightFace, selective)]                 GPU
        │
        ▼
[7. Draft Assembly (CapCut JSON + mp4 + zip)]            NVENC
        │
        ▼
[8. Notify Review Inbox]
```

### 6.2 各 Stage 規格

| # | Stage | 主要工作 | Lib | GPU |
|---|-------|---------|-----|-----|
| **0** | Ingest Probe | ffprobe metadata、抽 0.5fps 縮圖、sha256、寫 Asset | ffmpeg-python | 否 |
| **1** | Per-asset Analysis | (a) YOLOv11 物件偵測（COCO 80 類）<br>(b) CLIP zero-shot 任意 tag（"logo close-up", "leather seat", "exhaust pipe"）<br>(c) OpenCV blur (Laplacian var)、亮度直方圖、抖動偵測<br>(d) pyannote VAD 語音偵測<br>輸出：AssetTag + per-frame quality score | ultralytics, open_clip, cv2, pyannote-audio | 是 |
| **2** | Segment Selection | 滑窗（0.3–2.0 秒）內計算 profile.tag_weights 加權分；filter 掉低於 min_quality_score / 過 blur 的；輸出 AssetSegment | numpy | 否 |
| **3** | Music Analysis | librosa BPM + onset → beat grid（重拍時間點清單） | librosa | 否 |
| **4** | Cut Planning | Greedy + Diversity Penalty（演算法見 6.3） | 純 Python | 否 |
| **4.5** | Prompt Patch | 將使用者文字 + 當前 profile + 草稿結構 → Claude API → 產 patched profile（加權調整建議）→ 重跑 4 | anthropic SDK | 否 |
| **5** | Reframe Planning | YOLO + ByteTrack 對 profile.subject_class 追蹤；平滑後產 9:16 crop 關鍵幀；偵測不到回 fallback | ultralytics 內建 ByteTrack | 是 |
| **6** | Caption | faster-whisper medium 中文 ASR；輸出時間軸字幕（不燒進畫面，存成 CapCut text track） | faster-whisper | 是 |
| **7.5** | Face Blur | InsightFace 對 DraftSegment 用到的片段做臉偵測 + recognition；匹配 blur_identities_dir 內人臉者打 gaussian blur；產生新片段檔到 `assets/<project>/blurred/`；DraftSegment.blurred_source_path 指向新檔 | insightface | 是 |
| **7** | Draft Assembly | 把 timeline + reframe keyframes + caption + blurred sources → 寫 CapCut `draft_content.json` + ffmpeg compose mp4 preview（NVENC encode）+ zip 打包 | 自寫 adapter + ffmpeg | NVENC |
| **8** | Notify | Draft.status = ready_for_review；WebSocket 推 Inbox | — | 否 |

### 6.3 Cut Planning 演算法（Stage 4）

```
input:  segments_pool: List[AssetSegment]   # 每段已有 score
        beat_grid: List[float]               # 重拍時間點（秒）
        profile.editing_rules

output: timeline: List[DraftSegment]

algorithm:
  1. target_cuts = clip(len(beat_grid 重拍), min_cuts, max_cuts)
  2. 在 beat_grid 上挑 target_cuts 個切點 → target_cuts 個時間槽
  3. 對每個槽：
       candidates = segments_pool 排序 by score
       套 diversity_penalty：
         若上一個槽選的 tag = 目前候選的 tag → 候選分數 *0.3
       挑分最高的，標記已用
  4. 套 required_segments：
       - opening_hero=true → 第 0 槽強制換成 integral_hero_shot tag 的最高分 segment
         若候選池中無此 tag → log warning，保留原 greedy 結果
       - closing_hero=true → 末槽同上
  5. 輸出 DraftSegment 序列
```

不用 LP solver / DP — 30 秒 30 個切點，greedy 跑 < 50ms。

### 6.4 Object Detection 雙引擎

| 方法 | 抓什麼 | 為什麼 |
|------|--------|-------|
| YOLOv11 pretrained (COCO) | car, person, handbag…等 80 類 | 通用、快、好用、社群成熟 |
| CLIP zero-shot | "logo close-up", "interior leather", "wheel rim", "exhaust pipe" 任意 prompt | 不用訓練，profile 可宣告任何中英文 tag |

MVP 階段不 fine-tune YOLO。Phase β 累積 500+ 筆 review 決定後（approved / rejected 是天然 label），再做 fine-tune。

### 6.5 Prompt Patch（Stage 4.5）詳設

使用者於審片畫面點「加指令再剪」按鈕，輸入文字（例「多用車身特寫，開頭要 Hero shot」）。Pipeline：

```
1. 收集 context:
   - 當前 profile YAML 全文
   - 當前草稿 segments 清單（含 tag、score、時序）
   - 候選 segments 池（被 filter 掉但仍可用的）
   - beat_grid 摘要

2. 組 prompt 送 Claude API（Sonnet 4.6 預設，可換 Opus 4.7 if needed）:
   "你是影片剪輯助手。當前 profile 與草稿如下…使用者反饋：'<user_text>'
    請輸出一個 JSON patch，僅含 tag_weights 調整與 required_segments 變更。"

3. 解析 LLM 回應 → 產 patched_profile（記憶體中，不改 YAML 檔）

4. 用 patched_profile 重跑 stage 2 (segment selection 重打分) + stage 4 (cut planning)
   ※ 不重跑 stage 1 (per-asset analysis) — tag 沒變，只是權重變

5. 產 Draft v2，於 Review.prompt_feedback 紀錄使用者輸入
```

**成本估計**：每次重剪約 NT$0.3–1.5，月用 50 次約 NT$30–50。

**LLM 失敗時的 fallback**：API 超時或 quota 用盡 → fallback 到「重剪」（不串 LLM，用同 profile 隨機抽不同 segments）。

### 6.6 Auto-reframe（Stage 5）詳設

```
input:  DraftSegment.asset_id, on_timeline range
        profile.reframe.subject_class

algorithm:
  1. 對 asset 範圍每幀跑 YOLO + ByteTrack 追蹤 subject_class
  2. 每幀計算「最佳 9:16 crop window」中心 = subject 中心 + padding_pct
  3. 對所有幀的中心做時間軸平滑（moving average over smoothing_window_frames）
  4. 取每 200ms 一個關鍵幀，輸出 reframe_keyframes (jsonb)
  5. 偵測不到 subject 的 segment → 套 fallback (center_crop)
```

平滑後輸出格式為剪映可吃的位置/縮放關鍵幀（具體 schema 待 step 0 反向工程確認）。

### 6.7 Caption（Stage 6）詳設

- 對每個 DraftSegment 抽出對應原 asset 的音訊
- faster-whisper medium model + language=zh
- 輸出 (start_ms, end_ms, text) 的時間軸字幕
- 寫進 CapCut draft 的 **text track**，不燒進影像 — 她在剪映可直接改錯字、換字體
- mp4 preview 才燒進畫面（給她在 web 看的）
- 字型、大小、位置、外框走 profile.captions 配置

### 6.8 Face Blur（Stage 7.5）詳設

```
input:  Draft 內的 DraftSegment 清單
        profile.face_blur.blur_identities_dir

algorithm:
  1. mode=off → 跳過
  2. mode=all → 對所有偵測到的人臉打 blur
  3. mode=selective:
     a. 載入 blur_identities_dir 下所有 <person>/*.jpg → InsightFace 抽 embedding 建 reference 庫
     b. 對每個 DraftSegment 用到的時間範圍逐幀跑 InsightFace 偵測 + recognize
     c. 匹配 reference 庫者（cosine sim > 0.5）→ 該臉打 gaussian blur (strength=25)
     d. 平滑跨幀（避免閃爍）：對連續 5 幀沒匹配但前後都匹配的，補充打碼
  4. 產生「打碼版片段檔」存到 assets/<project>/blurred/<asset_id>_<seg_id>.mp4
  5. 對應 DraftSegment.blurred_source_path 指向新檔
  6. 原檔不動

只對「會用到的 segment」做，不對 150 個 asset 全跑（節省 GPU）
```

### 6.9 Draft Assembly（Stage 7）詳設

- **CapCut draft schema 是私有 JSON，動工前必須先反向工程**（見第 11.1 節 step 0）
- Adapter 介面 `JianyingDraftAdapter`，將 internal timeline model 轉為 CapCut JSON
- 鎖定一個剪映主版本（取使用者實際版本），加 schema 版本檢測，不一致時警告
- 輸出 zip 含：
  - `draft_content.json`
  - `draft_meta_info.json`
  - 縮圖 / cover.jpg
  - 必要的 metadata
  - **不含**素材副本（路徑引用 SMB 共享上的素材）
- 同時用 ffmpeg + NVENC 產一支 30 秒 mp4 給 web preview（合成 reframe + caption + blur）

### 6.10 Stage 序列化保險開關

環境變數 `MEDIA_PROCESSOR_GPU_SERIAL_MODE`：

- `1`（預設，MVP）：同一 project 的 GPU stage（1, 5, 6, 7.5, 7-encode）一律順序跑，避免 2070 8GB OOM
- `0`：允許併發，需要實測 2070 表現夠才打開

---

## 7. Review Inbox UI 設計

### 7.1 三個畫面

#### 畫面 1：案件列表

```
┌──────────────────────────────────────────────────────┐
│  媒體處理器     [+ 新案件]   alice                    │
├──────────────────────────────────────────────────────┤
│  進行中                                                │
│  ┌────────────────────────────────────────────────┐  │
│  │ carsmeet-Phantom-0428                            │  │
│  │ 150 素材 | profile: carsmeet-luxury              │  │
│  │ 草稿 v1 已就緒 | 1 待審        [→ 審片]          │  │
│  ├────────────────────────────────────────────────┤  │
│  │ 接案-王先生婚禮-0501                              │  │
│  │ 87 素材 | profile: universal                      │  │
│  │ AI Pipeline 處理中 (stage 5/8)                   │  │
│  ├────────────────────────────────────────────────┤  │
│  │ carsmeet-Bentley-0427                            │  │
│  │ 200 素材 | profile: carsmeet-luxury              │  │
│  │ 已通過 | 已下載草稿                               │  │
│  └────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────┘
```

#### 畫面 2：審片（核心）

```
┌──────────────────────────────────────────────────────┐
│  ← carsmeet-Phantom-0428 / 草稿 v1                    │
├──────────────────────────────────────────────────────┤
│                                                        │
│       ┌──────────────────────────────┐                │
│       │     mp4 預覽 (9:16 直式)      │   AI 信心度    │
│       │       ▶  00:12 / 00:30        │   8.4 / 10    │
│       └──────────────────────────────┘                │
│                                                        │
│  時間軸（每段一格、tag 標記、可點跳轉）                  │
│  ┌─┬─┬──┬─┬──┬─┬─┬──┬─┬─┬──┬─┬─┬──┬─┐            │
│  │L│W│Hr│L│Bd│W│L│Hr│I│L│Hr│W│L│Hr│★│            │
│  └─┴─┴──┴─┴──┴─┴─┴──┴─┴─┴──┴─┴─┴──┴─┘            │
│                                                        │
│  分數明細：                                            │
│   - 5 個 Logo 特寫 / 4 個輪框 / 3 個整車 hero          │
│   - 偵測到 2 張陌生人臉（未在打馬清單）                 │
│   - 字幕 12 行                                         │
│   - BPM 對拍 28 切點 / 30 重拍                         │
│                                                        │
├──────────────────────────────────────────────────────┤
│  [過稿]   [退回]   [加指令再剪]   [下載剪映 zip]       │
└──────────────────────────────────────────────────────┘
```

點時間軸任一格 → 跳出「AI 判斷理由」popup：

```
┌──────────────────────────────────┐
│  Segment #07  (00:14 - 00:15.2)  │
│  原始素材：IMG_3421.MOV           │
│                                  │
│  為什麼選這段：                   │
│  - Logo 特寫（飛天女神）信心 92% │
│  - 畫面穩定（無抖動）             │
│  - profile 加權 +1.5             │
│  - 對齊重拍 #14                  │
│                                  │
│  總分 8.7 / 10                   │
└──────────────────────────────────┘
```

#### 畫面 3：素材池（Phase β，MVP 不做）

審片時若想知道「為什麼 AI 沒選某素材」，Phase β 開放查看候選池與被 filter 原因。MVP 階段以 logs 提供。

### 7.2 四個審片動作

| 按鈕 | 動作 | 後續 |
|------|------|------|
| 過稿 | Draft.status = approved | 自動同步草稿到她剪映目錄 |
| 退回 | Draft.status = rejected；觸發「重抽」（不串 LLM，用同 profile 隨機抽不同 segments） | 產 v2 進 Inbox |
| 加指令再剪 | 跳輸入框；送 Claude API；觸發 stage 4.5 → 重跑 stage 4-7 | 產 v2 進 Inbox |
| 下載剪映 zip | 直接下載 zip（保留逃生口，平時自動同步可不點） | 結束 |

### 7.3 自動同步剪映目錄

走瀏覽器原生 **File System Access API**：

1. 第一次使用時，web app 跳「請選擇剪映草稿資料夾」
2. 她點 `~/Movies/JianyingPro/User Data/Projects/com.lveditor.draft/`（或 CapCut 對應路徑）
3. Chrome / Edge 記住這個 `FileSystemDirectoryHandle` 並持久化（IndexedDB）
4. 之後 Draft 過稿時：
   - 瀏覽器抓 zip blob
   - JSZip 在瀏覽器內解壓
   - 透過 stored handle 寫入剪映目錄
5. 她開剪映 → 草稿就在那

**限制**：必須 Chrome / Edge / Brave（Chromium 系），Safari 支援不完整。MVP 規範使用 Chromium 瀏覽器。Phase β 若需 Safari 支援再做 Tauri 原生 agent。

### 7.4 Tech 選型

| 層 | 技術 |
|---|------|
| Framework | React 18 + Vite + TypeScript |
| UI | shadcn/ui + Tailwind |
| 影片預覽 | HTML5 `<video>` + range 控制 |
| 狀態 | React Query（API cache）+ Zustand（local） |
| 即時 | WebSocket（pipeline 進度、新草稿就緒） |
| 客戶端解壓 | JSZip |
| 客戶端寫檔 | File System Access API（FileSystemDirectoryHandle） |

---

## 8. 技術棧總覽

### 8.1 完整 Stack

| 層 | 技術 | 備註 |
|---|------|------|
| Backend API | Python 3.11 + FastAPI | sync + WebSocket |
| 任務佇列 | Redis + RQ | MVP 規模夠 |
| AI Workers | Python（cv2, ultralytics, librosa, faster-whisper, insightface, pyannote-audio, ffmpeg-python） | 跑在 GPU container |
| LLM | Anthropic Python SDK，Claude Sonnet 4.6 | 僅 stage 4.5 用到 |
| DB | Postgres 16 | docker-compose 起 |
| Frontend | React 18 + Vite + TypeScript + Tailwind + shadcn/ui | Chromium 瀏覽器 |
| Web 反代 | Nginx | docker-compose 內 |
| 部署 | Docker Compose + WSL2 + NVIDIA Container Toolkit | Windows host |
| 儲存 | SMB 共享（素材）+ local volume（草稿、DB） | NAS 路徑非必須，只要 Mac/Windows 同網段 |

### 8.2 容器規劃

```yaml
# docker-compose.yml 概要
services:
  postgres:    # 資料庫
  redis:       # 任務佇列
  api:         # FastAPI HTTP + WebSocket
  watcher:     # 資料夾監看
  worker:      # AI pipeline，吃 GPU，可多 instance
  web:         # React 靜態站 by Nginx
```

GPU 透過 `deploy.resources.reservations.devices` 給 worker。

---

## 9. 錯誤處理與邊界情況

### 9.1 Per-stage 防護

每個 stage task 包：

```python
@retry(max_attempts=3, backoff_seconds=[2, 8, 30])
@timeout(stage_timeout)
def run_stage(asset_id):
    try:
        ...
    except CudaOOMError:
        # GPU OOM → 降 CPU 跑、標 project.status = "degraded"
        fallback_cpu()
    except CorruptAssetError:
        # 單支壞檔 → 跳過、標 asset.status = "failed"，project 繼續
        skip_with_log()
    except Exception:
        # 其他真錯 → raise，task fail，notify
        raise
```

### 9.2 Stage timeout 上限（MVP 預設）

| Stage | Timeout |
|-------|---------|
| 0 Ingest Probe | 2 min / asset |
| 1 Per-asset Analysis | 3 min / asset |
| 2 Segment Selection | 30 s / project |
| 3 Music Analysis | 1 min / BGM |
| 4 Cut Planning | 30 s / project |
| 4.5 Prompt Patch | 30 s（Anthropic API timeout） |
| 5 Reframe Planning | 5 min / draft |
| 6 Caption | 10 min / draft（30s 影片 ASR < 1 min） |
| 7.5 Face Blur | 5 min / draft |
| 7 Draft Assembly | 5 min / draft |

超時 → retry 一次（用更高 timeout），仍超則 task fail。

### 9.3 失敗模式與對策

| 失敗 | 偵測 | 對策 |
|------|------|------|
| GPU OOM | CudaOOMError | 序列化 mode 降 CPU；project.status = degraded |
| Asset 壞檔 | ffprobe 失敗 / decode error | 跳過、標 asset.status=failed、project 繼續 |
| BGM 檔不存在 | 找不到 file_path | project fail，要求使用者重指定 |
| draft_content.json schema 錯 | adapter 寫出後 schema check fail | 警告、退到 mp4-only 輸出（fallback） |
| Disk 寫滿 | OS write error | 拒絕新 ingest、警告使用者 |
| Anthropic API 失敗 | timeout / 4xx / 5xx / quota | fallback 到「重抽」（不串 LLM）、提示使用者 |
| InsightFace 偵測不到任何臉但 mode=selective | InsightFace 結果 empty | warning log，視為「無可疑人臉」直接通過（不打碼）|
| ByteTrack 追不到 subject | 全 segment 偵測 confidence < 0.3 | 套 reframe.fallback (center_crop) |
| WebSocket 中斷 | 心跳 timeout | client 自動 reconnect；server 重送最新狀態 |
| File System Access 權限被撤銷 | API 寫入失敗 | UI 跳「請重新授權剪映目錄」 |

### 9.4 Project-level fail 條件

整 project 標記 failed 的硬條件：

- 80% 以上 asset 壞檔（資料根本爛掉）
- DB 寫不進去
- Disk 寫滿
- BGM 必填但檔不存在
- 連續 3 個 stage retry 全 fail

### 9.5 Idempotent Stage 設計

每個 stage **應可重跑**：

- Stage 0: sha256 dedup，重跑跳過已存在 asset
- Stage 1-3: 寫入前先 delete 既有 tag/segment
- Stage 4-7: 產 Draft 是 v1, v2, v3…，重跑產新版本，不覆蓋
- Stage 7.5: 打碼檔以 `<asset_id>_<seg_id>.mp4` 命名，覆蓋同名檔

### 9.6 Progress Tracking

每個 task 寫進度到 Redis：`progress:<project_id>:<stage> = {percent, eta_seconds}`。
WebSocket 推給 web app，Inbox UI 顯示「Pipeline 處理中 (stage 5/8 reframe... 67%)」。

---

## 10. 安全與隱私

- **本地處理**：素材、模型推論、whisper、InsightFace 全在 Windows host 跑，無雲端
- **唯一外部呼叫**：stage 4.5 LLM patch 送 Anthropic API（**僅文字**：profile + 草稿摘要 + 使用者反饋；不送影像、音訊）
- **接案隱私**：客戶素材以資料夾隔離（`assets/<client>_<project>/...`），MVP 無 multi-tenant DB 隔離但 Phase β 會加
- **VIP 人臉打碼**：face_blur.blur_identities_dir 機制，使用者自行管理參考照片，照片本身不離開機器
- **網路存取**：Tailscale 或家用 LAN，不對公網開放
- **API 金鑰**：`.env` 檔，docker-compose 讀，不入 git

---

## 11. 動工前必驗證（Step 0）

下列項目在第一行 production code 之前必須先驗證：

### 11.1 CapCut 草稿 schema 反向工程

請使用者女朋友在剪映/CapCut Pro Mac 上：

1. 新建簡單草稿（拖 3 個素材 + 1 段 BGM + 1 段字幕）
2. 把整個草稿資料夾打包傳給開發者
3. 開發者拆 `draft_content.json` 確認 schema
4. 確認的點：素材引用、轉場、BGM 軌、字幕軌、位置/縮放關鍵幀的 JSON 結構與欄位
5. 鎖定她當下版本（記錄版本號）為 MVP 的 ground truth

**沒這個 step Stage 7 寫不下去**。

### 11.2 CLIP zero-shot 在車類 tag 的準度

抓 30 張 carsmeet 既有 reels 的截圖：

- 跑 CLIP zero-shot tag（"logo close-up", "leather seat", "exhaust pipe"…）
- 統計準度與 false positive
- 不行就調 prompt 用語、或回退到 review 階段手動修正 tag → Phase β fine-tune

### 11.3 SMB 連通性

- Windows host 開 SMB share `\\<windows-ip>\MediaProcessor`
- Mac 走 Finder「連線到伺服器」`smb://<windows-ip>/MediaProcessor` 掛載
- 測試讀寫權限、效能（拖 5GB 影片計時）

### 11.4 WSL2 GPU passthrough

- Windows host 安裝 NVIDIA Container Toolkit for WSL2
- `docker run --gpus all nvidia/cuda:12.x-base nvidia-smi` 應看到 RTX 2070
- 測試 ultralytics / faster-whisper / insightface 在 container 內能用 GPU

### 11.5 File System Access API 在她 Mac 行為

- Chrome on macOS 開 demo 頁面，授權目錄寫入測試
- 確認 `FileSystemDirectoryHandle` 持久化跨 session 保留授權
- 確認 JSZip 解壓寫入大量小檔效能（CapCut 草稿一個約 10–50 個檔）

---

## 12. 測試策略

### 12.1 單元測試

- Profile YAML 解析與驗證
- Cut Planning greedy + diversity 演算法（合成 fixtures）
- Segment scoring 加權邏輯
- BPM detection（已知 BPM 樣本）
- 剪映 draft adapter 寫出 JSON（snapshot test 比對 ground truth）

### 12.2 整合測試

- 跑完整 pipeline 在小型 fixture（10 個 30 秒 clip → 30 秒草稿）
- 各 stage 失敗注入測試（強制 GPU OOM、強制壞檔）
- LLM API mock 測試（vcr.py 或 anthropic mock）

### 12.3 E2E

- Playwright 跑 web UI 主要 flow（登入 / 看清單 / 審片 / 過稿）
- 自動同步剪映目錄的整合測試（檢查檔案落在預期路徑）

### 12.4 人工 QA

- 跑 5 場 carsmeet 真實素材，使用者女朋友審片，計算過稿率與重剪次數
- 跑 1 場接案案件（婚禮 / 其他主題），確認 universal profile 表現
- 確認 CapCut 打開草稿後可以正常編輯與輸出

---

## 13. 風險登錄

| 風險 | 影響 | 機率 | 緩解 |
|------|------|------|------|
| CapCut draft schema 跨版本破壞 | 高（核心輸出失效） | 中 | adapter 隔離、schema 版本檢測、mp4 fallback |
| AI 產出有「AI 味」傷品牌 | 中（內容力下降） | 高 | profile 強差異化、嚴格審片門檻、talking head 段保留人味 |
| RTX 2070 8GB 記憶體壓力 | 中（產線速度） | 中 | 序列化 mode 預設開、必要時 stage 1 batch 縮小 |
| LLM API 中斷 / quota | 低（degraded 模式存在） | 低 | fallback 到「重抽」 |
| File System Access API 在她 Mac 表現異常 | 中（核心 UX） | 低 | 保留手動下載 + Phase β Tauri agent |
| 拍攝 SOP 沒到位導致素材沒料剪 | 高（產線無料） | 中 | 與她共擬「必拍鏡頭清單」，Phase β 實作拍攝 checklist |
| 她審片速度成新瓶頸 | 中（產線塞車） | 中 | Phase β 加多 reviewer / 客戶過稿 |
| 接案素材保密事故 | 高（客戶信任） | 低 | 本地處理、folder 隔離、無雲端、VPN 限制 |

---

## 14. 工期估算

### 14.1 Phase α 各模組

| 模組 | 工期（全職） |
|------|------------|
| Docker Compose 基礎設施 + WSL2 GPU 配置 | 1 週 |
| Postgres schema + ORM + migration | 1 週 |
| Ingest Watcher + SMB 設定 | 1 週 |
| Stage 0 (Probe) + Stage 1 (Per-asset Analysis) | 3 週 |
| Stage 2 (Segment) + Stage 3 (BPM) + Stage 4 (Cut Planning) | 2 週 |
| Stage 4.5 (Prompt Patch + Claude API) | 1 週 |
| Stage 5 (Reframe + ByteTrack) | 2 週 |
| Stage 6 (Caption + Whisper) | 1 週 |
| Stage 7.5 (Face Blur + InsightFace) | 1 週 |
| Stage 7 (Draft Assembly + CapCut adapter) | 3 週 |
| Stage 8 (Notify + WebSocket) | 0.5 週 |
| Profile 系統 + 兩個 YAML | 0.5 週 |
| Review Inbox UI（畫面 1+2 + 4 個動作） | 3 週 |
| File System Access API 自動同步 | 0.5 週 |
| 錯誤處理 / fallback / 序列化 mode | 1 週 |
| 測試 / fixtures / E2E | 2 週 |
| 動工前驗證（step 0） | 1 週 |
| Buffer | 2 週 |
| **合計** | **約 25.5 週 ≈ 6 個月**（全職） |

業餘做 ×2–3 倍，約 1–1.5 年。

### 14.2 里程碑

| 里程碑 | 達成條件 |
|--------|---------|
| M1: 基礎設施就位 | docker-compose up 起來、Postgres、Redis、WSL2 GPU 通了 |
| M2: 端到端 hello-world | 拖 3 素材 → AI 產 mp4 預覽（不必草稿） |
| M3: CapCut 草稿輸出可用 | 草稿能在剪映打開，使用者可微調 |
| M4: Review Inbox 主要 flow | 過稿 / 退回 / 重抽 走得通 |
| M5: 完整 8.5 stage（含字幕、打碼、LLM patch） | 接近成品 |
| M6: 真實素材 dry run + QA | 跑 5 場 carsmeet 通過率 ≥ 60% |
| M7: Phase α GA | 一週穩定 10 支 reels |

---

## 15. 決策記錄

| 決策 | 結論 | 理由 |
|------|------|------|
| 編輯器路線 | 寫 CapCut draft（保留剪映微調），不自製 NLE | 自製 NLE = 多人年，自殺；CapCut 已是業界級，做整合最有效 |
| 自動化等級 | 從 Lv2（產草稿）起，未來升 Lv3（直出 mp4） | Lv2 保留人味 + 微調空間，最適合 carsmeet 品牌 |
| 產品定位 | 內容產線（factory）而非剪片助手 | 使用者明確要求「跟產片工廠一樣可以一直有產出」 |
| 部署 | Windows host + WSL2 + Docker | 使用者有 RTX 2070，最快上線 |
| 終端 | Web（瀏覽器）而非桌面 app | 多用戶、多客戶、多 reviewer 自然延伸 |
| 素材傳輸 | SMB share Mac↔Windows | 家用 LAN 自然方案，不需 NAS |
| DB | Postgres 而非 SQLite | 多 service 並發、jsonb、Phase β 延伸 |
| Profile | YAML 檔，不入 DB | git 版控、人類可讀、code review |
| 輸出方式 | CapCut draft zip + mp4 preview | 雙軌：草稿給她微調、mp4 給她快速看 |
| 草稿同步 | File System Access API（瀏覽器） | 不需做 Tauri 原生 agent，工程量省 |
| 人臉打碼 | InsightFace selective（指定要打的） | 接案 VIP 隱私需求 |
| 字幕 | MVP 啟用，faster-whisper medium | 接案常需字幕，Whisper 中文 medium 夠用 |
| LLM 加指令重剪 | MVP 啟用，Anthropic Claude API | 提升審片到產出的迴圈速度，成本可忽略 |
| GPU 使用模式 | 序列化 mode 預設開 | 2070 8GB 跑多 GPU stage 風險 OOM |
| Profile 起手 | carsmeet-luxury + universal | 一個專用 + 一個通用，覆蓋常見場景 |

---

## 16. 開放議題（待 Phase β 或更晚決議）

- Multi-client 隔離方案：folder vs DB row-level security
- Phase γ 的 IG / TikTok 發布 API 整合（Meta Graph API、TikTok Content Posting）
- 拍攝 SOP 與「必拍鏡頭 checklist」是否做成 app 模組
- Phase β fine-tune YOLO 的 label 累積策略（review 決定 → label）
- CapCut 海外版（CapCut）vs 中國版（剪映）schema 差異處理
- 商業化路徑：是否 SaaS 化、定價、客戶 onboarding

---

## 17. 變更歷史

| 日期 | 變更 | 作者 |
|------|------|------|
| 2026-04-30 | 初版 Draft | 開發者 + Claude（brainstorming session） |
