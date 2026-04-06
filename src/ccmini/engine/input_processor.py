"""Input preprocessor — port of ``processUserInput/processUserInput.ts``.

Transforms raw user text into structured messages before the query loop:

1. **Slash command routing** — ``/compact``, ``/buddy``, etc.
2. **@file expansion** — ``@path/to/file`` → file content attachment
3. **@url expansion** — ``@https://...`` → fetch content attachment
4. **Paste detection** — Large text blocks auto-wrapped
5. **Image attachment** — Paths ending in image extensions
6. **Multi-line joining** — Smart whitespace normalization

Usage::

    processor = InputProcessor(cwd="/project", command_registry=registry)
    result = await processor.process("@src/main.py explain this")
    if result.should_query:
        agent.send(result.messages)
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..commands import CommandRegistry

logger = logging.getLogger(__name__)

# ── Result type ──────────────────────────────────────────────────────


@dataclass
class ProcessedInput:
    """Result of input preprocessing."""
    messages: list[dict[str, Any]] = field(default_factory=list)
    should_query: bool = True
    command_output: str = ""
    attachments: list[dict[str, Any]] = field(default_factory=list)
    model_override: str = ""
    effort_override: str = ""
    allowed_tools: list[str] | None = None

    @property
    def user_text(self) -> str:
        """Extract the plain user text from messages."""
        for msg in self.messages:
            if msg.get("role") == "user":
                content = msg.get("content", "")
                if isinstance(content, str):
                    return content
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        return block.get("text", "")
        return ""


# ── @-mention patterns ───────────────────────────────────────────────

_AT_FILE_RE = re.compile(
    r"@((?:[a-zA-Z]:)?[\w./\\~-][\w./\\~\-: ]*\.\w+)"
)

_AT_URL_RE = re.compile(
    r"@(https?://\S+)"
)

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"}

_PASTE_THRESHOLD = 500  # chars — longer than this is "pasted"

# ── Slash command detection ──────────────────────────────────────────

_SLASH_RE = re.compile(r"^/(\w[\w-]*)")


# ── File content reader ──────────────────────────────────────────────


def _read_file_content(path: Path, *, max_bytes: int = 100_000) -> str | None:
    """Read file content with size limit. Returns None if unreadable."""
    if not path.exists() or not path.is_file():
        return None
    try:
        size = path.stat().st_size
        if size > max_bytes:
            text = path.read_text(encoding="utf-8", errors="replace")[:max_bytes]
            return text + f"\n\n[... truncated at {max_bytes:,} bytes, total {size:,} bytes]"
        return path.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError):
        return None


def _resolve_path(ref: str, cwd: str) -> Path | None:
    """Resolve an @-reference to an absolute path."""
    ref = ref.strip()
    if ref.startswith("~"):
        return Path(ref).expanduser()
    p = Path(ref)
    if p.is_absolute():
        return p
    resolved = Path(cwd) / ref
    if resolved.exists():
        return resolved
    return None


# ── Core processor ───────────────────────────────────────────────────


class InputProcessor:
    """Preprocesses user input before the agent query loop."""

    def __init__(
        self,
        cwd: str = "",
        *,
        command_registry: CommandRegistry | None = None,
        max_file_bytes: int = 100_000,
    ) -> None:
        self._cwd = cwd or os.getcwd()
        self._commands = command_registry
        self._max_file_bytes = max_file_bytes

    @property
    def cwd(self) -> str:
        return self._cwd

    @cwd.setter
    def cwd(self, value: str) -> None:
        self._cwd = value

    async def process(self, raw_input: str) -> ProcessedInput:
        """Process raw user input into a structured result.

        Steps:
        1. Check for slash commands
        2. Expand @file references
        3. Expand @url references
        4. Detect paste / multi-line
        5. Build message content blocks
        """
        text = raw_input.strip()
        if not text:
            return ProcessedInput(should_query=False)

        # ── Step 1: Slash commands ───────────────────────────────────
        slash_match = _SLASH_RE.match(text)
        if slash_match:
            result = await self._handle_slash_command(text)
            if result is not None:
                return result

        # ── Step 2-3: @-reference expansion ──────────────────────────
        attachments: list[dict[str, Any]] = []
        clean_text = text

        # @url references
        for url_match in _AT_URL_RE.finditer(text):
            url = url_match.group(1)
            attachments.append({
                "type": "url",
                "url": url,
                "source": url_match.group(0),
            })
            clean_text = clean_text.replace(url_match.group(0), "", 1)

        # @file references
        for file_match in _AT_FILE_RE.finditer(clean_text):
            ref = file_match.group(1)
            path = _resolve_path(ref, self._cwd)
            if path is None:
                continue

            ext = path.suffix.lower()
            if ext in _IMAGE_EXTS:
                attachments.append({
                    "type": "image",
                    "path": str(path),
                    "source": file_match.group(0),
                })
            else:
                content = _read_file_content(path, max_bytes=self._max_file_bytes)
                if content is not None:
                    attachments.append({
                        "type": "file",
                        "path": str(path),
                        "content": content,
                        "source": file_match.group(0),
                    })

            clean_text = clean_text.replace(file_match.group(0), "", 1)

        clean_text = clean_text.strip()

        # ── Step 4: Paste detection ──────────────────────────────────
        is_paste = len(raw_input) > _PASTE_THRESHOLD and "\n" in raw_input

        # ── Step 5: Build message ────────────────────────────────────
        content_blocks: list[dict[str, Any]] = []

        if clean_text:
            content_blocks.append({"type": "text", "text": clean_text})

        for att in attachments:
            if att["type"] == "file":
                header = f"File: {att['path']}"
                content_blocks.append({
                    "type": "text",
                    "text": f"<attached_file path=\"{att['path']}\">\n{att['content']}\n</attached_file>",
                })
            elif att["type"] == "image":
                content_blocks.append({
                    "type": "image",
                    "path": att["path"],
                })
            elif att["type"] == "url":
                content_blocks.append({
                    "type": "text",
                    "text": f"<url>{att['url']}</url>",
                })

        if not content_blocks:
            return ProcessedInput(should_query=False)

        if len(content_blocks) == 1 and content_blocks[0].get("type") == "text":
            user_content = content_blocks[0]["text"]
        else:
            user_content = content_blocks

        messages = [{"role": "user", "content": user_content}]

        return ProcessedInput(
            messages=messages,
            should_query=True,
            attachments=attachments,
        )

    async def _handle_slash_command(self, text: str) -> ProcessedInput | None:
        """Try to handle input as a slash command. Returns None if not a command."""
        if self._commands is None:
            return None

        parts = text[1:].split(None, 1)
        cmd_name = parts[0].lower() if parts else ""
        cmd_args = parts[1] if len(parts) > 1 else ""

        cmd = self._commands.get_by_name(cmd_name)
        if cmd is None:
            return None

        return ProcessedInput(
            messages=[],
            should_query=False,
            command_output=f"/{cmd_name} {cmd_args}".strip(),
        )


# ── Convenience functions ────────────────────────────────────────────


def expand_at_references(
    text: str,
    cwd: str = "",
    *,
    max_file_bytes: int = 100_000,
) -> tuple[str, list[dict[str, Any]]]:
    """Expand @file references inline, returning clean text + attachments.

    Synchronous convenience for non-async contexts.
    """
    cwd = cwd or os.getcwd()
    attachments: list[dict[str, Any]] = []
    result = text

    for file_match in _AT_FILE_RE.finditer(text):
        ref = file_match.group(1)
        path = _resolve_path(ref, cwd)
        if path is None:
            continue
        content = _read_file_content(path, max_bytes=max_file_bytes)
        if content is not None:
            attachments.append({
                "type": "file",
                "path": str(path),
                "content": content,
            })
            result = result.replace(
                file_match.group(0),
                f"[content of {path.name}]",
                1,
            )

    return result, attachments


def is_slash_command(text: str) -> bool:
    """Check if text starts with a slash command."""
    return bool(_SLASH_RE.match(text.strip()))


# ── Image processing ─────────────────────────────────────────────────

# Anthropic's token cost model: images are tiled into 768×768 blocks,
# each costing ~base tokens.  These constants mirror the API docs.
_IMAGE_BASE_TOKENS = 85
_IMAGE_TILE_TOKENS = 170
_TILE_SIZE = 768

_MAX_IMAGE_DIMENSION = 8_000
_MAX_IMAGE_BYTES = 20 * 1024 * 1024  # 20 MB — Anthropic API limit


def estimate_image_tokens(width: int, height: int) -> int:
    """Estimate the token cost of an image based on its dimensions.

    Uses the same tiling formula as the Anthropic API:
    ``base + tiles * per_tile`` where tiles = ceil(w/768) * ceil(h/768).
    """
    if width <= 0 or height <= 0:
        return _IMAGE_BASE_TOKENS
    import math
    tiles_x = math.ceil(width / _TILE_SIZE)
    tiles_y = math.ceil(height / _TILE_SIZE)
    return _IMAGE_BASE_TOKENS + (tiles_x * tiles_y) * _IMAGE_TILE_TOKENS


def should_include_image(
    image_path: str,
    remaining_budget: int,
    *,
    max_dimension: int = _MAX_IMAGE_DIMENSION,
    max_bytes: int = _MAX_IMAGE_BYTES,
) -> bool:
    """Check whether an image should be included in the API request.

    Validates:
    - File exists and is a supported image format
    - File size is under the API limit
    - Estimated token cost fits within the remaining budget
    """
    p = Path(image_path)
    if not p.exists() or not p.is_file():
        return False
    if p.suffix.lower() not in _IMAGE_EXTS:
        return False

    try:
        size = p.stat().st_size
        if size > max_bytes:
            return False
    except OSError:
        return False

    estimated_tokens = estimate_image_tokens(max_dimension, max_dimension)
    return estimated_tokens <= remaining_budget


def process_image_input(
    image_path: str,
    *,
    max_dimension: int = _MAX_IMAGE_DIMENSION,
    target_tokens: int | None = None,
) -> dict[str, Any] | None:
    """Process an image file for API inclusion.

    Returns a content block dict suitable for the Anthropic messages API,
    or ``None`` if the file cannot be processed.

    If *target_tokens* is given and the image exceeds that budget,
    metadata with a resize suggestion is returned instead of the
    raw image data.
    """
    p = Path(image_path)
    if not p.exists():
        return None

    ext = p.suffix.lower()
    media_map = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }
    media_type = media_map.get(ext)
    if media_type is None:
        return None

    try:
        import base64
        data = p.read_bytes()
        if len(data) > _MAX_IMAGE_BYTES:
            return {
                "type": "text",
                "text": f"[Image too large: {len(data):,} bytes, max {_MAX_IMAGE_BYTES:,}]",
            }

        b64 = base64.standard_b64encode(data).decode("ascii")
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type,
                "data": b64,
            },
        }
    except OSError as exc:
        logger.warning("Failed to read image %s: %s", image_path, exc)
        return None


# ── Attachment pipeline ──────────────────────────────────────────────


class AttachmentType:
    """Recognised attachment types."""
    IMAGE = "image"
    FILE = "file"
    URL = "url"
    AUDIO = "audio"


_ATTACHMENT_SIZE_LIMITS: dict[str, int] = {
    AttachmentType.IMAGE: 20 * 1024 * 1024,   # 20 MB
    AttachmentType.FILE: 10 * 1024 * 1024,     # 10 MB
    AttachmentType.URL: 1 * 1024 * 1024,       # 1 MB (fetched content)
    AttachmentType.AUDIO: 50 * 1024 * 1024,    # 50 MB
}

_AUDIO_EXTS = {".mp3", ".wav", ".ogg", ".m4a", ".flac", ".aac"}


@dataclass
class ProcessedAttachment:
    """A single processed attachment ready for message inclusion."""
    attachment_type: str
    content: dict[str, Any] | str
    source_path: str = ""
    token_estimate: int = 0
    error: str = ""


def process_attachments(
    attachments: list[dict[str, Any]],
    *,
    max_file_bytes: int = 100_000,
    cwd: str = "",
) -> list[ProcessedAttachment]:
    """Process a heterogeneous list of attachments.

    Each attachment dict should have at minimum a ``"type"`` key
    (``"image"``, ``"file"``, ``"url"``, ``"audio"``).
    """
    results: list[ProcessedAttachment] = []
    for att in attachments:
        att_type = att.get("type", "file")

        if att_type == AttachmentType.IMAGE:
            path = att.get("path", "")
            block = process_image_input(path)
            results.append(ProcessedAttachment(
                attachment_type=AttachmentType.IMAGE,
                content=block or {"type": "text", "text": f"[Image not found: {path}]"},
                source_path=path,
                token_estimate=estimate_image_tokens(2000, 2000),
                error="" if block else f"Failed to process image: {path}",
            ))

        elif att_type == AttachmentType.FILE:
            path = att.get("path", "")
            resolved = _resolve_path(path, cwd or os.getcwd()) if path else None
            if resolved and resolved.suffix.lower() in _DOCUMENT_EXTS:
                block = process_document_input(str(resolved), max_bytes=max_file_bytes)
                results.append(ProcessedAttachment(
                    attachment_type=AttachmentType.FILE,
                    content=block or f"[File not readable: {path}]",
                    source_path=str(resolved),
                    token_estimate=estimate_document_tokens(str(resolved)),
                    error="" if block else f"Failed to read: {path}",
                ))
            else:
                content = _read_file_content(resolved, max_bytes=max_file_bytes) if resolved else None
                results.append(ProcessedAttachment(
                    attachment_type=AttachmentType.FILE,
                    content=content or f"[File not readable: {path}]",
                    source_path=str(resolved or path),
                    token_estimate=len(content) // 4 if content else 0,
                    error="" if content else f"Failed to read: {path}",
                ))

        elif att_type == AttachmentType.URL:
            url = att.get("url", "")
            results.append(ProcessedAttachment(
                attachment_type=AttachmentType.URL,
                content=f"<url>{url}</url>",
                source_path=url,
                token_estimate=100,
            ))

        elif att_type == AttachmentType.AUDIO:
            path = att.get("path", "")
            results.append(ProcessedAttachment(
                attachment_type=AttachmentType.AUDIO,
                content=f"[Audio file: {path}]",
                source_path=path,
                token_estimate=500,
            ))

        else:
            results.append(ProcessedAttachment(
                attachment_type=att_type,
                content=str(att),
                error=f"Unknown attachment type: {att_type}",
            ))

    return results


# ── Input validation ─────────────────────────────────────────────────

_INJECTION_PATTERNS = [
    re.compile(r"<\|(?:system|im_start|im_end|endoftext)\|>", re.IGNORECASE),
    re.compile(r"(?:^|\n)\s*SYSTEM\s*:", re.IGNORECASE),
    re.compile(r"\bignore\s+(?:all\s+)?(?:previous|above)\s+instructions?\b", re.IGNORECASE),
    re.compile(r"\byou\s+are\s+now\s+(?:a|an|in)\b", re.IGNORECASE),
    re.compile(r"\b(?:forget|disregard)\s+(?:all\s+)?(?:your|the)\s+(?:instructions?|rules?|guidelines?)\b", re.IGNORECASE),
]


def detect_prompt_injection(text: str) -> list[str]:
    """Heuristic detection of common prompt injection patterns.

    Returns a list of matched pattern descriptions.  An empty list
    means no suspicious patterns were found.

    This is a best-effort defence layer — not a guarantee.
    """
    findings: list[str] = []
    for pattern in _INJECTION_PATTERNS:
        if pattern.search(text):
            findings.append(f"Suspicious pattern: {pattern.pattern[:60]}")
    return findings


def validate_user_input(text: str, *, max_length: int = 1_000_000) -> list[str]:
    """Validate user input for obvious problems.

    Returns a list of warning strings.  Empty means all clear.
    """
    warnings: list[str] = []
    if len(text) > max_length:
        warnings.append(f"Input too long: {len(text):,} chars (max {max_length:,})")

    injections = detect_prompt_injection(text)
    if injections:
        warnings.extend(injections)

    null_count = text.count("\x00")
    if null_count > 0:
        warnings.append(f"Input contains {null_count} null bytes")

    return warnings


def sanitize_input(text: str) -> str:
    """Clean potentially dangerous patterns from user input.

    Removes null bytes and normalises special tokens.
    Does NOT strip injection attempts — see ``validate_user_input``
    for detection and ``detect_prompt_injection`` for specifics.
    """
    cleaned = text.replace("\x00", "")

    cleaned = re.sub(r"<\|(?:system|im_start|im_end|endoftext)\|>", "[filtered_token]", cleaned)

    return cleaned


# ── Document / PDF attachment support ────────────────────────────────

_DOCUMENT_EXTS = {".pdf", ".doc", ".docx", ".txt", ".md", ".rst", ".csv", ".tsv"}

_DOCUMENT_MEDIA_MAP: dict[str, str] = {
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".rst": "text/x-rst",
    ".csv": "text/csv",
    ".tsv": "text/tab-separated-values",
}


def process_document_input(
    doc_path: str,
    *,
    max_bytes: int = 10 * 1024 * 1024,
) -> dict[str, Any] | None:
    """Process a document file for API inclusion.

    Returns a content block dict for the Anthropic messages API,
    or ``None`` if the file cannot be processed.  For PDF files,
    returns a ``document`` block; for text formats, returns a text block
    with the file content.
    """
    p = Path(doc_path)
    if not p.exists() or not p.is_file():
        return None

    ext = p.suffix.lower()
    if ext not in _DOCUMENT_EXTS:
        return None

    try:
        size = p.stat().st_size
        if size > max_bytes:
            return {
                "type": "text",
                "text": f"[Document too large: {size:,} bytes, max {max_bytes:,}]",
            }
    except OSError:
        return None

    if ext == ".pdf":
        try:
            import base64
            data = p.read_bytes()
            b64 = base64.standard_b64encode(data).decode("ascii")
            return {
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": b64,
                },
            }
        except OSError as exc:
            logger.warning("Failed to read document %s: %s", doc_path, exc)
            return None

    try:
        content = p.read_text(encoding="utf-8", errors="replace")
        return {
            "type": "text",
            "text": f"<document path=\"{doc_path}\" type=\"{ext.lstrip('.')}\">\n{content}\n</document>",
        }
    except OSError as exc:
        logger.warning("Failed to read document %s: %s", doc_path, exc)
        return None


def estimate_document_tokens(doc_path: str) -> int:
    """Rough token estimate for a document based on file size."""
    p = Path(doc_path)
    if not p.exists():
        return 0
    try:
        size = p.stat().st_size
        return max(1, size // 4)
    except OSError:
        return 0


# ── Content block image stripping ────────────────────────────────────


def strip_images_from_content_blocks(
    content: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Replace image/document blocks with text markers in content arrays.

    Ported from Claude Code's ``stripImagesFromMessages`` — used before
    sending messages to the compaction summariser.  Also handles images
    nested inside tool_result content arrays.
    """
    result: list[dict[str, Any]] = []
    for block in content:
        btype = block.get("type", "")

        if btype == "image":
            result.append({"type": "text", "text": "[image]"})
            continue
        if btype == "document":
            result.append({"type": "text", "text": "[document]"})
            continue

        if btype == "tool_result" and isinstance(block.get("content"), list):
            inner = []
            for item in block["content"]:
                item_type = item.get("type", "") if isinstance(item, dict) else ""
                if item_type == "image":
                    inner.append({"type": "text", "text": "[image]"})
                elif item_type == "document":
                    inner.append({"type": "text", "text": "[document]"})
                else:
                    inner.append(item)
            result.append({**block, "content": inner})
            continue

        result.append(block)

    return result


# ── Attachment type classification ───────────────────────────────────


def classify_attachment_type(path_or_url: str) -> str:
    """Classify an attachment by its extension or URL pattern.

    Returns one of the ``AttachmentType`` constants.
    """
    if path_or_url.startswith(("http://", "https://")):
        return AttachmentType.URL

    ext = Path(path_or_url).suffix.lower()
    if ext in _IMAGE_EXTS:
        return AttachmentType.IMAGE
    if ext in _AUDIO_EXTS:
        return AttachmentType.AUDIO
    if ext in _DOCUMENT_EXTS:
        return AttachmentType.FILE
    return AttachmentType.FILE


# ── Enhanced MIME detection ──────────────────────────────────────────


_MIME_MAP: dict[str, str] = {
    **{ext: "image/png" if ext == ".png" else
       "image/jpeg" if ext in {".jpg", ".jpeg"} else
       "image/gif" if ext == ".gif" else
       "image/webp" if ext == ".webp" else
       "image/bmp" if ext == ".bmp" else
       "image/svg+xml"
       for ext in _IMAGE_EXTS},
    **_DOCUMENT_MEDIA_MAP,
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".m4a": "audio/mp4",
    ".flac": "audio/flac",
    ".aac": "audio/aac",
}


def detect_mime_type(path: str) -> str | None:
    """Detect MIME type from file extension."""
    ext = Path(path).suffix.lower()
    return _MIME_MAP.get(ext)
