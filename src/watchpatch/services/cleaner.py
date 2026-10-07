import re
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup


def normalize_html(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for element in soup(["script", "style", "noscript"]):
        element.decompose()
    lines = (re.sub(r"\s+", " ", line).strip() for line in soup.get_text("\n").splitlines())
    return "\n".join(line for line in lines if line)


def filter_by_keyword(text: str, keywords: list[str]) -> str | None:
    if not keywords:
        return text
    lines = text.splitlines()
    terms = [word.casefold() for word in keywords]
    selected = set()
    for index, line in enumerate(lines):
        if any(term in line.casefold() for term in terms):
            selected.update(range(max(0, index - 1), min(len(lines), index + 2)))
    return "\n".join(lines[index] for index in sorted(selected)) if selected else None


def extract_douyin_posts(html: str, base_url: str) -> str:
    """Keep stable public post links, excluding page chrome and changing counters."""
    soup = BeautifulSoup(html, "html.parser")
    posts = {}
    for anchor in soup.select('a[href*="/video/"], a[href*="/note/"]'):
        address = urljoin(base_url, anchor.get("href", ""))
        parsed = urlsplit(address)
        if parsed.hostname not in {"douyin.com", "www.douyin.com"}:
            continue
        match = re.fullmatch(r"/(video|note)/([A-Za-z0-9_-]+)", parsed.path.rstrip("/"))
        if match:
            link = f"https://www.douyin.com/{match.group(1)}/{match.group(2)}"
            posts[link] = link
    return "\n".join(sorted(posts))
