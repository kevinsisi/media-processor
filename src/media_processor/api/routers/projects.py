"""Project endpoints — list, detail, create, drafts, script, analysis page."""
# ruff: noqa: I001

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, cast

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from media_processor.api.config import settings
from media_processor.api.deps import get_session
from media_processor.api.routers.assets import thumbnail_urls_for_asset
from media_processor.api.routers.drafts import _draft_url, _expected_draft_path
from media_processor.api.routers.watermark_presets import _load_preset_for_apply
from media_processor.api.schemas import (
    AffectedDraftOut,
    AssetAnalysisItem,
    AssetBatchDeleteOut,
    AssetBatchDeleteRequest,
    AssetBatchDeleteResultItem,
    BgmFadeOutPatch,
    CoverageSummaryOut,
    CropRegionOut,
    CropRegionPatch,
    DetectedClassOut,
    DraftSummary,
    EditModeLiteral,
    EditTriggerRequest,
    EditTriggerResponse,
    EmotionRangeOut,
    EmotionTagsOut,
    MotionSegmentOut,
    ProjectAnalysisOut,
    ProjectAssetStabilizeBatchItem,
    ProjectAssetStabilizeBatchRequest,
    ProjectAssetStabilizeBatchResponse,
    ProjectCreate,
    ProjectDetail,
    ProjectSummary,
    SceneTagOut,
    ScriptOut,
    ScriptUpsert,
    SecondarySubtitleSummaryOut,
    SmartCameraPatch,
    StoryScriptGenerateRequest,
    StoryScriptOut,
    StoryScriptSaveRequest,
    SubjectClassPatch,
    SubtitleStylePatch,
    TrackingSummaryOut,
    TranscriptSummaryOut,
    WatermarkPresetApplyRequest,
    WatermarkSettingsPatch,
)
from media_processor.models import (
    EDIT_STEP_VALUES,
    Asset,
    AssetTranscript,
    Draft,
    DraftStatus,
    Project,
    Script,
    ScriptCoverage,
    StoryScript,
)
from media_processor.services.object_tracking import aggregate_detected_classes
from media_processor.services.queue import (
    enqueue_asset_stabilization,
    enqueue_project_edit,
)
from media_processor.services import (
    asset_management as asset_mgmt,
    asset_variants,
    project_fork,
    story_script as story_scripts,
)

router = APIRouter(prefix="/projects", tags=["projects"])


def _story_script_out(row: StoryScript) -> StoryScriptOut:
    payload = dict(row.script_json or {})
    return StoryScriptOut(
        id=row.id,
        project_id=row.project_id,
        draft_id=row.draft_id,
        schema_version=row.schema_version,
        status=row.status,
        provider=row.provider,
        model=row.model,
        title=str(payload.get("title") or ""),
        summary=str(payload.get("summary") or ""),
        items=list(payload.get("items") or []),
        metadata=dict(row.metadata_json or {}),
        error=row.error,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _draft_edit_mode(draft: Draft) -> EditModeLiteral:
    flags = draft.render_flags_json if isinstance(draft.render_flags_json, dict) else {}
    raw = flags.get("edit_mode")
    return (
        cast(EditModeLiteral, raw)
        if raw
        in {"standard", "luxury_auto", "viral_short", "story", "documentary", "drama_explain"}
        else "standard"
    )


def _watermark_url(project: Project) -> str | None:
    """Public URL for the project's watermark PNG, if any.

    Built from the on-disk filename (which is ``{project_id}.png`` by
    convention) and the ``/media/watermarks`` static mount. Returns
    ``None`` when the project has no watermark set or the file vanished
    out from under us — the picker UI then shows the upload prompt
    instead of a stale thumbnail.
    """
    if not project.watermark_path:
        return None
    p = Path(project.watermark_path)
    if not p.is_file():
        return None
    # Use a cache-busting query so re-uploads at the same path aren't
    # served from a stale browser cache (mtime resolution is plenty).
    try:
        mtime = int(p.stat().st_mtime)
    except OSError:
        mtime = 0
    return f"/api/media/watermarks/{p.name}?v={mtime}"


def _draft_summary_with_urls(draft: Draft) -> DraftSummary:
    """Build a DraftSummary from a Draft row, populating mp4_url / subtitle_url
    when the renderer's expected files exist on disk (or the row already
    points at one)."""

    def _url_for(suffix: str, stored: str | None) -> str | None:
        if stored:
            return _draft_url(draft.project_id, draft.version, suffix)
        if _expected_draft_path(draft.project_id, draft.version, suffix).is_file():
            return _draft_url(draft.project_id, draft.version, suffix)
        return None

    return DraftSummary(
        id=draft.id,
        project_id=draft.project_id,
        profile_name=draft.profile_name,
        version=draft.version,
        status=draft.status,
        output_zip_path=draft.output_zip_path,
        mp4_preview_path=draft.mp4_preview_path,
        ai_score=draft.ai_score,
        created_at=draft.created_at,
        progress_steps=dict(draft.progress_steps_json or {}) or None,
        mp4_url=_url_for("mp4", draft.mp4_preview_path),
        subtitle_url=_url_for("srt", draft.subtitle_path),
        style_preset=getattr(draft, "style_preset", "custom") or "custom",
        edit_mode=_draft_edit_mode(draft),
    )


SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _crop_region_out(project: Project) -> CropRegionOut | None:
    """Project the JSON crop_region column into the API response shape.

    Tolerant — ``None``, malformed entries, or out-of-range floats
    return ``None`` (== centre). Renderer applies its own clamping
    too, but surfacing a sane shape here keeps the FE round-trip
    deterministic.
    """
    payload = getattr(project, "crop_region_json", None)
    if not isinstance(payload, dict):
        return None
    try:
        x = float(payload.get("x_norm"))  # type: ignore[arg-type]
        y = float(payload.get("y_norm"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
        return None
    return CropRegionOut(x_norm=x, y_norm=y)


def _project_detail(
    project: Project,
    *,
    asset_count: int,
    draft_count: int,
) -> ProjectDetail:
    """Build a ProjectDetail with every Project column projected through.

    Centralised so adding a column to ``Project`` only needs to change
    one place — the four endpoints that hand back a ProjectDetail used
    to repeat this constructor and silently drift apart.
    """
    return ProjectDetail(
        id=project.id,
        name=project.name,
        client=project.client,
        profile_name=project.profile_name,
        source_dir=project.source_dir,
        status=project.status,
        target_aspect_ratio=project.target_aspect_ratio,
        created_at=project.created_at,
        asset_count=asset_count,
        draft_count=draft_count,
        bgm_path=project.bgm_path,
        bgm_fade_out_sec=float(project.bgm_fade_out_sec),
        # v0.20.3 — watermark fields. A duplicate _project_detail used
        # to live above with these fields and was silently shadowed by
        # this one (Python last-definition-wins), so GET /projects/{id}
        # always returned watermark_path=null even when the upload had
        # set it. Folded into the single canonical builder so the bug
        # can't recur.
        watermark_path=project.watermark_path,
        watermark_url=_watermark_url(project),
        watermark_position=project.watermark_position,  # type: ignore[arg-type]
        watermark_scale=float(project.watermark_scale),
        watermark_opacity=float(project.watermark_opacity),
        subtitle_font=project.subtitle_font,  # type: ignore[arg-type]
        subtitle_color=project.subtitle_color,
        subtitle_outline_color=project.subtitle_outline_color,
        subtitle_position=project.subtitle_position,  # type: ignore[arg-type]
        subtitle_size=project.subtitle_size,  # type: ignore[arg-type]
        subtitle_outline_width=project.subtitle_outline_width,  # type: ignore[arg-type]
        subject_class=project.subject_class,
        crop_region=_crop_region_out(project),
        smart_camera_enabled=bool(getattr(project, "smart_camera_enabled", False)),
    )


@router.get("", response_model=list[ProjectSummary])
async def list_projects(session: SessionDep) -> list[ProjectSummary]:
    asset_count = (
        select(Asset.project_id, func.count(Asset.id).label("n"))
        .group_by(Asset.project_id)
        .subquery()
    )
    latest_draft = (
        select(Draft.project_id, func.max(Draft.version).label("v"))
        .group_by(Draft.project_id)
        .subquery()
    )
    stmt = (
        select(
            Project,
            func.coalesce(asset_count.c.n, 0).label("asset_count"),
            latest_draft.c.v.label("latest_draft_version"),
        )
        .outerjoin(asset_count, asset_count.c.project_id == Project.id)
        .outerjoin(latest_draft, latest_draft.c.project_id == Project.id)
        .order_by(Project.created_at.desc())
    )
    result = await session.execute(stmt)
    return [
        ProjectSummary(
            id=p.id,
            name=p.name,
            client=p.client,
            profile_name=p.profile_name,
            status=p.status,
            target_aspect_ratio=p.target_aspect_ratio,
            created_at=p.created_at,
            asset_count=int(ac),
            latest_draft_version=ldv,
        )
        for p, ac, ldv in result.all()
    ]


@router.post("", response_model=ProjectDetail, status_code=status.HTTP_201_CREATED)
async def create_project(
    payload: ProjectCreate,
    session: SessionDep,
) -> ProjectDetail:
    project = Project(
        name=payload.name,
        client=payload.client,
        profile_name=payload.profile_name,
        target_aspect_ratio=payload.target_aspect_ratio,
        source_dir="",
    )
    session.add(project)
    await session.flush()
    project.source_dir = str(Path(settings.assets_dir) / str(project.id))
    await session.commit()
    await session.refresh(project)
    return _project_detail(project, asset_count=0, draft_count=0)


@router.get("/{project_id}", response_model=ProjectDetail)
async def get_project(
    project_id: int,
    session: SessionDep,
) -> ProjectDetail:
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")
    asset_count = await session.scalar(
        select(func.count(Asset.id)).where(Asset.project_id == project_id)
    )
    draft_count = await session.scalar(
        select(func.count(Draft.id)).where(Draft.project_id == project_id)
    )
    return _project_detail(
        project,
        asset_count=int(asset_count or 0),
        draft_count=int(draft_count or 0),
    )


@router.post(
    "/{project_id}/fork",
    response_model=ProjectDetail,
    status_code=status.HTTP_201_CREATED,
)
async def fork_project(
    project_id: int,
    session: SessionDep,
) -> ProjectDetail:
    try:
        fork = await project_fork.fork_project(session, project_id)
        fork_id = fork.id
        await session.commit()
    except project_fork.ProjectForkNotFoundError as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except project_fork.ProjectForkMediaMissingError as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except project_fork.ProjectForkCopyFailedError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc

    fork_after_commit = await session.get(Project, fork_id)
    if (
        fork_after_commit is None
    ):  # pragma: no cover - commit succeeded but row vanished concurrently.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="forked project not found"
        )
    asset_count = await session.scalar(
        select(func.count(Asset.id)).where(Asset.project_id == fork_id)
    )
    return _project_detail(
        fork_after_commit,
        asset_count=int(asset_count or 0),
        draft_count=0,
    )


@router.get("/{project_id}/drafts", response_model=list[DraftSummary])
async def list_project_drafts(
    project_id: int,
    session: SessionDep,
) -> list[DraftSummary]:
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")
    stmt = select(Draft).where(Draft.project_id == project_id).order_by(Draft.version.asc())
    rows = (await session.execute(stmt)).scalars().all()
    return [_draft_summary_with_urls(d) for d in rows]


@router.post(
    "/{project_id}/edit",
    response_model=EditTriggerResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_project_edit(
    project_id: int,
    payload: EditTriggerRequest,
    session: SessionDep,
) -> EditTriggerResponse:
    """M5 — kick off the auto-edit pipeline for ``project_id``.

    Synchronously creates the Draft row in ``pending`` state and enqueues
    the render job; the worker adopts that row by id and flips it to
    ``processing`` when it picks it up. Returns 202 with the new draft id
    so the UI can start polling ``GET /drafts/{id}`` immediately without
    racing the worker. While a draft is already pending or processing,
    returns 409 unless ``force=true``.
    """
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")

    in_flight = (
        await session.execute(
            select(Draft)
            .where(Draft.project_id == project_id)
            .where(Draft.status.in_((DraftStatus.PENDING.value, DraftStatus.PROCESSING.value)))
            .order_by(Draft.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if in_flight is not None and not payload.force:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="another draft is already rendering for this project; "
            "pass force=true to start a new version anyway",
        )

    next_version = (
        int(
            (
                await session.scalar(
                    select(func.max(Draft.version)).where(Draft.project_id == project_id)
                )
            )
            or 0
        )
        + 1
    )
    # Mirror the worker's initial-progress shape (services.edit_orchestrator
    # uses the same map). Inlined here so the api container doesn't have to
    # import the orchestrator module.
    # v0.30.0 — resolve the smart-camera flag for the snapshot. Body
    # ``None`` ≡ inherit the project toggle; explicit ``True``/
    # ``False`` overrides for this run. Use ``is not None`` rather
    # than ``or`` because ``False`` is a meaningful explicit value
    # (v0.24.0 voice_volume=0 silent-drop lesson).
    effective_smart_camera = (
        payload.smart_camera
        if payload.smart_camera is not None
        else bool(getattr(project, "smart_camera_enabled", False))
    )
    new_draft = Draft(
        project_id=project_id,
        profile_name=project.profile_name,
        version=next_version,
        status=DraftStatus.PENDING.value,
        progress_steps_json=dict.fromkeys(EDIT_STEP_VALUES, "pending"),
        style_preset=payload.style_preset,
        # v0.21.1 — snapshot the operator's render-flag choices so the
        # skip-plan re-render endpoints (PATCH /drafts/{id}/order,
        # POST /drafts/{id}/rebuild-subtitles) can replay them instead
        # of silently defaulting every flag back to True.
        render_flags_json={
            "transitions": payload.transitions,
            "stabilize": payload.stabilize,
            "subtitles": payload.subtitles,
            "auto_reframe": payload.auto_reframe,
            # v0.30.11 — carried for orphan-watchdog retries that need
            # to recreate segments before a first render finishes.
            "initial_voice_volume": payload.initial_voice_volume,
            # v0.30.0 — snapshot the resolved smart-camera flag so a
            # later skip-plan re-render replays the same choice.
            "smart_camera": effective_smart_camera,
            "edit_mode": payload.edit_mode,
            "story_narration": payload.story_narration,
            "story_narration_fallback": payload.story_narration_fallback,
        },
    )
    session.add(new_draft)
    await session.commit()
    await session.refresh(new_draft)

    target_duration_ms = (
        payload.target_duration_seconds * 1000
        if payload.target_duration_seconds is not None
        else None
    )
    try:
        job_id = enqueue_project_edit(
            project_id,
            draft_id=new_draft.id,
            force=payload.force,
            target_duration_ms=target_duration_ms,
            stabilize=payload.stabilize,
            subtitles=payload.subtitles,
            transitions=payload.transitions,
            auto_reframe=payload.auto_reframe,
            initial_voice_volume=payload.initial_voice_volume,
            smart_camera=effective_smart_camera,
            style_preset=payload.style_preset,
            edit_mode=payload.edit_mode,
            story_narration=payload.story_narration,
            story_narration_fallback=payload.story_narration_fallback,
        )
    except Exception as exc:  # noqa: BLE001 — keep durable state truthful.
        new_draft.status = DraftStatus.FAILED.value
        new_draft.prompt_feedback = f"enqueue failed: {exc}"
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"edit enqueue failed: {exc}",
        ) from exc
    return EditTriggerResponse(
        project_id=project_id,
        draft_id=new_draft.id,
        job_id=job_id,
        status="enqueued",
    )


# M6.4 — BGM upload limits. Streamed to disk in BGM_CHUNK_BYTES blocks so a
# 50 MB file doesn't sit fully in RAM during the request.
BGM_MAX_BYTES = 50 * 1024 * 1024
BGM_CHUNK_BYTES = 1 * 1024 * 1024
BGM_ALLOWED_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}


@router.post("/{project_id}/bgm", response_model=ProjectDetail)
async def upload_project_bgm(
    project_id: int,
    session: SessionDep,
    file: UploadFile = File(...),  # noqa: B008
) -> ProjectDetail:
    """Upload (or replace) the project's background-music track.

    Single multipart POST — typical BGM is a few MB so chunked upload
    sessions are overkill. The file is streamed to disk in 1 MB chunks
    and rejected past 50 MB. Filename extension picks the on-disk
    extension; content_type is informational only.
    """
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")

    raw_name = (file.filename or "").lower()
    ext = "".join(Path(raw_name).suffixes[-1:]) if raw_name else ""
    if ext not in BGM_ALLOWED_EXTS:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"BGM must be one of {sorted(BGM_ALLOWED_EXTS)}; got {ext or 'no extension'!r}",
        )

    bgm_dir = Path(settings.bgm_dir)
    bgm_dir.mkdir(parents=True, exist_ok=True)
    target = bgm_dir / f"{project_id}{ext}"
    # Remove any prior BGM with a different extension so we don't leak files.
    for stale in bgm_dir.glob(f"{project_id}.*"):
        if stale != target:
            stale.unlink(missing_ok=True)

    written = 0
    with target.open("wb") as fh:
        while True:
            chunk = await file.read(BGM_CHUNK_BYTES)
            if not chunk:
                break
            written += len(chunk)
            if written > BGM_MAX_BYTES:
                fh.close()
                target.unlink(missing_ok=True)
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=f"BGM exceeds {BGM_MAX_BYTES // (1024 * 1024)} MB limit",
                )
            fh.write(chunk)
    if written == 0:
        target.unlink(missing_ok=True)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="empty BGM upload")

    project.bgm_path = str(target)
    await session.commit()
    await session.refresh(project)

    asset_count = await session.scalar(
        select(func.count(Asset.id)).where(Asset.project_id == project_id)
    )
    draft_count = await session.scalar(
        select(func.count(Draft.id)).where(Draft.project_id == project_id)
    )
    return _project_detail(
        project,
        asset_count=int(asset_count or 0),
        draft_count=int(draft_count or 0),
    )


# v0.18 — watermark / brand-logo overlay. Same shape as the BGM upload —
# single multipart POST, streamed to disk, capped well below the BGM
# limit because PNG logos are tiny in practice.
WATERMARK_MAX_BYTES = 5 * 1024 * 1024
WATERMARK_CHUNK_BYTES = 256 * 1024
# PNG only — keeps the alpha channel intact through the ffmpeg overlay
# chain. JPEG / WebP would silently lose transparency or need extra
# colorspace handling on libx264 input.
WATERMARK_ALLOWED_EXTS = {".png"}


@router.post("/{project_id}/watermark", response_model=ProjectDetail)
async def upload_project_watermark(
    project_id: int,
    session: SessionDep,
    file: UploadFile = File(...),  # noqa: B008
) -> ProjectDetail:
    """Upload (or replace) the project's brand watermark PNG.

    The picker UI sends a single multipart POST. The PNG is streamed to
    ``${WATERMARK_DIR}/{project_id}.png`` in 256 KB chunks (cap is 5 MB —
    a brand logo never legitimately exceeds that). Existing files at the
    same path are overwritten so re-uploading the same project replaces
    the artwork in place.
    """
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")

    raw_name = (file.filename or "").lower()
    ext = "".join(Path(raw_name).suffixes[-1:]) if raw_name else ""
    if ext not in WATERMARK_ALLOWED_EXTS:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=(
                f"watermark must be PNG (one of {sorted(WATERMARK_ALLOWED_EXTS)});"
                f" got {ext or 'no extension'!r}"
            ),
        )

    wm_dir = Path(settings.watermark_dir)
    wm_dir.mkdir(parents=True, exist_ok=True)
    target = wm_dir / f"{project_id}{ext}"

    written = 0
    with target.open("wb") as fh:
        while True:
            chunk = await file.read(WATERMARK_CHUNK_BYTES)
            if not chunk:
                break
            written += len(chunk)
            if written > WATERMARK_MAX_BYTES:
                fh.close()
                target.unlink(missing_ok=True)
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=f"watermark exceeds {WATERMARK_MAX_BYTES // (1024 * 1024)} MB limit",
                )
            fh.write(chunk)
    if written == 0:
        target.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="empty watermark upload"
        )

    project.watermark_path = str(target)
    await session.commit()
    await session.refresh(project)

    asset_count = await session.scalar(
        select(func.count(Asset.id)).where(Asset.project_id == project_id)
    )
    draft_count = await session.scalar(
        select(func.count(Draft.id)).where(Draft.project_id == project_id)
    )
    return _project_detail(
        project,
        asset_count=int(asset_count or 0),
        draft_count=int(draft_count or 0),
    )


@router.patch("/{project_id}/watermark", response_model=ProjectDetail)
async def update_project_watermark(
    project_id: int,
    payload: WatermarkSettingsPatch,
    session: SessionDep,
) -> ProjectDetail:
    """Update watermark layout (position / scale / opacity) without
    re-uploading the PNG. All three fields are independent partial
    updates — omit a field to leave it unchanged. Defaults are kept
    even when no PNG is uploaded so a future upload picks them up.
    """
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")

    if payload.position is not None:
        project.watermark_position = payload.position
    if payload.scale is not None:
        project.watermark_scale = float(payload.scale)
    if payload.opacity is not None:
        project.watermark_opacity = float(payload.opacity)
    await session.commit()
    await session.refresh(project)

    asset_count = await session.scalar(
        select(func.count(Asset.id)).where(Asset.project_id == project_id)
    )
    draft_count = await session.scalar(
        select(func.count(Draft.id)).where(Draft.project_id == project_id)
    )
    return _project_detail(
        project,
        asset_count=int(asset_count or 0),
        draft_count=int(draft_count or 0),
    )


@router.delete("/{project_id}/watermark", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project_watermark(
    project_id: int,
    session: SessionDep,
) -> None:
    """Remove the project's watermark PNG. Idempotent — 204 even if none
    set. Layout settings (position / scale / opacity) are intentionally
    preserved so a re-upload picks up the previous configuration.
    """
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")
    if project.watermark_path:
        Path(project.watermark_path).unlink(missing_ok=True)
        project.watermark_path = None
        await session.commit()


# v0.21.6 — apply a saved preset to this project. Reverse direction
# of POST /watermark-presets (which copies project → preset). The
# preset's own PNG file is the source of truth so deleting the
# preset later doesn't strip the project's watermark.
@router.post(
    "/{project_id}/watermark/apply-preset",
    response_model=ProjectDetail,
)
async def apply_watermark_preset(
    project_id: int,
    payload: WatermarkPresetApplyRequest,
    session: SessionDep,
) -> ProjectDetail:
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")
    preset, src_path = await _load_preset_for_apply(session, payload.preset_id)

    wm_dir = Path(settings.watermark_dir)
    wm_dir.mkdir(parents=True, exist_ok=True)
    target = wm_dir / f"{project_id}.png"
    try:
        shutil.copyfile(src_path, target)
    except OSError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"failed to copy preset into project watermark: {exc}",
        ) from exc

    project.watermark_path = str(target)
    project.watermark_position = preset.position
    project.watermark_scale = float(preset.scale)
    project.watermark_opacity = float(preset.opacity)
    await session.commit()
    await session.refresh(project)

    asset_count = await session.scalar(
        select(func.count(Asset.id)).where(Asset.project_id == project_id)
    )
    draft_count = await session.scalar(
        select(func.count(Draft.id)).where(Draft.project_id == project_id)
    )
    return _project_detail(
        project,
        asset_count=int(asset_count or 0),
        draft_count=int(draft_count or 0),
    )


@router.delete("/{project_id}/bgm", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project_bgm(
    project_id: int,
    session: SessionDep,
) -> None:
    """Remove the project's BGM track. Idempotent — 204 even if none set."""
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")
    if project.bgm_path:
        Path(project.bgm_path).unlink(missing_ok=True)
        project.bgm_path = None
        await session.commit()


# v0.21 — list classes detected across this project's assets. Powers
# the SubjectClassPicker dropdown so the user only sees classes that
# actually appear in their footage, sorted by total frame count so the
# most common subject is the natural first pick. Empty list when no
# asset has been tracked yet — the UI surfaces "complete tracking
# first" rather than offering a hard-coded 80-class menu.
@router.get(
    "/{project_id}/detected-classes",
    response_model=list[DetectedClassOut],
)
async def list_detected_classes(
    project_id: int,
    session: SessionDep,
) -> list[DetectedClassOut]:
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")
    rows = (
        (await session.execute(select(Asset.tracking_json).where(Asset.project_id == project_id)))
        .scalars()
        .all()
    )
    summaries = aggregate_detected_classes(list(rows))
    return [DetectedClassOut(**s) for s in summaries]


# v0.21 — subject-class PATCH. ``subject_class=None`` clears the filter
# (planner uses every asset at full duration); a non-null value is
# validated against the COCO-80 class list by SubjectClassPatch so we
# don't store strings the renderer's tracking_json lookup will miss.
@router.patch("/{project_id}/subject-class", response_model=ProjectDetail)
async def patch_project_subject_class(
    project_id: int,
    payload: SubjectClassPatch,
    session: SessionDep,
) -> ProjectDetail:
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")
    project.subject_class = payload.subject_class
    await session.commit()
    await session.refresh(project)

    asset_count = await session.scalar(
        select(func.count(Asset.id)).where(Asset.project_id == project_id)
    )
    draft_count = await session.scalar(
        select(func.count(Draft.id)).where(Draft.project_id == project_id)
    )
    return _project_detail(
        project,
        asset_count=int(asset_count or 0),
        draft_count=int(draft_count or 0),
    )


# v0.29.0 — static-crop anchor PATCH. Body shape:
#   ``{x_norm: float | null, y_norm: float | null}``
# When BOTH fields are null (or omitted) the override is cleared
# and the renderer falls back to centre. When BOTH are present they
# must each be in [0, 1]; mixed (one null, one not) returns 400 to
# avoid silently writing an undefined-anchor row.
#
# The crop is applied at render time inside ``aspect_filter`` and
# only kicks in for the static aspect-crop path — auto-reframe
# tracking paths (YOLO / point / custom_roi) keep doing their
# subject-centred crop because they already know better than the
# operator-picked anchor where the action is.
@router.patch("/{project_id}/crop-region", response_model=ProjectDetail)
async def patch_project_crop_region(
    project_id: int,
    payload: CropRegionPatch,
    session: SessionDep,
) -> ProjectDetail:
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")
    x = payload.x_norm
    y = payload.y_norm
    if x is None and y is None:
        # Explicit clear → revert to centre. Storing NULL keeps the
        # column compact + the renderer's "is None" branch fast.
        project.crop_region_json = None
    elif x is None or y is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="crop_region: both x_norm and y_norm must be provided (or both null to clear)",
        )
    else:
        project.crop_region_json = {"x_norm": float(x), "y_norm": float(y)}
    await session.commit()
    await session.refresh(project)
    asset_count = await session.scalar(
        select(func.count(Asset.id)).where(Asset.project_id == project_id)
    )
    draft_count = await session.scalar(
        select(func.count(Draft.id)).where(Draft.project_id == project_id)
    )
    return _project_detail(
        project,
        asset_count=int(asset_count or 0),
        draft_count=int(draft_count or 0),
    )


# v0.30.0 — opt-in AI Smart Camera toggle. Single-field PATCH, body
# is ``{"enabled": bool}``. Default ``False`` (we ship the feature
# off so existing operators don't suddenly burn extra Gemini quota
# without asking). Renderer reads ``Project.smart_camera_enabled``
# on every render through the orchestrator's
# ``_resolve_smart_camera_flag`` resolver, which also honours an
# ``EditTriggerRequest.smart_camera`` per-run override.
@router.patch("/{project_id}/smart-camera", response_model=ProjectDetail)
async def patch_project_smart_camera(
    project_id: int,
    payload: SmartCameraPatch,
    session: SessionDep,
) -> ProjectDetail:
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")
    project.smart_camera_enabled = bool(payload.enabled)
    await session.commit()
    await session.refresh(project)
    asset_count = await session.scalar(
        select(func.count(Asset.id)).where(Asset.project_id == project_id)
    )
    draft_count = await session.scalar(
        select(func.count(Draft.id)).where(Draft.project_id == project_id)
    )
    return _project_detail(
        project,
        asset_count=int(asset_count or 0),
        draft_count=int(draft_count or 0),
    )


# v0.24.0 — BGM tail-fade duration. Single-field PATCH, body is
# ``{"fade_out_sec": float}`` clamped 0..10 server-side. The mixer
# reads ``Project.bgm_fade_out_sec`` on every render — no separate
# render trigger needed; next re-render picks it up.
@router.patch("/{project_id}/bgm-fade-out", response_model=ProjectDetail)
async def patch_project_bgm_fade_out(
    project_id: int,
    payload: BgmFadeOutPatch,
    session: SessionDep,
) -> ProjectDetail:
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")
    project.bgm_fade_out_sec = float(payload.fade_out_sec)
    await session.commit()
    await session.refresh(project)

    asset_count = await session.scalar(
        select(func.count(Asset.id)).where(Asset.project_id == project_id)
    )
    draft_count = await session.scalar(
        select(func.count(Draft.id)).where(Draft.project_id == project_id)
    )
    return _project_detail(
        project,
        asset_count=int(asset_count or 0),
        draft_count=int(draft_count or 0),
    )


# v0.18 — subtitle style PATCH. Every field is optional so the UI can
# send a partial diff; unspecified fields keep whatever the project
# already has. Returns the updated ProjectDetail so the frontend can
# refresh its local state in one round-trip.
@router.patch("/{project_id}/subtitle-style", response_model=ProjectDetail)
async def patch_project_subtitle_style(
    project_id: int,
    payload: SubtitleStylePatch,
    session: SessionDep,
) -> ProjectDetail:
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")
    diff = payload.model_dump(exclude_unset=True)
    if not diff:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="no fields to update",
        )
    for field, value in diff.items():
        setattr(project, field, value)
    await session.commit()
    await session.refresh(project)

    asset_count = await session.scalar(
        select(func.count(Asset.id)).where(Asset.project_id == project_id)
    )
    draft_count = await session.scalar(
        select(func.count(Draft.id)).where(Draft.project_id == project_id)
    )
    return _project_detail(
        project,
        asset_count=int(asset_count or 0),
        draft_count=int(draft_count or 0),
    )


@router.get("/{project_id}/script", response_model=ScriptOut)
async def get_project_script(
    project_id: int,
    session: SessionDep,
) -> ScriptOut:
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")
    row = (
        await session.execute(select(Script).where(Script.project_id == project_id))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="script not set")
    return ScriptOut.model_validate(row)


@router.put("/{project_id}/script", response_model=ScriptOut)
async def upsert_project_script(
    project_id: int,
    payload: ScriptUpsert,
    session: SessionDep,
) -> ScriptOut:
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")
    row = (
        await session.execute(select(Script).where(Script.project_id == project_id))
    ).scalar_one_or_none()
    now = datetime.now(UTC)
    if row is None:
        row = Script(
            project_id=project_id,
            body=payload.body,
            source_filename=payload.source_filename,
            updated_at=now,
        )
        session.add(row)
    else:
        row.body = payload.body
        row.source_filename = payload.source_filename
        row.updated_at = now
    await session.commit()
    await session.refresh(row)

    # M4 — invalidate any prior coverage rows for this project's assets so
    # the next analyze run recomputes them against the new script body.
    asset_ids = [
        a_id
        for (a_id,) in (
            await session.execute(select(Asset.id).where(Asset.project_id == project_id))
        ).all()
    ]
    if asset_ids:
        await session.execute(delete(ScriptCoverage).where(ScriptCoverage.asset_id.in_(asset_ids)))
        await session.commit()
    return ScriptOut.model_validate(row)


@router.get("/{project_id}/story-script", response_model=StoryScriptOut)
async def get_project_story_script(
    project_id: int,
    session: SessionDep,
) -> StoryScriptOut:
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")
    row = await story_scripts.latest_story_script(session, project_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="story script not generated"
        )
    return _story_script_out(row)


@router.post("/{project_id}/story-script/generate", response_model=StoryScriptOut)
async def generate_project_story_script(
    project_id: int,
    payload: StoryScriptGenerateRequest,
    session: SessionDep,
) -> StoryScriptOut:
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")
    try:
        document = await story_scripts.generate_story_script(
            session,
            project_id,
            target_items=payload.target_items,
        )
        row = await story_scripts.save_story_script(session, document)
    except story_scripts.StoryScriptInputError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except story_scripts.StoryScriptValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except story_scripts.StoryScriptError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return _story_script_out(row)


@router.put("/{project_id}/story-script", response_model=StoryScriptOut)
async def save_project_story_script(
    project_id: int,
    payload: StoryScriptSaveRequest,
    session: SessionDep,
) -> StoryScriptOut:
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")
    assets = (
        (await session.execute(select(Asset).where(Asset.project_id == project_id))).scalars().all()
    )
    asset_durations = {asset.id: int(asset.duration_ms) for asset in assets}
    raw = {
        "schema_version": story_scripts.STORY_SCRIPT_SCHEMA_VERSION,
        "project_id": project_id,
        "title": payload.title,
        "summary": payload.summary,
        "items": [item.model_dump() for item in payload.items],
    }
    try:
        document = story_scripts.validate_story_script(
            raw,
            project_id=project_id,
            asset_durations=asset_durations,
        )
        document = story_scripts.StoryScriptDocument(
            project_id=document.project_id,
            title=document.title,
            summary=document.summary,
            items=document.items,
            metadata={"source": "manual_edit", "used_visual_context": False},
        )
        row = await story_scripts.save_story_script(session, document)
    except story_scripts.StoryScriptValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return _story_script_out(row)


# ----- M4 — project analysis page polling endpoint -----


def _filename_from_path(file_path: str) -> str:
    return Path(file_path).name


def _scene_tags_for(asset: Asset) -> list[SceneTagOut]:
    return sorted(
        [
            SceneTagOut(name=t.tag_name, confidence=t.confidence)
            for t in asset.tags
            if t.tag_type == "scene"
        ],
        key=lambda t: t.confidence,
        reverse=True,
    )


def _motion_segments_for(asset: Asset) -> list[MotionSegmentOut]:
    out: list[MotionSegmentOut] = []
    for tag in asset.tags:
        if tag.tag_type != "motion":
            continue
        ranges = list(tag.time_ranges_ms or [])
        for r in ranges:
            if not isinstance(r, list | tuple) or len(r) != 2:
                continue
            try:
                out.append(
                    MotionSegmentOut(
                        motion_type=tag.tag_name,  # type: ignore[arg-type]
                        start_ms=int(r[0]),
                        end_ms=int(r[1]),
                    )
                )
            except (TypeError, ValueError):
                continue
    out.sort(key=lambda m: m.start_ms)
    return out


_EMOTION_TAG_NAMES: frozenset[str] = frozenset({"happy", "surprised", "serious", "neutral"})


def _emotion_tags_for(asset: Asset) -> EmotionTagsOut | None:
    """Collapse the ``emotion``-typed AssetTag rows into the API shape.

    ``tag_name="dominant"`` rows stash the dominant class string in
    ``time_ranges_ms[0]`` (see ``services.analysis._run_emotion``); the
    other rows store actual time ranges per class. Returns None when
    the emotion stage hasn't produced any data so the UI can hide the
    chip rather than show an "(empty)" pill.
    """
    dominant: str | None = None
    ranges: list[EmotionRangeOut] = []
    saw_emotion_row = False
    for tag in asset.tags:
        if tag.tag_type != "emotion":
            continue
        saw_emotion_row = True
        if tag.tag_name == "dominant":
            stash = list(tag.time_ranges_ms or [])
            if stash and isinstance(stash[0], str) and stash[0] in _EMOTION_TAG_NAMES:
                dominant = stash[0]
            continue
        if tag.tag_name not in _EMOTION_TAG_NAMES:
            continue
        for r in list(tag.time_ranges_ms or []):
            if not isinstance(r, list | tuple) or len(r) != 2:
                continue
            try:
                ranges.append(
                    EmotionRangeOut(
                        emotion=tag.tag_name,  # type: ignore[arg-type]
                        start_ms=int(r[0]),
                        end_ms=int(r[1]),
                    )
                )
            except (TypeError, ValueError):
                continue
    if not saw_emotion_row:
        return None
    ranges.sort(key=lambda r: r.start_ms)
    return EmotionTagsOut(dominant=dominant or "neutral", ranges=ranges)  # type: ignore[arg-type]


def _tracking_summary_for(asset: Asset) -> TrackingSummaryOut | None:
    """v0.16 — surface a minimal YOLO tracking verdict for the analysis page.

    The full per-frame bbox track in ``Asset.tracking_json`` is way too
    chatty for the polling endpoint; we only return the headline fields
    (subject class + confidence + how many frames carry data) so the UI
    can render a single chip like "追蹤：汽車（92%, 142 幀）".
    """
    blob = getattr(asset, "tracking_json", None)
    if not isinstance(blob, dict):
        return None
    frames = blob.get("frames")
    return TrackingSummaryOut(
        subject_class=str(blob.get("subject_class") or ""),
        confidence=float(blob.get("confidence") or 0.0),
        frame_count=len(frames) if isinstance(frames, list) else 0,
        sampled_frames=int(blob.get("sampled_frames") or 0),
    )


def _secondary_subtitle_summary_for(asset: Asset) -> SecondarySubtitleSummaryOut | None:
    """v0.18 — surface whether a secondary-language translation is available.

    Returns ``None`` when ``Asset.subtitle_secondary_lang`` is unset
    (the asset has not been run through Whisper translate). Otherwise
    a small chip-friendly payload with the language code + segment
    count so the UI can show e.g. "EN · 24 段".
    """
    lang = getattr(asset, "subtitle_secondary_lang", None)
    if not lang:
        return None
    segments = getattr(asset, "subtitle_secondary_segments_json", None) or []
    return SecondarySubtitleSummaryOut(
        lang=str(lang),
        segment_count=len(segments) if isinstance(segments, list) else 0,
    )


# v0.26.0 — batch delete. Body lists Asset.id rows to drop; the
# response is a per-row outcome map so the FE can show "1 succeeded,
# 2 blocked because v3 is still using them." A blocking row inside
# the batch doesn't fail the request — successful rows still
# commit; the FE just lists the refused ones.
#
# v0.27.1 adds the ``?force=true`` query flag matching the single-
# asset endpoint. With force=False (default), rows whose deletion
# would invalidate an active draft come back with ``deleted=False``
# and a populated ``affected_drafts`` list — the FE prompts the user
# and re-issues the same body with ``?force=true``. With force=True,
# the per-asset path wipes those drafts' segments and flips the
# emptied drafts to ``failed`` before deleting the asset.
@router.delete("/{project_id}/assets/batch", response_model=AssetBatchDeleteOut)
async def batch_delete_project_assets(
    project_id: int,
    payload: AssetBatchDeleteRequest,
    session: SessionDep,
    force: bool = False,
) -> AssetBatchDeleteOut:
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")
    # Only allow deletion of assets that actually belong to this
    # project — a defensive narrow because the IDs come straight
    # from the request body. SQL injection isn't the concern (they
    # come through Pydantic + ORM); cross-project deletes are.
    valid_rows = (
        (
            await session.execute(
                select(Asset.id).where(
                    Asset.project_id == project_id,
                    Asset.id.in_(payload.asset_ids),
                )
            )
        )
        .scalars()
        .all()
    )
    valid_ids = set(valid_rows)
    results: list[AssetBatchDeleteResultItem] = []
    for asset_id in payload.asset_ids:
        if asset_id not in valid_ids:
            results.append(
                AssetBatchDeleteResultItem(
                    asset_id=asset_id,
                    deleted=False,
                    reason="asset not in this project",
                )
            )

    outcomes = await asset_mgmt.batch_delete_assets(session, sorted(valid_ids), force=force)
    for asset_id, result in outcomes.items():
        if result.not_found:
            reason = "not found"
        elif result.error_message is not None:
            reason = result.error_message
        elif not result.deleted and result.affected_drafts:
            versions = ", ".join(f"v{b.version}" for b in result.affected_drafts)
            reason = f"still used by active draft(s): {versions}"
        else:
            reason = None
        results.append(
            AssetBatchDeleteResultItem(
                asset_id=asset_id,
                deleted=result.deleted,
                affected_drafts=[
                    AffectedDraftOut(
                        draft_id=b.draft_id,
                        version=b.version,
                        status=b.status,
                    )
                    for b in result.affected_drafts
                ],
                invalidated_versions=list(result.invalidated_versions),
                reason=reason,
            )
        )

    await session.commit()

    deleted = sum(1 for r in results if r.deleted)
    needs_force = sum(1 for r in results if not r.deleted and r.affected_drafts)
    blocked = len(results) - deleted
    return AssetBatchDeleteOut(
        deleted_count=deleted,
        blocked_count=blocked,
        needs_force_count=needs_force,
        error_count=blocked - needs_force,
        results=results,
    )


@router.post(
    "/{project_id}/assets/stabilize",
    response_model=ProjectAssetStabilizeBatchResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def batch_stabilize_project_assets(
    project_id: int,
    payload: ProjectAssetStabilizeBatchRequest,
    session: SessionDep,
) -> ProjectAssetStabilizeBatchResponse:
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")

    assets = (
        (
            await session.execute(
                select(Asset).where(Asset.project_id == project_id).order_by(Asset.id)
            )
        )
        .scalars()
        .all()
    )
    results: list[ProjectAssetStabilizeBatchItem] = []
    enqueued_count = 0
    skipped_count = 0
    failed_count = 0
    skip_statuses = {
        asset_variants.STABILIZATION_PENDING,
        asset_variants.STABILIZATION_RUNNING,
    }
    if not payload.force:
        skip_statuses.add(asset_variants.STABILIZATION_DONE)
        skip_statuses.add(asset_variants.STABILIZATION_SKIPPED)

    for asset in assets:
        current_status = asset_variants.stabilization_status(asset)
        if current_status in skip_statuses:
            skipped_count += 1
            results.append(
                ProjectAssetStabilizeBatchItem(
                    asset_id=asset.id,
                    status="skipped",
                    reason=current_status,
                )
            )
            continue

        asset.stabilization_status = asset_variants.STABILIZATION_PENDING
        asset.stabilization_error = None
        asset.stabilized_path = str(asset_variants.stabilized_path_for_asset(asset))
        await session.commit()
        try:
            job_id = enqueue_asset_stabilization(asset.id, force=payload.force)
        except Exception as exc:  # noqa: BLE001 - continue with remaining assets.
            asset.stabilization_status = asset_variants.STABILIZATION_FAILED
            asset.stabilization_error = f"enqueue failed: {exc}"
            await session.commit()
            failed_count += 1
            results.append(
                ProjectAssetStabilizeBatchItem(
                    asset_id=asset.id,
                    status="failed",
                    reason=asset.stabilization_error,
                )
            )
            continue

        enqueued_count += 1
        results.append(
            ProjectAssetStabilizeBatchItem(
                asset_id=asset.id,
                status="enqueued",
                job_id=job_id,
            )
        )

    return ProjectAssetStabilizeBatchResponse(
        project_id=project_id,
        enqueued_count=enqueued_count,
        skipped_count=skipped_count,
        failed_count=failed_count,
        results=results,
    )


@router.get("/{project_id}/assets", response_model=ProjectAnalysisOut)
async def list_project_assets_with_analysis(
    project_id: int,
    session: SessionDep,
) -> ProjectAnalysisOut:
    """Drives the mobile-first /projects/:id/assets polling page.

    Returns the project, whether a script is set, and per-asset analysis
    state (status, per-step bookkeeping, transcript summary, scene/motion
    tags, coverage summary). All in one round-trip so the polling hook
    only hits one endpoint.
    """
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")

    asset_count = await session.scalar(
        select(func.count(Asset.id)).where(Asset.project_id == project_id)
    )
    draft_count = await session.scalar(
        select(func.count(Draft.id)).where(Draft.project_id == project_id)
    )
    project_detail = _project_detail(
        project,
        asset_count=int(asset_count or 0),
        draft_count=int(draft_count or 0),
    )

    script_row = (
        await session.execute(select(Script).where(Script.project_id == project_id))
    ).scalar_one_or_none()
    has_script = bool(script_row and (script_row.body or "").strip())

    assets = (
        (
            await session.execute(
                select(Asset)
                .where(Asset.project_id == project_id)
                .options(selectinload(Asset.tags))
                .order_by(Asset.id.asc())
            )
        )
        .scalars()
        .all()
    )

    asset_ids = [a.id for a in assets]
    transcripts: dict[int, AssetTranscript] = {}
    coverage: dict[int, ScriptCoverage] = {}
    if asset_ids:
        transcript_rows = (
            (
                await session.execute(
                    select(AssetTranscript).where(AssetTranscript.asset_id.in_(asset_ids))
                )
            )
            .scalars()
            .all()
        )
        transcripts = {t.asset_id: t for t in transcript_rows}
        coverage_rows = (
            (
                await session.execute(
                    select(ScriptCoverage).where(ScriptCoverage.asset_id.in_(asset_ids))
                )
            )
            .scalars()
            .all()
        )
        coverage = {c.asset_id: c for c in coverage_rows}

    items: list[AssetAnalysisItem] = []
    for asset in assets:
        tx = transcripts.get(asset.id)
        cov = coverage.get(asset.id)
        # v0.26.0 — stat the on-disk file for the size column. Best-
        # effort: a missing source file (manually pruned, never
        # uploaded fully) gives ``None`` and the FE renders a "—"
        # placeholder. We don't cache this on the Asset row because
        # the file lifecycle is owned by the upload + delete paths
        # and a stale stored size would lie about the current state.
        file_size_bytes: int | None = None
        if asset.file_path:
            try:
                file_size_bytes = asset_variants.selected_media_path(asset).stat().st_size
            except OSError:
                file_size_bytes = None
        items.append(
            AssetAnalysisItem(
                id=asset.id,
                file_path=asset.file_path,
                filename=_filename_from_path(asset.file_path),
                active_asset_variant=asset_variants.active_variant(asset),
                stabilized_path=getattr(asset, "stabilized_path", None),
                stabilization_status=asset_variants.stabilization_status(asset),
                stabilization_error=getattr(asset, "stabilization_error", None),
                variant_urls=asset_variants.variant_urls(asset),
                duration_ms=asset.duration_ms,
                resolution=asset.resolution,
                file_size_bytes=file_size_bytes,
                status=asset.status,
                analysis_steps=dict(asset.analysis_steps_json or {}) or None,
                transcript_summary=(
                    TranscriptSummaryOut(
                        segment_count=len(list(tx.segments_json or [])),
                        edited=tx.edited,
                        updated_at=tx.updated_at,
                    )
                    if tx is not None
                    else None
                ),
                coverage_summary=(
                    CoverageSummaryOut(
                        coverage_ratio_by_count=cov.coverage_ratio_by_count,
                        coverage_ratio_by_duration_ms=cov.coverage_ratio_by_duration_ms,
                        scripted_segment_count=cov.scripted_segment_count,
                        total_segment_count=cov.total_segment_count,
                    )
                    if cov is not None
                    else None
                ),
                scene_tags=_scene_tags_for(asset),
                motion_segments=_motion_segments_for(asset),
                emotion_tags=_emotion_tags_for(asset),
                tracking_summary=_tracking_summary_for(asset),
                secondary_subtitle_summary=_secondary_subtitle_summary_for(asset),
                thumbnail_urls=thumbnail_urls_for_asset(asset.id),
            )
        )

    latest_draft_row = (
        await session.execute(
            select(Draft)
            .where(Draft.project_id == project_id)
            .order_by(Draft.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    latest_draft = (
        _draft_summary_with_urls(latest_draft_row) if latest_draft_row is not None else None
    )

    return ProjectAnalysisOut(
        project=project_detail,
        has_script=has_script,
        assets=items,
        latest_draft=latest_draft,
    )
