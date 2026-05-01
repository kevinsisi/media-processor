"""Project endpoints — list, detail, create, drafts, script."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from media_processor.api.config import settings
from media_processor.api.deps import get_session
from media_processor.api.schemas import (
    DraftSummary,
    ProjectCreate,
    ProjectDetail,
    ProjectSummary,
    ScriptOut,
    ScriptUpsert,
)
from media_processor.models import Asset, Draft, Project, Script

router = APIRouter(prefix="/projects", tags=["projects"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


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
    return ProjectDetail(
        id=project.id,
        name=project.name,
        client=project.client,
        profile_name=project.profile_name,
        source_dir=project.source_dir,
        status=project.status,
        target_aspect_ratio=project.target_aspect_ratio,
        created_at=project.created_at,
        asset_count=0,
        draft_count=0,
    )


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
    return ProjectDetail(
        id=project.id,
        name=project.name,
        client=project.client,
        profile_name=project.profile_name,
        source_dir=project.source_dir,
        status=project.status,
        target_aspect_ratio=project.target_aspect_ratio,
        created_at=project.created_at,
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
    return [DraftSummary.model_validate(d) for d in rows]


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
    now = datetime.now(timezone.utc)
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
    return ScriptOut.model_validate(row)
