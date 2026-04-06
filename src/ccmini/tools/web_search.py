"""WebSearchTool — search the web for real-time information.

Mirrors Claude Code's WebSearchTool, which uses Anthropic's built-in
web_search connector.  In mini-agent, we provide two backends:

1. **Anthropic native** (default when using AnthropicProvider):
   Uses the ``web_search_20250305`` tool type — zero config needed.
2. **HTTP fallback**: Calls a user-provided search endpoint
   (e.g. SerpAPI, Brave Search) for non-Anthropic providers.

The LLM sees a single ``web_search`` tool regardless of backend.

Extended with:
- Multiple search engine support with automatic fallback
- Token-bucket rate limiting
- Result deduplication across URLs
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from enum import Enum
from typing import Any

from ..tool import Tool, ToolUseContext

logger = logging.getLogger(__name__)


def _current_month_year() -> str:
    return datetime.now().strftime("%B %Y")


DESCRIPTION = "Search the web for real-time information about any topic."

INSTRUCTIONS = f"""\
Search the web for real-time information about any topic. Returns \
summarized information from search results and relevant URLs.

Use this tool when you need up-to-date information that might not be \
available in your training data, or when you need to verify current facts. \
This includes:
- Libraries, frameworks, and tools whose APIs or best practices are \
frequently updated
- Current events or technology news
- Informational queries similar to what you might search for online

CRITICAL REQUIREMENT — After answering the user's question, you MUST \
include a "Sources:" section at the end of your response listing all \
relevant URLs as markdown links.

IMPORTANT — Use the correct year in search queries:
- The current month is {_current_month_year()}. Use this year when searching \
for recent information, documentation, or current events.
- Example: If the user asks for "latest React docs", search for \
"React documentation" with the current year, NOT last year.\
"""


# ── Search engine enum ───────────────────────────────────────────────


class SearchEngine(str, Enum):
    DUCKDUCKGO = "duckduckgo"
    TAVILY = "tavily"
    BRAVE = "brave"
    SERPER = "serper"


class WebSearchTool(Tool):
    """Web search tool with pluggable backend.

    Args:
        search_fn: Async callable ``(query: str) -> list[dict]`` that
            returns search results. Each dict should have ``title``,
            ``url``, and ``snippet`` keys.  If None, returns an error
            asking the host to configure a search backend.
        fallback_fn: Optional secondary search callable used when
            *search_fn* fails.
        engine: Primary search engine label (informational).
        fallback_engine: Fallback search engine label.
        max_requests_per_minute: Rate limit cap. Excess requests are
            queued automatically.
    """

    name = "WebSearch"
    description = DESCRIPTION
    instructions = INSTRUCTIONS
    is_read_only = True

    def __init__(
        self,
        search_fn: Any | None = None,
        *,
        fallback_fn: Any | None = None,
        engine: SearchEngine = SearchEngine.DUCKDUCKGO,
        fallback_engine: SearchEngine | None = None,
        max_requests_per_minute: int = 30,
    ) -> None:
        self._search_fn = search_fn
        self._fallback_fn = fallback_fn
        self._engine = engine
        self._fallback_engine = fallback_engine
        self._rate_limiter = _TokenBucketRateLimiter(max_requests_per_minute)

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query to look up.",
                },
                "allowed_domains": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Only include results from these domains.",
                },
                "blocked_domains": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Exclude results from these domains.",
                },
            },
            "required": ["query"],
        }

    async def execute(self, *, context: ToolUseContext, **kwargs: Any) -> str:
        query = kwargs.get("query", "")
        if not query:
            return "Error: 'query' parameter is required."

        allowed_domains: list[str] = kwargs.get("allowed_domains", [])
        blocked_domains: list[str] = kwargs.get("blocked_domains", [])

        if self._search_fn is None and self._fallback_fn is None:
            return (
                "Web search is not configured. The host application must "
                "provide a search_fn to WebSearchTool. Example:\n"
                "  WebSearchTool(search_fn=my_search_function)"
            )

        await self._rate_limiter.acquire()

        results: list[dict[str, Any]] | None = None

        if self._search_fn is not None:
            try:
                results = await self._search_fn(query)
            except Exception as exc:
                logger.warning(
                    "Primary search (%s) failed for '%s': %s",
                    self._engine.value, query, exc,
                )

        if (results is None or len(results) == 0) and self._fallback_fn is not None:
            logger.info("Falling back to %s for '%s'", self._fallback_engine, query)
            try:
                results = await self._fallback_fn(query)
            except Exception as exc:
                logger.warning(
                    "Fallback search (%s) failed for '%s': %s",
                    self._fallback_engine, query, exc,
                )
                return f"Search failed on both engines: {exc}"

        if not results:
            return f"No results found for: {query}"

        results = _filter_domains(results, allowed_domains, blocked_domains)
        results = _deduplicate_results(results)

        lines: list[str] = []
        for r in results[:8]:
            title = r.get("title", "")
            url = r.get("url", "")
            snippet = r.get("snippet", "")
            lines.append(f"**{title}**\n{snippet}\nURL: {url}\n")

        return "\n".join(lines)


# ── Rate limiting ────────────────────────────────────────────────────


class _TokenBucketRateLimiter:
    """Simple async token-bucket rate limiter.

    Allows *max_per_minute* requests per minute. Excess callers
    ``await acquire()`` until a token is available.
    """

    def __init__(self, max_per_minute: int) -> None:
        self._max = max(1, max_per_minute)
        self._tokens = float(self._max)
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            self._refill()
            while self._tokens < 1.0:
                deficit = 1.0 - self._tokens
                wait = deficit / (self._max / 60.0)
                await asyncio.sleep(wait)
                self._refill()
            self._tokens -= 1.0

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(float(self._max), self._tokens + elapsed * (self._max / 60.0))
        self._last_refill = now


# ── Deduplication & filtering ────────────────────────────────────────


def _deduplicate_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove duplicate URLs, merging snippets from the same URL."""
    seen: dict[str, dict[str, Any]] = {}
    for r in results:
        url = r.get("url", "")
        if not url:
            continue
        normalized = url.rstrip("/").lower()
        if normalized in seen:
            existing_snippet = seen[normalized].get("snippet", "")
            new_snippet = r.get("snippet", "")
            if new_snippet and new_snippet not in existing_snippet:
                seen[normalized]["snippet"] = existing_snippet + " … " + new_snippet
        else:
            seen[normalized] = dict(r)
    return list(seen.values())


def _filter_domains(
    results: list[dict[str, Any]],
    allowed: list[str],
    blocked: list[str],
) -> list[dict[str, Any]]:
    """Filter results by allowed/blocked domain lists."""
    if not allowed and not blocked:
        return results

    filtered: list[dict[str, Any]] = []
    for r in results:
        url = r.get("url", "")
        domain = _extract_domain(url)
        if allowed and domain not in allowed:
            continue
        if blocked and domain in blocked:
            continue
        filtered.append(r)
    return filtered


def _extract_domain(url: str) -> str:
    try:
        from urllib.parse import urlparse
        return urlparse(url).hostname or ""
    except Exception:
        return ""
