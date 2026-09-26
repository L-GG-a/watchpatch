import asyncio
import logging
from collections.abc import Callable
from datetime import timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlmodel import select

from watchpatch.database import Database
from watchpatch.models import Monitor, utcnow
from watchpatch.services.checker import CheckResult, check_monitor

logger = logging.getLogger(__name__)


def due_monitor_ids(db: Database) -> list[int]:
    now = utcnow()
    with db.session() as session:
        monitors = session.exec(select(Monitor).where(Monitor.enabled == True)).all()  # noqa: E712
        return [
            m.id
            for m in monitors
            if m.last_checked_at is None
            or m.last_checked_at + timedelta(minutes=m.interval_minutes) <= now
        ]


async def check_due(db: Database, report: Callable[[CheckResult], None]) -> None:
    for monitor_id in due_monitor_ids(db):
        # Re-read before each request so another CLI process can pause/remove tasks.
        with db.session() as session:
            monitor = session.get(Monitor, monitor_id)
            if monitor is None or not monitor.enabled:
                continue
        try:
            result = await check_monitor(db, monitor_id)
            if not result.repeated_error:
                report(result)
        except ValueError:
            logger.info("监控 %s 在检查期间被删除", monitor_id)


async def run_scheduler(db: Database, report: Callable[[CheckResult], None]) -> None:
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        check_due,
        "interval",
        seconds=5,
        args=[db, report],
        max_instances=1,
        coalesce=True,
        next_run_time=utcnow(),
    )
    scheduler.start()
    try:
        await asyncio.Event().wait()
    finally:
        scheduler.shutdown(wait=False)
        await asyncio.sleep(0)
