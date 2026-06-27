# gizmosql

Run a [GizmoSQL](https://github.com/gizmodata/gizmosql) Flight SQL server as
a managed subprocess from Python — no compile toolchain required, no
ahead-of-time install step. The matching `gizmosql_server` binary is
downloaded from GitHub Releases on first use and cached under
`~/.cache/gizmosql/`.

[![PyPI version](https://img.shields.io/pypi/v/gizmosql.svg)](https://pypi.org/project/gizmosql/)
[![Python versions](https://img.shields.io/pypi/pyversions/gizmosql.svg)](https://pypi.org/project/gizmosql/)
[![Downloads](https://img.shields.io/pypi/dm/gizmosql.svg)](https://pypi.org/project/gizmosql/)
[![License](https://img.shields.io/pypi/l/gizmosql.svg)](https://github.com/gizmodata/gizmosql-py/blob/main/LICENSE)
[![CI](https://github.com/gizmodata/gizmosql-py/actions/workflows/ci.yml/badge.svg)](https://github.com/gizmodata/gizmosql-py/actions/workflows/ci.yml)
[![Docs](https://img.shields.io/badge/docs-python_library-blue.svg)](https://docs.gizmosql.com/#/python_library)

📖 **Full reference: [docs.gizmosql.com/#/python_library](https://docs.gizmosql.com/#/python_library)** — covers all `Server()` parameters, the pytest fixture pattern, the multi-agent pattern, environment variables, and limitations. This README is a quick tour; the docs page is the authoritative reference.

## Why?

GizmoSQL is a **network endpoint** — Apache Arrow Flight SQL on top of DuckDB.
The point of running it from Python isn't to talk to it from the same
process (the [`duckdb`](https://pypi.org/project/duckdb/) package is better
at that), it's to expose a SQL endpoint to **other things**:

- **pytest fixtures** that exercise a real Flight SQL server in CI
- **Multi-tool / multi-agent workflows** where the Python process is the
  orchestrator and other tools (a JDBC driver, Power BI, AI agents, a
  separate worker) all connect to the same server
- **Notebook demos** that need a live server while showing client-side code
- **"Expose this DuckDB file to the network"** — three lines of Python and
  any Flight SQL client can query it

## Install

```bash
pip install gizmosql
# or, with the optional ADBC client for Server.connect():
pip install 'gizmosql[adbc]'
```

The package itself has zero runtime dependencies. The `gizmosql_server`
binary is fetched on first use (~10 MB compressed); subsequent starts use
the cached copy.

## Quick start

```python
import gizmosql

with gizmosql.Server(password="tiger") as srv:
    print(srv.url)            # grpc+tcp://127.0.0.1:42173
    print(srv.username, srv.password)
    # ... point any Flight SQL client at srv.url ...
```

The context manager:
1. Downloads the matching server binary on first use.
2. Picks a free TCP port (so multiple parallel test workers don't collide).
3. Starts the subprocess and blocks until it's accepting connections.
4. SIGTERMs it on exit, escalating to SIGKILL if it doesn't respond.

### From the same Python process (optional)

With the `adbc` extra, you can also query the server from the same Python
that started it:

```python
import gizmosql

with gizmosql.Server(password="tiger") as srv:
    with srv.connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT GIZMOSQL_VERSION(), GIZMOSQL_EDITION()")
        print(cur.fetchall())
```

## Common configurations

```python
# Persistent on-disk database with the TPC-H sample loaded at startup.
gizmosql.Server(
    password="tiger",
    database_filename="warehouse.duckdb",
    init_sql_commands="CALL dbgen(sf=1);",  # ~1 GB on disk
)

# Pin the LTS channel (DuckDB LTS release).
gizmosql.Server(password="tiger", channel="lts")

# Pin a specific version (overrides the package's default version).
gizmosql.Server(password="tiger", version="v1.25.1")

# Listen on all interfaces (default is loopback only).
gizmosql.Server(password="tiger", host="0.0.0.0")

# Forward arbitrary CLI flags to gizmosql_server.
gizmosql.Server(
    password="tiger",
    extra_args=["--print-queries", "--query-log-level", "debug"],
)
```

See `gizmosql_server --help` (in the cached binary) for the full set of CLI
flags — anything passed via `extra_args` is forwarded verbatim.

## Versioning

`gizmosql==X.Y.Z` ships in lock-step with the matching GizmoSQL server tag
`vX.Y.Z` — that's the version it'll download and run unless you override
with `version="..."`. Python-only patches (rare) use PEP 440 `.postN`
suffixes, e.g. `1.25.1.post1`.

## pytest fixture pattern

```python
# conftest.py
import pytest
import gizmosql

@pytest.fixture(scope="session")
def gizmosql_server(tmp_path_factory):
    db = tmp_path_factory.mktemp("gizmosql") / "test.duckdb"
    with gizmosql.Server(password="testpw", database_filename=str(db)) as srv:
        yield srv
```

```python
# test_my_app.py
def test_my_jdbc_client_can_query(gizmosql_server):
    # Hand srv.url to whatever client you're testing.
    my_client.connect(gizmosql_server.url, "gizmosql", "testpw")
    ...
```

The session scope reuses the server across the whole run; ports are
auto-picked, so parallel `pytest-xdist` workers don't collide.

## Environment variables

| Variable | Purpose |
|---|---|
| `GIZMOSQL_CACHE_DIR` | Override the binary cache root (default: `~/.cache/gizmosql/` on POSIX, `%LOCALAPPDATA%\gizmosql\Cache` on Windows). |
| `GIZMOSQL_VERSION`   | Default server version when `Server(version=...)` is unset. |
| `GIZMOSQL_RELEASE_REPO` | GitHub repo to download releases from (default `gizmodata/gizmosql`). |
| `GIZMOSQL_RELEASE_BASE_URL` | Full base URL override; for testing against a staging release page. |

## Limitations

- Subprocess only (no in-process / FFI mode in v1). The server runs in its
  own process and clients (including `Server.connect()`) talk to it over the
  loopback Flight SQL endpoint. This is the right architecture for the
  intended use cases — see *Why?* above.
- Pre-built binaries exist for: macOS arm64, Linux amd64, Linux arm64,
  Windows amd64, Windows arm64. Other platforms aren't supported.

## Links

- 📖 **[Python Library reference](https://docs.gizmosql.com/#/python_library)** — full API + patterns
- 🌐 [Product page](https://gizmodata.com/gizmosql)
- 📦 [Install picker (CLI, Homebrew, Docker, Python)](https://gizmodata.com/gizmosql/install)
- 🚀 [Quick Start guide](https://docs.gizmosql.com/#/quickstart)
- 📚 [GizmoSQL documentation](https://docs.gizmosql.com)
- 🦆 [LTS Channel guide](https://docs.gizmosql.com/#/lts_channel)
- 🐛 [Issues / requests](https://github.com/gizmodata/gizmosql-py/issues)

## License

Apache-2.0, matching the main GizmoSQL project.
