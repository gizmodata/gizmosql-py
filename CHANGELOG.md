# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
(with PEP 440 `.postN` suffixes for Python-only patches between GizmoSQL server releases).

## [Unreleased]

### Changed

- Bumped the `adbc-driver-gizmosql` floor from `>=1.0` to `>=2.0.0` in the
  `[adbc]` and `[test]` extras — the 2.0 driver is a Go-backed rewrite that is
  API byte-compatible with 1.x, so behavior is unchanged.
- Raised dependency floors to current stable releases: `pyarrow>=25`
  (was `>=15`) and `pytest>=9` (was `>=7`) in the `[adbc]`/`[test]` extras.
- CI: bumped `actions/setup-python` from v6 to v7 (checkout v7,
  upload-artifact v7, and download-artifact v8 were already current).
