"""
Download-on-first-use of the ``gizmosql_server`` binary.

The matching release zip is fetched from the public GitHub Releases
of ``gizmodata/gizmosql`` (or whatever ``GIZMOSQL_RELEASE_REPO`` points
at) and unpacked into a per-version cache directory:

    ~/.cache/gizmosql/<version>/[<channel>/]gizmosql_server[_lts]
    ~/.cache/gizmosql/<version>/[<channel>/]gizmosql_client[_lts]

Override the cache root with ``GIZMOSQL_CACHE_DIR`` and the released
artifact source with ``GIZMOSQL_RELEASE_BASE_URL`` (for testing against
a staging copy of the releases page).

This module is intentionally network-only stdlib — no ``requests``,
no ``httpx`` — so the package's dependency surface stays at zero.
"""

from __future__ import annotations

import hashlib
import os
import platform
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from urllib.error import HTTPError, URLError

REPO_ENV = "GIZMOSQL_RELEASE_REPO"
CACHE_ENV = "GIZMOSQL_CACHE_DIR"
BASE_URL_ENV = "GIZMOSQL_RELEASE_BASE_URL"

DEFAULT_REPO = "gizmodata/gizmosql"


class InstallError(RuntimeError):
    """Raised when the binary couldn't be downloaded or extracted."""


def _release_base_url() -> str:
    """e.g. 'https://github.com/gizmodata/gizmosql/releases'."""
    if override := os.environ.get(BASE_URL_ENV):
        return override.rstrip("/")
    repo = os.environ.get(REPO_ENV, DEFAULT_REPO)
    return f"https://github.com/{repo}/releases"


def _cache_root() -> Path:
    """Where downloaded binaries live. Resolves env override first."""
    if override := os.environ.get(CACHE_ENV):
        return Path(override).expanduser()
    # Follow the XDG cache convention on Linux/macOS; LOCALAPPDATA on Windows.
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "gizmosql" / "Cache"
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "gizmosql"


def _detect_os_arch() -> tuple[str, str]:
    """Return ('macos'|'linux'|'windows', 'amd64'|'arm64'). Raises if unsupported."""
    sys_name = platform.system().lower()
    if sys_name == "darwin":
        os_name = "macos"
    elif sys_name == "linux":
        os_name = "linux"
    elif sys_name == "windows":
        os_name = "windows"
    else:
        raise InstallError(f"unsupported platform.system(): {sys_name!r}")

    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        arch = "amd64"
    elif machine in ("aarch64", "arm64"):
        arch = "arm64"
    else:
        raise InstallError(f"unsupported platform.machine(): {machine!r}")

    # Validate (os, arch) tuples actually published.
    supported = {
        ("macos", "arm64"),
        ("linux", "amd64"),
        ("linux", "arm64"),
        ("windows", "amd64"),
        ("windows", "arm64"),
    }
    if (os_name, arch) not in supported:
        raise InstallError(
            f"no published GizmoSQL release for {os_name}/{arch}. "
            f"Supported combinations: {sorted(supported)}"
        )
    return os_name, arch


def _artifact_name(os_name: str, arch: str, channel: str) -> str:
    suffix = "_lts" if channel == "lts" else ""
    return f"gizmosql_cli_{os_name}_{arch}{suffix}.zip"


def _binary_names(os_name: str, channel: str) -> tuple[str, str]:
    """Return (server_basename, client_basename) for the channel + OS."""
    suffix = "_lts" if channel == "lts" else ""
    ext = ".exe" if os_name == "windows" else ""
    return (f"gizmosql_server{suffix}{ext}", f"gizmosql_client{suffix}{ext}")


def server_release_tag(version: str) -> str:
    """Map a package version to the upstream GizmoSQL release tag to download.

    Python-only releases carry a PEP 440 ``.postN`` suffix (and may grow a
    ``.devN`` or ``+local`` segment); the server binary they ship is the
    plain upstream ``X.Y.Z`` build — that's what's actually published as a
    GizmoSQL release. Strip everything from the first such suffix onward so
    the download URL and cache dir point at a tag that really exists.

        1.32.0        -> v1.32.0
        1.32.0.post1  -> v1.32.0
        v1.32.0.dev3  -> v1.32.0
    """
    v = version[1:] if version.startswith("v") else version
    for sep in (".post", ".dev", "+"):
        idx = v.find(sep)
        if idx != -1:
            v = v[:idx]
    return "v" + v


def cache_dir_for(version: str, channel: str) -> Path:
    """The directory the binaries for ``(version, channel)`` live in."""
    if not version.startswith("v"):
        version = "v" + version
    return _cache_root() / version / channel


def _download(url: str, dst: Path, *, timeout: float = 60.0) -> None:
    """Stream ``url`` into ``dst`` with a clean error if it doesn't exist."""
    req = urllib.request.Request(url, headers={"User-Agent": "gizmosql-py"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r, dst.open("wb") as f:
            shutil.copyfileobj(r, f)
    except HTTPError as e:
        if e.code == 404:
            raise InstallError(
                f"GizmoSQL release artifact not found: {url}\n"
                f"  (channel/version combination may not exist; "
                f"see {_release_base_url()})"
            ) from e
        raise InstallError(f"download failed ({e.code}): {url}") from e
    except URLError as e:
        raise InstallError(f"download failed: {url} ({e.reason})") from e


def _verify_sha256(zip_path: Path, expected: str) -> None:
    h = hashlib.sha256()
    with zip_path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    actual = h.hexdigest()
    if expected.lower() != actual.lower():
        raise InstallError(
            f"SHA-256 mismatch for {zip_path.name}:\n  expected: {expected}\n  actual:   {actual}"
        )


def ensure_binary(
    version: str,
    channel: str = "stable",
    *,
    progress: bool = True,
) -> Path:
    """
    Make sure ``gizmosql_server[_lts]`` for ``(version, channel)`` exists in
    the local cache, downloading + extracting the release zip if needed.

    Returns the absolute path to the server executable (it's already +x and
    on Windows it has the ``.exe`` suffix).
    """
    if channel not in ("stable", "lts"):
        raise InstallError(f"channel must be 'stable' or 'lts' (got {channel!r})")

    os_name, arch = _detect_os_arch()
    server_name, _client_name = _binary_names(os_name, channel)

    target_dir = cache_dir_for(version, channel)
    server_path = target_dir / server_name
    if server_path.exists() and os.access(server_path, os.X_OK):
        return server_path

    artifact = _artifact_name(os_name, arch, channel)
    url = f"{_release_base_url()}/download/{version if version.startswith('v') else 'v' + version}/{artifact}"

    if progress:
        print(f"gizmosql-py: downloading {artifact} from {url}", file=sys.stderr)

    target_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="gizmosql-install-") as td:
        zip_path = Path(td) / artifact
        _download(url, zip_path)

        # Best-effort SHA-256 verification — only if a .sha256 sibling exists.
        sha_url = f"{url}.sha256"
        sha_path = Path(td) / f"{artifact}.sha256"
        try:
            _download(sha_url, sha_path, timeout=15.0)
        except InstallError:
            sha_path = None  # type: ignore[assignment]
        if sha_path is not None and sha_path.exists():
            expected = sha_path.read_text().split()[0].strip()
            _verify_sha256(zip_path, expected)
            if progress:
                print("gizmosql-py: SHA-256 verified.", file=sys.stderr)

        # Extract — the zip's flat layout puts both binaries at the root.
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(target_dir)

    if not server_path.exists():
        raise InstallError(
            f"extracted {artifact} to {target_dir} but {server_name} wasn't there. "
            f"contents: {sorted(p.name for p in target_dir.iterdir())}"
        )

    # POSIX: zipfile drops the executable bit; restore it.
    if os.name != "nt":
        for f in target_dir.iterdir():
            if f.suffix not in (".dll",):
                f.chmod(f.stat().st_mode | 0o111)

    # macOS: clear quarantine so Gatekeeper doesn't reject the curl-downloaded
    # binary on first run. The binaries themselves are notarized by Apple.
    if os_name == "macos":
        try:
            import subprocess

            subprocess.run(
                ["xattr", "-d", "com.apple.quarantine", str(server_path)],
                capture_output=True,
                check=False,
            )
        except FileNotFoundError:  # xattr missing — rare
            pass

    return server_path
