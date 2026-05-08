"""
:class:`Server` — managed-subprocess wrapper around ``gizmosql_server``.

Typical use::

    import gizmosql

    with gizmosql.Server(password="tiger") as srv:
        print(srv.url)
        # Hand srv.url to anything that speaks Flight SQL.

Optional ADBC client (``pip install 'gizmosql[adbc]'``)::

    with gizmosql.Server(password="tiger") as srv:
        with srv.connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT GIZMOSQL_VERSION()")
            print(cur.fetchone())
"""

from __future__ import annotations

import contextlib
import dataclasses
import io
import os
import secrets
import signal
import socket
import subprocess
import sys
import time
import warnings
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import IO, Any, Literal

from gizmosql._install import InstallError, ensure_binary
from gizmosql._version import __version__

Channel = Literal["stable", "lts"]


class ServerError(RuntimeError):
    """Raised when the GizmoSQL server fails to start, becomes unhealthy, or
    can't be cleanly shut down."""


@dataclasses.dataclass(frozen=True)
class ServerConfig:
    """Resolved configuration for a Server instance — exposed as ``Server.config``
    so test fixtures and notebooks can introspect what's actually running."""

    host: str
    port: int
    health_port: int
    username: str
    password: str
    database_filename: str | None
    init_sql_commands: str | None
    channel: Channel
    version: str
    binary: Path

    @property
    def url(self) -> str:
        """Flight SQL URL (``grpc+tcp://...``). For TLS endpoints, callers should
        construct the URL themselves — this convenience always returns plaintext."""
        return f"grpc+tcp://{self.host}:{self.port}"


class Server:
    """
    Run a GizmoSQL server as a managed subprocess.

    On first use the matching binary is downloaded from
    https://github.com/gizmodata/gizmosql/releases into a per-user cache.

    Parameters
    ----------
    database_filename:
        Path passed to ``gizmosql_server --database-filename``. ``None``
        (default) uses an ephemeral in-memory DuckDB.
    username, password:
        Credentials. ``password`` defaults to a random 32-char token so a
        forgotten ``Server()`` doesn't leave an open auth-less endpoint.
    host:
        Bind interface. Defaults to ``"127.0.0.1"`` (loopback only).
    port, health_port:
        TCP ports to bind. ``0`` (default) picks a free port automatically —
        good for parallel test workers.
    channel:
        ``"stable"`` (default) or ``"lts"``. The wrapper downloads the matching
        artifact (``gizmosql_server`` vs ``gizmosql_server_lts``).
    version:
        GizmoSQL release tag, e.g. ``"v1.25.1"``. Defaults to the version this
        package was published as (so ``pip install gizmosql==1.25.1`` always
        runs server v1.25.1, with no surprise drift).
    init_sql_commands:
        SQL run at server startup, semicolon-separated. Useful for
        ``CALL dbgen(sf=0.01);`` style fixtures.
    extra_args:
        Additional argv passed verbatim to ``gizmosql_server``. Useful for TLS,
        token auth, instrumentation, etc.
    extra_env:
        Environment overrides (merged onto ``os.environ``).
    startup_timeout:
        Seconds to wait for the server to start accepting connections before
        raising :class:`ServerError`. Default 30 s.
    stdout, stderr:
        Where the server's logs go. Default is ``sys.stderr`` (so notebook /
        pytest users see live banner output). Pass ``subprocess.DEVNULL`` to
        silence, or a file-like object to capture.
    binary:
        Override the path to the server executable. By default the wrapper
        downloads + caches one based on (channel, version).
    """

    def __init__(
        self,
        *,
        database_filename: str | os.PathLike[str] | None = None,
        username: str = "gizmosql",
        password: str | None = None,
        host: str = "127.0.0.1",
        port: int = 0,
        health_port: int = 0,
        channel: Channel = "stable",
        version: str | None = None,
        init_sql_commands: str | None = None,
        extra_args: Iterable[str] = (),
        extra_env: Mapping[str, str] | None = None,
        startup_timeout: float = 30.0,
        stdout: IO[bytes] | int | None = None,
        stderr: IO[bytes] | int | None = None,
        binary: str | os.PathLike[str] | None = None,
    ) -> None:
        if channel not in ("stable", "lts"):
            raise ValueError(f"channel must be 'stable' or 'lts' (got {channel!r})")

        # Resolve version: explicit > env override > package version.
        resolved_version = version or os.environ.get("GIZMOSQL_VERSION") or __version__
        if not resolved_version.startswith("v"):
            resolved_version = "v" + resolved_version

        # Resolve binary: explicit path > download-on-first-use cache.
        if binary is not None:
            bin_path = Path(binary).expanduser().resolve()
            if not bin_path.exists():
                raise ServerError(f"binary override does not exist: {bin_path}")
        else:
            try:
                bin_path = ensure_binary(resolved_version, channel)
            except InstallError as e:
                raise ServerError(str(e)) from e

        # Auto-pick free ports when caller asked for ``0``. Note: there's a
        # small race between releasing the socket here and the server binding
        # to it; in practice it's fine for test workers because each worker
        # gets its own port pair.
        if port == 0:
            port = _free_port()
        if health_port == 0:
            health_port = _free_port(exclude=port)

        # Random password unless caller pinned one. We never persist this; if
        # the caller wants a reproducible password, they pass it explicitly.
        if password is None:
            password = secrets.token_urlsafe(24)

        self.config = ServerConfig(
            host=host,
            port=port,
            health_port=health_port,
            username=username,
            password=password,
            database_filename=str(database_filename) if database_filename is not None else None,
            init_sql_commands=init_sql_commands,
            channel=channel,
            version=resolved_version,
            binary=bin_path,
        )

        self._extra_args = list(extra_args)
        self._extra_env = dict(extra_env) if extra_env else {}
        self._startup_timeout = startup_timeout
        # Default to the parent's stderr (so banner output shows up in
        # notebooks / pytest with -s), but Popen requires a real OS-level
        # fileno; fall back to DEVNULL if the caller / testing harness has
        # replaced sys.stderr with a captured stream that doesn't have one.
        self._stdout = _resolve_subprocess_stream(stdout if stdout is not None else sys.stderr)
        self._stderr = _resolve_subprocess_stream(stderr if stderr is not None else sys.stderr)
        self._proc: subprocess.Popen[bytes] | None = None

    # ---- public API --------------------------------------------------------

    @property
    def url(self) -> str:
        """``grpc+tcp://host:port`` for connecting Flight SQL clients."""
        return self.config.url

    @property
    def host(self) -> str:
        return self.config.host

    @property
    def port(self) -> int:
        return self.config.port

    @property
    def username(self) -> str:
        return self.config.username

    @property
    def password(self) -> str:
        return self.config.password

    @property
    def pid(self) -> int | None:
        """OS process id of the running server (``None`` if not started)."""
        return self._proc.pid if self._proc else None

    def is_running(self) -> bool:
        """``True`` while the subprocess hasn't exited."""
        return self._proc is not None and self._proc.poll() is None

    def start(self) -> Server:
        """Spawn the server and block until it's accepting connections.

        Idempotent: calling ``start()`` on an already-running server is a no-op.
        """
        if self.is_running():
            return self

        argv = self._build_argv()
        env = {**os.environ, "GIZMOSQL_PASSWORD": self.config.password, **self._extra_env}

        self._proc = subprocess.Popen(
            argv,
            stdout=self._stdout,
            stderr=self._stderr,
            env=env,
            # New session so signals don't propagate to children we own and we
            # can kill the whole group on cleanup.
            start_new_session=os.name != "nt",
        )

        try:
            self._wait_until_ready()
        except Exception:
            # If we couldn't bring it up, don't leave a zombie running.
            self.stop()
            raise

        return self

    def stop(self, *, timeout: float = 10.0) -> None:
        """Politely SIGTERM the server, escalating to SIGKILL after ``timeout``."""
        if self._proc is None:
            return
        if self._proc.poll() is not None:
            self._proc = None
            return

        try:
            if os.name == "nt":
                self._proc.terminate()
            else:
                # SIGTERM the whole session so child threads/processes go too.
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(os.getpgid(self._proc.pid), signal.SIGTERM)
        except Exception as e:  # noqa: BLE001
            warnings.warn(f"could not signal gizmosql_server cleanly: {e}", stacklevel=2)

        try:
            self._proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            warnings.warn(
                f"gizmosql_server didn't exit within {timeout}s; sending SIGKILL", stacklevel=2
            )
            try:
                if os.name == "nt":
                    self._proc.kill()
                else:
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(os.getpgid(self._proc.pid), signal.SIGKILL)
            except Exception as e:  # noqa: BLE001
                warnings.warn(f"SIGKILL failed: {e}", stacklevel=2)
            self._proc.wait(timeout=5.0)
        finally:
            self._proc = None

    def connect(self, **kwargs: Any) -> Any:
        """Open an ADBC Flight SQL connection to this server.

        Requires the ``adbc`` extra (``pip install 'gizmosql[adbc]'``).

        Returns an ``adbc_driver_manager.dbapi.Connection``. All ``**kwargs``
        are merged into the driver options.
        """
        try:
            import adbc_driver_gizmosql.dbapi as gizmosql_dbapi  # type: ignore[import-not-found]
        except ImportError as e:  # pragma: no cover
            raise ImportError(
                "Server.connect() requires the 'adbc' extra. "
                "Install with:  pip install 'gizmosql[adbc]'"
            ) from e

        return gizmosql_dbapi.connect(
            uri=self.url,
            db_kwargs={
                "username": self.config.username,
                "password": self.config.password,
                **kwargs,
            },
        )

    # ---- context-manager glue ---------------------------------------------

    def __enter__(self) -> Server:
        return self.start()

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.stop()

    def __del__(self) -> None:
        # Best-effort cleanup if a caller forgot to use a context manager.
        # We deliberately swallow exceptions here — __del__ shouldn't raise.
        with contextlib.suppress(Exception):
            self.stop(timeout=2.0)

    # ---- internals ---------------------------------------------------------

    def _build_argv(self) -> list[str]:
        argv: list[str] = [
            str(self.config.binary),
            "--hostname",
            self.config.host,
            "--port",
            str(self.config.port),
            "--health-port",
            str(self.config.health_port),
            "--username",
            self.config.username,
            # Password is passed via env so it doesn't show up in `ps` output.
        ]
        if self.config.database_filename is not None:
            argv += ["--database-filename", self.config.database_filename]
        if self.config.init_sql_commands:
            argv += ["--init-sql-commands", self.config.init_sql_commands]
        argv += list(self._extra_args)
        return argv

    def _wait_until_ready(self) -> None:
        """Poll the bound port until something is listening, or raise."""
        deadline = time.monotonic() + self._startup_timeout
        last_err: BaseException | None = None
        while time.monotonic() < deadline:
            if self._proc is not None and self._proc.poll() is not None:
                rc = self._proc.returncode
                raise ServerError(
                    f"gizmosql_server exited during startup (returncode={rc}). "
                    f"Check the server's stderr for details."
                )
            try:
                with socket.create_connection((self.config.host, self.config.port), timeout=0.5):
                    return
            except OSError as e:  # not listening yet
                last_err = e
                time.sleep(0.1)
        raise ServerError(
            f"gizmosql_server didn't start listening on "
            f"{self.config.host}:{self.config.port} within "
            f"{self._startup_timeout}s (last error: {last_err})"
        )


# ---- helpers ---------------------------------------------------------------


def _resolve_subprocess_stream(stream: Any) -> Any:
    """``subprocess.Popen`` needs an int fd or a real file-like with ``fileno()``.

    pytest's default capture and Jupyter's IPython display both replace
    ``sys.stdout`` / ``sys.stderr`` with stream objects that don't expose a
    real fileno; passing those to ``Popen`` raises ``io.UnsupportedOperation:
    fileno``. Detect that case and fall back to ``DEVNULL`` rather than
    crashing — the user can still capture the server's output by passing an
    explicit ``open(...)``-backed file or by running ``pytest -s``.
    """
    # ints (DEVNULL=-3, PIPE=-1, STDOUT=-2) and None pass through unchanged.
    if stream is None or isinstance(stream, int):
        return stream
    fileno_fn = getattr(stream, "fileno", None)
    if fileno_fn is None:
        return subprocess.DEVNULL
    try:
        fileno_fn()
    except (OSError, ValueError, io.UnsupportedOperation):
        return subprocess.DEVNULL
    return stream


def _free_port(*, exclude: int | None = None, attempts: int = 8) -> int:
    """Ask the OS for a free TCP port. We bind+release rather than bind+hold so
    we don't accumulate socket handles in long-running test workers.

    With ``exclude`` set we'll retry up to ``attempts`` times to avoid handing
    out the same port twice for a single Server (port + health_port)."""
    for _ in range(attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        if port != exclude:
            return port
    raise ServerError("couldn't find a free TCP port")


__all__ = ["Channel", "Server", "ServerConfig", "ServerError"]
