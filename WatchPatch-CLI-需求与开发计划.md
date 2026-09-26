# WatchPatch CLI：命令行版需求与开发计划

## 1. 项目定位

WatchPatch CLI 是一个本地运行的网页和文档变化提醒工具。

用户添加一个网页地址和可选关键词后，程序按照设定的间隔检查页面，保存历史内容；发现重要变化时，显示差异并发送 Windows 桌面通知。

第一版的目标不是做成完整的网页监控平台，而是完成一条可靠的核心链路：

```text
添加监控
  -> 定时抓取
  -> 清理文本
  -> 保存快照
  -> 判断变化
  -> 显示差异
  -> 发送通知
```

## 2. 第一版用户场景

优先支持这些场景：

- 监控学校、考试和课程报名公告
- 监控政府或组织的政策公告
- 监控招聘页面是否出现新岗位
- 监控软件 Release 页面
- 监控一个页面中是否出现指定关键词

暂不支持：

- 需要登录的网站
- 验证码、付费墙和反爬绕过
- 云端账号和多用户
- 手机 App
- 邮件、微信、Telegram 等外部通知
- AI 总结
- 浏览器插件

## 3. MVP 功能清单

### 必须完成

- 通过命令行添加、查看、暂停、恢复和删除监控
- 使用 HTTP 获取普通 HTML 页面
- 提取网页纯文本并清理空白
- 按关键词过滤内容（可选）
- 使用 SQLite 保存监控和历史快照
- 手动检查和定时检查
- 检测内容是否变化
- 生成统一格式的文本差异
- Windows 桌面通知
- 清晰的错误提示和运行日志
- pytest 测试
- README、MIT License、GitHub Actions

### 可以放到 v0.2

- Playwright 动态网页抓取
- PDF 文本监控
- 监控 CSS Selector 指定区域
- HTML 页面中的广告和导航过滤
- 导出 Markdown/JSON
- Web 管理页面

## 4. 技术栈

- Python 3.12+
- CLI：Typer
- HTTP：httpx
- HTML 解析：BeautifulSoup4
- 数据库：SQLite
- ORM：SQLModel
- 定时任务：APScheduler
- 差异比较：Python 标准库 difflib
- Windows 通知：winotify；不可用时退化为终端提示
- 输出：Rich
- 测试：pytest、pytest-asyncio
- 质量检查：Ruff
- 打包：uv + pyproject.toml
- CI：GitHub Actions

## 5. 用户命令设计

程序名称暂定为 `watchpatch`。

### 添加监控

```bash
watchpatch add "https://example.com/notice" --name "课程公告" --interval 30
```

参数：

- `url`：必填，网页地址
- `--name`：可选，任务名称；未提供时使用网页域名
- `--interval`：可选，检查间隔，单位为分钟，默认 60，最小 5
- `--keyword`：可重复传入；只关心包含这些词的内容
- `--selector`：预留参数，MVP 暂不实现，传入时给出明确提示

示例：

```bash
watchpatch add "https://example.com/jobs" --name "招聘岗位" --keyword "Python" --interval 30
```

### 查看监控列表

```bash
watchpatch list
```

显示：

```text
ID  名称       状态  间隔  上次检查          最近结果
1   课程公告   开启  30m   2026-09-26 10:00  无变化
2   招聘岗位   暂停  60m   2026-09-26 09:30  发现变化
```

### 查看详情

```bash
watchpatch show 1
```

显示 URL、关键词、创建时间、上次检查、快照数量和最近一次变化。

### 手动检查

```bash
watchpatch check 1
watchpatch check --all
```

返回三种明确结果：

- `首次快照已保存`
- `没有变化`
- `检测到变化，已保存新快照`

### 查看差异

```bash
watchpatch diff 1
watchpatch diff 1 --latest 2
```

默认显示最近两次快照的差异，新增内容用绿色，删除内容用红色。

### 启用和暂停

```bash
watchpatch pause 1
watchpatch resume 1
```

### 删除监控

```bash
watchpatch remove 1
```

删除前必须要求用户确认；同时删除该监控的快照和事件记录。

### 后台运行

```bash
watchpatch run
watchpatch run --once
```

- `run`：持续运行定时检查，按 `Ctrl+C` 退出
- `run --once`：检查所有启用任务一次后退出，便于测试和计划任务调用

### 查看配置和版本

```bash
watchpatch config
watchpatch --version
watchpatch --help
```

## 6. 数据模型

### monitors 表

```text
id                 INTEGER PRIMARY KEY
name               TEXT NOT NULL
url                TEXT NOT NULL
keywords           TEXT NOT NULL DEFAULT '[]'  # JSON 数组
interval_minutes   INTEGER NOT NULL DEFAULT 60
enabled            BOOLEAN NOT NULL DEFAULT 1
last_hash          TEXT
last_checked_at    DATETIME
last_status        TEXT
created_at         DATETIME NOT NULL
updated_at         DATETIME NOT NULL
```

### snapshots 表

```text
id                 INTEGER PRIMARY KEY
monitor_id         INTEGER NOT NULL
content            TEXT NOT NULL
content_hash       TEXT NOT NULL
created_at         DATETIME NOT NULL
FOREIGN KEY(monitor_id) REFERENCES monitors(id)
```

### events 表

```text
id                 INTEGER PRIMARY KEY
monitor_id         INTEGER NOT NULL
old_snapshot_id    INTEGER
new_snapshot_id    INTEGER NOT NULL
event_type         TEXT NOT NULL  # changed / error
message            TEXT
created_at         DATETIME NOT NULL
FOREIGN KEY(monitor_id) REFERENCES monitors(id)
```

SQLite 文件默认位置：

```text
%LOCALAPPDATA%\\WatchPatch\\watchpatch.db
```

允许通过环境变量覆盖：

```text
WATCHPATCH_DB=C:\\path\\watchpatch.db
```

## 7. 核心处理规则

### 网页抓取

- 使用 `httpx.AsyncClient`
- 默认请求超时 20 秒
- 只允许 `http` 和 `https`
- 设置正常浏览器 User-Agent
- 最多重试 2 次，仅重试连接错误和 5xx
- 4xx、超时和解析失败都要记录可读错误
- 不绕过验证码、登录或网站访问限制

### 文本清理

```text
HTML
  -> BeautifulSoup.get_text()
  -> 删除 script/style/noscript
  -> 去除每行首尾空白
  -> 连续空白压缩为一个空格
  -> 删除空行
  -> 统一换行符
```

关键词存在时，只把包含关键词的行及其相邻上下文保留下来；如果没有命中，状态为 `keyword_not_found`，不生成变化事件。

### 变化判断

1. 对清理后的文本计算 SHA-256。
2. 没有历史快照时保存为初始快照。
3. 新旧哈希相同，记录检查成功，不发送通知。
4. 哈希不同，保存新快照，生成事件并发送通知。

```python
sha256(normalized_text.encode("utf-8")).hexdigest()
```

### 差异显示

- 使用 `difflib.unified_diff`
- 显示最多 200 行差异，避免终端被超长页面淹没
- 超出限制时提示“完整内容已保存在数据库”
- 终端支持颜色时使用 Rich；不支持颜色时仍输出纯文本

### 通知去重

- 同一个监控的同一个 `content_hash` 只通知一次
- 检查失败不发送“内容变化”通知
- 连续失败时只在第一次失败和恢复时提示，避免刷屏

## 8. 推荐代码结构

```text
watchpatch/
├── pyproject.toml
├── README.md
├── LICENSE
├── src/
│   └── watchpatch/
│       ├── __init__.py
│       ├── cli.py
│       ├── config.py
│       ├── database.py
│       ├── models.py
│       ├── commands/
│       │   ├── monitors.py
│       │   └── runner.py
│       └── services/
│           ├── fetcher.py
│           ├── cleaner.py
│           ├── hasher.py
│           ├── checker.py
│           ├── diff.py
│           ├── scheduler.py
│           └── notifier.py
├── tests/
│   ├── test_cleaner.py
│   ├── test_hasher.py
│   ├── test_checker.py
│   ├── test_database.py
│   └── test_cli.py
└── .github/
    └── workflows/
        └── test.yml
```

职责边界：

- `fetcher.py`：只负责获取网页，不做业务判断
- `cleaner.py`：只负责 HTML 到纯文本
- `hasher.py`：只负责标准化哈希
- `checker.py`：编排抓取、清洗、快照和事件
- `notifier.py`：只负责发送通知
- `cli.py`：只负责命令行参数和输出

## 9. 配置文件

MVP 不强制配置文件，所有任务存数据库。允许用户通过命令查看默认值：

```bash
watchpatch config
```

后续可以支持 `%LOCALAPPDATA%\\WatchPatch\\config.toml`：

```toml
request_timeout = 20
max_retries = 2
min_interval_minutes = 5
max_diff_lines = 200
notify_on_recovery = true
```

## 10. 异常和边界情况

必须处理并给出用户可理解的提示：

- URL 格式错误
- 不支持的 URL 协议
- 域名无法解析
- 请求超时
- 页面返回 403/404/429/500
- 页面内容为空
- 页面编码异常
- 数据库文件无法写入
- 间隔小于 5 分钟
- 关键词为空
- 删除不存在的监控 ID
- Ctrl+C 正常退出

不向用户展示完整 Python traceback；调试信息写入日志文件：

```text
%LOCALAPPDATA%\\WatchPatch\\watchpatch.log
```

## 11. 测试清单

### 单元测试

- `test_normalize_html_removes_script_and_style`
- `test_normalize_text_collapses_whitespace`
- `test_filter_by_keyword`
- `test_hash_is_stable`
- `test_diff_contains_added_and_removed_lines`
- `test_invalid_interval_is_rejected`

### 数据库测试

- 创建监控
- 查询列表
- 暂停和恢复
- 删除监控及关联快照
- 保存和查询快照
- 保存变化事件

### 业务测试

- 第一次检查保存初始快照
- 内容不变时不生成事件
- 内容变化时保存新快照
- 关键词未命中时不误报
- 同一个哈希不会重复通知
- 抓取失败不会破坏已有快照

### CLI 测试

- `--help` 可运行
- `add` 参数错误时返回非 0
- `list` 空数据库有友好提示
- `check --all` 只检查启用任务
- `remove` 需要确认

目标：核心业务测试覆盖率达到 80% 以上。

## 12. 4 周开发安排

每天约 2 小时，每周 6 天。

### 第 1 周：项目骨架和数据库

- 初始化 `pyproject.toml`
- 创建 SQLModel 模型
- 初始化 SQLite
- 完成 `add/list/show/pause/resume/remove`
- 为数据库和 CLI 参数写测试

验收：可以完整管理监控任务。

### 第 2 周：抓取和变化检测

- 实现 httpx 抓取
- 实现 HTML 清理
- 实现关键词过滤
- 实现 SHA-256
- 实现快照和事件
- 实现 `check` 和 `diff`

验收：修改测试网页后，程序能准确显示新增和删除内容。

### 第 3 周：定时运行和通知

- 集成 APScheduler
- 实现 `run` 和 `run --once`
- 实现 Windows 通知
- 增加日志
- 增加失败重试和恢复提示

验收：程序运行期间能自动检查并通知。

### 第 4 周：质量和发布

- 补充测试到 80% 以上
- 配置 Ruff 和 GitHub Actions
- 完善 README 和演示 GIF
- 添加 MIT License
- 打包为可安装 CLI
- 创建 `v0.1.0` Release

验收：新用户按照 README 能在 5 分钟内完成安装并创建第一个监控。

## 13. 版本范围

### v0.1.0

- URL 监控
- 关键词过滤
- SQLite 快照
- 手动和定时检查
- 终端差异
- Windows 通知
- 测试和 CI

### v0.2.0

- Playwright 动态网页
- CSS Selector
- PDF 文本监控
- JSON/Markdown 导出
- Web 管理页面

### v0.3.0

- 浏览器扩展
- Telegram/Discord/Webhook
- 价格数值提取
- RSS 输出

## 14. README 必须展示的内容

README 第一屏需要回答：

1. WatchPatch 是什么？
2. 它解决什么问题？
3. 一条命令如何安装？
4. 一个最小示例如何运行？

最小示例：

```bash
pip install watchpatch
watchpatch add "https://example.com/notice" --name "公告" --keyword "报名"
watchpatch check --all
watchpatch run
```

README 应包含：

- 终端 GIF 或截图
- 功能列表
- 安装方式
- 命令说明
- 隐私说明：默认只在本机保存内容
- 使用限制：不绕过登录、验证码和访问限制
- 测试状态和 CI 徽章
- Roadmap
- 贡献方式
- License

## 15. 简历描述

中文版：

> 使用 Python、Typer、httpx、SQLModel 和 APScheduler 开发本地网页变化监控 CLI，支持关键词过滤、SQLite 历史快照、SHA-256 内容指纹、统一差异报告和 Windows 桌面通知；通过 pytest 和 GitHub Actions 完成自动化测试与持续集成。

英文版：

> Built WatchPatch, a local-first webpage change monitoring CLI with Python, Typer, httpx, SQLModel and APScheduler. Implemented keyword filtering, SQLite snapshots, SHA-256 content hashing, unified diff reports, Windows desktop notifications, automated tests and GitHub Actions CI.

## 16. 今天的第一个开发任务

先只完成以下内容，不要提前做网页界面：

```text
1. 初始化 Python 项目
2. 创建 Monitor 和 Snapshot 模型
3. 初始化 SQLite
4. 实现 watchpatch add
5. 实现 watchpatch list
6. 写两个数据库测试
7. 提交 chore: initialize WatchPatch CLI
```

今天结束时，运行下面的命令应该能看到一条任务：

```bash
watchpatch add "https://example.com" --name "示例页面"
watchpatch list
```

只要这一步可以运行，项目就已经从想法进入可持续迭代状态。
