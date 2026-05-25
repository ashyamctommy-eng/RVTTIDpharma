"""
models/resource.py
------------------
SQLAlchemy ORM model for academic resource metadata.
"""

from datetime import datetime, timezone

from sqlalchemy import DateTime, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from core.database import Base


class Resource(Base):
    __tablename__ = "resources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True, autoincrement=True)

    title: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
        doc="Human-readable document title (e.g., 'Pharmaceutics I: Introduction to Dosage Forms')",
    )

    subject: Mapped[str] = mapped_column(
        String(256),
        nullable=False,
        index=True,
        doc="Academic subject code or name (e.g., 'Pharmacology', 'Biochemistry')",
    )

    semester: Mapped[str] = mapped_column(
        String(8),
        nullable=False,
        index=True,
        doc="Semester identifier – constrained to Y1S1 / Y1S2 / Y2S1 / Y2S2 / Y3S1 / Y3S2",
    )

    file_name: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
        unique=True,
        doc="Sanitized filename as persisted on disk",
    )

    file_path: Mapped[str] = mapped_column(
        String(1024),
        nullable=False,
        doc="Relative URL path served by the static file mount",
    )

    upload_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        doc="UTC timestamp of when this resource was uploaded",
    )

    # ── Composite index for the most common compound filter ─────────────────
    __table_args__ = (
        Index("ix_resources_semester_subject", "semester", "subject"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<Resource id={self.id!r} title={self.title!r} "
            f"subject={self.subject!r} semester={self.semester!r}>"
        )
