"""ram-first-context plugin — read-only FTS recall tool over live Mnemosyne.

Registers one tool (mnemosyne_fts_recall) in the 'ram_first' toolset.
Read-only by construction: every sqlite3.connect uses a mode=ro URI.
Reference pattern: bundled disk-cleanup plugin (plugins/disk-cleanup/).
"""
from __future__ import annotations

from .src.ram_first_tool import register  # re-export for the loader

__all__ = ["register"]
