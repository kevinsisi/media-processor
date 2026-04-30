"""Typed loader and validator for profile YAML files.

Profile schema lives at spec §5. The loader returns a `ProfileSpec` dataclass
tree; the on-disk YAML remains the canonical source of truth.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


class ProfileValidationError(ValueError):
    """Raised when a profile YAML fails structural or value validation."""


@dataclass(frozen=True)
class Filters:
    min_quality_score: float
    max_blur: float
    min_segment_duration_ms: int
    max_segment_duration_ms: int


@dataclass(frozen=True)
class RequiredSegments:
    opening_hero: bool = False
    closing_hero: bool = False
    hero_tag: str = "integral_hero_shot"


@dataclass(frozen=True)
class EditingRules:
    target_duration_ms: int
    min_cuts: int
    max_cuts: int
    diversity_penalty_same_tag: float = 1.0
    required_segments: RequiredSegments = field(default_factory=RequiredSegments)


@dataclass(frozen=True)
class ReframeConfig:
    subject_class: str
    subject_padding_pct: int
    smoothing_window_frames: int
    fallback: str


@dataclass(frozen=True)
class CaptionsConfig:
    enabled: bool
    language: str = "zh"
    font: str | None = None
    font_size: int | None = None
    position: str | None = None
    outline: bool = False
    outline_color: str | None = None


@dataclass(frozen=True)
class FaceBlurConfig:
    mode: str
    blur_identities_dir: str | None = None
    blur_style: str = "gaussian"
    blur_strength: int = 25


@dataclass(frozen=True)
class ProfileSpec:
    name: str
    description: str
    tag_weights: dict[str, float]
    filters: Filters
    editing_rules: EditingRules
    reframe: ReframeConfig
    captions: CaptionsConfig
    face_blur: FaceBlurConfig
    raw_yaml: str


def load_profile(path: Path) -> ProfileSpec:
    """Read a profile YAML file and return a validated ProfileSpec."""
    if not path.exists():
        raise ProfileValidationError(f"profile file not found: {path}")

    text = path.read_text(encoding="utf-8")
    try:
        data: Any = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ProfileValidationError(f"invalid YAML in {path}: {exc}") from exc

    if not isinstance(data, dict):
        raise ProfileValidationError(f"profile {path} must be a YAML mapping at the top level")

    return _build_profile(data, raw_yaml=text)


def _build_profile(data: dict[str, Any], *, raw_yaml: str) -> ProfileSpec:
    name = _require(data, "name", str)
    description = _optional(data, "description", str, default="")
    tag_weights = _require_tag_weights(data)
    filters = _build_filters(_require(data, "filters", dict))
    editing_rules = _build_editing_rules(_require(data, "editing_rules", dict))
    reframe = _build_reframe(_require(data, "reframe", dict))
    captions = _build_captions(_require(data, "captions", dict))
    face_blur = _build_face_blur(_require(data, "face_blur", dict))

    return ProfileSpec(
        name=name,
        description=description,
        tag_weights=tag_weights,
        filters=filters,
        editing_rules=editing_rules,
        reframe=reframe,
        captions=captions,
        face_blur=face_blur,
        raw_yaml=raw_yaml,
    )


def _require_tag_weights(data: dict[str, Any]) -> dict[str, float]:
    raw = _require(data, "tag_weights", dict)
    out: dict[str, float] = {}
    for k, v in raw.items():
        if not isinstance(k, str):
            raise ProfileValidationError(f"tag_weights key must be string, got {type(k).__name__}")
        if not isinstance(v, int | float) or isinstance(v, bool):
            raise ProfileValidationError(
                f"tag_weights['{k}'] must be a number, got {type(v).__name__}"
            )
        out[k] = float(v)
    if not out:
        raise ProfileValidationError("tag_weights must contain at least one entry")
    return out


def _build_filters(d: dict[str, Any]) -> Filters:
    f = Filters(
        min_quality_score=_require(d, "min_quality_score", (int, float)),
        max_blur=_require(d, "max_blur", (int, float)),
        min_segment_duration_ms=_require(d, "min_segment_duration_ms", int),
        max_segment_duration_ms=_require(d, "max_segment_duration_ms", int),
    )
    if f.min_segment_duration_ms <= 0 or f.max_segment_duration_ms <= 0:
        raise ProfileValidationError("filters segment durations must be positive")
    if f.min_segment_duration_ms >= f.max_segment_duration_ms:
        raise ProfileValidationError(
            "filters.min_segment_duration_ms must be < max_segment_duration_ms"
        )
    return f


def _build_editing_rules(d: dict[str, Any]) -> EditingRules:
    target_ms = _require(d, "target_duration_ms", int)
    if target_ms <= 0:
        raise ProfileValidationError("editing_rules.target_duration_ms must be positive")
    min_cuts = _require(d, "min_cuts", int)
    max_cuts = _require(d, "max_cuts", int)
    if min_cuts <= 0 or max_cuts <= 0:
        raise ProfileValidationError("editing_rules min_cuts/max_cuts must be positive")
    if min_cuts > max_cuts:
        raise ProfileValidationError("editing_rules.min_cuts must be <= max_cuts")

    diversity_raw = d.get("diversity_penalty") or {}
    if not isinstance(diversity_raw, dict):
        raise ProfileValidationError("editing_rules.diversity_penalty must be a mapping if present")
    diversity_factor = float(diversity_raw.get("same_tag_consecutive", 1.0))

    rs_raw = d.get("required_segments") or {}
    if not isinstance(rs_raw, dict):
        raise ProfileValidationError("editing_rules.required_segments must be a mapping if present")
    required = RequiredSegments(
        opening_hero=bool(rs_raw.get("opening_hero", False)),
        closing_hero=bool(rs_raw.get("closing_hero", False)),
        hero_tag=str(rs_raw.get("hero_tag", "integral_hero_shot")),
    )

    return EditingRules(
        target_duration_ms=target_ms,
        min_cuts=min_cuts,
        max_cuts=max_cuts,
        diversity_penalty_same_tag=diversity_factor,
        required_segments=required,
    )


def _build_reframe(d: dict[str, Any]) -> ReframeConfig:
    return ReframeConfig(
        subject_class=_require(d, "subject_class", str),
        subject_padding_pct=_require(d, "subject_padding_pct", int),
        smoothing_window_frames=_require(d, "smoothing_window_frames", int),
        fallback=_require(d, "fallback", str),
    )


def _build_captions(d: dict[str, Any]) -> CaptionsConfig:
    return CaptionsConfig(
        enabled=_require(d, "enabled", bool),
        language=_optional(d, "language", str, default="zh"),
        font=d.get("font"),
        font_size=d.get("font_size"),
        position=d.get("position"),
        outline=bool(d.get("outline", False)),
        outline_color=d.get("outline_color"),
    )


def _build_face_blur(d: dict[str, Any]) -> FaceBlurConfig:
    mode = _require(d, "mode", str)
    if mode not in {"off", "all", "selective"}:
        raise ProfileValidationError(
            f"face_blur.mode must be one of off/all/selective, got '{mode}'"
        )
    return FaceBlurConfig(
        mode=mode,
        blur_identities_dir=d.get("blur_identities_dir"),
        blur_style=str(d.get("blur_style", "gaussian")),
        blur_strength=int(d.get("blur_strength", 25)),
    )


def _require(data: dict[str, Any], key: str, types: type | tuple[type, ...]) -> Any:
    if key not in data:
        raise ProfileValidationError(f"missing required key: '{key}'")
    value = data[key]
    if not isinstance(value, types):
        expected = (
            types.__name__ if isinstance(types, type) else "/".join(t.__name__ for t in types)
        )
        raise ProfileValidationError(f"key '{key}' must be {expected}, got {type(value).__name__}")
    return value


def _optional(data: dict[str, Any], key: str, type_: type, *, default: Any) -> Any:
    if key not in data:
        return default
    value = data[key]
    if not isinstance(value, type_):
        raise ProfileValidationError(
            f"key '{key}' must be {type_.__name__}, got {type(value).__name__}"
        )
    return value
