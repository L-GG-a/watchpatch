import json

from sqlmodel import select

from watchpatch.models import Event, Monitor, Snapshot
from watchpatch.services.checker import check_monitor
from watchpatch.services.fetcher import FetchError


def add_monitor(db, keywords=None, url="https://example.com"):
    with db.session() as session:
        monitor = Monitor(name="课程", url=url, keywords=json.dumps(keywords or []))
        session.add(monitor)
        session.commit()
        return monitor.id


async def test_snapshot_change_and_notification_dedup(db):
    monitor_id = add_monitor(db)
    contents = iter(["A", "A", "B", "A", "B"])
    notifications = []

    async def fetch(_):
        return f"<p>{next(contents)}</p>"

    statuses = []
    for _ in range(5):
        result = await check_monitor(
            db, monitor_id, fetch, lambda *args: notifications.append(args)
        )
        statuses.append(result.status)
    assert statuses == ["initial", "unchanged", "changed", "changed", "changed"]
    assert len(notifications) == 2
    with db.session() as session:
        assert len(session.exec(select(Snapshot)).all()) == 4
        assert len(session.exec(select(Event)).all()) == 3


async def test_keyword_missing_preserves_previous_snapshot(db):
    monitor_id = add_monitor(db, ["Python"])
    contents = iter(["Python A", "unrelated", "Python A"])

    async def fetch(_):
        return f"<p>{next(contents)}</p>"

    assert (await check_monitor(db, monitor_id, fetch)).status == "initial"
    assert (await check_monitor(db, monitor_id, fetch)).status == "keyword_not_found"
    assert (await check_monitor(db, monitor_id, fetch)).status == "unchanged"
    with db.session() as session:
        assert len(session.exec(select(Snapshot)).all()) == 1
        assert not session.exec(select(Event)).all()


async def test_failure_preserves_snapshot_and_recovery_notifies_once(db):
    monitor_id = add_monitor(db)
    notifications = []

    async def success(_):
        return "<p>stable</p>"

    async def failure(_):
        raise FetchError("网络不可用")

    def notify(*args):
        notifications.append(args)

    await check_monitor(db, monitor_id, success, notify)
    first = await check_monitor(db, monitor_id, failure, notify)
    second = await check_monitor(db, monitor_id, failure, notify)
    recovered = await check_monitor(db, monitor_id, success, notify)
    assert first.status == second.status == "error"
    assert not first.repeated_error and second.repeated_error
    assert recovered.status == "unchanged"
    assert [item[0] for item in notifications] == ["检查失败", "检查已恢复"]
    with db.session() as session:
        assert len(session.exec(select(Snapshot)).all()) == 1
        assert len(session.exec(select(Event)).all()) == 2
        assert session.get(Monitor, monitor_id).last_error is None


async def test_empty_page_does_not_create_snapshot(db):
    monitor_id = add_monitor(db)

    async def fetch(_):
        return "<script>app()</script>"

    result = await check_monitor(db, monitor_id, fetch, lambda *_: None)
    assert result.status == "error"
    with db.session() as session:
        assert not session.exec(select(Snapshot)).all()


async def test_wait_placeholder_does_not_create_snapshot(db):
    monitor_id = add_monitor(db)

    async def fetch(_):
        return "<p>Please wait...</p><script>loadContent()</script>"

    result = await check_monitor(db, monitor_id, fetch, lambda *_: None)
    assert result.status == "error"
    assert "等待占位内容" in result.message
    with db.session() as session:
        assert not session.exec(select(Snapshot)).all()


async def test_douyin_requires_public_post_links(db):
    monitor_id = add_monitor(db, url="https://www.douyin.com/user/test")

    async def blocked(_):
        return "<p>Please wait...</p>"

    result = await check_monitor(db, monitor_id, blocked, lambda *_: None)
    assert result.status == "error"
    with db.session() as session:
        assert not session.exec(select(Snapshot)).all()

    async def public(_):
        return '<a href="/video/123">浏览 10</a>'

    result = await check_monitor(db, monitor_id, public, lambda *_: None)
    assert result.status == "initial"
    with db.session() as session:
        assert session.exec(select(Snapshot)).one().content == "https://www.douyin.com/video/123"


async def test_notification_failure_does_not_lose_change(db):
    monitor_id = add_monitor(db)
    contents = iter(["A", "B"])

    async def fetch(_):
        return next(contents)

    def notify(*_):
        raise RuntimeError("toast failed")

    await check_monitor(db, monitor_id, fetch, notify)
    assert (await check_monitor(db, monitor_id, fetch, notify)).status == "changed"
    with db.session() as session:
        assert len(session.exec(select(Snapshot)).all()) == 2
