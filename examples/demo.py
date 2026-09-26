"""Run a deterministic end-to-end demo against a local HTTP server."""

import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4


class DemoPage(BaseHTTPRequestHandler):
    html = "<html><meta charset='utf-8'><h1>课程公告</h1><p>Python 课程报名尚未开始</p></html>"

    def do_GET(self):
        body = self.html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


def main():
    root = Path(__file__).resolve().parents[1]
    demo_path = root / ".demo" / f"run-{uuid4().hex[:8]}" / "watchpatch.db"
    env = {
        **os.environ,
        "WATCHPATCH_DB": str(demo_path),
        "WATCHPATCH_NO_NOTIFY": "1",
        "PYTHONIOENCODING": "utf-8",
        "NO_PROXY": "127.0.0.1,localhost",
    }
    server = ThreadingHTTPServer(("127.0.0.1", 0), DemoPage)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def command(*args):
        print("\n> watchpatch " + " ".join(args), flush=True)
        subprocess.run([sys.executable, "-m", "watchpatch", *args], env=env, check=True)

    try:
        print("WatchPatch 本地演示：首次快照 → 无变化 → 更新网页 → 查看差异", flush=True)
        url = f"http://127.0.0.1:{server.server_port}/notice"
        command("add", url, "--name", "课程公告", "--keyword", "报名", "--interval", "5")
        command("check", "1")
        command("check", "1")
        DemoPage.html = (
            "<html><meta charset='utf-8'><h1>课程公告</h1>"
            "<p>Python 课程报名已开放：10 月 1 日截止</p></html>"
        )
        command("check", "1")
        command("diff", "1")
        command("list")
        command("show", "1")
        command("pause", "1")
        command("run", "--once")
        command("resume", "1")
        print(f"\n演示完成。演示数据库：{demo_path}", flush=True)
        print("本地测试服务器已关闭；演示记录与日常监控数据相互独立。", flush=True)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
