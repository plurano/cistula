# Cistula

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
[praetor-hiss-agents]: https://github.com/plurano/cistula/blob/HEAD/AGENTS.md
<!-- praetor:readme-governance:end -->

Shared Python helpers of the Plurano framework family: one tested
implementation of the helpers that every repository used to repeat.

## Name

Cistula (pronounced KIS-tu-la) is Latin for a small chest: the case that held
a craftsman's or a scribe's small tools. The word is the diminutive of *cista*,
from Greek *kiste*. The repository, the distribution and the import package
all carry the name `cistula`.

## Status

Private; nothing is released yet. The version is `0.1.0.dev0`.

## What the first release contains

Three modules. They use the standard library only, and no function contacts a
network.

| Module | What it does |
| --- | --- |
| `process` | `run` starts a command under a time limit the caller must state. `child_env` builds the environment for such a child. |
| `files` | `read_json` and `loads_json` refuse a repeated key and a number JSON does not have. `write_bytes_atomic`, `write_text_atomic` and `write_json` replace a file in one step. `open_bytes_atomic`, `open_text_atomic` and `staged_path` do that for content written in a `with` block. Files that can be made again (caches, per-item results) take `durable=False`; everything else keeps the default, which costs milliseconds per file (see Guarantees). |
| `digest` | `sha256_file`, `sha256_bytes` and `sha256_text`. |

```python
from cistula import read_json, run, sha256_file, write_json

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
- **Writing in a block.** Content that is produced in steps, or by a producer
  that takes a file name, is written in a `with` block under the same
  guarantee: the target is replaced when the block ends, and a block that
  raises leaves the old file whole and no temporary file behind.

  ```python
  with open_text_atomic("rows.jsonl") as rows:
      for record in records:
          rows.write(json.dumps(record) + "\n")

  with staged_path("table.parquet") as temporary:
      frame.to_parquet(temporary)
  ```

  `open_bytes_atomic` and `open_text_atomic` lend an open file. Text is
  written as UTF-8 with the line endings given, and what is still buffered
  when the block ends is written before the rename. `staged_path` lends a
  name instead, for a library call or a child process that opens the file
  itself: an absolute path beside the target, of a file that does not exist
  yet, ending with the suffix of the target. A relative target is tied to the
  working directory at the start of the block, so the block may change the
  directory ([tests/test_files.py](tests/test_files.py),
  `test_text_still_buffered_when_the_block_ends_is_in_the_file_before_the_rename`,
  `test_an_error_in_the_block_leaves_the_old_file_whole_and_reaches_the_caller`,
  `test_a_block_may_change_the_current_directory`).
- **What `staged_path` checks.** The producer must have closed the file before
  the block ends. Content a producer still holds in memory is not in the
  file, and only the producer knows. Two signs of that mistake are refused,
  with the old file whole: no file under the name raises `FileNotFoundError`,
  an empty file raises `ValueError`. A caller for whom an empty file is a
  valid result passes `allow_empty=True`; a missing file is refused then too.
  A link, a pipe or a directory under the name is refused, and a directory is
  not removed. An existing target's permissions are applied when the block
  ends; a new file keeps the permissions the producer gave it
  (`test_staged_path_refuses_a_producer_that_wrote_no_file`,
  `test_staged_path_refuses_the_file_of_a_producer_that_has_not_written_yet`).
- **Durable by default.** The writers flush the file before the rename and the
  directory after it. On a disk a flush takes milliseconds where the write
  alone takes microseconds. Measured for one small file on NVMe disks
  (median of 200 calls, `PYTHONPATH=src python
  tools/measure_durable_write.py DIRECTORY`):

  | File system | Default (`durable=True`) | `durable=False` |
  | --- | --- | --- |
  | btrfs | 10 ms (18 to 36 ms in earlier runs) | 15 µs |
  | ZFS, `sync=standard` | 3 ms | 22 to 28 µs |
  | tmpfs (memory, no disk) | 11 to 13 µs | 10 to 11 µs |

  A caller that writes many files it can make again (caches, per-item
  results) passes `durable=False`. That call is **not crash-safe**: after a
  power failure or a crash of the operating system the target can hold the
  old content, the new content, or an empty or incomplete file. The replace
  is still one step for every reader. A flush shared by a group of files is
  not offered; [ADR 0002](docs/adr/0002-grouped-flush.md) proposes one.
- **Permissions.** An existing file keeps its permissions. A new file gets the
  permissions of any newly created file, so the process umask decides who can
  read it (mode 644 under umask 022). The writers make no file private; a
  caller that needs that sets the umask or the mode itself.
- **Directories.** A missing directory of the target raises
  `FileNotFoundError`. A caller that wants it created passes `parents=True`;
  missing directories above it are created too, as `os.makedirs` does. A new
  directory gets its permissions the way a new file does, from the umask (mode
  755 under umask 022); a directory that exists is left as it is. Directories
  created stay when the write then fails: the one step is the file, not its
  directories ([tests/test_files.py](tests/test_files.py),
  `test_new_directories_take_their_permissions_from_the_umask`,
  `test_directories_created_stay_when_the_write_fails`).
- **Strict JSON.** A repeated key, a byte-order mark and invalid UTF-8 are
  errors when reading. So are NaN, the two infinities and a number too large
  for a float, none of which JSON has: `json.loads` reads them silently, the
  readers raise `ValueError`. `write_json` refuses the same values and leaves
  the old file whole. A caller that must read a file holding such a number
  passes `finite_numbers=False`; that lifts this one check and no other, as
  `unique_keys=False` does for a repeated key
  ([tests/test_files.py](tests/test_files.py),
  `test_each_keyword_lifts_its_own_check_only`).

### Limits

- Tested on Linux only: CI runs Ubuntu 24.04
  ([ci.yml](.github/workflows/ci.yml)). The code uses POSIX interfaces only,
  but no test runs on macOS or a BSD, and the guarantees can differ there. On
  macOS, for one, `fsync` hands the data to the drive without asking the drive
  to store it; that takes `F_FULLFSYNC`
  ([fsync(2)](https://developer.apple.com/library/archive/documentation/System/Conceptual/ManPages_iPhoneOS/man2/fsync.2.html)),
  which the writers do not call. Process groups do not exist on Windows; `run`
  raises `NotImplementedError` there instead of running without the guarantee.
- A descendant that moves itself into another session leaves the group and
  outlives the kill. `run` still returns within the limit plus the grace
  period.
- When the whole process is killed between write and replace, or inside a
  block, the old file is whole, but the temporary file
  `.<name>.<16 hex digits>.tmp` stays in the directory. The temporary file of
  `staged_path` has the suffix of the target after `.tmp`.
- `staged_path` cannot refuse a file that is neither empty nor complete: a
  producer that wrote a part and still holds the rest gets that part
  published
  (`test_staged_path_cannot_see_what_a_producer_still_holds_in_a_file_that_is_not_empty`).
- Python 3.11 or newer.

## Installing

While the repository is private there is no package index entry. A consumer
installs the built wheel from a local path and pins its hash:

```sh
make package          # prints the SHA-256 of dist/*.whl
pip install --require-hashes -r requirements.txt
```

with this line in the consumer's `requirements.txt`:

```text
cistula @ file:///path/to/dist/cistula-0.1.0.dev0-py3-none-any.whl --hash=sha256:<hash printed by make package>
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

This is the whole gate, and CI runs the same target. It needs `python3.11`,
`python3.12`, `python3.13` and `python3.14` on `PATH` (or `PYTHONS="..."` with full paths),
`reuse` 6.2.0, `trivy` 0.76.0 (or `TRIVY=...`) and the standards engine. An
interpreter or a tool that is missing fails the gate.

The engine is the commit that `PRAETOR_REF` in
[ci.yml](.github/workflows/ci.yml) pins, for the gate and for the Git hooks
alike. [.config/lefthook/engine.sh](.config/lefthook/engine.sh) installs it
once per pin with `go install` and fails when it cannot; a `praetorctl` on
`PATH` is not used in its place. `make praetor-engine-path` prints the binary
in use, and the gate stops at its first step (`make verify-engine-pin`) when
no workflow declares the pin.

1. One environment per interpreter is installed from `requirements/dev.txt`.
   Every package is pinned by hash; nothing else is installed.
2. Format, lint, types, licence headers, the exception list and the list of
   supported Python versions are checked once. Then
   [tools/scanners.py](tools/scanners.py) runs semgrep with the rules in
   [.config/semgrep/hiss.yml](.config/semgrep/hiss.yml) and trivy on both
   locks. Each scanner first has to report the defects planted in a scratch
   folder; a scanner that reports nothing there fails the gate.
3. One source distribution and one wheel are built, and their sizes are
   checked against the figures under [Reproducible package](#reproducible-package).
4. Every interpreter builds both once more. One differing byte fails.
5. The wheel of step 3 is installed into each environment, and the tests run
   against the installed package with 100 % branch coverage required. Before
   them, [tools/check_consumer.py](tools/check_consumer.py) installs the wheel
   as [Installing](#installing) describes, into a new environment that holds
   nothing else, and [tests/consumer.py](tests/consumer.py) calls the public
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
  is size: about 307 kB and 66 kB instead of 63 kB and 25 kB.
  [tools/check_package_size.py](tools/check_package_size.py) measures the
  four sizes in the gate (`make verify-package-size`) and fails when a figure
  in this sentence is off by more than a tenth.

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

### Supported Python versions

The classifiers in [pyproject.toml](pyproject.toml) are the list; the oldest
version is the floor. Every version on it is tested: `PYTHONS` in the
[Makefile](Makefile) and the interpreter list in
[ci.yml](.github/workflows/ci.yml) name the same versions, and
`requires-python`, the lint target in [ruff.toml](ruff.toml), `python_version`
of the type check and the lock are set to the floor.
[tools/check_python_versions.py](tools/check_python_versions.py) fails the
gate when one of these places disagrees, so adding or dropping a version means
changing all of them in one commit, and then `make lock`.

### Exceptions

Every check binds every file. A file that is left out of a check has an entry
in [exceptions.toml](exceptions.toml): one file, one rule, a reason and an
expiry at most 90 days ahead. The gate fails when an entry has expired, when
its file is gone or passes the check again, and when `ruff.toml` leaves a file
out of the lint without an entry. The three entries today are scripts the
standards engine writes; they end when the engine ships them clean.

## Licence

EUPL-1.2, see [LICENSE](LICENSE).
