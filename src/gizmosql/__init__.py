"""
gizmosql — run a GizmoSQL Flight SQL server from Python.

The package wraps the ``gizmosql_server`` binary as a managed subprocess:
it downloads the matching release on first use (cached under
``~/.cache/gizmosql/<version>/``) and exposes a small context-manager
API so test suites, notebooks, and AI-agent harnesses can spin up a
real Flight SQL endpoint in one line.

Quick start::

    import gizmosql

    with gizmosql.Server(password="tiger") as srv:
        print(srv.url)            # grpc+tcp://localhost:42173
        # ... point any Flight SQL client at srv.url ...

For a live connection from this same Python process (requires the
optional ``adbc`` extra)::

    pip install 'gizmosql[adbc]'

    with gizmosql.Server(password="tiger") as srv:
        with srv.connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT GIZMOSQL_VERSION()")
            print(cur.fetchone())
"""

from gizmosql._version import __version__
from gizmosql.server import Channel, Server, ServerConfig, ServerError

__all__ = [
    "__version__",
    "Channel",
    "Server",
    "ServerConfig",
    "ServerError",
]
