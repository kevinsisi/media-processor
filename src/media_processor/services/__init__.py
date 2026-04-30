"""Pure-Python services for the media-processor pipeline.

Most services are deterministic and do not touch the database, network, or GPU.
The Stage 4.5 ``llm_patcher`` is the exception: it talks to a remote LLM via
``httpx`` with a key-pool fallback. DB hydration / persistence happens at the
API/router layer for every service.
"""

from media_processor.services.capcut_writer import CapCutDraftWriter
from media_processor.services.cut_planner import (
    PlannedSegment,
    SegmentInput,
    plan_cuts,
)
from media_processor.services.llm_patcher import (
    DraftSegmentSummary,
    GeminiKeyPoolConfig,
    LLMPatcher,
    LLMPatchError,
    LLMQuotaExhaustedError,
    LLMResponseInvalidError,
    ProfilePatch,
    apply_patch,
)

__all__ = [
    "CapCutDraftWriter",
    "DraftSegmentSummary",
    "GeminiKeyPoolConfig",
    "LLMPatchError",
    "LLMPatcher",
    "LLMQuotaExhaustedError",
    "LLMResponseInvalidError",
    "PlannedSegment",
    "ProfilePatch",
    "SegmentInput",
    "apply_patch",
    "plan_cuts",
]
