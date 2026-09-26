import logging
import os
import sys

from rich.console import Console

logger = logging.getLogger(__name__)


def notify(title: str, message: str) -> None:
    if sys.platform == "win32" and os.getenv("WATCHPATCH_NO_NOTIFY") != "1":
        try:
            from winotify import Notification

            Notification(app_id="WatchPatch", title=title, msg=message, duration="short").show()
            return
        except Exception:
            logger.warning("Windows 通知不可用，使用终端提示", exc_info=True)
    Console().print(f"[通知] {title}：{message}", markup=False, highlight=False)
