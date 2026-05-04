"""RQ worker entry point — `python -m media_processor.workers [QUEUE …]`.

Connects to the project's Redis instance (settings.redis_url) and
listens on the queues passed as positional CLI arguments.

  * ``python -m media_processor.workers``  — legacy single-worker
    mode: listen on all three queues (analysis → editing → bgm) in
    dispatch order. Used when ``docker-compose.yml`` declares one
    ``worker`` service.

  * ``python -m media_processor.workers analysis``  — listen on a
    single queue. Used by the v0.27.0 multi-worker compose where
    each worker process is dedicated to one queue type so a slow
    BGM MusicGen run doesn't head-of-line block analysis or
    editing, and three editing workers can render in parallel
    on the AMD 3700X's eight cores.

Unknown queue names error out at startup so a typo in compose
(``analsis``) doesn't silently sit idle on a queue nobody enqueues to.

SIGTERM stops the worker between jobs (not mid-job) so a
``docker compose down`` doesn't corrupt a running pipeline.
"""

from __future__ import annotations

import logging
import sys

from redis import Redis
from rq import Queue, Worker

from media_processor.api.config import settings
from media_processor.workers import ANALYSIS_QUEUE, BGM_QUEUE, EDITING_QUEUE

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("media_processor.workers")

VALID_QUEUES = {ANALYSIS_QUEUE, EDITING_QUEUE, BGM_QUEUE}


def main() -> int:
    redis_conn = Redis.from_url(settings.redis_url)

    # CLI args restrict which queues this worker listens on. v0.27.0
    # multi-worker setups declare one process per queue (analysis +
    # bgm on GPU, editing × 3 on CPU); legacy single-process mode
    # listens on all three when no args are given.
    requested = sys.argv[1:]
    if requested:
        unknown = [q for q in requested if q not in VALID_QUEUES]
        if unknown:
            logger.error(
                "unknown queue name(s): %s; valid choices: %s",
                unknown,
                sorted(VALID_QUEUES),
            )
            return 2
        # Preserve user-supplied order — RQ polls queues in the
        # order they're listed on each tick, so the order also
        # dictates dispatch priority within a multi-queue worker.
        # For single-queue workers there's only one entry.
        queue_names = requested
    else:
        # Legacy "listen on everything in worker dispatch order".
        queue_names = [ANALYSIS_QUEUE, EDITING_QUEUE, BGM_QUEUE]

    queues = [Queue(name, connection=redis_conn) for name in queue_names]

    # ``name=None`` lets RQ auto-generate a unique worker id from
    # ``hostname + pid``. Important under the v0.27.0 multi-worker
    # setup: docker assigns each container a different hostname, so
    # the auto-generated names stay unique across all 5 worker
    # containers. Pre-0.27 we used a fixed ``media-worker-{api_host}``
    # which collided when the api_host setting was the same across
    # containers (the default ``0.0.0.0``) — RQ's worker registry
    # would then track only one of them.
    worker = Worker(queues, connection=redis_conn)
    logger.info(
        "starting RQ worker on queues %s (worker_name=%s, redis=%s)",
        queue_names,
        worker.name,
        settings.redis_url,
    )
    worker.work(with_scheduler=False, logging_level="INFO")
    return 0


if __name__ == "__main__":
    sys.exit(main())
