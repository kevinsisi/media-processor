"""Project endpoints — list, detail, create, drafts, script, analysis page."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from media_processor.api.config import settings
from media_processor.api.deps import get_session
from media_processor.api.routers.assets import thumbnail_urls_for_asset
from media_processor.api.routers.drafts import _draft_url, _expected_draft_path
from media_processor.api.schemas import (
    AssetAnalysisItem,
    CoverageSummaryOut,
    DraftSummary,
    EditTriggerRequest,
    EditTriggerResponse,
    EmotionRangeOut,
    EmotionTagsOut,
    MotionSegmentOut,
    ProjectAnalysisOut,
    ProjectCreate,
    ProjectDetail,
    ProjectSummary,
    SceneTagOut,
    ScriptOut,
    ScriptUpsert,
    SubtitleStylePatch,
    TrackingSummaryOut,
    TranscriptSummaryOut,
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
)
from media_processor.services.queue import enqueue_project_edit

router = APIRouter(prefix="/projects", tags=["projects"])


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
    )


SessionDep = Annotated[AsyncSession, Depends(get_session)]


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
        subtitle_font=project.subtitle_font,  # type: ignore[arg-type]
        subtitle_color=project.subtitle_color,
        subtitle_outline_color=project.subtitle_outline_color,
        subtitle_position=project.subtitle_position,  # type: ignore[arg-type]
        subtitle_size=project.subtitle_size,  # type: ignore[arg-type]
        subtitle_outline_width=project.subtitle_outline_width,  # type: ignore[arg-type]
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
    new_draft = Draft(
        project_id=project_id,
        profile_name=project.profile_name,
        version=next_version,
        status=DraftStatus.PENDING.value,
        progress_steps_json=dict.fromkeys(EDIT_STEP_VALUES, "pending"),
    )
    session.add(new_draft)
    await session.commit()
    await session.refresh(new_draft)

    target_duration_ms = (
        payload.target_duration_seconds * 1000
        if payload.target_duration_seconds is not None
        else None
    )
    job_id = enqueue_project_edit(
        project_id,
        draft_id=new_draft.id,
        force=payload.force,
        target_duration_ms=target_duration_ms,
        stabilize=payload.stabilize,
        subtitles=payload.subtitles,
        transitions=payload.transitions,
        auto_reframe=payload.auto_reframe,
    )
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
        items.append(
            AssetAnalysisItem(
                id=asset.id,
                file_path=asset.file_path,
                filename=_filename_from_path(asset.file_path),
                duration_ms=asset.duration_ms,
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
