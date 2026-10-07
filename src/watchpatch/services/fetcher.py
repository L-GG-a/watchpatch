import asyncio
from urllib.parse import urlsplit

import httpx
from bs4 import UnicodeDammit

from watchpatch.config import MAX_RETRIES, REQUEST_TIMEOUT


class FetchError(Exception):
    pass


def is_douyin_url(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return host == "douyin.com" or host.endswith(".douyin.com")


async def fetch_page(url: str) -> str:
    """Use browser rendering for Douyin and static HTTP for ordinary pages."""
    validate_url(url)
    if is_douyin_url(url):
        from watchpatch.services.browser_fetcher import fetch_rendered_html

        return await fetch_rendered_html(url)
    return await fetch_html(url)


def validate_url(url: str) -> str:
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError
        if parsed.username or parsed.password or any(char.isspace() for char in url):
            raise ValueError
        _ = parsed.port
        httpx.URL(url)
    except (ValueError, httpx.InvalidURL) as exc:
        raise ValueError("URL 无效：请输入完整的 http/https 地址，且不要包含账号密码。") from exc
    return url


async def fetch_html(url: str, transport: httpx.AsyncBaseTransport | None = None) -> str:
    validate_url(url)
    async with httpx.AsyncClient(
        timeout=REQUEST_TIMEOUT,
        follow_redirects=True,
        transport=transport,
        headers={"User-Agent": "Mozilla/5.0 (compatible; WatchPatch/0.1; local monitor)"},
    ) as client:
        for attempt in range(MAX_RETRIES + 1):
            try:
                response = await client.get(url)
                response.raise_for_status()
            except httpx.TimeoutException as exc:
                raise FetchError(f"请求超时（{REQUEST_TIMEOUT} 秒），请稍后重试。") from exc
            except httpx.ConnectError as exc:
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(0.5 * (attempt + 1))
                    continue
                raise FetchError("无法连接网页，请检查域名、网络或代理设置。") from exc
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if status >= 500 and attempt < MAX_RETRIES:
                    await asyncio.sleep(0.5 * (attempt + 1))
                    continue
                raise FetchError(f"网页返回 HTTP {status}，请确认网址及访问权限。") from exc
            except httpx.HTTPError as exc:
                raise FetchError(f"网页请求失败：{type(exc).__name__}。") from exc

            content_type = response.headers.get("content-type", "").lower()
            if content_type and not any(t in content_type for t in ("text/html", "xhtml")):
                raise FetchError("当前 demo 仅支持 HTML 页面，暂不支持 PDF 或其他文件。")
            if not response.content.strip():
                raise FetchError("网页内容为空，保留原有快照。")
            hint = response.encoding if "charset=" in content_type else None
            decoded = UnicodeDammit(
                response.content, known_definite_encodings=[hint] if hint else [], is_html=True
            )
            if decoded.unicode_markup is None or decoded.contains_replacement_characters:
                raise FetchError("网页编码无法正确解析，保留原有快照。")
            return decoded.unicode_markup
    raise FetchError("网页请求失败。")
