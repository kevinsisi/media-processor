"""Tests for the profile YAML loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from media_processor.profile import ProfileValidationError, load_profile

REPO_ROOT = Path(__file__).resolve().parents[2]
CARSMEET = REPO_ROOT / "profiles" / "carsmeet-luxury.yaml"
UNIVERSAL = REPO_ROOT / "profiles" / "universal.yaml"


def test_load_carsmeet_profile() -> None:
    profile = load_profile(CARSMEET)
    assert profile.name == "carsmeet-luxury"
    assert profile.tag_weights["logo_close_up"] == pytest.approx(1.5)
    assert profile.editing_rules.target_duration_ms == 30000
    assert profile.editing_rules.required_segments.opening_hero is True
    assert profile.editing_rules.required_segments.closing_hero is True
    assert profile.face_blur.mode == "selective"


def test_load_universal_profile() -> None:
    profile = load_profile(UNIVERSAL)
    assert profile.name == "universal"
    assert profile.face_blur.mode == "off"
    assert profile.tag_weights  # non-empty


def test_missing_editing_rules_raises(tmp_path: Path) -> None:
    p = tmp_path / "broken.yaml"
    p.write_text(
        "name: x\n"
        "tag_weights: {a: 1.0}\n"
        "filters: {min_quality_score: 0.5, max_blur: 0.4, "
        "min_segment_duration_ms: 100, max_segment_duration_ms: 1000}\n"
        "reframe: {subject_class: car, subject_padding_pct: 10, "
        "smoothing_window_frames: 30, fallback: center_crop}\n"
        "captions: {enabled: true}\n"
        "face_blur: {mode: 'off'}\n",
        encoding="utf-8",
    )
    with pytest.raises(ProfileValidationError, match="editing_rules"):
        load_profile(p)


def test_negative_target_duration_raises(tmp_path: Path) -> None:
    p = tmp_path / "neg.yaml"
    p.write_text(
        "name: x\n"
        "tag_weights: {a: 1.0}\n"
        "filters: {min_quality_score: 0.5, max_blur: 0.4, "
        "min_segment_duration_ms: 100, max_segment_duration_ms: 1000}\n"
        "editing_rules: {target_duration_ms: -1, min_cuts: 5, max_cuts: 10}\n"
        "reframe: {subject_class: car, subject_padding_pct: 10, "
        "smoothing_window_frames: 30, fallback: center_crop}\n"
        "captions: {enabled: true}\n"
        "face_blur: {mode: 'off'}\n",
        encoding="utf-8",
    )
    with pytest.raises(ProfileValidationError, match="target_duration_ms"):
        load_profile(p)


def test_face_blur_mode_validated(tmp_path: Path) -> None:
    p = tmp_path / "bad_blur.yaml"
    p.write_text(
        "name: x\n"
        "tag_weights: {a: 1.0}\n"
        "filters: {min_quality_score: 0.5, max_blur: 0.4, "
        "min_segment_duration_ms: 100, max_segment_duration_ms: 1000}\n"
        "editing_rules: {target_duration_ms: 30000, min_cuts: 5, max_cuts: 10}\n"
        "reframe: {subject_class: car, subject_padding_pct: 10, "
        "smoothing_window_frames: 30, fallback: center_crop}\n"
        "captions: {enabled: true}\n"
        "face_blur: {mode: nope}\n",
        encoding="utf-8",
    )
    with pytest.raises(ProfileValidationError, match="face_blur.mode"):
        load_profile(p)


def test_empty_tag_weights_rejected(tmp_path: Path) -> None:
    p = tmp_path / "empty_weights.yaml"
    p.write_text(
        "name: x\n"
        "tag_weights: {}\n"
        "filters: {min_quality_score: 0.5, max_blur: 0.4, "
        "min_segment_duration_ms: 100, max_segment_duration_ms: 1000}\n"
        "editing_rules: {target_duration_ms: 30000, min_cuts: 5, max_cuts: 10}\n"
        "reframe: {subject_class: car, subject_padding_pct: 10, "
        "smoothing_window_frames: 30, fallback: center_crop}\n"
        "captions: {enabled: true}\n"
        "face_blur: {mode: 'off'}\n",
        encoding="utf-8",
    )
    with pytest.raises(ProfileValidationError, match="tag_weights"):
        load_profile(p)
