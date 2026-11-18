<!--
SPDX-FileCopyrightText: 2026 Lusoris <lusoris@proton.me>
SPDX-License-Identifier: EUPL-1.2
-->

# Development

```sh
make verify-all
```

This is the whole gate, and CI runs the same target. It needs `python3.11`,
`python3.12`, `python3.13`, `python3.14` and `python3.15` on `PATH` (or
`PYTHONS="..."` with full paths), `reuse` 6.2.0, `trivy` 0.76.0 (or
`TRIVY=...`) and the standards engine. An interpreter or a tool that is
missing fails the gate.

The engine is the commit that `PRAETOR_REF` in
[ci.yml](https://github.com/plurano/cistula/blob/main/.github/workflows/ci.yml) pins, for the gate and for the Git hooks
alike. [.config/lefthook/engine.sh](https://github.com/plurano/cistula/blob/main/.config/lefthook/engine.sh) installs it
once per pin with `go install` and fails when it cannot; a `praetorctl` on
`PATH` is not used in its place. `make praetor-engine-path` prints the binary
in use, and the gate stops at its first step (`make verify-engine-pin`) when
no workflow declares the pin.

1. One environment per interpreter is installed from `requirements/dev.txt`.
   Every package is pinned by hash; nothing else is installed.
2. Format, lint, types, licence headers, the exception list, the Codacy
   exclusions and the list of supported Python versions are checked once. Then
   [tools/scanners.py](https://github.com/plurano/cistula/blob/main/tools/scanners.py) runs semgrep with the rules in
   [.config/semgrep/hiss.yml](https://github.com/plurano/cistula/blob/main/.config/semgrep/hiss.yml) and trivy on both
   locks. Each scanner first has to report the defects planted in a scratch
   folder; a scanner that reports nothing there fails the gate.
3. One source distribution and one wheel are built, and their sizes are
   checked against the figures under [Reproducible package](#reproducible-package).
4. Every interpreter builds both once more. One differing byte fails.
5. The wheel of step 3 is installed into each environment, and the tests run
   against the installed package with 100 % branch coverage required. Before
   them, [tools/check_consumer.py](https://github.com/plurano/cistula/blob/main/tools/check_consumer.py) installs the wheel
   as [Installing](https://github.com/plurano/cistula#install) describes, into a new environment that holds
   nothing else, and [tests/consumer.py](https://github.com/plurano/cistula/blob/main/tests/consumer.py) calls the public
   interface there from an empty directory.
6. The standards engine checks that the agent contexts equal `AGENTS.md`,
   checks the register of the configured texts and runs its audit.

`make verify` runs steps 1 to 5 and needs no engine.

`make lock` regenerates `requirements/dev.txt` from `requirements/dev.in`
and `requirements/semgrep.txt` from `requirements/semgrep.in`. A release
younger than three days is not eligible. semgrep has its own environment
under `.venv/semgrep`, so its dependencies never pin those of the tests.

Every public function has positive, negative and boundary tests. A test that
guards a guarantee was seen failing once with the guarantee removed from the
code.

## Reproducible package

`make package` gives the same bytes for the same commit on every host. Four
things make it so:

- **Date.** `SOURCE_DATE_EPOCH` is the time of the last commit
  ([Makefile](https://github.com/plurano/cistula/blob/main/Makefile)); the wall clock is never used.
- **Source distribution.** The build backend records the build time, the
  owner and the mode of each file. [tools/reproducible_sdist.py](https://github.com/plurano/cistula/blob/main/tools/reproducible_sdist.py)
  writes the archive again: members sorted by name, owned by nobody, mode 644
  or 755, dated by the commit.
- **Wheel.** It is built from the rewritten source distribution under the
  file mode mask 022.
- **No compression.** Both files store their members uncompressed
  ([setup.cfg](https://github.com/plurano/cistula/blob/main/setup.cfg) for the wheel). zlib and zlib-ng give different
  bytes for the same input, and interpreters are built with either. The price
  is size: about 348 kB and 73 kB instead of 71 kB and 25 kB.
  [tools/check_package_size.py](https://github.com/plurano/cistula/blob/main/tools/check_package_size.py) measures the
  four sizes in the gate (`make verify-package-size`) and fails when a figure
  in this sentence is off by more than a tenth.

`make verify-reproducible` is the check, and part of the gate: each
interpreter builds the package again under another file mode mask, and
[tools/compare_packages.py](https://github.com/plurano/cistula/blob/main/tools/compare_packages.py) fails on one differing
byte, naming the archive member and the field.

Two limits:

- The date comes from the commit, so the hash means something for a clean
  tree only.
- A pull request run in CI builds the forge's test merge commit, which has
  its own date. Its hashes are not those of the branch. The hashes of a build
  on the main branch or on a tag are the ones to compare and to pin.

## Supported Python versions

The classifiers in [pyproject.toml](https://github.com/plurano/cistula/blob/main/pyproject.toml) are the list; the oldest
version is the floor. Every version on it is tested: `PYTHONS` in the
[Makefile](https://github.com/plurano/cistula/blob/main/Makefile) and the interpreter list in
[ci.yml](https://github.com/plurano/cistula/blob/main/.github/workflows/ci.yml) name the same versions, and
`requires-python`, the lint target in [ruff.toml](https://github.com/plurano/cistula/blob/main/ruff.toml), `python_version`
of the type check and the package lock are set to the floor. The documentation
lock is compiled for the interpreter
[pages.yml](https://github.com/plurano/cistula/blob/main/.github/workflows/pages.yml) sets up, since the site
builds there.
[tools/check_python_versions.py](https://github.com/plurano/cistula/blob/main/tools/check_python_versions.py) fails the
gate when one of these places disagrees, so adding or dropping a version means
changing all of them in one commit, and then `make lock`.

## Exceptions

Every check binds every file. A file that is left out of a check has an entry
in [exceptions.toml](https://github.com/plurano/cistula/blob/main/exceptions.toml): one file, one rule, a reason and an
expiry at most 90 days ahead. The gate fails when an entry has expired, when
its file is gone or passes the check again, and when `ruff.toml` leaves a file
out of the lint without an entry. The three entries today are scripts the
standards engine writes; they end when the engine ships them clean.

Codacy skips the files [.codacy.yml](https://github.com/plurano/cistula/blob/main/.codacy.yml) names: the four agent
context files and the engine's hook script `common.py`, all written by the
standards engine. `make verify-codacy` admits only files that a check here
holds to the engine (`praetorctl compile-context --verify` for the context
files, `make verify-hook-scripts` in CI for the script), and it fails from the
day after the end date the file names, ten days after the entry.
