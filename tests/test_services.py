import httpx
import pytest

from watchpatch.services.cleaner import extract_douyin_posts, filter_by_keyword, normalize_html
from watchpatch.services.diff import make_diff
from watchpatch.services.fetcher import (
    FetchError,
    fetch_html,
    fetch_page,
    is_douyin_url,
    validate_url,
)
from watchpatch.services.hasher import content_hash


def test_normalize_html_removes_script_and_style():
    assert (
        normalize_html(
            "<style>css</style><script>code</script><noscript>hidden</noscript><p>Hello</p>"
        )
        == "Hello"
    )


def test_normalize_text_collapses_whitespace():
    assert (
        normalize_html("<p> Hello   world </p>\r\n<p> next\tline </p>") == "Hello world\nnext line"
    )


def test_filter_by_keyword():
    text = "ignored\nbefore\nPython job\nafter\nignored too"
    assert filter_by_keyword(text, ["python"]) == "before\nPython job\nafter"
    assert filter_by_keyword(text, ["missing"]) is None
    assert filter_by_keyword(text, []) == text


def test_douyin_posts_are_stable_links():
    html = (
        '<a href="/video/123?share=1">点赞 10</a>'
        '<a href="https://www.douyin.com/note/456">笔记</a>'
        '<a href="https://other.example/video/789">无关链接</a>'
    )
    assert extract_douyin_posts(html, "https://www.douyin.com/user/test") == (
        "https://www.douyin.com/note/456\nhttps://www.douyin.com/video/123"
    )


def test_hash_is_stable():
    assert content_hash("abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_diff_contains_added_and_removed_lines():
    diff = make_diff("old", "new")
    assert "-old" in diff and "+new" in diff
    long_diff = make_diff("\n".join(f"old{x}" for x in range(300)), "new")
    assert len(long_diff.splitlines()) == 201
    assert "完整内容已保存在数据库" in long_diff


@pytest.mark.parametrize(
    "url", ["ftp://example.com", "https://", "no-url", "https://x:abc", "https://a b"]
)
def test_invalid_urls(url):
    with pytest.raises(ValueError, match="URL 无效"):
        validate_url(url)


async def test_fetch_decodes_html():
    content = '<meta charset="gb2312"><p>报名公告</p>'.encode("gb2312")
    transport = httpx.MockTransport(lambda _: httpx.Response(200, content=content))
    assert "报名公告" in await fetch_html("https://example.com", transport)


@pytest.mark.parametrize("status,expected_calls", [(403, 1), (404, 1), (429, 1), (500, 3)])
async def test_http_failures_retry_only_server_errors(status, expected_calls, monkeypatch):
    calls = []

    async def no_sleep(_):
        pass

    monkeypatch.setattr("watchpatch.services.fetcher.asyncio.sleep", no_sleep)

    def handler(request):
        calls.append(request)
        return httpx.Response(status)

    with pytest.raises(FetchError, match=str(status)):
        await fetch_html("https://example.com", httpx.MockTransport(handler))
    assert len(calls) == expected_calls


@pytest.mark.parametrize(
    "exception,expected_calls", [(httpx.ConnectError, 3), (httpx.ReadTimeout, 1)]
)
async def test_network_retry_rules(exception, expected_calls, monkeypatch):
    calls = []

    async def no_sleep(_):
        pass

    monkeypatch.setattr("watchpatch.services.fetcher.asyncio.sleep", no_sleep)

    def handler(request):
        calls.append(request)
        raise exception("offline", request=request)

    with pytest.raises(FetchError):
        await fetch_html("https://example.com", httpx.MockTransport(handler))
    assert len(calls) == expected_calls


@pytest.mark.parametrize(
    "body,headers", [(b"", {}), (b"%PDF", {"content-type": "application/pdf"})]
)
async def test_empty_or_non_html_response(body, headers):
    with pytest.raises(FetchError):
        await fetch_html(
            "https://example.com",
            httpx.MockTransport(lambda _: httpx.Response(200, content=body, headers=headers)),
        )


def test_douyin_host_match_is_exact_or_subdomain():
    assert is_douyin_url("https://www.douyin.com/user/example")
    assert is_douyin_url("https://douyin.com/user/example")
    assert not is_douyin_url("https://douyin.com.evil.example/user/example")


async def test_fetch_page_selects_renderer_for_douyin(monkeypatch):
    async def rendered(url):
        return f"<p>{url}</p>"

    monkeypatch.setattr("watchpatch.services.browser_fetcher.fetch_rendered_html", rendered)
    result = await fetch_page("https://www.douyin.com/user/example")
    assert "www.douyin.com" in result
