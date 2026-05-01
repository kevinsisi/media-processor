"""M4 RQ worker package.

The worker process is launched via ``python -m media_processor.workers`` and
consumes jobs from the ``analysis`` Redis queue. Job functions live in
``analysis_jobs`` so callers (RQ, tests) can import them without booting the
worker entry point.
"""

ANALYSIS_QUEUE = "analysis"
