"""Local, single-user web interface backed by the same database as the CLI."""

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import delete
from sqlmodel import select

from watchpatch.config import MAX_DIFF_LINES, MIN_INTERVAL_MINUTES, REQUEST_TIMEOUT, database_path
from watchpatch.database import Database, latest_snapshots
from watchpatch.models import Event, Monitor, Snapshot, utcnow
from watchpatch.services.checker import check_monitor
from watchpatch.services.diff import make_diff
from watchpatch.services.fetcher import validate_url
from watchpatch.services.scheduler import check_due

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(ROOT / "templates"))
STATUS = {
    None: "等待首次检查",
    "initial": "首次快照",
    "unchanged": "正常",
    "changed": "发现变化",
    "keyword_not_found": "未命中关键词",
    "error": "检查失败",
}


def local_time(value):
    from datetime import UTC

    return (
        value.replace(tzinfo=UTC).astimezone().strftime("%Y-%m-%d %H:%M") if value else "尚未检查"
    )


templates.env.filters["local_time"] = local_time
templates.env.filters["keywords"] = lambda raw: "、".join(json.loads(raw)) or "全部内容"
templates.env.globals["status_label"] = STATUS


def view(request: Request, name: str, **context):
    return templates.TemplateResponse(request, name, context)


def redirect(url: str):
    return RedirectResponse(url, status_code=303)


def find_monitor(db: Database, monitor_id: int) -> Monitor:
    with db.session() as session:
        monitor = session.get(Monitor, monitor_id)
        if monitor is None:
            raise HTTPException(404, "监控任务不存在")
        return monitor


def dashboard_data(db: Database, filter_by: str = "all"):
    with db.session() as session:
        all_monitors = session.exec(select(Monitor).order_by(Monitor.id.desc())).all()
        since = utcnow() - timedelta(days=1)
        changed = len(
            session.exec(
                select(Event.id).where(Event.event_type == "changed", Event.created_at >= since)
            ).all()
        )
        failed = sum(item.last_status == "error" for item in all_monitors)
    monitors = all_monitors
    if filter_by == "changed":
        monitors = [item for item in monitors if item.last_status == "changed"]
    elif filter_by == "error":
        monitors = [item for item in monitors if item.last_status == "error"]
    stats = {
        "total": len(all_monitors),
        "active": sum(item.enabled for item in all_monitors),
        "changed": changed,
        "failed": failed,
    }
    return monitors, stats


def detail_data(db: Database, monitor_id: int):
    monitor = find_monitor(db, monitor_id)
    with db.session() as session:
        snapshots = session.exec(
            select(Snapshot).where(Snapshot.monitor_id == monitor_id).order_by(Snapshot.id.desc())
        ).all()
        events = session.exec(
            select(Event).where(Event.monitor_id == monitor_id).order_by(Event.id.desc())
        ).all()
    latest_change = next((event for event in events if event.event_type == "changed"), None)
    failures = sum(event.event_type == "error" for event in events)
    return {
        "monitor": monitor,
        "snapshots": snapshots,
        "events": events,
        "latest_change": latest_change,
        "failures": failures,
    }


def is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


def create_app(db_path: Path | None = None, *, schedule: bool = True) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db = Database(db_path)
        app.state.db = db
        task = None
        if schedule:

            async def poll():
                while True:
                    try:
                        await check_due(
                            db,
                            lambda result: logger.info(
                                "web scheduler monitor=%s status=%s",
                                result.monitor_id,
                                result.status,
                            ),
                        )
                    except Exception:
                        logger.exception("定时检查失败")
                    await asyncio.sleep(5)

            task = asyncio.create_task(poll())
        try:
            yield
        finally:
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            db.close()

    app = FastAPI(title="WatchPatch", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        if request.client and request.client.host not in ("127.0.0.1", "::1", "testclient"):
            return HTMLResponse("仅允许从本机访问。", status_code=403)
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            if origin and origin != f"{request.url.scheme}://{request.url.netloc}":
                return HTMLResponse("跨站请求已拒绝。", status_code=403)
        return await call_next(request)

    @app.exception_handler(404)
    async def missing(request: Request, exc: HTTPException):
        return HTMLResponse(
            view(request, "error.html", message="找不到这个监控任务。", code=404).body,
            status_code=404,
        )

    @app.exception_handler(Exception)
    async def failed(request: Request, exc: Exception):
        logger.exception("网页请求失败")
        return HTMLResponse(
            view(request, "error.html", message="操作失败，请查看本地日志后重试。", code=500).body,
            status_code=500,
        )

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request, filter: str = "all", notice: str = ""):
        if filter not in ("all", "changed", "error"):
            filter = "all"
        monitors, stats = dashboard_data(request.app.state.db, filter)
        return view(
            request, "dashboard.html", monitors=monitors, stats=stats, filter=filter, notice=notice
        )

    @app.get("/partials/monitor-table", response_class=HTMLResponse)
    def monitor_table(request: Request, filter: str = "all"):
        monitors, _ = dashboard_data(request.app.state.db, filter)
        return view(request, "partials/monitor_table.html", monitors=monitors, filter=filter)

    @app.get("/partials/monitor-row/{monitor_id}", response_class=HTMLResponse)
    def monitor_row(request: Request, monitor_id: int):
        return view(
            request,
            "partials/monitor_row.html",
            monitor=find_monitor(request.app.state.db, monitor_id),
        )

    @app.get("/partials/monitor-status/{monitor_id}", response_class=HTMLResponse)
    def monitor_status(request: Request, monitor_id: int):
        return view(
            request,
            "partials/monitor_status.html",
            monitor=find_monitor(request.app.state.db, monitor_id),
        )

    @app.get("/partials/flash-messages", response_class=HTMLResponse)
    def flash_messages(request: Request, message: str = ""):
        return view(request, "partials/flash.html", message=message)

    @app.get("/monitors/new", response_class=HTMLResponse)
    def new_monitor(request: Request):
        return view(request, "monitor_form.html", values={"interval": 30}, errors={})

    @app.post("/monitors")
    async def create_monitor(
        request: Request,
        name: str = Form(""),
        url: str = Form(""),
        interval: str = Form("30"),
        keywords: str = Form(""),
        check_now: bool = Form(False),
    ):
        values = {
            "name": name,
            "url": url,
            "interval": interval,
            "keywords": keywords,
            "check_now": check_now,
        }
        errors = {}
        name = name.strip()
        url = url.strip()
        if not name:
            errors["name"] = "请输入任务名称。"
        if not url:
            errors["url"] = "请输入网页地址。"
        else:
            try:
                validate_url(url)
            except ValueError as exc:
                errors["url"] = str(exc)
        try:
            minutes = int(interval)
            if minutes < MIN_INTERVAL_MINUTES:
                raise ValueError
        except ValueError:
            errors["interval"] = "检查间隔不能小于 5 分钟。"
        words = [part.strip() for part in keywords.replace("，", ",").split(",") if part.strip()]
        if errors:
            return HTMLResponse(
                view(request, "monitor_form.html", values=values, errors=errors).body,
                status_code=422,
            )
        with request.app.state.db.session() as session:
            monitor = Monitor(
                name=name,
                url=url,
                interval_minutes=minutes,
                keywords=json.dumps(list(dict.fromkeys(words)), ensure_ascii=False),
            )
            session.add(monitor)
            session.commit()
            monitor_id = monitor.id
        if check_now:
            await check_monitor(request.app.state.db, monitor_id)
        return redirect(f"/monitors/{monitor_id}?notice=created")

    @app.get("/monitors/{monitor_id}", response_class=HTMLResponse)
    def monitor_detail(request: Request, monitor_id: int, notice: str = ""):
        return view(
            request,
            "monitor_detail.html",
            **detail_data(request.app.state.db, monitor_id),
            notice=notice,
        )

    @app.post("/monitors/{monitor_id}/check")
    async def check_one(request: Request, monitor_id: int):
        find_monitor(request.app.state.db, monitor_id)
        result = await check_monitor(request.app.state.db, monitor_id)
        if is_htmx(request):
            return view(
                request,
                "monitor_detail.html",
                result=result,
                **detail_data(request.app.state.db, monitor_id),
                notice=result.status,
            )
        return redirect(f"/monitors/{monitor_id}?notice={result.status}")

    @app.post("/check-all")
    async def check_all(request: Request):
        with request.app.state.db.session() as session:
            ids = list(session.exec(select(Monitor.id).where(Monitor.enabled == True)))  # noqa: E712
        for monitor_id in ids:
            await check_monitor(request.app.state.db, monitor_id)
        if is_htmx(request):
            monitors, stats = dashboard_data(request.app.state.db)
            return view(
                request,
                "partials/dashboard_content.html",
                monitors=monitors,
                stats=stats,
                filter="all",
                notice=f"已检查 {len(ids)} 项启用任务。",
            )
        return redirect("/?notice=checked")

    @app.post("/monitors/{monitor_id}/pause")
    @app.post("/monitors/{monitor_id}/resume")
    def toggle(request: Request, monitor_id: int):
        enabled = request.url.path.endswith("/resume")
        with request.app.state.db.session() as session:
            monitor = session.get(Monitor, monitor_id)
            if monitor is None:
                raise HTTPException(404)
            monitor.enabled = enabled
            monitor.updated_at = utcnow()
            session.add(monitor)
            session.commit()
        if is_htmx(request):
            return view(
                request,
                "partials/detail_header.html",
                monitor=monitor,
                notice="已恢复自动检查。" if enabled else "已暂停自动检查。",
            )
        return redirect(f"/monitors/{monitor_id}?notice={'resumed' if enabled else 'paused'}")

    @app.post("/monitors/{monitor_id}/delete")
    def delete_monitor(request: Request, monitor_id: int):
        with request.app.state.db.session() as session:
            monitor = session.get(Monitor, monitor_id)
            if monitor is None:
                raise HTTPException(404)
            session.exec(delete(Event).where(Event.monitor_id == monitor_id))
            session.exec(delete(Snapshot).where(Snapshot.monitor_id == monitor_id))
            session.delete(monitor)
            session.commit()
        return redirect("/?notice=deleted")

    @app.get("/monitors/{monitor_id}/diff", response_class=HTMLResponse)
    def diff_page(request: Request, monitor_id: int):
        monitor = find_monitor(request.app.state.db, monitor_id)
        with request.app.state.db.session() as session:
            snapshots = latest_snapshots(session, monitor_id)
        old = snapshots[1] if len(snapshots) > 1 else None
        new = snapshots[0] if snapshots else None
        lines = make_diff(old.content, new.content).splitlines() if old else []
        return view(
            request,
            "diff.html",
            monitor=monitor,
            old=old,
            new=new,
            lines=lines,
            clipped=len(lines) >= MAX_DIFF_LINES,
        )

    @app.get("/monitors/{monitor_id}/snapshots/{snapshot_id}", response_class=HTMLResponse)
    def snapshot_page(request: Request, monitor_id: int, snapshot_id: int):
        monitor = find_monitor(request.app.state.db, monitor_id)
        with request.app.state.db.session() as session:
            snapshot = session.get(Snapshot, snapshot_id)
            if snapshot is None or snapshot.monitor_id != monitor_id:
                raise HTTPException(404)
        return view(request, "snapshot.html", monitor=monitor, snapshot=snapshot)

    @app.get("/monitors/{monitor_id}/diff.md")
    def export_diff(request: Request, monitor_id: int):
        monitor = find_monitor(request.app.state.db, monitor_id)
        with request.app.state.db.session() as session:
            snapshots = latest_snapshots(session, monitor_id)
        if len(snapshots) < 2:
            raise HTTPException(404)
        content = make_diff(snapshots[1].content, snapshots[0].content)
        markdown = f"# {monitor.name} · 内容变化\n\n```diff\n{content}\n```\n"
        return Response(
            markdown,
            media_type="text/markdown; charset=utf-8",
            headers={
                "Content-Disposition": f"attachment; filename=watchpatch-{monitor_id}-diff.md"
            },
        )

    @app.get("/settings", response_class=HTMLResponse)
    def settings(request: Request, notice: str = ""):
        with request.app.state.db.session() as session:
            total = len(session.exec(select(Monitor.id)).all())
        return view(
            request,
            "settings.html",
            db_path=db_path or database_path(),
            timeout=REQUEST_TIMEOUT,
            max_diff=MAX_DIFF_LINES,
            total=total,
            notice=notice,
        )

    return app
