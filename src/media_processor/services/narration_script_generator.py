"""NarratoAI documentary narration script generation.

Takes a completed frame_analysis_json (from frame_analysis_service) and
produces a StoryScriptDocument using the same LLM pipeline as story_script.
The documentary path differs in that:
  - Input is frame observations instead of Whisper transcript
  - Narration is generated from visual content, not spoken words
  - All segments use audio_intent="narration" by default

For drama_explain mode, uses the existing story_script pipeline with a
drama-focused prompt override.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from media_processor.models import Asset
from media_processor.services import story_script
from media_processor.services.frame_analysis_service import analysis_to_markdown
from media_processor.services.opencode_client import call_opencode_text
from media_processor.services.settings_store import build_opencode_config, get_llm_api_keys
from media_processor.services.story_script import (
    STORY_SCRIPT_SCHEMA_VERSION,
    StoryInputBundle,
    StoryInputSegment,
    StoryScriptDocument,
    StoryScriptInputError,
    _call_gemini,
    _heuristic_document,
    _repair_json_payload,
    validate_story_script,
)

logger = logging.getLogger(__name__)

_DOCUMENTARY_PROMPT_TEMPLATE = """
你是一位頂級紀錄片解說編劇。請根據以下視頻幀分析，產生 NarratoAI 風格的短影音 StoryScript。

目標：
- 產生 {target_items} 段以內的解說腳本。
- 開頭必須有引人入勝的鉤子（疑問、衝突或反差）。
- 每段都要有明確的畫面描述 picture 與繁體中文 narration。
- narration 是解說旁白，不是原聲字幕。
- 所有片段使用 audio_intent="narration"（解說旁白覆蓋原聲）。
- source_start_ms / source_end_ms 必須落在幀分析時間範圍內。
- 每段時長建議 3–10 秒，合計不超過 {max_duration_s} 秒。

專案名稱：{project_name}
{brief_block}

幀分析內容：
{frame_markdown}

只輸出嚴格 JSON，不要 markdown，不要解釋：
{{
  "schema_version": "{schema_version}",
  "title": "短影音標題",
  "summary": "一句話說明這支短影音主軸",
  "items": [
    {{
      "order": 1,
      "asset_id": {asset_id},
      "source_start_ms": <int>,
      "source_end_ms": <int>,
      "picture": "畫面描述",
      "narration": "繁體中文解說旁白",
      "audio_intent": "narration",
      "beat_type": "hook" | "setup" | "conflict" | "payoff" | "closing",
      "hook_type": "contrast" | "question" | null,
      "reason": "為什麼這段適合短影音"
    }}
  ]
}}
""".strip()

_DRAMA_EXPLAIN_PROMPT_TEMPLATE = """
你是一位專業短劇解說主播。請根據以下逐字稿，產生 NarratoAI 風格的短劇解說 StoryScript。

目標：
- 產生 {target_items} 段以內的解說腳本，挑選劇情高潮、爆點、反轉片段。
- 開頭 3 秒要有強烈鉤子——揭秘、衝突或懸念。
- narration 是第三人稱解說旁白（例如：「沒想到她竟然…」），語氣活潑有節奏感。
- 解說片段使用 audio_intent="narration"，重要原聲橋段用 audio_intent="original"。
- source_start_ms / source_end_ms 必須落在逐字稿時間範圍內。

專案名稱：{project_name}
{brief_block}

逐字稿片段：
{transcript_block}

只輸出嚴格 JSON，不要 markdown，不要解釋：
{{
  "schema_version": "{schema_version}",
  "title": "短劇解說標題",
  "summary": "一句話說明這支解說影片主軸",
  "items": [
    {{
      "order": 1,
      "asset_id": <int>,
      "source_start_ms": <int>,
      "source_end_ms": <int>,
      "picture": "畫面描述",
      "narration": "繁體中文解說旁白",
      "audio_intent": "narration" | "original",
      "beat_type": "hook" | "setup" | "conflict" | "payoff" | "closing",
      "hook_type": "conflict" | "question" | "contrast" | null,
      "reason": "為什麼這段適合短劇解說"
    }}
  ]
}}
""".strip()


async def _call_llm(prompt: str, session: AsyncSession) -> str | None:
    """Call OpenCode (primary) → Gemini (fallback). Return raw text or None."""
    opencode_config = await build_opencode_config(session)
    if opencode_config is not None:
        for server_url in opencode_config.servers:
            raw = await call_opencode_text(
                prompt=prompt,
                system_prompt="你是專業短影音腳本生成器。只輸出 JSON。",
                server_url=server_url,
                password=opencode_config.password,
                model=opencode_config.model,
                variant=opencode_config.variant,
                timeout_s=opencode_config.timeout_s,
            )
            if raw:
                return raw

    api_keys = await get_llm_api_keys(session)
    if api_keys:
        raw, _ = await _call_gemini(prompt, api_keys=api_keys)
        return raw
    return None


async def generate_documentary_script(
    session: AsyncSession,
    asset: Asset,
    *,
    project_name: str,
    project_brief: str = "",
    target_items: int = 8,
    max_duration_s: int = 90,
) -> StoryScriptDocument:
    """Generate a narration StoryScript from asset.frame_analysis_json.

    Raises StoryScriptInputError if frame_analysis_json is absent.
    Falls back to heuristic document if LLM fails.
    """
    fa = asset.frame_analysis_json
    if not isinstance(fa, dict) or not fa.get("batches"):
        raise StoryScriptInputError(
            f"asset {asset.id} has no frame_analysis_json; run frame analysis first"
        )

    asset_duration_ms = int(asset.duration_ms)
    markdown = analysis_to_markdown(fa)
    brief_block = f"創作方向：{project_brief.strip()}" if project_brief.strip() else ""

    prompt = _DOCUMENTARY_PROMPT_TEMPLATE.format(
        target_items=target_items,
        max_duration_s=max_duration_s,
        project_name=project_name,
        brief_block=brief_block,
        frame_markdown=markdown,
        asset_id=asset.id,
        schema_version=STORY_SCRIPT_SCHEMA_VERSION,
    )

    raw = await _call_llm(prompt, session)
    if raw:
        try:
            payload = _repair_json_payload(raw)
            document = validate_story_script(
                payload,
                project_id=asset.project_id,
                asset_durations={asset.id: asset_duration_ms},
            )
            return StoryScriptDocument(
                project_id=document.project_id,
                title=document.title,
                summary=document.summary,
                items=document.items,
                metadata={
                    "mode": "documentary",
                    "asset_id": asset.id,
                    "frame_count": fa.get("frame_count", 0),
                    "used_fallback": False,
                },
            )
        except Exception as exc:
            logger.warning("documentary script parse failed, using fallback: %s", exc)

    # Heuristic fallback: pick evenly-spaced segments from the asset
    interval_s = float(fa.get("interval_seconds", 3.0))
    batches = fa.get("batches", [])
    segments: list[StoryInputSegment] = []
    for batch in batches[:target_items]:
        obs_list = batch.get("frame_observations", [])
        summary = batch.get("overall_activity_summary", "")
        if not obs_list:
            continue
        first_obs = obs_list[0]
        start_ms = _parse_ts_ms(first_obs.get("timestamp", "00:00:00,000"))
        end_ms = min(asset_duration_ms, start_ms + int(interval_s * len(obs_list) * 1000))
        if end_ms > start_ms:
            segments.append(
                StoryInputSegment(
                    asset_id=asset.id,
                    asset_duration_ms=asset_duration_ms,
                    start_ms=start_ms,
                    end_ms=end_ms,
                    text=summary or "（自動選段）",
                )
            )

    bundle = StoryInputBundle(
        project_id=asset.project_id,
        project_name=project_name,
        segments=tuple(segments),
        asset_durations={asset.id: asset_duration_ms},
        used_transcripts=False,
        used_visual_context=True,
    )
    document = _heuristic_document(bundle, target_items=target_items)
    return StoryScriptDocument(
        project_id=document.project_id,
        title=document.title,
        summary=document.summary,
        items=document.items,
        metadata={"mode": "documentary", "asset_id": asset.id, "used_fallback": True},
    )


async def generate_drama_explain_script(
    session: AsyncSession,
    project_id: int,
    *,
    project_name: str,
    project_brief: str = "",
    target_items: int = 8,
) -> StoryScriptDocument:
    """Generate a drama-explanation StoryScript from Whisper transcripts.

    Uses a drama-focused prompt instead of the generic story prompt.
    Falls back to existing story_script.generate_story_script on failure.
    """
    try:
        bundle = await story_script.gather_story_inputs(session, project_id)
    except StoryScriptInputError:
        raise

    lines = []
    for idx, seg in enumerate(bundle.segments[:120], 1):
        lines.append(f"{idx}. asset_id={seg.asset_id} [{seg.start_ms}-{seg.end_ms}ms] {seg.text}")
    transcript_block = "\n".join(lines)
    brief_block = f"解說方向：{project_brief.strip()}" if project_brief.strip() else ""

    prompt = _DRAMA_EXPLAIN_PROMPT_TEMPLATE.format(
        target_items=target_items,
        project_name=project_name,
        brief_block=brief_block,
        transcript_block=transcript_block,
        schema_version=STORY_SCRIPT_SCHEMA_VERSION,
    )

    raw = await _call_llm(prompt, session)
    if raw:
        try:
            payload = _repair_json_payload(raw)
            document = validate_story_script(
                payload,
                project_id=project_id,
                asset_durations=bundle.asset_durations,
            )
            return StoryScriptDocument(
                project_id=document.project_id,
                title=document.title,
                summary=document.summary,
                items=document.items,
                metadata={**bundle.metadata(), "mode": "drama_explain", "used_fallback": False},
            )
        except Exception as exc:
            logger.warning("drama_explain script parse failed, falling back to story: %s", exc)

    # Fallback: use the standard story pipeline
    return await story_script.generate_story_script(session, project_id, target_items=target_items)


def _parse_ts_ms(ts: str) -> int:
    """Parse SRT timestamp (HH:MM:SS,mmm) to milliseconds."""
    try:
        ts = ts.replace(".", ",")
        hms, ms_str = ts.split(",", 1)
        parts = hms.split(":")
        while len(parts) < 3:
            parts.insert(0, "0")
        h, m, s = int(parts[-3]), int(parts[-2]), int(parts[-1])
        return (h * 3600 + m * 60 + s) * 1000 + int(ms_str)
    except Exception:
        return 0
