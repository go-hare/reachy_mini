"""GitHub integration — auth, API, context, and PR helpers.

Uses the ``gh`` CLI under the hood for authenticated operations and
falls back to the GitHub REST API when ``gh`` is not available but a
token is provided (via ``GITHUB_TOKEN`` env var).

All subprocess calls are async and use a 10-second timeout by default.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any
from urllib.parse import urlparse

from ..messages import user_message

log = logging.getLogger(__name__)

_GH_TIMEOUT = 10  # seconds for gh CLI calls
_API_TIMEOUT = 15


# =====================================================================
# GitHubAuth — check if `gh` is authenticated
# =====================================================================

class AuthState(str, Enum):
    AUTHENTICATED = "authenticated"
    NOT_AUTHENTICATED = "not_authenticated"
    NOT_INSTALLED = "not_installed"


@dataclass(slots=True)
class AuthStatus:
    authenticated: bool
    username: str | None = None
    scopes: list[str] | None = None


class GitHubAuth:
    """Inspect ``gh`` CLI authentication status."""

    @staticmethod
    async def check_gh_auth() -> AuthState:
        """Fast check — ``gh auth token`` reads local config only."""
        gh = await _which_gh()
        if gh is None:
            return AuthState.NOT_INSTALLED
        rc, _, _ = await _run_gh("auth", "token", timeout=5)
        return AuthState.AUTHENTICATED if rc == 0 else AuthState.NOT_AUTHENTICATED

    @staticmethod
    async def get_auth_status() -> AuthStatus:
        """Richer check — calls ``gh auth status`` and parses output."""
        gh = await _which_gh()
        if gh is None:
            return AuthStatus(authenticated=False)
        rc, out, err = await _run_gh("auth", "status", timeout=_GH_TIMEOUT)
        if rc != 0:
            return AuthStatus(authenticated=False)

        combined = out + "\n" + err
        username = _parse_field(combined, r"Logged in to .+ as (\S+)")
        scopes_str = _parse_field(combined, r"Token scopes: (.+)")
        scopes = [s.strip().strip("'") for s in scopes_str.split(",")] if scopes_str else None

        return AuthStatus(authenticated=True, username=username, scopes=scopes)

    @staticmethod
    async def get_auth_token() -> str | None:
        """Return the ``gh`` auth token, or ``GITHUB_TOKEN`` env var."""
        env_token = os.environ.get("GITHUB_TOKEN")
        if env_token:
            return env_token

        rc, out, _ = await _run_gh("auth", "token", timeout=5)
        return out.strip() if rc == 0 and out.strip() else None


# =====================================================================
# GitHubAPI — high-level wrappers around common operations
# =====================================================================

class GitHubAPI:
    """GitHub operations via ``gh`` CLI (preferred) or REST API fallback."""

    def __init__(self, cwd: str | None = None) -> None:
        self._cwd = cwd

    async def create_pr(
        self,
        title: str,
        body: str,
        base: str | None = None,
        head: str | None = None,
    ) -> dict[str, Any]:
        args = ["pr", "create", "--title", title, "--body", body]
        if base:
            args += ["--base", base]
        if head:
            args += ["--head", head]
        rc, out, err = await _run_gh(*args, cwd=self._cwd, timeout=_API_TIMEOUT)
        if rc != 0:
            return {"error": err or out}
        return {"url": out.strip()}

    async def list_prs(self, state: str = "open") -> list[dict[str, Any]]:
        rc, out, _ = await _run_gh(
            "pr", "list", "--state", state, "--json",
            "number,title,state,author,url",
            cwd=self._cwd, timeout=_API_TIMEOUT,
        )
        if rc != 0:
            return []
        return _safe_json_loads(out, [])

    async def get_pr(self, number: int) -> dict[str, Any]:
        rc, out, _ = await _run_gh(
            "pr", "view", str(number), "--json",
            "number,title,body,state,author,url,labels,reviewRequests",
            cwd=self._cwd, timeout=_API_TIMEOUT,
        )
        if rc != 0:
            return {}
        return _safe_json_loads(out, {})

    async def create_issue(
        self,
        title: str,
        body: str,
        labels: list[str] | None = None,
    ) -> dict[str, Any]:
        args = ["issue", "create", "--title", title, "--body", body]
        if labels:
            args += ["--label", ",".join(labels)]
        rc, out, err = await _run_gh(*args, cwd=self._cwd, timeout=_API_TIMEOUT)
        if rc != 0:
            return {"error": err or out}
        return {"url": out.strip()}

    async def list_issues(
        self,
        state: str = "open",
        labels: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        args = ["issue", "list", "--state", state, "--json",
                "number,title,state,author,url,labels"]
        if labels:
            args += ["--label", ",".join(labels)]
        rc, out, _ = await _run_gh(*args, cwd=self._cwd, timeout=_API_TIMEOUT)
        if rc != 0:
            return []
        return _safe_json_loads(out, [])

    async def add_comment(self, issue_number: int, body: str) -> dict[str, Any]:
        rc, out, err = await _run_gh(
            "issue", "comment", str(issue_number), "--body", body,
            cwd=self._cwd, timeout=_API_TIMEOUT,
        )
        if rc != 0:
            return {"error": err or out}
        return {"status": "ok", "output": out.strip()}

    async def get_repo_info(self) -> dict[str, Any]:
        rc, out, _ = await _run_gh(
            "repo", "view", "--json",
            "name,owner,defaultBranchRef,url,description",
            cwd=self._cwd, timeout=_API_TIMEOUT,
        )
        if rc != 0:
            return {}
        data = _safe_json_loads(out, {})
        if "defaultBranchRef" in data and isinstance(data["defaultBranchRef"], dict):
            data["default_branch"] = data["defaultBranchRef"].get("name")
        return data


# =====================================================================
# GitHubContext — local git state helpers
# =====================================================================

class GitHubContext:
    """Read local git state relevant to GitHub operations."""

    def __init__(self, cwd: str | None = None) -> None:
        self._cwd = cwd

    async def get_current_branch(self) -> str | None:
        rc, out, _ = await _run_git(
            "rev-parse", "--abbrev-ref", "HEAD", cwd=self._cwd,
        )
        return out.strip() if rc == 0 and out.strip() else None

    async def get_remote_url(self, remote: str = "origin") -> str | None:
        rc, out, _ = await _run_git(
            "remote", "get-url", remote, cwd=self._cwd,
        )
        return out.strip() if rc == 0 and out.strip() else None

    async def is_github_repo(self) -> bool:
        url = await self.get_remote_url()
        if not url:
            return False
        return "github.com" in url

    @staticmethod
    def parse_github_url(url: str) -> tuple[str, str] | None:
        """Extract ``(owner, repo)`` from a GitHub URL.

        Supports HTTPS and SSH formats::

            https://github.com/owner/repo.git
            git@github.com:owner/repo.git
        """
        # SSH format
        m = re.match(r"git@github\.com:([^/]+)/([^/.]+)(?:\.git)?", url)
        if m:
            return m.group(1), m.group(2)

        # HTTPS format
        parsed = urlparse(url)
        if "github.com" not in (parsed.hostname or ""):
            return None
        parts = parsed.path.strip("/").rstrip(".git").split("/")
        if len(parts) >= 2:
            return parts[0], parts[1]
        return None


# =====================================================================
# PRHelper — AI-assisted PR utilities
# =====================================================================

class PRHelper:
    """Convenience helpers for pull-request workflows."""

    def __init__(self, cwd: str | None = None) -> None:
        self._cwd = cwd

    async def generate_pr_description(
        self,
        diff_text: str,
        provider: Any = None,
    ) -> str:
        """Generate a PR description from a diff.

        If *provider* (an LLM provider with a ``complete`` method) is
        given, uses it to produce the description.  Otherwise returns
        a basic template populated from the diff stats.
        """
        if provider is not None:
            try:
                prompt = (
                    "Summarise the following git diff as a pull-request "
                    "description with a '## Summary' section (bullet points) "
                    "and a '## Test plan' section:\n\n"
                    f"```diff\n{diff_text[:8000]}\n```"
                )
                result_msg = await provider.complete(
                    messages=[user_message(prompt)],
                    system=(
                        "You write concise pull-request descriptions for software teams."
                    ),
                    max_tokens=4096,
                    temperature=0.3,
                    query_source="github_pr_description",
                )
                text = result_msg.text.strip()
                if text:
                    return text
            except Exception:
                log.debug("LLM PR description failed, falling back", exc_info=True)

        # Fallback: basic stats
        additions = diff_text.count("\n+") - diff_text.count("\n+++")
        deletions = diff_text.count("\n-") - diff_text.count("\n---")
        return (
            "## Summary\n"
            f"- {additions} addition(s), {deletions} deletion(s)\n\n"
            "## Test plan\n"
            "- [ ] Manual verification\n"
        )

    async def suggest_reviewers(self, changed_files: list[str]) -> list[str]:
        """Suggest reviewers based on recent file authorship.

        Uses ``git log --format='%ae'`` on each changed file to find
        the most frequent authors (excluding the current user).
        """
        rc, current_email, _ = await _run_git(
            "config", "user.email", cwd=self._cwd,
        )
        current_email = current_email.strip().lower() if rc == 0 else ""

        author_counts: dict[str, int] = {}
        for fpath in changed_files[:20]:  # cap to avoid excessive spawns
            rc, out, _ = await _run_git(
                "log", "--format=%ae", "-10", "--", fpath, cwd=self._cwd,
            )
            if rc != 0:
                continue
            for email in out.strip().splitlines():
                email = email.strip().lower()
                if email and email != current_email:
                    author_counts[email] = author_counts.get(email, 0) + 1

        sorted_authors = sorted(author_counts, key=author_counts.get, reverse=True)  # type: ignore[arg-type]
        return sorted_authors[:5]


# =====================================================================
# Internal helpers
# =====================================================================

async def _run_gh(
    *args: str,
    cwd: str | None = None,
    timeout: float = _GH_TIMEOUT,
) -> tuple[int, str, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            "gh", *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return (
            proc.returncode or 0,
            stdout.decode("utf-8", errors="replace"),
            stderr.decode("utf-8", errors="replace"),
        )
    except FileNotFoundError:
        return -1, "", "gh CLI not found"
    except asyncio.TimeoutError:
        return -1, "", f"gh command timed out after {timeout}s"
    except Exception as exc:
        return -1, "", str(exc)


async def _run_git(
    *args: str,
    cwd: str | None = None,
    timeout: float = _GH_TIMEOUT,
) -> tuple[int, str, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return (
            proc.returncode or 0,
            stdout.decode("utf-8", errors="replace"),
            stderr.decode("utf-8", errors="replace"),
        )
    except FileNotFoundError:
        return -1, "", "git not found"
    except asyncio.TimeoutError:
        return -1, "", f"git command timed out after {timeout}s"
    except Exception as exc:
        return -1, "", str(exc)


async def _which_gh() -> str | None:
    """Locate the ``gh`` binary without spawning a subprocess."""
    import shutil
    return shutil.which("gh")


def _parse_field(text: str, pattern: str) -> str | None:
    m = re.search(pattern, text)
    return m.group(1) if m else None


def _safe_json_loads(text: str, default: Any) -> Any:
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return default
