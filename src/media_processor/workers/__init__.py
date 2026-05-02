"""M4 + M5 RQ worker package.

The worker process is launched via ``python -m media_processor.workers`` and
consumes jobs from the ``analysis`` and ``editing`` Redis queues. Job
functions live in ``analysis_jobs`` (M4) and ``edit_jobs`` (M5) so callers
(RQ, tests) can import them without booting the worker entry point.
"""

ANALYSIS_QUEUE = "analysis"
EDITING_QUEUE = "editing"
# v0.15 — AI BGM generation lives on its own queue so a slow MusicGen
# inference (~30-60 s on small GPU) can't head-of-line block analysis
# or editing jobs. The worker process listens on all three.
BGM_QUEUE = "bgm"
