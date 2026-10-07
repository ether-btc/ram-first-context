#!/usr/bin/env python3
"""RAM-first context memory provider plugin — registers mnemosyne_fts_recall tool."""
import json
import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

LIVE_DB = Path.home() / ".hermes/mnemosyne/data/mnemosyne.db"

# Stage-0 telemetry (arbiter ruling 2026-09-05): append-only JSONL counter so the
# observation window yields the invocation number that decides Stage 1 (A) vs (C)-terminal.
USAGE_LOG = Path.home() / ".hermes/ram-first/usage.jsonl"


def _log_usage(query: str, hits: int) -> None:
    """Best-effort append; never raises into the tool path.

    Log-and-continue on OSError (logging can't raise into the tool path either;
    a broken telemetry sink must never break recall).
    """
    try:
        USAGE_LOG.parent.mkdir(parents=True, exist_ok=True)
        import time as _t
        line = json.dumps({"ts": _t.strftime("%Y-%m-%dT%H:%M:%S"), "q_len": len(query), "hits": hits})
        with open(USAGE_LOG, "a") as f:
            f.write(line + "\n")
    except OSError as e:
        logger.warning("ram-first telemetry write failed (continuing): %s", e)


def _fts_quote_term(t: str) -> str:
    """Wrap a term as an FTS5 string literal so punctuation can't break MATCH."""
    return '"' + t.strip().replace('"', '""') + '"'


def _like_escape(term: str) -> str:
    """Escape SQL LIKE wildcards so the fallback path matches user text literally.

    Mirrors the FTS path's literal-keyword contract: '%'/'_' (and a stray
    backslash) in the query are data, not wildcards. The caller strips the
    trailing '*' prefix marker before calling — LIKE has no prefix operator.
    """
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _err_envelope(msg: str) -> str:
    """Fail-closed error envelope: a JSON array holding one error record."""
    return json.dumps([{"error": msg}])


def fts_recall(query: str, limit: int = 5) -> str:
    """Search live Mnemosyne FTS5 index (read-only).

    Fail-closed by contract: every return value — including error paths — is a
    JSON array. No exception escapes this function.

    Args:
        query: Search terms (space-separated, AND logic; each term is matched as
               a literal keyword — FTS5 operators are not interpreted; a trailing
               * is a prefix).
        limit: Max results (default 5).

    Returns:
        JSON string of results: id, content, source, importance, timestamp, rank.
    """
    # Type validation at the boundary. The tool schema declares str/int, and the
    # live Hermes path JSON-schema-validates args before dispatch; but direct
    # module calls (tests, __main__, ad-hoc use) may pass anything. Fail closed
    # instead of raising, so the "never an exception" contract holds everywhere.
    if not isinstance(query, str):
        return _err_envelope(f"query must be a string, got {type(query).__name__}")
    if limit is None:
        limit = 5
    if isinstance(limit, bool) or not isinstance(limit, int):
        return _err_envelope(f"limit must be an integer, got {type(limit).__name__}")

    if not LIVE_DB.exists():
        return _err_envelope(f"Live DB not found at {LIVE_DB}")

    raw_terms = [t for t in query.split() if t.strip()]
    if not raw_terms:
        return json.dumps([])

    fts_query = " AND ".join((_fts_quote_term(t.rstrip('*')) + '*' if t.endswith('*') else _fts_quote_term(t)) for t in raw_terms)

    try:
        con = sqlite3.connect(f"file:{LIVE_DB}?mode=ro", uri=True)
        try:
            con.row_factory = sqlite3.Row
            rows = con.execute(
                """
                SELECT wm.id, wm.content, wm.source, wm.importance,
                       wm.timestamp, wm.memory_type, wm.metadata_json, rank
                FROM fts_working f
                JOIN working_memory wm ON wm.id = f.id
                WHERE fts_working MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (fts_query, limit)
            ).fetchall()
        finally:
            con.close()
        _log_usage(query, len(rows))
        return json.dumps([dict(row) for row in rows])

    except sqlite3.Error:
        # Any sqlite failure on the FTS path degrades to the LIKE fallback so a
        # bad index or file never breaks recall. This covers a missing
        # fts_working index (OperationalError) AND a corrupt/truncated DB file
        # (DatabaseError, "file is not a database"). If the file itself is
        # unreadable, the LIKE path below fails cleanly into the error envelope.
        like_terms = [_like_escape(t.rstrip('*')) for t in raw_terms]
        like = "%" + "%".join(like_terms) + "%"
        try:
            con = sqlite3.connect(f"file:{LIVE_DB}?mode=ro", uri=True)
            try:
                con.row_factory = sqlite3.Row
                rows = con.execute(
                    """
                    SELECT id, content, source, importance, timestamp, memory_type, metadata_json, 0 as rank
                    FROM working_memory
                    WHERE content LIKE ? ESCAPE '\\'
                    ORDER BY importance DESC, timestamp DESC
                    LIMIT ?
                    """,
                    (like, limit)
                ).fetchall()
            finally:
                con.close()
            _log_usage(query, len(rows))
            return json.dumps([dict(row) for row in rows])
        except sqlite3.Error as e:
            return _err_envelope(f"FTS fallback failed: {e}")


def register(ctx):
    """Register mnemosyne_fts_recall as a tool in the 'ram_first' toolset."""
    ctx.register_tool(
        name="mnemosyne_fts_recall",
        toolset="ram_first",
        schema={
            "name": "mnemosyne_fts_recall",
            "description": "Search live Mnemosyne FTS5 index (read-only) for keyword matches over working_memory. Returns ranked results with content, source, importance, timestamp.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search terms (space-separated, AND logic; each term is matched as a literal keyword — FTS5 operators are not interpreted; a trailing * is a prefix)."
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results (default 5)",
                        "default": 5
                    }
                },
                "required": ["query"]
            }
        },
        handler=lambda args, **kw: fts_recall(
            query=args.get("query", ""),
            limit=args.get("limit", 5)
        ),
        check_fn=lambda: LIVE_DB.exists()
    )


if __name__ == "__main__":
    print("This module registers a Hermes tool plugin. Import it in a plugin context.")
