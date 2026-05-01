"""UploadSession ORM entity — chunked-upload state, the source of truth for resume."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from media_processor.models.base import Base
from media_processor.models.enums import (
    UPLOAD_KIND_VALUES,
    UPLOAD_STATUS_VALUES,
    UploadStatus,
)


def _sql_in_list(values: tuple[str, ...]) -> str:
    quoted = ",".join(f"'{v}'" for v in values)
    return f"({quoted})"


def _new_session_id() -> str:
    return uuid.uuid4().hex


class UploadSession(Base):
    __tablename__ = "upload_sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_session_id)
    project_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    total_size: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_size: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    received_chunks: Mapped[Any] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=UploadStatus.PENDING.value,
        server_default=UploadStatus.PENDING.value,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    __table_args__ = (
        CheckConstraint(
            "kind IN " + _sql_in_list(UPLOAD_KIND_VALUES),
            name="ck_upload_sessions_kind",
        ),
        CheckConstraint(
            "status IN " + _sql_in_list(UPLOAD_STATUS_VALUES),
            name="ck_upload_sessions_status",
        ),
        CheckConstraint("total_size >= 0", name="ck_upload_sessions_total_size"),
        CheckConstraint("chunk_size > 0", name="ck_upload_sessions_chunk_size"),
    )
