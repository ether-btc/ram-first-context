"""Behavioural contract for src/ram_first_tool.py.

Every test here was derived from a measured invariant of the live module
(see PROGRESS.md / killprobe.py v2), not from the docstrings:

- I1  reserved words / punctuation no longer break or narrow the FTS path
      (term quoting: `_fts_quote_term` wraps each term as an FTS5 string
      literal; embedded quotes are doubled).
- I3  no pathological input escapes fts_recall() unhandled.
- P1  a trailing `*` stays a prefix, not an exact word (STEP B fix).
- F1  when the fts_working index is absent/corrupt, the LIKE fallback keeps
      the tool answering (rank=0, importance/timestamp order).
- R1  read-only by construction: the DB file's bytes are untouched by a
      successful query (verified by pre/post SHA-256).
- T1  telemetry is append-only JSONL with the exact {ts, q_len, hits} shape;
      a broken telemetry sink must never break recall.
- C1  register() hands the loader exactly the manifest-declared tool.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "plugin" / "src"  # the shipped module


def _sha256(p) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def query_rows(mod, query: str, limit: int = 5) -> list:
    """Parse the tool's JSON envelope; return rows ([]) or the error envelope."""
    return json.loads(mod.fts_recall(query, limit))


# ── I1: quoting hardening ──────────────────────────────────────────────────

def test_reserved_word_stays_fts_path_no_crash(tool_module):
    # 'AND' is an FTS5 operator; raw join turned `AND` into a syntax error ->
    # silent LIKE fallback. Quoted, it matches the literal word.
    rows = query_rows(tool_module, "AND")
    assert rows and "error" not in rows[0]
    assert any(r["id"] == "id1" for r in rows)


def test_punctuation_term_does_not_fallback(tool_module):
    # ':' makes the raw FTS expression a column-filter on a missing column ->
    # OperationalError -> LIKE (rank forced to 0). Quoted, it stays on the
    # FTS path with a real bm25 rank.
    rows = query_rows(tool_module, "port 8080:8081")
    assert rows and "error" not in rows[0]
    assert any(r["id"] == "id4" for r in rows)
    # rank is a real FTS rank (bm25 returns negatives), not the fallback's 0
    assert any(r["rank"] != 0 for r in rows)


def test_ordinary_keywords_unchanged(tool_module):
    rows = query_rows(tool_module, "hermes")
    assert [r["id"] for r in rows] == ["id2"]


def test_empty_and_whitespace_queries_return_empty(tool_module):
    assert query_rows(tool_module, "") == []
    assert query_rows(tool_module, "   ") == []


# ── I3: containment ────────────────────────────────────────────────────────

PATHOLOGICAL = ['"unterminated', 'NEAR:', '8080:8081', '"abc', 'a"b', '*', '"', 'AND', 'NOT']


def test_pathological_inputs_never_raise(tool_module):
    for q in PATHOLOGICAL:
        out = query_rows(tool_module, q)  # raises if the module doesn't contain it
        assert isinstance(out, list)


# ── P1: trailing * is a prefix ─────────────────────────────────────────────

def test_trailing_star_is_prefix_not_exact(tool_module):
    assert [r["id"] for r in query_rows(tool_module, "terminal*")] == ["id5"]
    # two-word AND-join still holds with a prefix term
    rows = query_rows(tool_module, "terminal emulator*")
    assert [r["id"] for r in rows] == ["id5"]


def test_stars_are_contained(tool_module):
    for q in ("**", 'a"b*'):
        out = query_rows(tool_module, q)
        assert isinstance(out, list)


# ── F1: LIKE fallback when the FTS index is gone ──────────────────────────

def test_like_fallback_when_fts_index_missing(tmp_path, fixture_db):
    """Rebuild the fixture DB with NO fts_working table; the module must
    degrade to the LIKE path (rank 0, importance/timestamp ordering)."""
    import importlib

    fb = tmp_path / "no-fts.db"
    con = sqlite3.connect(fb)
    con.executescript(
        """
        CREATE TABLE working_memory (
            id TEXT PRIMARY KEY, content TEXT, source TEXT, importance REAL,
            timestamp TEXT, memory_type TEXT, metadata_json TEXT);
        INSERT INTO working_memory VALUES
          ('id2', 'hermes plugin near test',                 's', 0.9, '2026-01-02T00:00:00', 't', '{}'),
          ('id4', 'hermes port 8080:8081 mapping note',      's', 0.7, '2026-01-04T00:00:00', 't', '{}');
        """
    )
    con.commit()
    con.close()

    mod_dir = tmp_path / "rft_nofts"
    mod_dir.mkdir()
    mod_dir.joinpath("ram_first_tool_nofts.py").write_text(
        (SRC / "ram_first_tool.py").read_text()
    )
    import sys
    sys.path.insert(0, str(mod_dir))
    try:
        mod = importlib.import_module("ram_first_tool_nofts")
    finally:
        sys.path.remove(str(mod_dir))
    mod.LIVE_DB = fb
    mod.USAGE_LOG = tmp_path / "usage.jsonl"

    rows = json.loads(mod.fts_recall("hermes"))
    assert rows and "error" not in rows[0]
    assert all(r["rank"] == 0 for r in rows)         # LIKE path marker
    # both rows contain 'hermes' in this fixture; importance DESC is the
    # documented order on the LIKE path
    assert [r["id"] for r in rows] == ["id2", "id4"]  # 0.9 > 0.7

    # a query matching nothing still returns a clean empty list
    assert json.loads(mod.fts_recall("zzz-not-there")) == []


# ── R1: read-only by construction ─────────────────────────────────────────

def test_successful_query_does_not_mutate_db_file(tool_module, fixture_db):
    before = _sha256(fixture_db)
    for q in ("hermes", "port 8080:8081", "terminal*", '"unterminated'):
        query_rows(tool_module, q)
    after = _sha256(fixture_db)
    assert before == after


def test_missing_db_returns_structured_error(tool_module_no_db):
    out = query_rows(tool_module_no_db, "hermes")
    assert len(out) == 1 and "error" in out[0] and "not found" in out[0]["error"].lower()
    assert not tool_module_no_db.USAGE_LOG.exists()  # early return: no telemetry


# ── T1: telemetry contract ─────────────────────────────────────────────────

def test_telemetry_is_append_only_jsonl_with_contract_shape(tool_module, tmp_path, monkeypatch):
    # aim at a fresh log; the fixture_db is shared, so re-point USAGE_LOG
    log = tmp_path / "t.jsonl"
    monkeypatch.setattr(tool_module, "USAGE_LOG", log)

    tool_module.fts_recall("hermes", 3)
    tool_module.fts_recall("port", 5)

    lines = [json.loads(line) for line in log.read_text().splitlines()]
    assert len(lines) == 2
    ts_re = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$")
    for entry, q_len in zip(lines, (6, 4)):
        assert set(entry) == {"ts", "q_len", "hits"}, "telemetry shape drifted"
        assert ts_re.match(entry["ts"])
        assert entry["q_len"] == q_len
        assert isinstance(entry["hits"], int)


def test_broken_telemetry_sink_never_breaks_recall(tool_module, tmp_path, monkeypatch):
    """Usage dir points at a file, so mkdir(parents=True) would raise; the
    tool must still return its rows (log-and-continue)."""
    blocker = tmp_path / "blocker"
    blocker.write_text("i am a file, not a dir")
    monkeypatch.setattr(tool_module, "USAGE_LOG", blocker / "usage.jsonl")

    rows = query_rows(tool_module, "hermes")
    assert [r["id"] for r in rows] == ["id2"]


# ── C1: register() contract ───────────────────────────────────────────────

def test_register_hands_loader_the_manifest_tool(tool_module):
    captured = {}

    class FakeCtx:
        def register_tool(self, **kw):
            captured.update(kw)
            return None

    tool_module.register(FakeCtx())

    assert captured["name"] == "mnemosyne_fts_recall"
    assert captured["toolset"] == "ram_first"
    props = captured["schema"]["parameters"]["properties"]
    assert set(props) == {"query", "limit"}
    assert "query" in captured["schema"]["parameters"]["required"]
    # handler + check_fn wired, and the check gate is the LIVE_DB existence
    # (handler shape: `lambda args, **kw:` — one dict, optional kwargs)
    out = json.loads(captured["handler"]({"query": "hermes", "limit": 1}))
    assert [r["id"] for r in out] == ["id2"]
    assert tool_module.register.__doc__.startswith("Register mnemosyne_fts_recall")
