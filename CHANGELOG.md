<!--
SPDX-FileCopyrightText: 2026 Lusoris <lusoris@proton.me>
SPDX-License-Identifier: EUPL-1.2
-->

# Changelog

Every change a consumer of the package notices is listed here. The format
follows [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/), and
versions follow [PEP 440](https://peps.python.org/pep-0440/).

## [Unreleased]

### Added

- Module `process`: `run` starts a command with a required timeout and ends
  its whole process group on expiry; `child_env` builds the environment of a
  child process.
- Module `files`: `read_json` and `loads_json` refuse duplicate keys and
  non-finite numbers; `write_bytes_atomic`, `write_text_atomic` and
  `write_json` replace a file in one step, and `write_json(ensure_ascii=True)`
  escapes non-ASCII text as the standard serialiser does; `open_bytes_atomic`,
  `open_text_atomic` and `staged_path` publish a file written in a block.
- Module `digest`: `sha256_file`, `sha256_bytes` and `sha256_text`.
- A reproducible package: the same commit gives the same source
  distribution and wheel on every host (README, "Reproducible package").
- Release workflows `.github/workflows/release.yml` and
  `.github/workflows/release-build.yml`: a tag builds, signs and attests the
  files, reads them back, uploads the packages to PyPI and stores every file
  in a draft forge release (docs/adr/0001-release-job.md).
