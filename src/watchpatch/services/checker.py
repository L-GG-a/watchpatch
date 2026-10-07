import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sqlmodel import select

from watchpatch.database import Database, get_monitor, latest_snapshots
from watchpatch.models import Event, Snapshot, utcnow
from watchpatch.services.cleaner import extract_douyin_posts, filter_by_keyword, normalize_html
from watchpatch.services.diff import make_diff
from watchpatch.services.fetcher import FetchError, fetch_page, is_douyin_url
from watchpatch.services.hasher import content_hash
from watchpatch.services.notifier import notify

logger = logging.getLogger(__name__)


@dataclass
class CheckResult:
    monitor_id: int
    name: str
    status: str
    message: str
    diff: str = ""
    repeated_error: bool = False


async def check_monitor(
    db: Database,
    monitor_id: int,
    fetch: Callable[[str], Awaitable[str]] | None = None,
    send_notification: Callable[[str, str], None] | None = None,
) -> CheckResult:
    fetch = fetch or fetch_page
    send_notification = send_notification or notify
    # Never hold a database transaction open while waiting for the network.
    with db.session() as session:
        target = get_monitor(session, monitor_id)
        url, keywords = target.url, json.loads(target.keywords)
    error = None
    filtered = None
    try:
        html = await fetch(url)
        try:
            if is_douyin_url(url):
                text = extract_douyin_posts(html, url)
                if not text:
                    raise FetchError(
                        "公开作品列表未能读取，可能需要登录、验证，或页面结构已变化；旧快照已保留。"
                    )
            else:
                text = normalize_html(html)
        except Exception as exc:
            if isinstance(exc, FetchError):
                raise
            raise FetchError("网页文本解析失败，保留原有快照。") from exc
        if not text:
            raise FetchError("页面没有可读取文本，可能需要 JavaScript 渲染。")
        if text.casefold().strip(". …\n\r\t") in {
            "please wait",
            "just a moment",
            "请稍候",
            "请稍等",
        }:
            raise FetchError("页面只返回等待占位内容，未保存快照；可能需要登录或网站限制了访问。")
        filtered = filter_by_keyword(text, keywords)
    except FetchError as exc:
        error = str(exc)
        logger.warning("monitor=%s %s", monitor_id, error, exc_info=True)

    notifications = []
    with db.session() as session:
        monitor = get_monitor(session, monitor_id)
        was_error = monitor.last_status == "error"
        monitor.last_checked_at = utcnow()
        monitor.updated_at = utcnow()
        monitor.last_error = error
        if error:
            monitor.last_status = "error"
            session.add(Event(monitor_id=monitor_id, event_type="error", message=error))
            result = CheckResult(monitor_id, monitor.name, "error", error, repeated_error=was_error)
            if not was_error:
                notifications.append(("检查失败", f"{monitor.name}：{error}"))
        else:
            if was_error:
                notifications.append(("检查已恢复", monitor.name))
            if filtered is None:
                monitor.last_status = "keyword_not_found"
                result = CheckResult(
                    monitor_id,
                    monitor.name,
                    monitor.last_status,
                    "未找到关键词，保留原有快照，不生成变化事件。",
                )
            else:
                digest = content_hash(filtered)
                previous = latest_snapshots(session, monitor_id, 1)
                old = previous[0] if previous else None
                if old and old.content_hash == digest:
                    monitor.last_status = "unchanged"
                    result = CheckResult(monitor_id, monitor.name, "unchanged", "没有变化")
                else:
                    # Deduplicate notifications across A -> B -> A -> B, including restarts.
                    already_notified = (
                        session.exec(
                            select(Event)
                            .join(Snapshot, Event.new_snapshot_id == Snapshot.id)
                            .where(
                                Event.monitor_id == monitor_id,
                                Event.notified == True,  # noqa: E712
                                Snapshot.content_hash == digest,
                            )
                        ).first()
                        is not None
                    )
                    snapshot = Snapshot(
                        monitor_id=monitor_id, content=filtered, content_hash=digest
                    )
                    session.add(snapshot)
                    session.flush()
                    if old:
                        monitor.last_status = "changed"
                        session.add(
                            Event(
                                monitor_id=monitor_id,
                                old_snapshot_id=old.id,
                                new_snapshot_id=snapshot.id,
                                event_type="changed",
                                message="检测到变化，已保存新快照",
                                notified=not already_notified,
                            )
                        )
                        result = CheckResult(
                            monitor_id,
                            monitor.name,
                            "changed",
                            "检测到变化，已保存新快照",
                            make_diff(old.content, filtered),
                        )
                        if not already_notified:
                            notifications.append(("检测到网页变化", monitor.name))
                    else:
                        monitor.last_status = "initial"
                        result = CheckResult(monitor_id, monitor.name, "initial", "首次快照已保存")
                monitor.last_hash = digest
        session.add(monitor)
        session.commit()

    # Persist first: desktop notification failures must not roll back snapshots.
    for title, message in notifications:
        try:
            send_notification(title, message)
        except Exception:
            logger.warning("通知发送失败", exc_info=True)
    logger.info("monitor=%s status=%s", monitor_id, result.status)
    return result
