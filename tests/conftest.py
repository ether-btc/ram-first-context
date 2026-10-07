# Shared fixtures: a throwaway Mnemosyne-shaped DB the test module points at.
#
# The plugin's module-level LIVE_DB / USAGE_LOG constants are the seams under
# test: patching them (attribute assignment) is the only supported way to aim
# fts_recall() at a fixture, which keeps the shipped source path-coupled to
# ~/.hermes for production use while staying unit-testable anywhere.

from __future__ import annotations

import importlib
import sqlite3
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "plugin" / "src"  # the shipped module


def _build_fixture_db(db_path: Path) -> None:
    con = sqlite3.connect(db_path)
    try:
        con.executescript(
            """
            CREATE TABLE working_memory (
                id TEXT PRIMARY KEY,
                content TEXT,
                source TEXT,
                importance REAL,
                timestamp TEXT,
                memory_type TEXT,
                metadata_json TEXT
            );
            CREATE VIRTUAL TABLE fts_working USING fts5(
                content, id UNINDEXED,
                content='working_memory', content_rowid='rowid'
            );
            CREATE TRIGGER wm_ai AFTER INSERT ON working_memory BEGIN
              INSERT INTO fts_working(rowid, id, content)
              VALUES (new.rowid, new.id, new.content);
            END;
            INSERT INTO working_memory VALUES
              ('id1', 'the AND gate is a reserved word', 's', 0.5, '2026-01-01T00:00:00', 't', '{}'),
              ('id2', 'hermes plugin near test',         's', 0.9, '2026-01-02T00:00:00', 't', '{}'),
              ('id3', 'unicode \\u00fcber content here',   's', 0.2, '2026-01-03T00:00:00', 't', '{}'),
              ('id4', 'port 8080:8081 mapping note',       's', 0.7, '2026-01-04T00:00:00', 't', '{}'),
              ('id5', 'terminal emulator startup flags',   's', 0.3, '2026-01-05T00:00:00', 't', '{}');
            """
        )
        con.commit()
    finally:
        con.close()


@pytest.fixture(scope="session")
def fixture_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    db = tmp_path_factory.mktemp("db") / "mnemosyne.db"
    _build_fixture_db(db)
    return db


@pytest.fixture()
def tool_module(fixture_db: Path, tmp_path: Path):
    """Load src/ram_first_tool.py as a standalone module, re-aimed at the fixture."""
    mod_dir = tmp_path / "rft"
    mod_dir.mkdir()
    target = mod_dir / "ram_first_tool.py"
    target.write_text((SRC / "ram_first_tool.py").read_text())
    sys.path.insert(0, str(mod_dir))
    try:
        mod = importlib.import_module("ram_first_tool")
    finally:
        sys.path.remove(str(mod_dir))
    mod.LIVE_DB = fixture_db
    mod.USAGE_LOG = tmp_path / "usage.jsonl"
    return mod


@pytest.fixture()
def tool_module_no_db(tmp_path: Path) -> object:
    """Same module, but aimed at a DB path that does not exist."""
    mod_dir = tmp_path / "rft_nodb"
    mod_dir.mkdir()
    target = mod_dir / "ram_first_tool_nodb.py"
    target.write_text((SRC / "ram_first_tool.py").read_text())
    sys.path.insert(0, str(mod_dir))
    try:
        mod = importlib.import_module("ram_first_tool_nodb")
    finally:
        sys.path.remove(str(mod_dir))
    mod.LIVE_DB = tmp_path / "definitely-missing" / "mnemosyne.db"
    mod.USAGE_LOG = tmp_path / "usage.jsonl"
    return mod


__all__ = ["fixture_db", "tool_module", "tool_module_no_db"]
