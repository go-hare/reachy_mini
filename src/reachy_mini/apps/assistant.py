"""Reachy Mini app assistant functions."""

from pathlib import Path
import os
import subprocess

import questionary
from jinja2 import Environment, FileSystemLoader
from rich.console import Console


def validate_app_name(text: str) -> bool | str:
    """Validate the app name."""
    if not text.strip():
        return "App name cannot be empty."
    if " " in text:
        return "App name cannot contain spaces."
    if "-" in text:
        return "App name cannot contain dashes ('-'). Please use underscores ('_') instead."
    if "/" in text or "\\" in text:
        return "App name cannot contain slashes or backslashes ('/' or '\\')."
    if "*" in text or "?" in text or "." in text:
        return "App name cannot contain wildcard characters ('*', '?', or '.')."
    return True


def is_git_repo(path: Path) -> bool:
    """Check if the given path is inside a git repository."""
    try:
        subprocess.check_output(
            ["git", "-C", str(path), "rev-parse", "--is-inside-work-tree"],
            stderr=subprocess.STDOUT,
        )
        return True
    except subprocess.CalledProcessError:
        return False


def validate_location_and_git_repo(text: str) -> bool | str:
    """Validate the creation root and ensure it is not already in a git repo."""
    path = Path(text).expanduser().resolve()
    if not path.exists():
        return f"The path {path} does not exist."
    if is_git_repo(path):
        return f"The path {path} is already inside a git repository."
    return True


def create_cli(
    console: Console, app_name: str | None, app_path: Path | None
) -> tuple[str, Path]:
    """Gather the values needed to create a new app project."""
    if app_name is None:
        console.print("$ What is the name of your app?")
        app_name = questionary.text(
            ">",
            default="",
            validate=validate_app_name,
        ).ask()
        if app_name is None:
            console.print("[red]Aborted.[/red]")
            raise SystemExit(1)
        app_name = app_name.strip().lower()

    app_name = app_name.replace("-", "_")

    if app_path is None:
        console.print("\n$ Where do you want to create your app project?")
        app_path_value = questionary.path(
            ">",
            default="",
            validate=validate_location_and_git_repo,
        ).ask()
        if app_path_value is None:
            console.print("[red]Aborted.[/red]")
            raise SystemExit(1)
        app_path = Path(app_path_value).expanduser().resolve()

    if is_git_repo(app_path):
        console.print(
            f"[red]The path {app_path} is already inside a git repository. "
            "Please choose another path. Aborted.[/red]"
        )
        raise SystemExit(1)

    return app_name, app_path


def create(console: Console, app_name: str | None, app_path: Path | None) -> Path:
    """Create a new Reachy Mini app project."""
    app_name, app_path = create_cli(console, app_name, app_path)

    template_dir = Path(__file__).parent / "templates"
    env = Environment(loader=FileSystemLoader(template_dir))

    def render_template(filename: str, context: dict[str, str]) -> str:
        template = env.get_template(filename)
        return template.render(context)

    base_path = app_path / app_name
    if base_path.exists():
        console.print(f"[bold red]Folder {base_path} already exists.[/bold red]")
        raise SystemExit(1)

    module_name = app_name
    entrypoint_name = app_name.replace("-", "_")
    class_name = "".join(word.capitalize() for word in module_name.split("_"))
    class_name_display = " ".join(word.capitalize() for word in module_name.split("_"))

    (base_path / module_name / "static").mkdir(parents=True)

    context = {
        "app_name": app_name,
        "package_name": app_name,
        "module_name": module_name,
        "class_name": class_name,
        "class_name_display": class_name_display,
        "entrypoint_name": entrypoint_name,
    }

    (base_path / module_name / "__init__.py").touch()
    (base_path / module_name / "main.py").write_text(
        render_template("main.py.j2", context),
        encoding="utf-8",
    )
    (base_path / module_name / "static" / "index.html").write_text(
        render_template("static/index.html.j2", context),
        encoding="utf-8",
    )
    (base_path / module_name / "static" / "style.css").write_text(
        render_template("static/style.css.j2", context),
        encoding="utf-8",
    )
    (base_path / module_name / "static" / "main.js").write_text(
        render_template("static/main.js.j2", context),
        encoding="utf-8",
    )

    (base_path / "pyproject.toml").write_text(
        render_template("pyproject.toml.j2", context),
        encoding="utf-8",
    )
    (base_path / "README.md").write_text(
        render_template("README.md.j2", context),
        encoding="utf-8",
    )
    (base_path / "index.html").write_text(
        render_template("index.html.j2", context),
        encoding="utf-8",
    )
    (base_path / "style.css").write_text(
        render_template("style.css.j2", context),
        encoding="utf-8",
    )
    (base_path / ".gitignore").write_text(
        render_template("gitignore.j2", context),
        encoding="utf-8",
    )

    console.print(f"[bold green]Created app '{app_name}' in {base_path}/[/bold green]")
    return base_path
