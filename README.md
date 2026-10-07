# WatchPatch

WatchPatch 是本地运行的网页变化监控工具，提供 CLI 和 Web 页面。它保存公开网页的文本快照，在内容变化时展示差异，并可发送 Windows 桌面通知。

## 安装与启动

需要 Python 3.12 及以上版本。在项目目录运行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\web.cmd
```

浏览器打开 `http://127.0.0.1:8787`。`web.cmd` 会尝试自动打开页面；关闭终端或按 `Ctrl+C` 停止服务。也可使用 `watchpatch web` 启动、`watchpatch --help` 查看 CLI 命令。

## 公开抖音页面（可选）

抖音页面通常需要浏览器渲染。使用前安装可选依赖和 Chromium：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[browser]"
.\.venv\Scripts\python.exe -m playwright install chromium
```

添加公开的抖音主页 URL 后，程序尝试从渲染后的页面提取公开作品链接，用链接集合判断新增或移除。它不读取私人动态，不登录，不解决验证码，也不绕过平台访问限制。若页面只有“Please wait...”或未能读到公开作品链接，检查会显示错误并保留旧快照。平台页面结构变化时，提取规则可能需要更新。

若 Chromium 下载到用户目录失败，可先设置 `$env:PLAYWRIGHT_BROWSERS_PATH = "$PWD\.demo\playwright-browsers"`，再安装 Chromium；启动 `web.cmd` 的终端也需保留这个环境变量。本项目的抖音页面抓取尚未用具体公开主页验证。

## 数据与测试

默认数据存放于 `%LOCALAPPDATA%\WatchPatch\watchpatch.db`；`web.cmd` 默认改用项目内 `.demo/watchpatch.db`。CLI 和 Web 使用同一数据库路径时可共享任务。可通过 `WATCHPATCH_DB` 指定路径。

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\ruff.exe check .
```

仅监控你有权访问的公开网页。使用限制、完整功能和计划见仓库中的需求文档。项目采用 MIT License。
