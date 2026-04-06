"""Default web search backend for WebSearchTool.

Provides a lightweight DuckDuckGo HTML search implementation using only the
standard library so the default CLI/runtime can expose a working web_search
tool without extra dependencies.
"""

from __future__ import annotations

import asyncio
import html
import re
import urllib.parse
import urllib.request
from typing import Any

_SEARCH_URL = "https://html.duckduckgo.com/html/"
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

_TITLE_RE = re.compile(
    r'<a[^>]*class="result__a"[^>]*href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>',
    re.DOTALL,
)
_SNIPPET_RE = re.compile(
    r'<a[^>]*class="result__snippet"[^>]*>(?P<snippet>.*?)</a>',
    re.DOTALL,
)


def _normalize_duckduckgo_href(href: str) -> str:
    if not href:
        return ""
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("http://") or href.startswith("https://"):
        return href
    if href.startswith("/"):
        parsed = urllib.parse.urlparse(href)
        query = urllib.parse.parse_qs(parsed.query)
        uddg = query.get("uddg", [""])
        if uddg[0]:
            return urllib.parse.unquote(uddg[0])
        return ""
    return href


def _strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(re.sub(r"\s+", " ", text)).strip()


def _search_duckduckgo_sync(query: str) -> list[dict[str, str]]:
    payload = urllib.parse.urlencode({"q": query}).encode("utf-8")
    request = urllib.request.Request(
        _SEARCH_URL,
        data=payload,
        headers={
            "User-Agent": _USER_AGENT,
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        raw = response.read().decode("utf-8", errors="replace")

    results: list[dict[str, str]] = []
    title_matches = list(_TITLE_RE.finditer(raw))
    for index, title_match in enumerate(title_matches):
        start = title_match.end()
        end = title_matches[index + 1].start() if index + 1 < len(title_matches) else len(raw)
        block = raw[start:end]
        url = _normalize_duckduckgo_href(title_match.group("href"))
        title = _strip_html(title_match.group("title"))
        snippet_match = _SNIPPET_RE.search(block)
        snippet = _strip_html(snippet_match.group("snippet")) if snippet_match else ""
        if title and url:
            results.append({"title": title, "url": url, "snippet": snippet})
    if results:
        return results[:8]

    # Fallback: salvage any absolute URLs if DDG markup changes.
    urls = re.findall(r"https?://[^\s\"'<>]+", raw)
    deduped: list[str] = []
    for url in urls:
        cleaned = html.unescape(url.rstrip(".,);"))
        if cleaned not in deduped:
            deduped.append(cleaned)
    return [
        {"title": url, "url": url, "snippet": ""}
        for url in deduped[:8]
    ]


async def default_web_search(query: str) -> list[dict[str, Any]]:
    """Run a web search using the built-in DuckDuckGo backend."""
    return await asyncio.to_thread(_search_duckduckgo_sync, query)
