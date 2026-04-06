"""Optional host subprocess alongside in-process teammates (tmux/CLI parity).

Reference uses tmux panes and ``TEAMMATE_COMMAND``-style hosts. Python keeps
:class:`PersistentTeammate` in-process; when ``MINI_AGENT_TEAMMATE_COMMAND`` is
set, we also spawn a **sidecar** process with mailbox/team paths in the
environment so an external runner (e.g. shell script opening a pane) can
observe the same ``FileMailbox`` JSON files.

When ``MINI_AGENT_TEAMMATE_EXTERNAL_ONLY=1``, ``Team.spawn_teammate`` starts
only the sidecar (no ``PersistentTeammate``); requires
``MINI_AGENT_TEAMMATE_COMMAND`` and on-disk mailboxes (``Team`` switches from
``MemoryMailbox`` to ``FileMailbox`` when no in-process teammates exist).
"""

from __future__ import annotations

import logging
import os
import shlex
import subprocess
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .teammate import TeammateConfig

logger = logging.getLogger(__name__)


def launch_teammate_command_sidecar(config: TeammateConfig) -> subprocess.Popen[Any] | None:
    """If ``MINI_AGENT_TEAMMATE_COMMAND`` is set, spawn it once (fire-and-forget).

    Environment passed to the child (in addition to ``os.environ``):

    - ``MINI_AGENT_TEAM_NAME`` — sanitized team name
    - ``MINI_AGENT_TEAMMATE_NAME`` — config.name
    - ``MINI_AGENT_MAILBOX_DIR`` — :func:`team_files.get_team_mailbox_dir`
    - ``MINI_AGENT_TEAM_DIR`` — :func:`team_files.get_team_dir`
    - ``MINI_AGENT_AGENT_ID`` — ``name@team``
    """
    raw = (os.environ.get("MINI_AGENT_TEAMMATE_COMMAND") or "").strip()
    if not raw:
        return None

    from .team_files import get_team_dir, get_team_mailbox_dir

    try:
        argv = shlex.split(raw)
    except ValueError:
        logger.warning("MINI_AGENT_TEAMMATE_COMMAND parse failed; ignoring")
        return None

    team = config.team_name
    mb = str(get_team_mailbox_dir(team))
    td = str(get_team_dir(team))
    agent_id = f"{config.name}@{team}"
    env = os.environ.copy()
    env.update({
        "MINI_AGENT_TEAM_NAME": team,
        "MINI_AGENT_TEAMMATE_NAME": config.name,
        "MINI_AGENT_MAILBOX_DIR": mb,
        "MINI_AGENT_TEAM_DIR": td,
        "MINI_AGENT_AGENT_ID": agent_id,
    })
    try:
        proc = subprocess.Popen(  # noqa: S603 — intentional host hook
            argv,
            env=env,
            cwd=config.working_directory or os.getcwd(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        logger.info(
            "Teammate sidecar PID %s for %s (MINI_AGENT_TEAMMATE_COMMAND)",
            proc.pid,
            agent_id,
        )
        return proc
    except OSError:
        logger.warning("Teammate sidecar spawn failed", exc_info=True)
        return None


def teammate_external_only() -> bool:
    v = (os.environ.get("MINI_AGENT_TEAMMATE_EXTERNAL_ONLY") or "").strip().lower()
    return v in ("1", "true", "yes", "on")
