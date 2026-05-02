---
name: gemini-prompts/script-coverage
description: 把 Whisper 逐字稿每段 segment 與專案腳本做語意比對，分類為 scripted / improvised。Gemini 只負責配對，覆蓋率由伺服器算（防模型膨脹數字）。
type: prompt
related_code: src/media_processor/services/script_coverage.py:22 (`_PROMPT_TEMPLATE`)
---

# Script Coverage Prompt（script-coverage）

## 何時使用

- 拍攝後上傳腳本 + 跑完 Whisper STT，要判斷「照稿率」
- 對比結果寫進 `CoverageResult`，給 edit_planner 當 per-segment hint（`source_kind` 偏好）
- 每段 asset 一次 Gemini call（一隻 asset 內所有 transcript segments 一起送）

## 分工原則：Gemini 配對、Server 算總

- Gemini **只回 matches list**：`(transcript_idx, classification, confidence, matched_excerpt)`
- 覆蓋率（segment count / duration）一律在 server 端用 validated matches 算
- 為什麼：見過模型自己回 `coverage_ratio = 0.85` 但實際 matches 只有 2/14 — 不能信

## Prompt 模板

```text
你是影片剪輯助手。下面是「腳本」與「逐字稿片段」。請判斷每個逐字稿片段是否與腳本任一段落語意接近
（不需逐字相同；若主旨、訴求、講述順序大致相符即視為「照稿」）。

腳本：
{script_body}

逐字稿片段（idx, [start_ms - end_ms] text）：
{numbered_segments}

請輸出嚴格 JSON：
{
  "matches": [
    {
      "transcript_idx": <int>,
      "classification": "scripted" | "improvised",
      "confidence": <float 0..1>,
      "matched_script_excerpt": <string>
    }
  ]
}
```

## 後處理規則

- `classification` 只接受 `scripted | improvised`，其他值整筆丟掉（不 fallback）
- `transcript_idx` 必須在當批送進去的 idx 集合內（防模型自編 idx）
- Server 算 `coverage_ratio_by_count = scripted / total`、`coverage_ratio_by_duration_ms` 同理
- 沒腳本 → 直接 raise `ScriptCoverageMissingScriptError`，orchestrator 標 `failed:missing-script`

## 失敗模式

- 模型回 fence + JSON → `_strip_fence` 處理
- 模型漏回某些 idx → 沒在 matches 內的 segment 預設算 improvised
- `ScriptCoverageQuotaError` → 整 asset 標 `failed:coverage-quota`，下游 stage 用空 coverage

## 變更紀錄

- v1（M4）— 初版，匹配 + server 算總
