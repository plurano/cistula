# Plurano Python library (working name)

<!-- praetor:readme-governance:start -->
[![HISS Adopted][praetor-hiss-badge]][praetor-hiss-agents]

Praetor manages this repository's declared governance policy. This managed
block records adoption state; it is not a verification certificate.

**Verification**: `make verify-all` runs the repository's configured
verification cascade.

**HISS Audit**: `praetorctl audit` enforces policy, generated-surface
integrity, and the debt ratchet.

**Context Sync**: `praetorctl compile-context --verify` verifies every
generated agent context against `AGENTS.md`.

**Debt Baseline**: `.standards-baseline.json` anchors the debt ratchet at
0 recorded infractions; audit forbids growth.

[praetor-hiss-badge]: https://img.shields.io/badge/Standards-HISS%20Adopted-blue
[praetor-hiss-agents]: https://github.com/plurano/python-library/blob/HEAD/AGENTS.md
<!-- praetor:readme-governance:end -->

Shared Python helpers of the Plurano framework family: one tested
implementation of the helpers that every repository used to repeat.

## Name

`python-library` is a working name, and so are the distribution name
`plurano-python-library` and the import name `plurano_python_library`. The real
name is chosen before the first release; both names change with it.

## Status

Private; nothing is released yet. The version is `0.1.0.dev0`.

## What the first release contains

Three modules. They use the standard library only, and no function contacts a
network.

| Module | What it does |
| --- | --- |
| `process` | `run` starts a command under a time limit the caller must state. `child_env` builds the environment for such a child. |
| `files` | `read_json` and `loads_json` refuse a repeated key. `write_bytes_atomic`, `write_text_atomic` and `write_json` replace a file in one step. |
| `digest` | `sha256_file`, `sha256_bytes` and `sha256_text`. |

```python
from plurano_python_library import read_json, run, sha256_file, write_json

result = run(["git", "rev-parse", "HEAD"], timeout=30, check=True)
record = read_json("record.json")
record["commit"] = result.stdout.strip()
record["digest"] = sha256_file("artifact.bin")
write_json("record.json", record)
```

### Guarantees

- **Time limit.** `run` has no default time limit and accepts no value that
  means "for ever": zero, a negative number, infinity and NaN are refused.
- **Group kill.** The child runs in a process group of its own. When the limit
  expires the whole group gets `SIGTERM` and, after `kill_after` seconds,
  `SIGKILL`. A child that ignores the first signal, or a descendant that holds
  the output open, cannot keep the caller waiting.
- **Atomic write.** New content goes to a temporary file in the same directory,
  is flushed to disk and then replaces the target in one step. A failure at any
  point before the replace leaves the old file whole and no temporary file
  behind.
- **Strict JSON.** A repeated key, a byte-order mark and invalid UTF-8 are
  errors.

### Limits

- POSIX only. Process groups do not exist on Windows; `run` raises
  `NotImplementedError` there instead of running without the guarantee.
- A descendant that moves itself into another session leaves the group and
  outlives the kill. `run` still returns within the limit plus the grace
  period.
- When the whole process is killed between write and replace, the old file is
  whole, but the temporary file `.<name>.<16 hex digits>.tmp` stays in the
  directory.
- Python 3.12 or newer.

## Installing

While the repository is private there is no package index entry. A consumer
installs the built wheel from a local path and pins its hash:

```sh
make package          # prints the SHA-256 of dist/*.whl
pip install --require-hashes -r requirements.txt
```

with this line in the consumer's `requirements.txt`:

```text
plurano-python-library @ file:///path/to/dist/plurano_python_library-0.1.0.dev0-py3-none-any.whl --hash=sha256:<hash printed by make package>
```

The bytes of the wheel and of the source distribution depend on the commit
only: every host and every supported interpreter builds the same two files
from the same commit. The hash to pin is the one `make package` prints for
that commit, and a second build of it must print the same
(see [Reproducible package](#reproducible-package)).

## Development

```sh
make verify-all
```

This is the whole gate, and CI runs the same target. It needs `python3.12`,
`python3.13` and `python3.14` on `PATH` (or `PYTHONS="..."` with full paths),
`reuse` 6.2.0 and `praetorctl`, the command of the standards engine. An
interpreter or a tool that is missing fails the gate.

1. One environment per interpreter is installed from `requirements/dev.txt`.
   Every package is pinned by hash; nothing else is installed.
2. Format, lint, types, licence headers and the exception list are checked
   once.
3. One source distribution and one wheel are built.
4. Every interpreter builds both once more. One differing byte fails.
5. The wheel of step 3 is installed into each environment, and the tests run
   against the installed package with 100 % branch coverage required.
6. The standards engine checks that the agent contexts equal `AGENTS.md`,
   checks the register of the configured texts and runs its audit.

`make verify` runs steps 1 to 5 and needs no engine.

`make lock` regenerates `requirements/dev.txt` from `requirements/dev.in`. A
release younger than three days is not eligible.

Every public function has positive, negative and boundary tests. A test that
guards a guarantee was seen failing once with the guarantee removed from the
code.

### Reproducible package

`make package` gives the same bytes for the same commit on every host. Four
things make it so:

- **Date.** `SOURCE_DATE_EPOCH` is the time of the last commit
  ([Makefile](Makefile)); the wall clock is never used.
- **Source distribution.** The build backend records the build time, the
  owner and the mode of each file. [tools/reproducible_sdist.py](tools/reproducible_sdist.py)
  writes the archive again: members sorted by name, owned by nobody, mode 644
  or 755, dated by the commit.
- **Wheel.** It is built from the rewritten source distribution under the
  file mode mask 022.
- **No compression.** Both files store their members uncompressed
  ([setup.cfg](setup.cfg) for the wheel). zlib and zlib-ng give different
  bytes for the same input, and interpreters are built with either. The price
  is size: about 174 kB and 46 kB instead of 36 kB and 19 kB.

`make verify-reproducible` is the check, and part of the gate: each
interpreter builds the package again under another file mode mask, and
[tools/compare_packages.py](tools/compare_packages.py) fails on one differing
byte, naming the archive member and the field.

Two limits:

- The date comes from the commit, so the hash means something for a clean
  tree only.
- A pull request run in CI builds the forge's test merge commit, which has
  its own date. Its hashes are not those of the branch. The hashes of a build
  on the main branch or on a tag are the ones to compare and to pin.

### Exceptions

Every check binds every file. A file that is left out of a check has an entry
in [exceptions.toml](exceptions.toml): one file, one rule, a reason and an
expiry at most 90 days ahead. The gate fails when an entry has expired, when
its file is gone or passes the check again, and when `ruff.toml` leaves a file
out of the lint without an entry. The three entries today are scripts the
standards engine writes; they end when the engine ships them clean.

## Licence

EUPL-1.2, see [LICENSE](LICENSE).
