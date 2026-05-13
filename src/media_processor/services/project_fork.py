"""Project forking service.

Creates an independent project copy for experiments: project settings,
script, assets, analysis metadata, and source media are copied; rendered drafts
are intentionally left behind.
"""

from __future__ import annotations

import copy
import logging
import shutil
from pathlib import Path
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from media_processor.api.config import settings
from media_processor.models import (
    Asset,
    AssetSegment,
    AssetStatus,
    AssetTag,
    AssetTranscript,
    Project,
    Script,
    ScriptCoverage,
)
from media_processor.services import asset_variants
from media_processor.services import thumbnails as thumbnails_svc

logger = logging.getLogger(__name__)


class ProjectForkError(RuntimeError):
    """Base class for project fork failures."""


class ProjectForkNotFoundError(ProjectForkError):
    """The source project does not exist."""


class ProjectForkMediaMissingError(ProjectForkError):
    """A source row points at a media file required for the fork, but it is gone."""


class ProjectForkCopyFailedError(ProjectForkError):
    """A filesystem copy failed."""


def _clone_json(value: Any) -> Any:
    return copy.deepcopy(value)


def _require_file(src: Path) -> None:
    if not src.is_file():
        raise ProjectForkMediaMissingError(f"source media file missing: {src}")


def _copy_file(src: Path, dst: Path, copied_paths: list[Path]) -> Path:
    _require_file(src)
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    except OSError as exc:
        raise ProjectForkCopyFailedError(f"failed to copy {src} to {dst}: {exc}") from exc
    copied_paths.append(dst)
    return dst


def _copy_tree(src: Path, dst: Path, copied_paths: list[Path]) -> None:
    if not src.is_dir():
        return
    try:
        shutil.copytree(src, dst, dirs_exist_ok=True)
    except OSError as exc:
        raise ProjectForkCopyFailedError(f"failed to copy {src} to {dst}: {exc}") from exc
    copied_paths.append(dst)


def _cleanup_paths(paths: list[Path]) -> None:
    for path in reversed(paths):
        try:
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)
        except OSError as exc:  # pragma: no cover - best-effort cleanup.
            logger.warning("project-fork cleanup failed for %s: %s", path, exc)


def _copy_project_bgm(
    source_path: str | None, fork_id: int, copied_paths: list[Path]
) -> str | None:
    if source_path is None:
        return None
    src = Path(source_path)
    suffix = src.suffix if src.suffix else ".wav"
    dst = Path(settings.bgm_dir) / f"{fork_id}{suffix}"
    return str(_copy_file(src, dst, copied_paths))


def _copy_project_watermark(
    source_path: str | None,
    fork_id: int,
    copied_paths: list[Path],
) -> str | None:
    if source_path is None:
        return None
    src = Path(source_path)
    suffix = src.suffix if src.suffix else ".png"
    dst = Path(settings.watermark_dir) / f"{fork_id}{suffix}"
    return str(_copy_file(src, dst, copied_paths))


def _copy_raw_asset(source_asset: Asset, fork_project_id: int, copied_paths: list[Path]) -> str:
    src = Path(source_asset.file_path)
    dst = Path(settings.assets_dir) / str(fork_project_id) / src.name
    return str(_copy_file(src, dst, copied_paths))


def _stabilized_source_to_copy(source_asset: Asset) -> Path | None:
    source_path = getattr(source_asset, "stabilized_path", None)
    if source_path is None:
        return None

    src = Path(source_path)
    if src.is_file():
        return src

    if (
        asset_variants.stabilization_status(source_asset) == asset_variants.STABILIZATION_DONE
        or asset_variants.active_variant(source_asset) == asset_variants.STABILIZED_VARIANT
    ):
        raise ProjectForkMediaMissingError(f"source stabilized media file missing: {src}")
    return None


def _copy_stabilized_asset(
    source_asset: Asset,
    fork_asset: Asset,
    copied_paths: list[Path],
) -> str | None:
    src = _stabilized_source_to_copy(source_asset)
    if src is None:
        return None
    dst = asset_variants.stabilized_path_for_asset(fork_asset)
    return str(_copy_file(src, dst, copied_paths))


def _fork_asset_status(source_asset: Asset) -> str:
    if source_asset.status == AssetStatus.ANALYZING.value:
        return AssetStatus.PENDING.value
    return source_asset.status


def _fork_point_tracking_status(source_asset: Asset) -> str | None:
    status = source_asset.point_tracking_status
    if status == "pending":
        return None
    return status


def _fork_stabilization_status(source_asset: Asset, copied_stabilized_path: str | None) -> str:
    status = asset_variants.stabilization_status(source_asset)
    if status in {asset_variants.STABILIZATION_PENDING, asset_variants.STABILIZATION_RUNNING}:
        return asset_variants.STABILIZATION_NOT_STARTED
    if copied_stabilized_path is None and status == asset_variants.STABILIZATION_DONE:
        return asset_variants.STABILIZATION_NOT_STARTED
    return status


def _fork_active_variant(source_asset: Asset, copied_stabilized_path: str | None) -> str:
    if (
        copied_stabilized_path is not None
        and asset_variants.active_variant(source_asset) == asset_variants.STABILIZED_VARIANT
    ):
        return asset_variants.STABILIZED_VARIANT
    return asset_variants.RAW_VARIANT


def _loaded_source_stmt(project_id: int) -> Select[tuple[Project]]:
    return (
        select(Project)
        .where(Project.id == project_id)
        .options(
            selectinload(Project.script),
            selectinload(Project.assets).selectinload(Asset.tags),
            selectinload(Project.assets).selectinload(Asset.segments),
            selectinload(Project.assets).selectinload(Asset.transcript),
            selectinload(Project.assets).selectinload(Asset.coverage),
        )
    )


async def fork_project(session: AsyncSession, source_project_id: int) -> Project:
    """Create an independent copy of ``source_project_id``.

    The caller owns committing the returned project. On copy failures this
    function rolls back pending database work and removes files already copied.
    """
    copied_paths: list[Path] = []
    result = await session.execute(_loaded_source_stmt(source_project_id))
    source = result.scalar_one_or_none()
    if source is None:
        raise ProjectForkNotFoundError(f"project {source_project_id} not found")

    try:
        fork = Project(
            name=f"{source.name} (copy)",
            client=source.client,
            profile_name=source.profile_name,
            source_dir="",
            status=source.status,
            target_aspect_ratio=source.target_aspect_ratio,
            bgm_path=None,
            bgm_fade_out_sec=source.bgm_fade_out_sec,
            watermark_path=None,
            watermark_position=source.watermark_position,
            watermark_scale=source.watermark_scale,
            watermark_opacity=source.watermark_opacity,
            subtitle_font=source.subtitle_font,
            subtitle_color=source.subtitle_color,
            subtitle_outline_color=source.subtitle_outline_color,
            subtitle_position=source.subtitle_position,
            subtitle_size=source.subtitle_size,
            subtitle_outline_width=source.subtitle_outline_width,
            subject_class=source.subject_class,
            crop_region_json=_clone_json(source.crop_region_json),
            smart_camera_enabled=source.smart_camera_enabled,
        )
        session.add(fork)
        await session.flush()
        fork.source_dir = str(Path(settings.assets_dir) / str(fork.id))
        fork.bgm_path = _copy_project_bgm(source.bgm_path, fork.id, copied_paths)
        fork.watermark_path = _copy_project_watermark(source.watermark_path, fork.id, copied_paths)

        fork_script: Script | None = None
        if source.script is not None:
            fork_script = Script(
                project_id=fork.id,
                body=source.script.body,
                source_filename=source.script.source_filename,
            )
            session.add(fork_script)
            await session.flush()

        for source_asset in sorted(source.assets, key=lambda asset: asset.id):
            raw_path = _copy_raw_asset(source_asset, fork.id, copied_paths)
            fork_asset = Asset(
                project_id=fork.id,
                file_path=raw_path,
                stabilized_path=None,
                stabilization_status=asset_variants.STABILIZATION_NOT_STARTED,
                stabilization_error=None,
                active_asset_variant=asset_variants.RAW_VARIANT,
                duration_ms=source_asset.duration_ms,
                resolution=source_asset.resolution,
                fps=source_asset.fps,
                codec=source_asset.codec,
                sha256=source_asset.sha256,
                thumbnail_path=None,
                status=_fork_asset_status(source_asset),
                analysis_steps_json=_clone_json(source_asset.analysis_steps_json),
                tracking_json=_clone_json(source_asset.tracking_json),
                tracked_object_index=source_asset.tracked_object_index,
                custom_roi_json=_clone_json(source_asset.custom_roi_json),
                point_tracking_json=_clone_json(source_asset.point_tracking_json),
                point_tracking_origin=_clone_json(source_asset.point_tracking_origin),
                point_tracking_status=_fork_point_tracking_status(source_asset),
                point_tracking_error=source_asset.point_tracking_error,
                subtitle_secondary_lang=source_asset.subtitle_secondary_lang,
                subtitle_secondary_segments_json=_clone_json(
                    source_asset.subtitle_secondary_segments_json
                ),
            )
            session.add(fork_asset)
            await session.flush()

            copied_stabilized_path = _copy_stabilized_asset(source_asset, fork_asset, copied_paths)
            fork_asset.stabilized_path = copied_stabilized_path
            fork_asset.stabilization_status = _fork_stabilization_status(
                source_asset,
                copied_stabilized_path,
            )
            fork_asset.stabilization_error = (
                source_asset.stabilization_error
                if fork_asset.stabilization_status == asset_variants.STABILIZATION_FAILED
                else None
            )
            fork_asset.active_asset_variant = _fork_active_variant(
                source_asset,
                copied_stabilized_path,
            )

            _copy_tree(
                thumbnails_svc.asset_thumb_dir(settings.thumbnails_dir, source_asset.id),
                thumbnails_svc.asset_thumb_dir(settings.thumbnails_dir, fork_asset.id),
                copied_paths,
            )

            session.add_all(
                AssetTag(
                    asset_id=fork_asset.id,
                    tag_type=tag.tag_type,
                    tag_name=tag.tag_name,
                    confidence=tag.confidence,
                    source_model=tag.source_model,
                    time_ranges_ms=_clone_json(tag.time_ranges_ms),
                )
                for tag in source_asset.tags
            )
            session.add_all(
                AssetSegment(
                    asset_id=fork_asset.id,
                    start_ms=segment.start_ms,
                    end_ms=segment.end_ms,
                    score=segment.score,
                    used_in_draft=False,
                )
                for segment in source_asset.segments
            )
            if source_asset.transcript is not None:
                session.add(
                    AssetTranscript(
                        asset_id=fork_asset.id,
                        language=source_asset.transcript.language,
                        model=source_asset.transcript.model,
                        transcript_text=source_asset.transcript.transcript_text,
                        segments_json=_clone_json(source_asset.transcript.segments_json),
                        edited=source_asset.transcript.edited,
                    )
                )
            if source_asset.coverage is not None and fork_script is not None:
                session.add(
                    ScriptCoverage(
                        asset_id=fork_asset.id,
                        script_id=fork_script.id,
                        model=source_asset.coverage.model,
                        scripted_segment_count=source_asset.coverage.scripted_segment_count,
                        total_segment_count=source_asset.coverage.total_segment_count,
                        coverage_ratio_by_count=source_asset.coverage.coverage_ratio_by_count,
                        coverage_ratio_by_duration_ms=source_asset.coverage.coverage_ratio_by_duration_ms,
                        match_details_json=_clone_json(source_asset.coverage.match_details_json),
                    )
                )

        await session.flush()
        return fork
    except ProjectForkError:
        await session.rollback()
        _cleanup_paths(copied_paths)
        raise
    except Exception as exc:
        await session.rollback()
        _cleanup_paths(copied_paths)
        raise ProjectForkCopyFailedError(f"project fork failed: {exc}") from exc


__all__ = [
    "ProjectForkCopyFailedError",
    "ProjectForkError",
    "ProjectForkMediaMissingError",
    "ProjectForkNotFoundError",
    "fork_project",
]
