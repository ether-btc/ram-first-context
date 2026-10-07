# ram-first-context

A **read-only RAM-first context memory plugin** for [Hermes Agent](https://hermes-agent.nousresearch.com/docs).

It registers one tool, `mnemosyne_fts_recall`, that runs FTS5 keyword searches
over the **live** Mnemosyne SQLite database — no in-process copy, no
embedding model, no network. Recall is sub-millisecond and degrades gracefully
when the index is absent.

The Mnemosyne memory provider plugin (the `mnemosyne` entry-point) owns writes;
this plugin only ever opens the database with a `mode=ro` URI. Read-only is a
property of the construction, not a promise.

## What the tool does

`mnemosyne_fts_recall(query, limit=5)` searches the `working_memory` table of
`~/.hermes/mnemosyne/data/mnemosyne.db`:

- **AND** semantics: every whitespace-separated term must match.
- **Literal keywords**: each term is wrapped as an FTS5 string literal, so
  FTS5 operators in your text (`AND`, `NOT`, `NEAR`, `:`, `"`…) are matched
  as words, never parsed as query syntax.
- **Prefix search**: a trailing `*` searches a word prefix (`terminal*` →
  `terminal`, `terminals`).
- **Ranked**: FTS5 `bm25` rank via the `fts_working` index.
- **Fallback**: if the FTS index is missing or the FTS path throws (including a
  corrupt or truncated DB file), the tool degrades to a `LIKE` search ordered
  by `importance` then `timestamp` (rank reported as `0`), so recall keeps
  working on a fresh or corrupt database. The fallback treats `%`, `_`, and
  `\` in the query as **literal** characters (`LIKE … ESCAPE '\'`), matching
  the FTS path's literal-keyword contract. One deliberate difference: the
  trailing-`*` prefix marker is FTS-path-only — a `*` in a term is stripped on
  the fallback, where prefix matching has no `LIKE` equivalent (documented,
  not a bug).
- **Fail-closed envelope**: every return value — including errors and a corrupt
  database — is a JSON array (e.g. `[{"error": "Live DB not found at …"}]`),
  never an exception. Non-`str` `query` or non-`int` `limit` arguments return
  an error envelope rather than raising, so the contract holds on direct
  module calls, not just the schema-validated tool path.

### Result shape

```json
[
  {
    "id": "wm_2026…",
    "content": "the AND gate is a reserved word",
    "source": "user",
    "importance": 0.9,
    "timestamp": "2026-10-07T…",
    "memory_type": "preference",
    "metadata_json": "{…}",
    "rank": -2.17   // FTS bm25 rank; 0 on the LIKE fallback path
  }
]
```

### Telemetry

Each call appends one compact JSONL line `{ts, q_len, hits}` to
`~/.hermes/ram-first/usage.jsonl` (query text is intentionally **not**
logged). The write is best-effort: a broken telemetry sink is logged and
swallowed, it never breaks a recall.

## Requirements

- Hermes Agent `>= 0.19` (manifest `requires_hermes` gate).
- A Mnemosyne database at `~/.hermes/mnemosyne/data/mnemosyne.db`
  (shipped by the `mnemosyne` memory-provider plugin — the one already active
  in your Hermes home). Without it the tool reports a clean error envelope
  and its loader health check disables it.
- Python 3.10+; **zero third-party runtime dependencies** (stdlib only:
  `sqlite3`, `json`, `logging`, `pathlib`).

## Installation

Hermes discovers user plugins from `~/.hermes/plugins/<name>/` and activates
them via the `plugins.enabled` allow-list in `~/.hermes/config.yaml` —
user plugins are opt-in, so a copy in the scan dir does nothing until enabled.

Two equivalent paths land you at the same key, `ram-first-context`:

**A. Copy the plugin (the classic user-plugin path)**

```bash
git clone https://github.com/ether-btc/ram-first-context \
  /tmp/ram-first-context
mkdir -p ~/.hermes/plugins
cp -r /tmp/ram-first-context/plugin ~/.hermes/plugins/ram-first-context
rm -rf /tmp/ram-first-context
hermes plugins enable ram-first-context
```

**B. Install as a distribution (entry-point discovery)**

```bash
pip install git+https://github.com/ether-btc/ram-first-context.git
hermes plugins enable ram-first-context
```

Path B registers the plugin in the `hermes_agent.plugins` entry-point group
(`ram_first_tool:register`); Hermes's entry-point scanner discovers it, and
the same `plugins.enabled` key activates it. Both paths use the directory
loader's package context for `user`-source manifests, so the plugin's relative
imports are unchanged.

After enabling, the tool appears under the toolset `ram_first`. A restart of
the Hermes gateway picks up the new toolset in running sessions.

Verify:

```bash
hermes plugins list | grep ram-first   # → enabled, 0.1.1
```

## Repository layout

```
plugin/
  __init__.py            # package entry: re-exports register() for the loader
  plugin.yaml            # Hermes plugin manifest (name, version, provides_tools, …)
  src/ram_first_tool.py  # the tool: fts_recall(), register(), telemetry, quoting
  src/__init__.py        # marker, so the pip/entry-point path resolves plugin.src.*
tests/                   # behavioural contract suite (pytest, no network, no live DB)
pyproject.toml           # packaging + ruff/pytest config + entry-point declaration
MANIFEST.in              # ensures plugin.yaml ships in the wheel
.github/workflows/ci.yml   # lint, test, and wheel-build gates
```

The `plugin/` directory is the exact live-install layout: copied verbatim into
`~/.hermes/plugins/<name>/` (Path A) or pip-installed as the `plugin` package
with an entry point (Path B). The live user-plugin install under
`~/.hermes/plugins/ram-first-context/` on the author's machine is the
three-file subset (`__init__.py`, `plugin.yaml`, `src/ram_first_tool.py`) —
`src/__init__.py` is a repo-only marker because Python's namespace-package
machinery already resolves `src` in the directory-copy path. Keep the
three-file live install in sync with `plugin/src/ram_first_tool.py` here
(which is the audited module — the 2026-10-07 audit record is at
`~/.hermes/handoffs/ram-first-audit-20261007/PROGRESS.md`).

## Development

```bash
pip install -e .[dev]   # or: pip install pytest ruff
ruff check plugin/src tests
pytest -q
```

The test suite builds a throwaway Mnemosyne-shaped SQLite DB (with the
`fts_working` index and insert trigger, exactly as the real one is shaped)
and aims the module at it through its `LIVE_DB`/`USAGE_LOG` constants. The
invariants it pins:

| # | Invariant |
|---|-----------|
| I1 | Reserved words (`AND`) and punctuation (`:`) match literally on the FTS path; no crash, no silent LIKE fallback with rank 0 |
| I3 | No pathological input (`"unterminated`, `NEAR:`, `**`, `"`, …) escapes `fts_recall()` |
| P1 | Trailing `*` is a prefix, not an exact-word search |
| F1 | Missing/corrupt `fts_working` → clean LIKE fallback, importance-ordered, rank 0 |
| H1 | A corrupt/truncated DB file (a `DatabaseError`) stays inside the fail-closed error envelope — no exception escapes `fts_recall()` |
| H2 | Non-`str` `query` / non-`int` `limit` return an error envelope, not a raised exception (bool rejected; `None` limit → default 5) |
| M1 | The LIKE fallback treats `%`, `_`, `\` in the query as literal data (`LIKE … ESCAPE`), matching the FTS path's literal-keyword contract |
| R1 | A successful query never changes a byte of the DB file (SHA-256 pre/post) |
| T1 | Telemetry is append-only `{ts, q_len, hits}` JSONL; a broken sink never breaks recall |
| C1 | `register()` hands the loader exactly the manifest-declared tool |

`I2` is intentionally absent: during the 2026-10-07 audit it ("punctuation must not
trigger a silent LIKE fallback with rank 0") was merged into `I1`, so the
numbering skips it. The identifiers are kept stable so they remain
traceable to the audit record, not renumbered.

The live install in this author's Hermes home stays a plain directory
(deliberately not a git checkout); this repository is its clean, documented
publication. The shipped `src/ram_first_tool.py` is the audited module — the
reconstructed diff of the audit session lives at
`projects/ram-first-audit-2026-09-05` (private) and in the wiki filing
`projects/ram-first-fts-recall-audit-2026-10-07`.

## License

MIT — see [LICENSE](LICENSE).
