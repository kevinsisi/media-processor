"""RQ worker entry point — `python -m media_processor.workers`.

Connects to the project's Redis instance (settings.redis_url) and listens on
the ``analysis`` queue. SIGTERM stops the worker between jobs (not mid-job)
so a `docker compose down` doesn't corrupt a running pipeline.
"""

from __future__ import annotations

import logging
import sys

from redis import Redis
from rq import Queue, Worker

from media_processor.api.config import settings
from media_processor.workers import ANALYSIS_QUEUE

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("media_processor.workers")


def main() -> int:
    redis_conn = Redis.from_url(settings.redis_url)
    queue = Queue(ANALYSIS_QUEUE, connection=redis_conn)
    worker = Worker([queue], connection=redis_conn, name=f"analysis-worker-{settings.api_host}")
    logger.info("starting RQ worker on queue %r (redis=%s)", ANALYSIS_QUEUE, settings.redis_url)
    worker.work(with_scheduler=False, logging_level="INFO")
    return 0


if __name__ == "__main__":
    sys.exit(main())
