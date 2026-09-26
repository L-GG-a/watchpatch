import re

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
