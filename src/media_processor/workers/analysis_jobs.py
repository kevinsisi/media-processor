"""RQ job entry points for M4 asset analysis.

RQ runs sync functions; the project's services are async, so each job target
here wraps an asyncio.run() call to the async orchestrator. Keeping the job
target small means RQ never holds a reference to the SQLAlchemy session — the
orchestrator opens and closes its own session per job.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

# The set of canonical step names the orchestrator accepts. The worker
# rejects unknown names before any work runs (see analysis-pipeline REQ-1).
VALID_STEPS = ("stt", "scene", "motion", "emotion", "tracking", "coverage")


def analyze_asset(
    asset_id: int,
    *,
    steps: list[str] | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """RQ job — run the analysis pipeline for a single Asset.

    Returns a small dict so RQ stores something useful in the job result. The
    orchestrator handles all status persistence to Postgres; this return value
    is for debugging only.
    """

    if steps is not None:
        unknown = [s for s in steps if s not in VALID_STEPS]
        if unknown:
            raise ValueError(f"unknown analysis steps: {unknown}")

    logger.info(
        "analyze_asset: asset_id=%d steps=%s force=%s",
        asset_id,
        steps if steps is not None else "all",
        force,
    )

    # Local import: the orchestrator transitively imports faster-whisper +
    # OpenCV; keeping the import inside the function means importing this
    # module from the api container (just to enqueue) doesn't pull in the
    # heavy deps.
    from media_processor.services.analysis import run_pipeline

    return asyncio.run(run_pipeline(asset_id, steps=steps, force=force))


def translate_asset_subtitle(asset_id: int, *, lang: str = "en") -> dict[str, Any]:
    """RQ job — run Whisper task="translate" for a single Asset.

    Persists the resulting English (today the only supported lang)
    segments to ``Asset.subtitle_secondary_segments_json`` and the
    language tag to ``Asset.subtitle_secondary_lang``. Existing primary
    transcript / analysis state is untouched.

    Returns a small summary dict for RQ's job-result store; the API
    polls Asset state to learn whether the translation landed.
    """
    logger.info("translate_asset_subtitle: asset_id=%d lang=%s", asset_id, lang)

    # Same lazy-import pattern as analyze_asset — keeps faster-whisper
    # off the api container's import graph.
    from media_processor.services.analysis import run_translate_subtitle

    return asyncio.run(run_translate_subtitle(asset_id, lang=lang))
