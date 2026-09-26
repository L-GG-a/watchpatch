import asyncio
import json
import logging
from contextlib import contextmanager
from datetime import UTC, datetime
from functools import wraps
from typing import Annotated
from urllib.parse import urlsplit

import typer
from rich.console import Console
from rich.table import Table
from sqlalchemy import delete
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import select

from watchpatch import __version__
from watchpatch.config import (
    MAX_DIFF_LINES,
    MAX_RETRIES,
    MIN_INTERVAL_MINUTES,
    REQUEST_TIMEOUT,
    database_path,
    log_path,
    setup_logging,
)
from watchpatch.database import Database, get_monitor, latest_snapshots
from watchpatch.models import Event, Monitor, Snapshot, utcnow
from watchpatch.services.checker import CheckResult, check_monitor
from watchpatch.services.diff import make_diff
from watchpatch.services.fetcher import validate_url
from watchpatch.services.scheduler import run_scheduler

app = typer.Typer(help="WatchPatch · 本地网页变化监控", no_args_is_help=True)
console = Console(highlight=False)
STATUS = {
    None: "尚未检查",
    "initial": "首次快照",
    "unchanged": "没有变化",
    "changed": "发现变化",
    "keyword_not_found": "未命中关键词",
    "error": "检查失败",
}


def friendly_errors(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except ValueError as exc:
            console.print(f"错误：{exc}", style="red", markup=False)
            raise typer.Exit(1) from exc
        except (OSError, SQLAlchemyError) as exc:
            logging.getLogger(__name__).exception("本地数据操作失败")
            console.print(
                "无法读写本地数据，请检查 WATCHPATCH_DB 路径、目录权限和磁盘空间。",
                style="red",
            )
            raise typer.Exit(1) from exc

    return wrapped


@contextmanager
def database():
    db = Database()
    try:
        yield db
    finally:
        db.close()


def time_label(value: datetime | None) -> str:
    return value.replace(tzinfo=UTC).astimezone().strftime("%Y-%m-%d %H:%M:%S") if value else "—"


def print_diff(value: str) -> None:
    for line in value.splitlines():
        style = "green" if line.startswith("+") else "red" if line.startswith("-") else None
        console.print(line, style=style, markup=False)


def report(result: CheckResult) -> None:
    console.print(
        f"#{result.monitor_id} {result.name} · {result.message}",
        style="red" if result.status == "error" else "cyan",
        markup=False,
    )
    if result.diff:
        print_diff(result.diff)


def version_callback(value: bool):
    if value:
        console.print(f"WatchPatch {__version__}")
        raise typer.Exit()


@app.callback()
def callback(
    version: Annotated[
        bool, typer.Option("--version", callback=version_callback, is_eager=True, help="查看版本")
    ] = False,
):
    try:
        setup_logging()
    except OSError:
        console.print("日志目录不可写，本次仅显示终端提示。", style="yellow")


@app.command()
@friendly_errors
def add(
    url: Annotated[str, typer.Argument(help="完整的 http/https 网页地址")],
    name: Annotated[str | None, typer.Option(help="监控名称，默认使用域名")] = None,
    interval: Annotated[int, typer.Option(min=MIN_INTERVAL_MINUTES, help="检查间隔（分钟）")] = 60,
    keyword: Annotated[list[str] | None, typer.Option(help="关键词，可重复传入")] = None,
    selector: Annotated[str | None, typer.Option(help="预留参数，demo 暂不支持")] = None,
):
    """添加一条网页监控。"""
    validate_url(url)
    if selector is not None:
        raise ValueError("--selector 将在后续版本支持，当前请省略此参数。")
    words = [word.strip() for word in keyword or []]
    if any(not word for word in words):
        raise ValueError("关键词不能为空。")
    label = name.strip() if name is not None else urlsplit(url).hostname
    if not label:
        raise ValueError("监控名称不能为空。")
    with database() as db, db.session() as session:
        monitor = Monitor(
            name=label,
            url=url,
            interval_minutes=interval,
            keywords=json.dumps(list(dict.fromkeys(words)), ensure_ascii=False),
        )
        session.add(monitor)
        session.commit()
        console.print(f"已添加 #{monitor.id} {monitor.name} · 每 {interval} 分钟检查", markup=False)
        console.print(f"运行 watchpatch check {monitor.id} 保存首次快照。", style="dim")


@app.command("list")
@friendly_errors
def list_monitors():
    """列出所有监控。"""
    with database() as db, db.session() as session:
        monitors = session.exec(select(Monitor).order_by(Monitor.id)).all()
        if not monitors:
            console.print('暂无监控。试试：watchpatch add "https://example.com" --name "示例页面"')
            return
        table = Table(title="WatchPatch · 监控列表")
        for header in ["ID", "名称", "状态", "间隔", "上次检查", "最近结果"]:
            table.add_column(header)
        from rich.text import Text

        for monitor in monitors:
            table.add_row(
                str(monitor.id),
                Text(monitor.name),
                "开启" if monitor.enabled else "暂停",
                f"{monitor.interval_minutes}m",
                time_label(monitor.last_checked_at),
                STATUS.get(monitor.last_status, monitor.last_status or "—"),
            )
        console.print(table)


@app.command()
@friendly_errors
def show(monitor_id: Annotated[int, typer.Argument(help="监控 ID")]):
    """查看监控详情和最近一次变化。"""
    with database() as db, db.session() as session:
        monitor = get_monitor(session, monitor_id)
        snapshots = session.exec(select(Snapshot.id).where(Snapshot.monitor_id == monitor_id)).all()
        change = session.exec(
            select(Event)
            .where(Event.monitor_id == monitor_id, Event.event_type == "changed")
            .order_by(Event.id.desc())
        ).first()
        fields = {
            "ID": monitor.id,
            "名称": monitor.name,
            "URL": monitor.url,
            "关键词": "、".join(json.loads(monitor.keywords)) or "全部内容",
            "状态": "开启" if monitor.enabled else "暂停",
            "间隔": f"{monitor.interval_minutes} 分钟",
            "创建时间": time_label(monitor.created_at),
            "上次检查": time_label(monitor.last_checked_at),
            "最近结果": STATUS[monitor.last_status],
            "快照数量": len(snapshots),
            "最近变化": time_label(change.created_at) if change else "—",
            "最近错误": monitor.last_error or "—",
        }
        for key, value in fields.items():
            console.print(f"{key}：{value}", markup=False)


async def check_many(db: Database, ids: list[int]) -> bool:
    failed = False
    for monitor_id in ids:
        result = await check_monitor(db, monitor_id)
        report(result)
        failed = failed or result.status == "error"
    return failed


def enabled_ids(db: Database) -> list[int]:
    with db.session() as session:
        return list(
            session.exec(
                select(Monitor.id)
                .where(
                    Monitor.enabled == True  # noqa: E712
                )
                .order_by(Monitor.id)
            )
        )


@app.command()
@friendly_errors
def check(
    monitor_id: Annotated[int | None, typer.Argument(help="监控 ID，允许手动检查暂停任务")] = None,
    all_monitors: Annotated[bool, typer.Option("--all", help="检查所有启用的任务")] = False,
):
    """立即检查网页，变化时显示差异。"""
    if (monitor_id is None) == (not all_monitors):
        raise ValueError("请指定监控 ID 或 --all，且不要同时指定。")
    with database() as db:
        ids = enabled_ids(db) if all_monitors else [monitor_id]
        if not ids:
            console.print("暂无启用的监控。")
        if asyncio.run(check_many(db, ids)):
            raise typer.Exit(1)


@app.command("diff")
@friendly_errors
def diff_command(
    monitor_id: Annotated[int, typer.Argument(help="监控 ID")],
    latest: Annotated[
        int, typer.Option(min=2, max=100, help="比较最近 N 个快照中的最早与最新")
    ] = 2,
):
    """查看已保存快照的差异（默认最近两个）。"""
    with database() as db, db.session() as session:
        get_monitor(session, monitor_id)
        snapshots = latest_snapshots(session, monitor_id, latest)
        if len(snapshots) < 2:
            console.print("至少需要两个不同内容的快照。请在页面变化后再次 check。")
            return
        old, new = snapshots[-1], snapshots[0]
        value = make_diff(old.content, new.content, f"快照 #{old.id}", f"快照 #{new.id}")
        print_diff(value or "所选快照内容相同。")


def set_enabled(monitor_id: int, enabled: bool):
    with database() as db, db.session() as session:
        monitor = get_monitor(session, monitor_id)
        monitor.enabled = enabled
        monitor.updated_at = utcnow()
        session.add(monitor)
        session.commit()
        console.print(
            f"已{'恢复' if enabled else '暂停'} #{monitor_id} {monitor.name}", markup=False
        )


@app.command()
@friendly_errors
def pause(monitor_id: int):
    """暂停自动检查。"""
    set_enabled(monitor_id, False)


@app.command()
@friendly_errors
def resume(monitor_id: int):
    """恢复自动检查。"""
    set_enabled(monitor_id, True)


@app.command()
@friendly_errors
def remove(monitor_id: int):
    """确认后删除监控及其全部历史记录。"""
    with database() as db:
        with db.session() as session:
            monitor = get_monitor(session, monitor_id)
            name = monitor.name
        if not typer.confirm(f"删除 #{monitor_id} {name} 及其全部快照和事件？", default=False):
            console.print("已取消删除。")
            return
        with db.session() as session:
            monitor = get_monitor(session, monitor_id)
            session.exec(delete(Event).where(Event.monitor_id == monitor_id))
            session.exec(delete(Snapshot).where(Snapshot.monitor_id == monitor_id))
            session.delete(monitor)
            session.commit()
        console.print(f"已删除 #{monitor_id}。")


@app.command()
@friendly_errors
def run(once: Annotated[bool, typer.Option(help="检查所有启用任务一次后退出")] = False):
    """持续运行定时检查，Ctrl+C 退出；请保持此终端开启。"""
    with database() as db:
        if once:
            ids = enabled_ids(db)
            if not ids:
                console.print("暂无启用的监控。")
            if asyncio.run(check_many(db, ids)):
                raise typer.Exit(1)
        else:
            console.print("WatchPatch 正在运行 · 每 5 秒检查到期任务 · Ctrl+C 退出", style="cyan")
            console.print("任务间隔最少 5 分钟；新增、暂停和恢复会自动生效。", style="dim")
            try:
                asyncio.run(run_scheduler(db, report))
            except KeyboardInterrupt:
                console.print("已停止监控。")


@app.command("config")
@friendly_errors
def config_command():
    """显示数据位置及默认配置。"""
    for key, value in {
        "数据库": database_path(),
        "日志": log_path(),
        "超时（秒）": REQUEST_TIMEOUT,
        "最大重试次数": MAX_RETRIES,
        "最小间隔（分钟）": MIN_INTERVAL_MINUTES,
        "最大差异行数": MAX_DIFF_LINES,
    }.items():
        console.print(f"{key}：{value}", markup=False)


@app.command()
def web(
    host: Annotated[str, typer.Option(help="监听地址（默认仅本机）")] = "127.0.0.1",
    port: Annotated[int, typer.Option(min=1, max=65535, help="端口")] = 8787,
    open_browser: Annotated[
        bool, typer.Option("--open-browser/--no-open-browser", help="服务就绪后打开浏览器")
    ] = True,
):
    """启动本地交互网页。"""
    import socket
    import threading
    import time
    import webbrowser

    import uvicorn

    from watchpatch.web import create_app

    browse_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    url = f"http://{browse_host}:{port}"
    console.print(f"WatchPatch 网页：{url}", style="cyan")
    if open_browser:

        def open_when_ready():
            for _ in range(60):
                try:
                    with socket.create_connection((browse_host, port), timeout=0.2):
                        webbrowser.open(url)
                        return
                except OSError:
                    time.sleep(0.25)

        threading.Thread(target=open_when_ready, daemon=True).start()
    uvicorn.run(create_app(), host=host, port=port, log_level="warning")


def main():
    try:
        app()
    except KeyboardInterrupt:
        console.print("已停止。")
    except Exception:
        logging.getLogger(__name__).exception("未预期的错误")
        console.print("运行失败，详细信息请查看 watchpatch.log。", style="red")
        raise SystemExit(1) from None
