from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CaptureSession(Base):
    __tablename__ = "capture_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    code: Mapped[str] = mapped_column(String(7), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="waiting_for_phone", index=True)
    desktop_token_hash: Mapped[str] = mapped_column(String(64))
    phone_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    total_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    revision: Mapped[int] = mapped_column(default=0)

    photos: Mapped[list["CapturePhoto"]] = relationship(
        back_populates="capture_session", cascade="all, delete-orphan"
    )


class CapturePhoto(Base):
    __tablename__ = "capture_photos"
    __table_args__ = (
        UniqueConstraint("capture_session_id", "sha256", name="uq_capture_photo_hash"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    capture_session_id: Mapped[str] = mapped_column(
        ForeignKey("capture_sessions.id", ondelete="CASCADE"), index=True
    )
    original_name: Mapped[str] = mapped_column(String(255))
    storage_key: Mapped[str] = mapped_column(String(512), unique=True)
    media_type: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    capture_session: Mapped[CaptureSession] = relationship(back_populates="photos")
