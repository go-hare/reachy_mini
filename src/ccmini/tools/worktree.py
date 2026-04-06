"""Git Worktree tools — create and manage isolated worktrees.

``EnterWorktreeTool`` creates a git worktree and switches the session
into it.  ``ExitWorktreeTool`` returns to the original directory and
optionally removes the worktree.

Auto-cleanup is registered via :mod:`atexit` so orphaned worktrees are
removed if the agent process exits without an explicit exit call.
"""

from __future__ import annotations

import asyncio
import atexit
import hashlib
import logging
import os
import re
import shutil
import sys
import time
from dataclasses import dataclass, field
from typing import Any

from ..tool import Tool, ToolUseContext

log = logging.getLogger(__name__)

_WORKTREE_BASE = ".git/mini-agent-worktrees"
_WORKTREE_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*$")


@dataclass(slots=True)
class WorktreeSession:
    """Tracks state for an active worktree created by EnterWorktreeTool."""
    worktree_path: str
    branch_name: str
    original_cwd: str
    original_head_commit: str | None = None
    created_at: float = field(default_factory=time.time)


@dataclass(slots=True)
class AgentWorktree:
    """Temporary worktree used for Agent tool isolation."""
    worktree_path: str
    branch_name: str
    original_cwd: str
    original_head_commit: str | None = None
    repo_root: str = ""


_active_session: WorktreeSession | None = None


def get_active_worktree_session() -> WorktreeSession | None:
    return _active_session


async def create_agent_worktree(
    *,
    cwd: str,
    slug: str,
    base_branch: str = "HEAD",
) -> AgentWorktree:
    """Create a throwaway git worktree for an agent invocation."""
    if not await _is_git_repo(cwd):
        raise RuntimeError("Not inside a git repository.")

    repo_root = await _canonical_git_root(cwd)
    if not repo_root:
        raise RuntimeError("Could not determine git repository root.")

    head_commit = await _head_commit(repo_root)
    branch_name = f"{slug}-{hashlib.sha1(f'{slug}-{time.time_ns()}'.encode()).hexdigest()[:8]}"
    wt_path = _generate_worktree_path(repo_root, branch_name)
    os.makedirs(os.path.dirname(wt_path), exist_ok=True)

    rc, out, err = await _run_git(
        "worktree", "add", "-b", branch_name, wt_path, base_branch,
        cwd=repo_root,
    )
    if rc != 0:
        raise RuntimeError(f"Error creating worktree: {err or out}")

    return AgentWorktree(
        worktree_path=wt_path,
        branch_name=branch_name,
        original_cwd=cwd,
        original_head_commit=head_commit,
        repo_root=repo_root,
    )


async def cleanup_agent_worktree(
    worktree: AgentWorktree,
    *,
    keep_on_changes: bool = True,
) -> dict[str, str]:
    """Remove an agent worktree unless it contains changes."""
    changes = await _count_worktree_changes(
        worktree.worktree_path,
        worktree.original_head_commit,
    )
    if keep_on_changes and changes is not None:
        changed_files, commits = changes
        if changed_files > 0 or commits > 0:
            return {
                "status": "kept",
                "worktree_path": worktree.worktree_path,
                "branch_name": worktree.branch_name,
            }

    rc, _, err = await _run_git(
        "worktree", "remove", "--force", worktree.worktree_path,
        cwd=worktree.repo_root or worktree.original_cwd,
    )
    if rc != 0:
        shutil.rmtree(worktree.worktree_path, ignore_errors=True)
        log.warning("git worktree remove failed (%s), used shutil fallback", err)
    await _run_git(
        "branch", "-D", worktree.branch_name,
        cwd=worktree.repo_root or worktree.original_cwd,
    )
    return {"status": "removed"}


async def _run_git(*args: str, cwd: str | None = None) -> tuple[int, str, str]:
    """Run a git command and return (returncode, stdout, stderr)."""
    proc = await asyncio.create_subprocess_exec(
        "git", *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
    )
    stdout, stderr = await proc.communicate()
    return (
        proc.returncode or 0,
        stdout.decode("utf-8", errors="replace").strip(),
        stderr.decode("utf-8", errors="replace").strip(),
    )


async def _is_git_repo(path: str) -> bool:
    rc, _, _ = await _run_git("rev-parse", "--is-inside-work-tree", cwd=path)
    return rc == 0


async def _git_root(path: str) -> str | None:
    rc, out, _ = await _run_git("rev-parse", "--show-toplevel", cwd=path)
    return out if rc == 0 else None


async def _git_common_dir(path: str) -> str | None:
    rc, out, _ = await _run_git(
        "rev-parse",
        "--path-format=absolute",
        "--git-common-dir",
        cwd=path,
    )
    return out if rc == 0 else None


async def _canonical_git_root(path: str) -> str | None:
    common_dir = await _git_common_dir(path)
    if common_dir:
        normalized = os.path.normpath(common_dir)
        if os.path.basename(normalized) == ".git":
            return os.path.dirname(normalized)
    return await _git_root(path)


async def _head_commit(cwd: str) -> str | None:
    rc, out, _ = await _run_git("rev-parse", "HEAD", cwd=cwd)
    return out if rc == 0 else None


def _validate_worktree_name(name: str) -> str:
    slug = str(name).strip()
    if not slug:
        raise ValueError("Worktree name must not be empty.")
    if len(slug) > 64:
        raise ValueError("Worktree name must be 64 characters or fewer.")
    if not _WORKTREE_NAME_RE.fullmatch(slug):
        raise ValueError(
            "Worktree name may contain only letters, digits, dots, underscores, dashes, and '/'.",
        )
    if any(not segment for segment in slug.split("/")):
        raise ValueError("Worktree name must not contain empty path segments.")
    return slug


def _default_worktree_name() -> str:
    return f"worktree-{hashlib.sha1(str(time.time_ns()).encode()).hexdigest()[:8]}"


def _generate_worktree_path(repo_root: str, branch_name: str) -> str:
    """Produce a unique worktree directory under the repo's worktree base."""
    suffix = hashlib.sha1(
        f"{branch_name}-{time.time_ns()}".encode()
    ).hexdigest()[:8]
    safe_name = branch_name.replace("/", "-").replace("\\", "-")
    return os.path.join(repo_root, _WORKTREE_BASE, f"{safe_name}-{suffix}")


async def _count_worktree_changes(
    worktree_path: str,
    original_head: str | None,
) -> tuple[int, int] | None:
    """Return (changed_files, commits) or None if state can't be determined."""
    rc, out, _ = await _run_git("status", "--porcelain", cwd=worktree_path)
    if rc != 0:
        return None
    changed = sum(1 for line in out.splitlines() if line.strip())

    if not original_head:
        return None

    rc, out, _ = await _run_git(
        "rev-list", "--count", f"{original_head}..HEAD", cwd=worktree_path,
    )
    if rc != 0:
        return None
    commits = int(out) if out.isdigit() else 0
    return changed, commits


def _cleanup_at_exit() -> None:
    """atexit handler — synchronously remove any lingering worktree."""
    global _active_session  # noqa: PLW0603
    session = _active_session
    if session is None:
        return
    _active_session = None
    log.info("atexit: cleaning up worktree at %s", session.worktree_path)
    try:
        os.chdir(session.original_cwd)
    except OSError:
        pass
    if sys.platform == "win32":
        shutil.rmtree(session.worktree_path, ignore_errors=True)
    else:
        import subprocess
        subprocess.run(
            ["git", "worktree", "remove", "--force", session.worktree_path],
            capture_output=True,
            cwd=session.original_cwd,
        )


atexit.register(_cleanup_at_exit)


class EnterWorktreeTool(Tool):
    """Create and enter a git worktree for isolated work."""

    name = "EnterWorktree"
    description = (
        "Creates an isolated worktree and switches the session into it."
    )
    instructions = """\
Use this tool ONLY when the user explicitly asks to work in a worktree.

## When to Use
- The user explicitly says "worktree" (e.g. "start a worktree", "create a \
worktree")

## When NOT to Use
- The user asks to create/switch branches — use git commands instead
- The user asks to fix a bug or work on a feature — use normal workflow \
unless they specifically mention worktrees

## Requirements
- Must be in a git repository
- Must not already be in an active worktree session

## Parameters
- `name` (optional): Name for the worktree. A random name is generated if omitted.\
"""
    is_read_only = False

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": (
                        "Optional name for the worktree. Each '/'-separated "
                        "segment may contain only letters, digits, dots, "
                        "underscores, and dashes; max 64 chars total."
                    ),
                },
            },
        }

    async def execute(self, *, context: ToolUseContext, **kwargs: Any) -> str:
        global _active_session  # noqa: PLW0603

        if _active_session is not None:
            return "Error: Already in a worktree session. Exit the current worktree first."

        original_cwd = os.getcwd()
        requested_name = str(kwargs.get("name", "")).strip()
        try:
            slug = _validate_worktree_name(requested_name) if requested_name else _default_worktree_name()
        except ValueError as exc:
            return f"Error: {exc}"

        if not await _is_git_repo(original_cwd):
            return "Error: Not inside a git repository."

        repo_root = await _canonical_git_root(original_cwd)
        if not repo_root:
            return "Error: Could not determine git repository root."

        head_commit = await _head_commit(repo_root)
        branch_name = slug.replace("/", "-")

        wt_path = _generate_worktree_path(repo_root, branch_name)
        os.makedirs(os.path.dirname(wt_path), exist_ok=True)

        rc, out, err = await _run_git(
            "worktree", "add", "-b", branch_name, wt_path, "HEAD",
            cwd=repo_root,
        )
        if rc != 0:
            return f"Error creating worktree: {err or out}"

        _active_session = WorktreeSession(
            worktree_path=wt_path,
            branch_name=branch_name,
            original_cwd=original_cwd,
            original_head_commit=head_commit,
        )

        try:
            os.chdir(wt_path)
        except OSError as exc:
            _active_session = None
            return f"Error switching to worktree directory: {exc}"

        log.info("Created worktree at %s on branch %s", wt_path, branch_name)
        return (
            f"Created worktree at {wt_path} on branch {branch_name}. "
            f"Session is now working in the worktree. "
            f"Use ExitWorktree to leave."
        )


class ExitWorktreeTool(Tool):
    """Exit and optionally remove an active worktree session."""

    name = "ExitWorktree"
    description = (
        "Exits a worktree session created by EnterWorktree and "
        "restores the original working directory."
    )
    instructions = """\
Exits the current worktree session.

## Parameters
- `action`: "keep" to preserve the worktree on disk, "remove" to delete it.
- `discard_changes` (optional, bool): Required true when action is "remove" \
and the worktree has uncommitted changes or unmerged commits.\
"""
    is_read_only = False

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["keep", "remove"],
                    "description": '"keep" preserves the worktree; "remove" deletes it.',
                },
                "discard_changes": {
                    "type": "boolean",
                    "description": (
                        "Must be true to remove a worktree with uncommitted "
                        "changes or unmerged commits."
                    ),
                },
            },
            "required": ["action"],
        }

    async def execute(self, *, context: ToolUseContext, **kwargs: Any) -> str:
        global _active_session  # noqa: PLW0603

        action: str = kwargs["action"]
        discard_changes: bool = kwargs.get("discard_changes", False)

        session = _active_session
        if session is None:
            return (
                "No-op: there is no active EnterWorktree session to exit. "
                "This tool only operates on worktrees created by EnterWorktree "
                "in the current session."
            )

        wt_path = session.worktree_path
        original_cwd = session.original_cwd

        if action == "remove" and not discard_changes:
            changes = await _count_worktree_changes(
                wt_path, session.original_head_commit,
            )
            if changes is None:
                return (
                    f"Could not verify worktree state at {wt_path}. "
                    "Re-invoke with discard_changes: true to force remove, "
                    'or use action: "keep" to preserve it.'
                )
            changed_files, commits = changes
            if changed_files > 0 or commits > 0:
                parts: list[str] = []
                if changed_files > 0:
                    noun = "file" if changed_files == 1 else "files"
                    parts.append(f"{changed_files} uncommitted {noun}")
                if commits > 0:
                    noun = "commit" if commits == 1 else "commits"
                    parts.append(f"{commits} {noun} on {session.branch_name}")
                return (
                    f"Worktree has {' and '.join(parts)}. "
                    "Removing will discard this work permanently. "
                    "Re-invoke with discard_changes: true to confirm, "
                    'or use action: "keep".'
                )

        try:
            os.chdir(original_cwd)
        except OSError:
            pass

        _active_session = None

        if action == "keep":
            log.info("Kept worktree at %s", wt_path)
            return (
                f"Exited worktree. Work preserved at {wt_path} on branch "
                f"{session.branch_name}. Session is back in {original_cwd}."
            )

        # action == "remove"
        rc, _, err = await _run_git(
            "worktree", "remove", "--force", wt_path, cwd=original_cwd,
        )
        if rc != 0:
            shutil.rmtree(wt_path, ignore_errors=True)
            log.warning("git worktree remove failed (%s), used shutil fallback", err)

        # Also try to delete the branch
        await _run_git("branch", "-D", session.branch_name, cwd=original_cwd)

        log.info("Removed worktree at %s", wt_path)
        return (
            f"Exited and removed worktree at {wt_path}. "
            f"Session is back in {original_cwd}."
        )
