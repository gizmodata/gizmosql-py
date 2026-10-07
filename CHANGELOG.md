# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
(with PEP 440 `.postN` suffixes for Python-only patches between GizmoSQL server releases).

## [Unreleased]

## [1.41.0.post2] - 2026-10-07

### Added
- `Server.uri` / `ServerConfig.uri`: the `gizmosql://host:port?transport=tcp`
  connection URI that GizmoSQL drivers (ADBC, JDBC, ...) understand.
  `gizmosql://` is TLS by default, so `?transport=tcp` marks the plaintext
  loopback endpoint this wrapper starts.

### Changed
- `Server.connect()` connects with `Server.uri`, and the README and docstrings
  show `srv.uri`. `Server.url` is unchanged (`grpc+tcp://host:port`) for
  generic Flight SQL clients such as `pyarrow.flight`.

## [1.41.0.post1] - 2026-10-06

Ships the GizmoSQL v1.41.0 server. The plain `1.41.0` package was never
published: its CI ran the macOS tests on `macos-14`, where the v1.41.0 server
(which requires macOS 15) aborts at startup, so the test gate held the release.

### Added
- `channel="edge"` — GizmoSQL's new edge release channel (v1.41.0+): the next
  DuckDB major, pre-release (today DuckDB `v2.0.0-alpha43763`). **Experimental,
  not for production workloads**: database files it creates can't be opened by
  the stable or LTS channels. Downloads `gizmosql_cli_<os>_<arch>_edge.zip` and
  runs `gizmosql_server_edge`, cached separately from the other channels.
- Network tests that start real LTS and edge servers and check their
  `-LTS` / `-EDGE` versions over ADBC.

### Changed
- CI runs the macOS tests on `macos-15`; GizmoSQL v1.41.0+ macOS binaries
  require macOS 15 (Sequoia) or later.
- Require `adbc-driver-gizmosql` >= 2.0.8. v2.0.8 fixes geometry-aware bulk ingest against GizmoSQL >= 1.37.0 (which now creates `GEOMETRY` columns server-side); earlier driver builds fail there with `No function matches 'st_geomfromwkb(GEOMETRY)'`.

## [1.35.1.post1] - 2026-07-29

### Changed

- Bumped the `adbc-driver-gizmosql` floor from `>=1.0` to `>=2.0.0` in the
  `[adbc]` and `[test]` extras — the 2.0 driver is a Go-backed rewrite
  (powered by the native Go
  [GizmoSQL ADBC driver](https://github.com/gizmodata/gizmosql-adbc)) that is
  API byte-compatible with 1.x, so behavior is unchanged. It brings DDL/DML
  immediate execution, `RETURNING` support, `gizmosql://` URIs, and OAuth/SSO
  via the shared Go driver library.
- Raised dependency floors to current stable releases: `pyarrow>=25`
  (was `>=15`) and `pytest>=9` (was `>=7`) in the `[adbc]`/`[test]` extras.
- CI: bumped `actions/setup-python` from v6 to v7 (checkout v7,
  upload-artifact v7, and download-artifact v8 were already current).
