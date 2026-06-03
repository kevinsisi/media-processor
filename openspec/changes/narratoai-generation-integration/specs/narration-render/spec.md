# Capability: narration-render

在現有 ffmpeg cut → concat → subtitle burn 之後，加入 TTS 人聲解說音訊軌混音，產出帶解說的最終影片。

## Behaviour

1. **觸發條件**：`draft.narration_audio_path` 存在且 `edit_mode ∈ {documentary, drama_explain}`。
2. **混音策略**：
   ```
   ffmpeg filter_complex:
     [0:a]  原聲（可選，OST=1 則保留，音量 × 0.15）
     [1:a]  解說人聲（narration_full.mp3，音量 × 1.0）
     [2:a]  BGM（現有 bgm_mixer 輸出，音量 × 0.3，duck to 0.08 when narration active）
     amix=inputs=N:duration=first:normalize=0
   ```
3. **Duck 邏輯**：narration 有聲時 BGM 音量自動降為 0.08（移植 NarratoAI `audio_normalizer` 的 duck 思路，但用 ffmpeg `sidechaincompress` 實作）。
4. **字幕**：解說模式的字幕來源從 Whisper cue 切換為 `narration_words.json`（TTS 詞級時間戳）；若 `narration_words.json` 不存在則不燒字幕（輸出仍有解說聲）。
5. **無解說退化**：若 `narration_audio_path` 為 None（TTS 失敗），渲染繼續走現有無聲路徑，draft 狀態仍為 done（非 failed）。
6. **輸出檔名**：`vN.mp4`（與現有 draft 版本命名一致，不新增欄位）。

## Constraints

- ffmpeg amix timeout：現有 `BURN_TIMEOUT_S`（不另設新常數，narration render 音訊複雜度與 viral overlay 相近）
- narration_audio_path 必須是絕對路徑；render 前 preflight 確認檔案存在
- worker-editing 佇列（現有）
- 不改變 `video_renderer.py` 的 `burn_subtitles` 介面；新 narration 混音邏輯包在 `_mix_narration_audio(draft, video_path)` helper，由 `run_render` 在 concat 完成後條件呼叫

## Modified Capability: edit-mode

`EditMode` enum 新增：
- `documentary` — 觸發：幀分析 → 解說稿生成 → TTS → narration render
- `drama_explain` — 觸發：drama_script_parsing → TTS → narration render

現有 `standard` / `luxury_auto` / `viral_short` 路徑完全不受影響。
