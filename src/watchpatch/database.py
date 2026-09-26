from pathlib import Path

from sqlalchemy import URL, event
from sqlmodel import Session, SQLModel, create_engine, select

from watchpatch.config import database_path
from watchpatch.models import Monitor, Snapshot


class Database:
    def __init__(self, path: Path | None = None):
        path = path or database_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(
            URL.create("sqlite", database=str(path)),
            connect_args={"check_same_thread": False, "timeout": 20},
        )

        @event.listens_for(self.engine, "connect")
        def configure_connection(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA journal_mode=WAL")

        SQLModel.metadata.create_all(self.engine)

    def session(self) -> Session:
        return Session(self.engine, expire_on_commit=False)

    def close(self) -> None:
        self.engine.dispose()


def get_monitor(session: Session, monitor_id: int) -> Monitor:
    monitor = session.get(Monitor, monitor_id)
    if monitor is None:
        raise ValueError(f"监控 ID {monitor_id} 不存在。")
    return monitor


def latest_snapshots(session: Session, monitor_id: int, limit: int = 2) -> list[Snapshot]:
    return list(
        session.exec(
            select(Snapshot)
            .where(Snapshot.monitor_id == monitor_id)
            .order_by(Snapshot.id.desc())
            .limit(limit)
        )
    )
