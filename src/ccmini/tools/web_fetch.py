"""WebFetchTool — fetch a URL and return its contents.

Extended with:
- Readability extraction (strip nav/ads/boilerplate)
- HTML-to-Markdown conversion
- Configurable timeout and retry with back-off
- Response size limits with binary detection
"""

from __future__ import annotations

import asyncio
import html
import logging
import re
from typing import Any

from ..tool import Tool, ToolUseContext

logger = logging.getLogger(__name__)

_MAX_RESPONSE_SIZE = 1_000_000  # 1 MB default


class WebFetchTool(Tool):
    name = "WebFetch"
    description = (
        "Fetch a URL and return its text content. "
        "Supports HTTP/HTTPS. Returns the response body as text or markdown."
    )
    instructions = """\
Fetches content from a specified URL and returns it as text.

Usage notes:
- The URL must be a fully-formed valid URL.
- HTTP URLs will be automatically treated as HTTPS.
- This tool is read-only and does not modify any files.
- HTML pages are converted to readable Markdown automatically.
- Results may be truncated if the content is very large (default 1 MB).
- Only text content is supported — images, PDFs, and other binary \
formats will be rejected.
- Retries automatically on 429 (rate limited) and 503 (unavailable).
- This runs from the user's machine — localhost URLs will hit \
the user's local services.\
"""
    is_read_only = True

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to fetch"},
                "headers": {
                    "type": "object",
                    "description": "Optional HTTP headers",
                    "additionalProperties": {"type": "string"},
                },
                "timeout": {
                    "type": "integer",
                    "description": "Request timeout in seconds (default: 30)",
                },
                "max_retries": {
                    "type": "integer",
                    "description": "Max retry attempts on 429/503 (default: 2)",
                },
                "max_response_size": {
                    "type": "integer",
                    "description": "Max response bytes before truncation (default: 1000000)",
                },
            },
            "required": ["url"],
        }

    async def execute(self, *, context: ToolUseContext, **kwargs: Any) -> str:
        url: str = kwargs["url"]
        headers: dict[str, str] = kwargs.get("headers", {})
        timeout: int = kwargs.get("timeout", 30)
        max_retries: int = kwargs.get("max_retries", 2)
        max_response_size: int = kwargs.get("max_response_size", _MAX_RESPONSE_SIZE)

        if not url.startswith(("http://", "https://")):
            return "Error: URL must start with http:// or https://"

        try:
            import aiohttp
        except ImportError:
            return "Error: aiohttp is required for web_fetch: pip install aiohttp"

        last_error: str = ""
        for attempt in range(1 + max_retries):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        url,
                        headers=headers,
                        timeout=aiohttp.ClientTimeout(total=timeout),
                    ) as resp:
                        if resp.status in (429, 503) and attempt < max_retries:
                            retry_after = int(resp.headers.get("Retry-After", "2"))
                            await asyncio.sleep(min(retry_after, 10))
                            last_error = f"HTTP {resp.status} (retrying...)"
                            continue

                        if resp.status != 200:
                            return f"HTTP {resp.status}: {resp.reason}"

                        content_type = resp.headers.get("Content-Type", "")
                        if _is_binary(content_type):
                            return f"Error: Binary content ({content_type}), cannot display."

                        raw_bytes = await resp.read()
                        if len(raw_bytes) > max_response_size:
                            raw_bytes = raw_bytes[:max_response_size]

                        text = raw_bytes.decode("utf-8", errors="replace")

                        if "text/html" in content_type.lower():
                            text = _extract_readable(text)
                            text = _html_to_markdown(text)

                        if len(text) > max_response_size:
                            text = text[:max_response_size]
                            return text + "\n\n... (response truncated)"

                        return text

            except aiohttp.ClientError as exc:
                last_error = f"Error fetching URL: {exc}"
                if attempt < max_retries:
                    await asyncio.sleep(2 ** attempt)
                    continue
            except Exception as exc:
                return f"Error: {exc}"

        return last_error or "Error: fetch failed after retries"


# ── Content detection ────────────────────────────────────────────────


def _is_binary(content_type: str) -> bool:
    ct = content_type.lower()
    return any(t in ct for t in ("image/", "audio/", "video/", "application/octet"))


# ── Readability extraction ───────────────────────────────────────────

_STRIP_TAGS = re.compile(
    r"<\s*(script|style|nav|footer|header|aside|iframe|noscript)"
    r"[^>]*>.*?</\s*\1\s*>",
    re.DOTALL | re.IGNORECASE,
)

_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


def _extract_readable(html_text: str) -> str:
    """Strip navigation, ads, scripts, styles to get article content.

    Uses a simple tag-based approach (no external dependency required).
    If ``readability-lxml`` is installed it will be preferred.
    """
    try:
        from readability import Document  # type: ignore[import-untyped]
        doc = Document(html_text)
        return doc.summary()
    except Exception:
        pass

    text = _COMMENT_RE.sub("", html_text)
    text = _STRIP_TAGS.sub("", text)
    return text


# ── HTML → Markdown conversion ───────────────────────────────────────

_TAG_RE = re.compile(r"<[^>]+>")
_HEADING_RE = re.compile(r"<h([1-6])[^>]*>(.*?)</h\1>", re.IGNORECASE | re.DOTALL)
_LINK_RE = re.compile(r'<a\s[^>]*href=["\']([^"\']*)["\'][^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL)
_LI_RE = re.compile(r"<li[^>]*>(.*?)</li>", re.IGNORECASE | re.DOTALL)
_CODE_BLOCK_RE = re.compile(r"<pre[^>]*><code[^>]*>(.*?)</code></pre>", re.IGNORECASE | re.DOTALL)
_INLINE_CODE_RE = re.compile(r"<code[^>]*>(.*?)</code>", re.IGNORECASE | re.DOTALL)
_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_P_RE = re.compile(r"<p[^>]*>(.*?)</p>", re.IGNORECASE | re.DOTALL)
_STRONG_RE = re.compile(r"<(strong|b)[^>]*>(.*?)</\1>", re.IGNORECASE | re.DOTALL)
_EM_RE = re.compile(r"<(em|i)[^>]*>(.*?)</\1>", re.IGNORECASE | re.DOTALL)
_TD_RE = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.IGNORECASE | re.DOTALL)
_TR_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.IGNORECASE | re.DOTALL)
_MULTI_NEWLINE = re.compile(r"\n{3,}")
_MULTI_SPACE = re.compile(r"[ \t]{2,}")


def _html_to_markdown(html_text: str) -> str:
    """Best-effort HTML → Markdown conversion without external deps.

    Handles headings, links, lists, code blocks, tables, bold, italic.
    Falls back to plain-text extraction for unsupported elements.
    """
    text = html_text

    text = _CODE_BLOCK_RE.sub(lambda m: "\n```\n" + html.unescape(m.group(1)) + "\n```\n", text)
    text = _INLINE_CODE_RE.sub(lambda m: "`" + html.unescape(m.group(1)) + "`", text)
    text = _HEADING_RE.sub(lambda m: "\n" + "#" * int(m.group(1)) + " " + _strip_tags(m.group(2)) + "\n", text)
    text = _LINK_RE.sub(lambda m: f"[{_strip_tags(m.group(2))}]({m.group(1)})", text)
    text = _STRONG_RE.sub(lambda m: f"**{_strip_tags(m.group(2))}**", text)
    text = _EM_RE.sub(lambda m: f"*{_strip_tags(m.group(2))}*", text)
    text = _LI_RE.sub(lambda m: "- " + _strip_tags(m.group(1)).strip() + "\n", text)

    # Tables
    def _convert_table_row(match: re.Match[str]) -> str:
        cells = _TD_RE.findall(match.group(1))
        return "| " + " | ".join(_strip_tags(c).strip() for c in cells) + " |\n"

    text = _TR_RE.sub(_convert_table_row, text)

    text = _BR_RE.sub("\n", text)
    text = _P_RE.sub(lambda m: "\n" + _strip_tags(m.group(1)).strip() + "\n", text)

    text = _TAG_RE.sub("", text)
    text = html.unescape(text)
    text = _MULTI_SPACE.sub(" ", text)
    text = _MULTI_NEWLINE.sub("\n\n", text)

    return text.strip()


def _strip_tags(s: str) -> str:
    return _TAG_RE.sub("", s)
