"""M4 + M5 RQ worker package.

The worker process is launched via ``python -m media_processor.workers [queue...]``.
The current compose deployment runs dedicated analysis / editing / bgm workers;
the no-arg legacy mode still listens on all queues.
"""

ANALYSIS_QUEUE = "analysis"
EDITING_QUEUE = "editing"
# v0.15 — AI BGM generation lives on its own queue so a slow MusicGen
# inference (~30-60 s on small GPU) can't head-of-line block editing jobs.
BGM_QUEUE = "bgm"
