import pytest
from sqlmodel import select

from watchpatch.cli import app
from watchpatch.models import Event, Monitor, Snapshot


@pytest.mark.parametrize("args", [["--help"], ["--version"], ["config"]])
def test_metadata_commands(runner, args):
    assert runner.invoke(app, args).exit_code == 0


def test_empty_list(runner):
    assert "暂无监控" in runner.invoke(app, ["list"]).output


@pytest.mark.parametrize(
    "args",
    [
        ["add", "ftp://example.com"],
        ["add", "https://example.com", "--interval", "4"],
        ["add", "https://example.com", "--keyword", " "],
        ["add", "https://example.com", "--selector", "main"],
        ["add", "https://example.com", "--name", " "],
        ["check"],
        ["check", "1", "--all"],
        ["show", "999"],
        ["remove", "999"],
    ],
)
def test_invalid_arguments(runner, args):
    result = runner.invoke(app, args)
    assert result.exit_code != 0
    assert "Traceback" not in result.output


def test_add_list_show_pause_resume(runner, db):
    result = runner.invoke(app, ["add", "https://example.com", "--name", "课程公告"])
    assert result.exit_code == 0, result.output
    assert "课程公告" in runner.invoke(app, ["list"]).output
    assert "https://example.com" in runner.invoke(app, ["show", "1"]).output
    assert runner.invoke(app, ["pause", "1"]).exit_code == 0
    with db.session() as session:
        assert session.get(Monitor, 1).enabled is False
    assert runner.invoke(app, ["resume", "1"]).exit_code == 0
    with db.session() as session:
        assert session.get(Monitor, 1).enabled is True


@pytest.mark.parametrize("args", [["check", "--all"], ["run", "--once"]])
def test_check_all_only_enabled(runner, db, monkeypatch, args):
    for name in ["enabled", "paused"]:
        runner.invoke(app, ["add", f"https://{name}.example.com"])
    runner.invoke(app, ["pause", "2"])
    urls = []

    async def fetch(url):
        urls.append(url)
        return "<p>content</p>"

    monkeypatch.setattr("watchpatch.services.checker.fetch_page", fetch)
    assert runner.invoke(app, args).exit_code == 0
    assert urls == ["https://enabled.example.com"]


def test_remove_requires_confirmation_and_deletes_history(runner, db):
    runner.invoke(app, ["add", "https://example.com"])
    with db.session() as session:
        snapshot = Snapshot(monitor_id=1, content="test", content_hash="abc")
        session.add(snapshot)
        session.flush()
        session.add(Event(monitor_id=1, new_snapshot_id=snapshot.id, event_type="changed"))
        session.commit()
    assert "已取消" in runner.invoke(app, ["remove", "1"], input="n\n").output
    with db.session() as session:
        assert session.get(Monitor, 1) is not None
    assert runner.invoke(app, ["remove", "1"], input="y\n").exit_code == 0
    with db.session() as session:
        assert not session.exec(select(Monitor)).all()
        assert not session.exec(select(Snapshot)).all()
        assert not session.exec(select(Event)).all()


def test_check_and_diff(runner, monkeypatch):
    runner.invoke(app, ["add", "https://example.com"])
    assert "至少需要两个" in runner.invoke(app, ["diff", "1"]).output
    contents = iter(["old", "new"])

    async def fetch(_):
        return next(contents)

    monkeypatch.setattr("watchpatch.services.checker.fetch_page", fetch)
    monkeypatch.setattr("watchpatch.services.checker.notify", lambda *_: None)
    assert "首次快照已保存" in runner.invoke(app, ["check", "1"]).output
    assert "检测到变化" in runner.invoke(app, ["check", "1"]).output
    output = runner.invoke(app, ["diff", "1"]).output
    assert "-old" in output and "+new" in output
