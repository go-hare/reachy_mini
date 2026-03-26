"""Scaffolding helpers for app projects."""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader

TEMPLATE_ROOT = Path(__file__).resolve().parents[1] / "apps" / "templates"
PROFILE_TEMPLATE_DIR = TEMPLATE_ROOT / "profile"

INVALID_APP_NAME_CHARS = ("/", "\\", "*", "?", ".")


def normalize_app_name(app_name: str) -> str:
    """Normalize the user-facing app name into a Python package name."""
    normalized = str(app_name or "").strip().replace("-", "_")
    if not normalized:
        raise ValueError("App name cannot be empty.")
    if " " in normalized:
        raise ValueError("App name cannot contain spaces.")
    if any(character in normalized for character in INVALID_APP_NAME_CHARS):
        raise ValueError(
            "App name cannot contain '/', '\\', '*', '?', or '.'."
        )
    return normalized


def _build_class_name(module_name: str) -> str:
    """Convert a module name like ``demo_agent`` into ``DemoAgent``."""
    return "".join(part.capitalize() for part in module_name.split("_") if part)


def _render_tree(
    *,
    env: Environment,
    template_dir: Path,
    output_root: Path,
    context: dict[str, str],
) -> None:
    """Render an entire template tree into ``output_root``."""
    for template_path in sorted(template_dir.rglob("*.j2")):
        relative_path = template_path.relative_to(TEMPLATE_ROOT)
        output_path = output_root / template_path.relative_to(template_dir).with_suffix("")
        output_path.parent.mkdir(parents=True, exist_ok=True)

        template = env.get_template(relative_path.as_posix())
        output_path.write_text(template.render(context), encoding="utf-8")


def _render_file(
    *,
    env: Environment,
    template_name: str,
    output_path: Path,
    context: dict[str, str],
) -> None:
    """Render one named template file."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    template = env.get_template(template_name)
    output_path.write_text(template.render(context), encoding="utf-8")


def create_app_project(app_root: Path, app_name: str) -> Path:
    """Create a new installable app project."""
    target = app_root.expanduser().resolve()
    if target.exists():
        raise FileExistsError(f"App already exists: {target}")

    normalized_app_name = normalize_app_name(app_name)
    env = Environment(
        loader=FileSystemLoader(TEMPLATE_ROOT),
        keep_trailing_newline=True,
    )
    context = {
        "app_name": normalized_app_name,
        "profile_name": normalized_app_name,
        "module_name": normalized_app_name,
        "entrypoint_name": normalized_app_name,
        "class_name": _build_class_name(normalized_app_name),
        "class_name_display": " ".join(
            part.capitalize() for part in normalized_app_name.split("_") if part
        ),
    }

    package_root = target / normalized_app_name
    profile_bundle_root = target / "profiles"
    target.mkdir(parents=True, exist_ok=False)

    _render_tree(
        env=env,
        template_dir=PROFILE_TEMPLATE_DIR,
        output_root=profile_bundle_root,
        context=context,
    )
    _render_file(
        env=env,
        template_name="app/__init__.py.j2",
        output_path=package_root / "__init__.py",
        context=context,
    )
    _render_file(
        env=env,
        template_name="app/main.py.j2",
        output_path=package_root / "main.py",
        context=context,
    )
    _render_file(
        env=env,
        template_name="app/README.md.j2",
        output_path=target / "README.md",
        context=context,
    )
    _render_file(
        env=env,
        template_name="app/pyproject.toml.j2",
        output_path=target / "pyproject.toml",
        context=context,
    )
    _render_file(
        env=env,
        template_name="app/gitignore.j2",
        output_path=target / ".gitignore",
        context=context,
    )
    _render_file(
        env=env,
        template_name="app/index.html.j2",
        output_path=target / "index.html",
        context=context,
    )
    _render_file(
        env=env,
        template_name="app/style.css.j2",
        output_path=target / "style.css",
        context=context,
    )
    _render_file(
        env=env,
        template_name="app/static/index.html.j2",
        output_path=package_root / "static" / "index.html",
        context=context,
    )
    _render_file(
        env=env,
        template_name="app/static/style.css.j2",
        output_path=package_root / "static" / "style.css",
        context=context,
    )
    _render_file(
        env=env,
        template_name="app/static/main.js.j2",
        output_path=package_root / "static" / "main.js",
        context=context,
    )

    return target
