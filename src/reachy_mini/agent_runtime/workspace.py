"""Workspace scaffolding helpers for standalone profile workspaces."""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader

TEMPLATE_DIR = Path(__file__).parent / "templates" / "profile_workspace"


def create_profile_workspace(profile_root: Path, profile_name: str) -> Path:
    """Create a new standalone profile workspace."""
    target = profile_root.expanduser().resolve()
    if target.exists():
        raise FileExistsError(f"Profile workspace already exists: {target}")

    env = Environment(loader=FileSystemLoader(TEMPLATE_DIR))
    context = {"profile_name": profile_name}

    for template_path in sorted(TEMPLATE_DIR.rglob("*.j2")):
        relative_path = template_path.relative_to(TEMPLATE_DIR)
        output_path = target / relative_path.with_suffix("")
        output_path.parent.mkdir(parents=True, exist_ok=True)

        template = env.get_template(relative_path.as_posix())
        output_path.write_text(template.render(context), encoding="utf-8")

    return target
