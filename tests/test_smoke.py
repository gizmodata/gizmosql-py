"""Smoke tests for the gizmosql Python package.

Two tiers:

* ``unit``   — no network, no subprocess. Validate imports, version parsing,
               argv construction, ports.
* ``network``— spins up an actual server, downloading the binary on first
               use. Marked ``@pytest.mark.network`` so CI can opt in.

Run only the offline tier::

    pytest -m "not network"
"""

from __future__ import annotations

import socket

import pytest

import gizmosql
from gizmosql import _install
from gizmosql import server as server_mod

# ---- offline tier ----------------------------------------------------------


def test_package_exports() -> None:
    assert hasattr(gizmosql, "Server")
    assert hasattr(gizmosql, "ServerConfig")
    assert hasattr(gizmosql, "ServerError")
    assert hasattr(gizmosql, "__version__")
    assert isinstance(gizmosql.__version__, str)
    assert gizmosql.__version__.split(".")[0].isdigit()  # PEP-440-ish


def test_detect_os_arch_returns_supported() -> None:
    """We don't assert the actual values (depends on test machine), just
    that the function doesn't blow up on a supported runner."""
    os_name, arch = _install._detect_os_arch()
    assert os_name in ("macos", "linux", "windows")
    assert arch in ("amd64", "arm64")


def test_artifact_naming_matches_release_convention() -> None:
    assert _install._artifact_name("linux", "amd64", "stable") == "gizmosql_cli_linux_amd64.zip"
    assert _install._artifact_name("linux", "amd64", "lts") == "gizmosql_cli_linux_amd64_lts.zip"
    assert _install._artifact_name("macos", "arm64", "stable") == "gizmosql_cli_macos_arm64.zip"
    assert (
        _install._artifact_name("windows", "amd64", "lts") == "gizmosql_cli_windows_amd64_lts.zip"
    )


def test_binary_names_carry_lts_suffix_and_exe_on_windows() -> None:
    assert _install._binary_names("linux", "stable") == ("gizmosql_server", "gizmosql_client")
    assert _install._binary_names("linux", "lts") == ("gizmosql_server_lts", "gizmosql_client_lts")
    assert _install._binary_names("windows", "stable") == (
        "gizmosql_server.exe",
        "gizmosql_client.exe",
    )
    assert _install._binary_names("windows", "lts") == (
        "gizmosql_server_lts.exe",
        "gizmosql_client_lts.exe",
    )


def test_cache_dir_for_includes_version_and_channel(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GIZMOSQL_CACHE_DIR", str(tmp_path))
    p = _install.cache_dir_for("v1.25.1", "lts")
    assert p == tmp_path / "v1.25.1" / "lts"

    # Versions without leading 'v' get one prepended for stable cache layout.
    p2 = _install.cache_dir_for("1.25.1", "stable")
    assert p2 == tmp_path / "v1.25.1" / "stable"


def test_free_port_returns_distinct() -> None:
    p1 = server_mod._free_port()
    p2 = server_mod._free_port(exclude=p1)
    assert p1 != p2
    assert 1 <= p1 <= 65535


def test_server_rejects_bad_channel() -> None:
    with pytest.raises(ValueError, match="channel must be"):
        gizmosql.Server(channel="experimental")  # type: ignore[arg-type]


def test_server_rejects_missing_binary_override(tmp_path) -> None:
    bogus = tmp_path / "no-such-binary"
    with pytest.raises(gizmosql.ServerError, match="binary override does not exist"):
        gizmosql.Server(binary=bogus)


def test_server_argv_builds_expected_flags(tmp_path) -> None:
    """We can construct a Server without starting it (binary override skips
    the network step) and inspect the argv it would exec."""
    fake = tmp_path / "fake_server"
    fake.write_text("#!/bin/sh\necho fake\n")
    fake.chmod(0o755)

    srv = gizmosql.Server(
        binary=fake,
        username="alice",
        password="rabbit",
        host="0.0.0.0",
        port=12345,
        health_port=12346,
        database_filename="/tmp/x.duckdb",
        init_sql_commands="CALL dbgen(sf=0.01);",
        extra_args=["--print-queries"],
    )
    argv = srv._build_argv()
    assert argv[0] == str(fake)
    assert "--username" in argv
    assert "alice" in argv
    assert "--port" in argv
    assert "12345" in argv
    assert "--health-port" in argv
    assert "12346" in argv
    assert "--database-filename" in argv
    assert "/tmp/x.duckdb" in argv
    assert "--init-sql-commands" in argv
    assert "CALL dbgen(sf=0.01);" in argv
    assert "--print-queries" in argv
    # Password is passed via env, not argv (so it doesn't show up in `ps`).
    assert "rabbit" not in argv


def test_server_random_password_when_unset(tmp_path) -> None:
    fake = tmp_path / "fake_server"
    fake.write_text("#!/bin/sh\necho fake\n")
    fake.chmod(0o755)
    srv = gizmosql.Server(binary=fake)
    assert srv.password
    assert len(srv.password) >= 16  # token_urlsafe(24) is comfortably above this


def test_url_property_uses_grpc_tcp(tmp_path) -> None:
    fake = tmp_path / "fake_server"
    fake.write_text("#!/bin/sh\necho fake\n")
    fake.chmod(0o755)
    srv = gizmosql.Server(binary=fake, host="127.0.0.1", port=31337)
    assert srv.url == "grpc+tcp://127.0.0.1:31337"


# ---- network tier ----------------------------------------------------------


@pytest.mark.network
def test_real_server_starts_and_accepts_connection(tmp_path) -> None:
    """End-to-end: download the binary if needed, spin up a server, and check
    that something is actually listening on the bound port."""
    with gizmosql.Server(password="tiger", database_filename=str(tmp_path / "smoke.duckdb")) as srv:
        print(f"  server URL: {srv.url}  pid: {srv.pid}  binary: {srv.config.binary}")
        # The context manager doesn't return until the port is open, so the
        # connection here should always succeed if start() did.
        with socket.create_connection((srv.host, srv.port), timeout=2.0):
            pass
        assert srv.is_running()
    # After the with-block the subprocess is gone.
    assert not srv.is_running()
    print("  → server stopped cleanly")


@pytest.mark.network
def test_real_server_runs_init_sql(tmp_path) -> None:
    """Start with --init-sql-commands and confirm the server didn't bail on it
    (it would exit during startup if the SQL was malformed)."""
    with gizmosql.Server(
        password="tiger",
        database_filename=str(tmp_path / "init.duckdb"),
        init_sql_commands="SELECT 1; SELECT 2;",
    ) as srv:
        print(f"  server up at {srv.url} (pid {srv.pid}) with init SQL applied")
        assert srv.is_running()


@pytest.mark.network
def test_real_server_query_via_adbc(tmp_path) -> None:
    """End-to-end SQL: spin up the server, connect via the optional ADBC
    extra, and verify GIZMOSQL_VERSION() returns the package's pinned version.

    This is the most thorough of the network tests — it exercises the full
    download → spawn → bind → handshake → auth → execute → fetch → shutdown
    path that real users will hit. Skipped automatically when adbc isn't
    installed (the test environment ships it via the [test] extra)."""
    pytest.importorskip("adbc_driver_gizmosql")

    with gizmosql.Server(
        password="tiger",
        database_filename=str(tmp_path / "adbc.duckdb"),
    ) as srv:
        print(f"  server URL: {srv.url}  pid: {srv.pid}")
        with srv.connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT GIZMOSQL_VERSION(), GIZMOSQL_EDITION();")
            version, edition = cur.fetchone()
        print(f"  query result: GIZMOSQL_VERSION()={version!r}  GIZMOSQL_EDITION()={edition!r}")
        print(f"  package version: {gizmosql.__version__}")

        # The server's GIZMOSQL_VERSION() reports whatever the *server binary*
        # was tagged as, which by default is the package's own version.
        # Strip the leading 'v' for comparison since gizmosql.__version__ is
        # the bare semver and the SQL function returns "vX.Y.Z".
        assert version.lstrip("v").startswith(gizmosql.__version__.lstrip("v"))
        assert edition in ("Core", "Enterprise")


@pytest.mark.network
def test_real_server_with_tpch_init_sql(tmp_path) -> None:
    """End-to-end fixture-style flow: bake TPC-H scale-factor 0.01 into the
    server at startup via --init-sql-commands, then run real SELECTs through
    the ADBC client.

    This is the workflow we recommend in the Quick Start guide and the
    'pytest fixture' section of the README, so we'd better be sure it works.

    sf=0.01 generates ~10 MB on disk: 8 standard TPC-H tables, ~60k rows in
    lineitem, ~25 nations, etc. Big enough to exercise joins, small enough
    to run in CI in well under a second."""
    pytest.importorskip("adbc_driver_gizmosql")

    with (
        gizmosql.Server(
            password="tiger",
            database_filename=str(tmp_path / "tpch.duckdb"),
            init_sql_commands="CALL dbgen(sf=0.01);",
        ) as srv,
        srv.connect() as conn,
    ):
        # 1. The 8 TPC-H tables are present after dbgen.
        with conn.cursor() as cur:
            cur.execute("SHOW TABLES;")
            tables = {row[0] for row in cur.fetchall()}
        expected = {
            "customer",
            "lineitem",
            "nation",
            "orders",
            "part",
            "partsupp",
            "region",
            "supplier",
        }
        assert expected <= tables, f"missing TPC-H tables: {expected - tables}"

        # 2. Single-table SELECT — sf=0.01 always loads exactly 25 nations.
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM nation;")
            (n_count,) = cur.fetchone()
        assert n_count == 25

        # 3. lineitem row count is the standard sf=0.01 value (within a small
        #    tolerance — dbgen has known minor variability across releases).
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM lineitem;")
            (li_count,) = cur.fetchone()
        assert 50_000 < li_count < 70_000, f"unexpected lineitem row count: {li_count}"

        # 4. Multi-table join — the marquee example from the Quick Start guide.
        with conn.cursor() as cur:
            cur.execute("""
                SELECT n_name, COUNT(*) AS customer_count
                FROM nation, customer
                WHERE n_nationkey = c_nationkey
                GROUP BY n_name
                ORDER BY customer_count DESC, n_name
                LIMIT 5;
            """)
            top5 = cur.fetchall()
        assert len(top5) == 5
        for name, count in top5:
            assert isinstance(name, str)
            assert name
            assert isinstance(count, int)
            assert count > 0


@pytest.mark.network
def test_two_servers_get_distinct_ports(tmp_path) -> None:
    """Auto-port-picking should give parallel servers non-overlapping ports —
    the property pytest-xdist users rely on for parallel test runs."""
    db1 = tmp_path / "a.duckdb"
    db2 = tmp_path / "b.duckdb"
    with (
        gizmosql.Server(password="x", database_filename=str(db1)) as a,
        gizmosql.Server(password="y", database_filename=str(db2)) as b,
    ):
        assert a.port != b.port
        assert a.config.health_port != b.config.health_port
        assert a.is_running()
        assert b.is_running()
