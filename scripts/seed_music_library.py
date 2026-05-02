"""v0.15 — one-shot script to pre-generate the BGM library.

Run inside the worker container after a deploy:

    docker exec media-processor-worker-1 \
        python /app/scripts/seed_music_library.py

It writes 5 wav files under ``${BGM_DIR}/_library/`` covering the
common short-form-video styles. Subsequent runs skip files that
already exist so the script is idempotent — re-running is safe and
costs nothing.

The styles are tuned for typical content categories: car shoots, food
vlogs, lifestyle short-form, etc. Adjust the ``LIBRARY_TRACKS`` list to
seed different defaults. ``MUSICGEN_FAKE=1`` produces silent stubs so
CI / first-deploy environments can populate the library without
downloading the model.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("seed_music_library")


# (filename_prefix, prompt). The ``[style] `` prefix is the convention
# the music router parses into MusicLibraryItem.style for the UI.
LIBRARY_TRACKS: list[tuple[str, str]] = [
    (
        "[電影感] cinematic-suspense",
        "cinematic instrumental score with strings and piano, slow build-up, "
        "tense and emotional, 60 BPM, suitable for product reveal videos",
    ),
    (
        "[輕快流行] upbeat-pop",
        "upbeat indie pop instrumental, acoustic guitar and claps, warm and "
        "happy, 110 BPM, suitable for lifestyle vlogs",
    ),
    (
        "[lo-fi] chill-lofi",
        "lo-fi hip hop with mellow piano and soft drums, relaxing and cozy, "
        "75 BPM, suitable for cafe and study videos",
    ),
    (
        "[節奏感] energetic-edm",
        "energetic electronic dance instrumental, synth lead and punchy "
        "kick drum, exciting and modern, 128 BPM, suitable for sports and "
        "car content",
    ),
    (
        "[環境音] ambient-warm",
        "warm ambient pad with soft synth textures and gentle piano, calm "
        "and meditative, 60 BPM, suitable for nature and travel content",
    ),
]


def main() -> int:
    # Local imports keep ``--help`` cheap and make this script importable
    # in tests without booting torch.
    from media_processor.api.config import settings
    from media_processor.services import music_gen

    library_dir = Path(settings.bgm_dir) / "_library"
    library_dir.mkdir(parents=True, exist_ok=True)

    skipped: list[str] = []
    generated: list[str] = []
    failed: list[tuple[str, str]] = []

    for stem, prompt in LIBRARY_TRACKS:
        target = library_dir / f"{stem}.wav"
        if target.is_file() and target.stat().st_size > 0:
            logger.info("skipping %s (already exists)", target.name)
            skipped.append(target.name)
            continue
        logger.info("generating %s …", target.name)
        try:
            result = music_gen.generate(prompt, target)
            logger.info(
                "  → %.1fs at %d Hz (%s)",
                result.duration_s,
                result.sample_rate,
                result.model,
            )
            generated.append(target.name)
        except music_gen.MusicGenUnavailableError as exc:
            logger.error("MusicGen unavailable: %s", exc)
            logger.error(
                "  → check that transformers + torch are installed and "
                "the worker can reach huggingface.co for the model "
                "download (or set MUSICGEN_FAKE=1 to seed silent stubs)"
            )
            return 2
        except Exception as exc:  # noqa: BLE001 — record + continue
            logger.exception("failed to generate %s", target.name)
            failed.append((target.name, str(exc)))

    logger.info(
        "seed complete: %d generated, %d skipped, %d failed",
        len(generated),
        len(skipped),
        len(failed),
    )
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
