import asyncio
from datetime import timedelta

import pytest
from sqlmodel import select

from watchpatch.models import Monitor, Snapshot, utcnow
from watchpatch.services.scheduler import check_due, due_monitor_ids, run_scheduler


def test_create_monitor_and_query_list(db):
    with db.session() as session:
        session.add(Monitor(name="示例页面", url="https://example.com"))
        session.commit()
    with db.session() as session:
        monitors = session.exec(select(Monitor)).all()
        assert len(monitors) == 1
        assert monitors[0].name == "示例页面"


def test_save_and_query_snapshot(db):
    with db.session() as session:
        monitor = Monitor(name="demo", url="https://example.com")
        session.add(monitor)
        session.flush()
        session.add(Snapshot(monitor_id=monitor.id, content="报名开始", content_hash="hash"))
        session.commit()
    with db.session() as session:
        snapshot = session.exec(select(Snapshot)).one()
        assert snapshot.content == "报名开始"
        assert snapshot.monitor_id == monitor.id


async def test_scheduler_checks_only_due_enabled_monitors(db, monkeypatch):
    with db.session() as session:
        for name, enabled, checked in [
            ("new", True, None),
            ("paused", False, None),
            ("recent", True, utcnow()),
            ("due", True, utcnow() - timedelta(minutes=61)),
        ]:
            session.add(
                Monitor(
                    name=name,
                    url=f"https://{name}.example.com",
                    enabled=enabled,
                    last_checked_at=checked,
                )
            )
        session.commit()
    assert due_monitor_ids(db) == [1, 4]

    async def fetch(_):
        return "<p>content</p>"

    monkeypatch.setattr("watchpatch.services.checker.fetch_page", fetch)
    results = []
    await check_due(db, results.append)
    assert [result.monitor_id for result in results] == [1, 4]
    assert due_monitor_ids(db) == []


async def test_scheduler_runs_immediately_and_can_stop(db, monkeypatch):
    with db.session() as session:
        session.add(Monitor(name="scheduled", url="https://example.com"))
        session.commit()

    async def fetch(_):
        return "<p>scheduled content</p>"

    monkeypatch.setattr("watchpatch.services.checker.fetch_page", fetch)
    checked = asyncio.Event()
    task = asyncio.create_task(run_scheduler(db, lambda _: checked.set()))
    try:
        await asyncio.wait_for(checked.wait(), timeout=3)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert due_monitor_ids(db) == []
