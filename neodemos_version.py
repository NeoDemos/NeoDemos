"""
NeoDemos version — single source of truth.

Import this wherever version is needed:
    from neodemos_version import __version__, VERSION_LABEL
"""
from pathlib import Path

__version__ = (Path(__file__).parent / "VERSION").read_text().strip()

# Display label used in MCP server name, UI, logs
VERSION_LABEL = f"v{__version__}"
PRODUCT_NAME = "NeoDemos"
STAGE = "alpha"  # alpha → beta → rc → (empty for GA)
DISPLAY_NAME = f"{PRODUCT_NAME} ({STAGE})" if STAGE else PRODUCT_NAME
