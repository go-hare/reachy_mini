"""Load the official ``mcp`` SDK without being shadowed by ``mini_agent.mcp``."""

from __future__ import annotations

import importlib
import importlib.util
import site
import sys
from pathlib import Path
from types import ModuleType

_EXTERNAL_MCP_ALIAS = "_mini_agent_external_mcp"


def _external_mcp_root() -> Path:
    for site_path in site.getsitepackages():
        candidate = Path(site_path) / "mcp" / "__init__.py"
        if candidate.exists():
            return candidate
    raise ModuleNotFoundError("Official 'mcp' package not found in site-packages")


def load_external_mcp_package() -> ModuleType:
    existing = sys.modules.get(_EXTERNAL_MCP_ALIAS)
    if existing is not None:
        return existing

    init_path = _external_mcp_root()
    spec = importlib.util.spec_from_file_location(
        _EXTERNAL_MCP_ALIAS,
        init_path,
        submodule_search_locations=[str(init_path.parent)],
    )
    if spec is None or spec.loader is None:
        raise ModuleNotFoundError(f"Unable to load official 'mcp' package from {init_path}")

    module = importlib.util.module_from_spec(spec)
    current_top_level = sys.modules.get("mcp")
    sys.modules["mcp"] = module
    sys.modules[_EXTERNAL_MCP_ALIAS] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        if current_top_level is not None:
            sys.modules["mcp"] = current_top_level
        else:
            sys.modules.pop("mcp", None)
        sys.modules.pop(_EXTERNAL_MCP_ALIAS, None)
        raise
    return module


def import_external_mcp_module(module_name: str) -> ModuleType:
    load_external_mcp_package()
    return importlib.import_module(f"{_EXTERNAL_MCP_ALIAS}.{module_name}")
