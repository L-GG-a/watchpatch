from datetime import UTC, datetime

from sqlalchemy import DateTime
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    # SQLite uses naive timestamps; all persisted times are UTC.
    return datetime.now(UTC).replace(tzinfo=None)


class Monitor(SQLModel, table=True):
    __tablename__ = "monitors"

    id: int | None = Field(default=None, primary_key=True)
    name: str
    url: str
    keywords: str = "[]"
    interval_minutes: int = 60
    enabled: bool = True
    last_hash: str | None = None
    last_checked_at: datetime | None = Field(default=None, sa_type=DateTime)
    last_status: str | None = None
    last_error: str | None = None
    created_at: datetime = Field(default_factory=utcnow, sa_type=DateTime)
    updated_at: datetime = Field(default_factory=utcnow, sa_type=DateTime)


class Snapshot(SQLModel, table=True):
    __tablename__ = "snapshots"

    id: int | None = Field(default=None, primary_key=True)
    monitor_id: int = Field(foreign_key="monitors.id", ondelete="CASCADE", index=True)
    content: str
    content_hash: str = Field(index=True)
    created_at: datetime = Field(default_factory=utcnow, sa_type=DateTime)


class Event(SQLModel, table=True):
    __tablename__ = "events"

    id: int | None = Field(default=None, primary_key=True)
    monitor_id: int = Field(foreign_key="monitors.id", ondelete="CASCADE", index=True)
    old_snapshot_id: int | None = Field(default=None, foreign_key="snapshots.id")
    # A failed request has no new snapshot.
    new_snapshot_id: int | None = Field(default=None, foreign_key="snapshots.id")
    event_type: str
    message: str = ""
    notified: bool = False
    created_at: datetime = Field(default_factory=utcnow, sa_type=DateTime)
