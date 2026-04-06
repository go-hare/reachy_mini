from __future__ import annotations

from pathlib import Path

from setuptools import find_packages, setup


ROOT = Path(__file__).resolve().parent
DISCOVERED_SUBPACKAGES = find_packages(where=str(ROOT))
PACKAGES = ["ccmini", *[f"ccmini.{name}" for name in DISCOVERED_SUBPACKAGES]]


setup(
    name="ccmini",
    version="0.0.1",
    description="Shared agent core plus local launcher for the ccmini frontend.",
    long_description=(ROOT / "README_ZH.md").read_text(encoding="utf-8"),
    long_description_content_type="text/markdown",
    python_requires=">=3.10",
    packages=PACKAGES,
    package_dir={"ccmini": "."},
    include_package_data=True,
    install_requires=[
        "aiohttp>=3.9",
        "anthropic>=0.40",
        "httpx>=0.27",
        "mcp>=1.26",
        "openai>=1.40",
        "pydantic>=2",
        "requests>=2.28",
        "websockets>=12,<16",
    ],
    extras_require={
        "dev": [
            "pytest>=8",
            "pytest-asyncio>=0.24",
            "ruff>=0.12",
        ],
        "pdf": [
            "Pillow",
            "pdfplumber",
            "PyPDF2",
        ],
        "test": [
            "pytest>=8",
            "pytest-asyncio>=0.24",
        ],
        "voice": [
            "numpy",
            "sounddevice",
            "faster-whisper",
            "openai-whisper",
        ],
        "web": [
            "readability-lxml",
        ],
    },
    entry_points={
        "console_scripts": [
            "ccmini=ccmini.cli:main",
            "ccmini-frontend-host=ccmini.frontend_host:main",
        ],
    },
)
