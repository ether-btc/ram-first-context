# Shared fixtures: a throwaway Mnemosyne-shaped DB the test module points at.
#
# The plugin's LIVE_DB / USAGE_LOG module attributes are the test seams: when
# set (as the fixtures below do) they aim fts_recall() at a fixture; when None
# (the shipped default) every path resolves per call from the ACTIVE Hermes
# home, which keeps the shipped code profile-correct in production and
# unit-testable anywhere.

from __future__ import annotations

import importlib
import os
import sqlite3
import sys
import types
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "plugin" / "src"  # the shipped module


def _ensure_hermes_constants() -> None:
    """Make the shipped module's `hermes_constants` import resolvable anywhere.

    Inside a live Hermes process the genuine module is present because Hermes
    IS the host runtime. Standalone test environments (this repo's CI installs
    only pytest) get a minimal stub with the same top-level contract as
    ``hermes_constants.get_hermes_home()``: the HERMES_HOME env var when set,
    otherwise the platform default (``~/.hermes``). The module resolves all
    paths per call through that helper.
    """
    try:
        importlib.import_module("hermes_constants")
        return
    except ImportError:
        pass
    stub = types.ModuleType("hermes_constants")
    setattr(
        stub,
        "get_hermes_home",
        lambda: Path(os.environ["HERMES_HOME"]).expanduser()
        if os.environ.get("HERMES_HOME", "").strip()
        else Path.home() / ".hermes",
    )
    sys.modules["hermes_constants"] = stub


_ensure_hermes_constants()


def _load_module_copy(mod_name: str, mod_dir: Path) -> types.ModuleType:
    """Import a fresh copy of the shipped module under mod_name."""
    mod_dir.mkdir(exist_ok=True)
    (mod_dir / f"{mod_name}.py").write_text((SRC / "ram_first_tool.py").read_text())
    sys.path.insert(0, str(mod_dir))
    try:
        return importlib.import_module(mod_name)
    finally:
        sys.path.remove(str(mod_dir))


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
              ('id3', 'unicode \u00fcber content here',   's', 0.2, '2026-01-03T00:00:00', 't', '{}'),
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
    mod = _load_module_copy("ram_first_tool", tmp_path / "rft")
    mod.LIVE_DB = fixture_db
    mod.USAGE_LOG = tmp_path / "usage.jsonl"
    return mod


@pytest.fixture()
def tool_module_no_db(tmp_path: Path) -> object:
    """Same module, but aimed at a DB path that does not exist."""
    mod = _load_module_copy("ram_first_tool_nodb", tmp_path / "rft_nodb")
    mod.LIVE_DB = tmp_path / "definitely-missing" / "mnemosyne.db"
    mod.USAGE_LOG = tmp_path / "usage.jsonl"
    return mod


@pytest.fixture()
def raw_module(tmp_path: Path) -> object:
    """Load the shipped module with NO test seams: shipped per-call path
    resolution (active Hermes home) is what is under test."""
    mod = _load_module_copy("ram_first_tool_raw", tmp_path / "rft_raw")
    mod.LIVE_DB = None
    mod.USAGE_LOG = None
    return mod


__all__ = ["fixture_db", "tool_module", "tool_module_no_db", "raw_module"]
