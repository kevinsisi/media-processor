from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from media_processor.services import bgm_mixer


def test_mix_narration_keeps_long_narration_audio(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    video = tmp_path / "video.mp4"
    audio = tmp_path / "narration.m4a"
    output = tmp_path / "out.mp4"
    video.write_bytes(b"video")
    audio.write_bytes(b"audio")
    commands: list[list[str]] = []

    def fake_run(cmd: list[str], **_: object) -> subprocess.CompletedProcess[bytes]:
        commands.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(bgm_mixer.subprocess, "run", fake_run)
    monkeypatch.setattr(bgm_mixer.shutil, "which", lambda _: "/usr/bin/ffmpeg")

    bgm_mixer.mix_narration(
        video,
        [bgm_mixer.NarrationClip(start_s=0.0, audio_path=audio)],
        output,
    )

    assert commands
    assert "duration=longest" in " ".join(commands[0])
    assert "-shortest" not in commands[0]
