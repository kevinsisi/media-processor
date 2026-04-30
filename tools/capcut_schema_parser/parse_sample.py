"""Read a CapCut/JianyingPro draft folder and extract its top-level structure."""

import json
from pathlib import Path
from typing import Any, cast


def parse_draft(draft_dir: Path) -> dict[str, Any]:
    """Return the parsed content of `draft_content.json` from a draft folder.

    Falls back to `draft_content.json` at the top level. If the file uses an
    alternate name (some versions ship `draft_info.json` companion files), the
    primary `draft_content.json` is still the canonical source.
    """
    content_path = draft_dir / "draft_content.json"
    if not content_path.exists():
        raise FileNotFoundError(f"draft_content.json not found in {draft_dir}")
    return cast(dict[str, Any], json.loads(content_path.read_text(encoding="utf-8")))


def summarize(draft_dir: Path) -> None:
    """Print top-level structure of a draft for human inspection."""
    data = parse_draft(draft_dir)
    print(f"Top-level keys: {sorted(data.keys())}")
    print(f"Schema version: {data.get('version', 'UNKNOWN')}")
    tracks = data.get("tracks", [])
    print(f"Track count: {len(tracks)}")
    for i, t in enumerate(tracks):
        print(f"  Track {i}: type={t.get('type')}, segments={len(t.get('segments', []))}")
    materials = data.get("materials", {})
    print(f"Material categories: {sorted(materials.keys())}")


if __name__ == "__main__":
    import sys
    target = (
        Path(sys.argv[1]) if len(sys.argv) > 1 else Path("samples/capcut_draft/mp_sample_001")
    )
    summarize(target)
