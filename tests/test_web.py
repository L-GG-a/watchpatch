from pathlib import Path

from fastapi.testclient import TestClient
from sqlmodel import select

from watchpatch.models import Event, Monitor, Snapshot
from watchpatch.web import create_app


def client(tmp_path: Path):
    return TestClient(create_app(tmp_path / "web.db", schedule=False))


def test_dashboard_and_form_validation(tmp_path):
    with client(tmp_path) as web:
        response = web.get("/")
        assert response.status_code == 200
        assert "还没有监控任务" in response.text
        assert "添加网页监控" in web.get("/monitors/new").text
        bad = web.post("/monitors", data={"name": " ", "url": "ftp://bad", "interval": "2"})
        assert bad.status_code == 422
        assert "请输入任务名称" in bad.text
        assert "URL 无效" in bad.text
        assert "不能小于 5 分钟" in bad.text


def test_web_create_check_diff_pause_resume_delete(tmp_path, monkeypatch):
    contents = iter(["<h1>报名尚未开始</h1>", "<h1>报名已开放</h1>"])

    async def fetch(_):
        return next(contents)

    monkeypatch.setattr("watchpatch.services.checker.fetch_page", fetch)
    monkeypatch.setattr("watchpatch.services.checker.notify", lambda *_: None)
    with client(tmp_path) as web:
        created = web.post(
            "/monitors",
            data={
                "name": "课程公告",
                "url": "https://example.com",
                "interval": "5",
                "keywords": "报名",
                "check_now": "true",
            },
            follow_redirects=False,
        )
        assert created.status_code == 303
        assert created.headers["location"] == "/monitors/1?notice=created"
        assert "课程公告" in web.get("/").text
        assert "首次快照" in web.get("/monitors/1").text

        checked = web.post("/monitors/1/check", headers={"HX-Request": "true"})
        assert checked.status_code == 200
        assert "发现变化" in checked.text
        assert "历史快照" in checked.text
        diff = web.get("/monitors/1/diff")
        assert diff.status_code == 200
        assert "报名尚未开始" in diff.text
        assert "报名已开放" in diff.text
        markdown = web.get("/monitors/1/diff.md")
        assert markdown.status_code == 200
        assert "+报名已开放" in markdown.text
        snapshot = web.get("/monitors/1/snapshots/1")
        assert snapshot.status_code == 200
        assert "报名尚未开始" in snapshot.text

        assert web.post("/monitors/1/pause", follow_redirects=False).status_code == 303
        assert "已暂停" in web.get("/monitors/1").text
        resumed = web.post("/monitors/1/resume", headers={"HX-Request": "true"})
        assert resumed.status_code == 200
        assert "已恢复" in resumed.text
        assert "已暂停" not in web.get("/monitors/1").text
        assert web.post("/monitors/1/delete", follow_redirects=False).status_code == 303
        assert web.get("/monitors/1").status_code == 404
        assert web.get("/monitors/1/diff").status_code == 404
        assert "还没有监控任务" in web.get("/").text
        with web.app.state.db.session() as session:
            assert not session.exec(select(Monitor)).all()
            assert not session.exec(select(Snapshot)).all()
            assert not session.exec(select(Event)).all()


def test_partials_filters_and_settings(tmp_path):
    with client(tmp_path) as web:
        web.post("/monitors", data={"name": "Demo", "url": "https://example.com", "interval": "30"})
        assert "Demo" in web.get("/partials/monitor-table").text
        assert "Demo" in web.get("/partials/monitor-row/1").text
        assert "等待首次检查" in web.get("/partials/monitor-status/1").text
        assert "暂无变化任务" in web.get("/partials/monitor-table?filter=changed").text
        assert "暂无失败任务" in web.get("/?filter=error").text
        assert "测试消息" in web.get("/partials/flash-messages?message=测试消息").text
        assert "web.db" in web.get("/settings").text
        assert web.get("/monitors/1/diff").status_code == 200
        assert "还没有可比较的版本" in web.get("/monitors/1/diff").text
        assert web.get("/monitors/1/snapshots/999").status_code == 404


def test_html_escapes_untrusted_snapshot_content(tmp_path, monkeypatch):
    async def fetch(_):
        return "<p>&lt;script&gt;alert(1)&lt;/script&gt;</p>"

    monkeypatch.setattr("watchpatch.services.checker.fetch_page", fetch)
    with client(tmp_path) as web:
        web.post(
            "/monitors",
            data={"name": "<script>bad</script>", "url": "https://example.com", "interval": "5"},
        )
        web.post("/monitors/1/check")
        page = web.get("/monitors/1/snapshots/1").text
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
        assert "<script>bad</script>" not in web.get("/").text


def test_cross_origin_write_is_rejected(tmp_path):
    with client(tmp_path) as web:
        response = web.post(
            "/monitors",
            headers={"Origin": "https://unrelated.example"},
            data={"name": "bad", "url": "https://example.com", "interval": "5"},
        )
        assert response.status_code == 403
        assert "还没有监控任务" in web.get("/").text
