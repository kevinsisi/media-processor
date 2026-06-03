# Capability: tts-synthesis

將 `NarrationCue` 列表逐條合成為 MP3 音訊，回傳詞級時間戳供字幕對齊，最後用 ffmpeg concatenate 合併為單一解說音訊軌。

## Behaviour

1. **輸入**：`draft.narration_cues_json`（`NarrationCue` 列表）+ `project.narration_voice`（edge-tts voice name）+ `project.narration_speed`。
2. **逐條合成**：
   - 呼叫 `edge_tts.Communicate(text, voice=voice, rate=f"{speed_pct:+d}%")`
   - 輸出 `{MEDIA_STORAGE_DIR}/drafts/{draft_id}/narration_{i:04d}.mp3`
   - 同步收集 `SubMaker` 詞級時間戳（`WordBoundary` events）
3. **合併**：用 ffmpeg `concat` demuxer 將所有 cue MP3 按 `start_ms` 排序合併為 `narration_full.mp3`；cue 間空白用 `anullsrc` 填充。
4. **時間戳輸出**：生成 `narration_words.json`（詞 + 絕對起止毫秒），供渲染時疊加字幕用（可選，不影響主路徑）。
5. **狀態**：`draft.narration_audio_path` 寫入 `narration_full.mp3` 路徑；失敗時清空路徑並 log，渲染繼續（輸出無聲解說版）。

## Voice 選擇

預設 voice：`zh-TW-HsiaoChenNeural`（台灣女聲）。使用者可在 Project 設定選擇任意 edge-tts voice；UI 提供常用清單（zh-TW/zh-CN/en-US 各 2-3 個）。速度 `narration_speed` 範圍 `[0.5, 2.0]`，換算為 edge-tts rate 格式（`+50%` / `-25%` 等）。

## Constraints

- 每條 cue 合成 timeout：30 s；整體 timeout：`len(cues) × 35 s`（上限 900 s）
- 重試：每條最多 2 次重試（transient edge-tts network error）；2 次後該條 cue 靜音跳過
- 依賴網路（Azure edge-tts）；本地離線時整個 TTS 步驟靜默 skip
- worker-editing 佇列（CPU-only，多副本可並行）
- `edge-tts>=6.1.9` 加入 `pyproject.toml`；`docker/worker.Dockerfile` 同步更新

## API

```
POST /drafts/{id}/synthesize-narration
  Body: { "force": false }
  Response: { "status": "pending", "draft_id": "..." }

GET /drafts/{id}/narration-status
  Response: { "status": "done", "audio_path": "/media/drafts/1/narration_full.mp3" }
```
