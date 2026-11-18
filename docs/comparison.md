<!--
SPDX-FileCopyrightText: 2026 Lusoris <lusoris@proton.me>
SPDX-License-Identifier: EUPL-1.2
-->

# Compared with other libraries

Checked on 2026-10-17 against the newest release on PyPI and its source.

| Need | Libraries compared | What they do that cistula does not |
| --- | --- | --- |
| Atomic file writes | atomicwrites 1.4.1 (archived, last release 2022), safer 6.1.0, boltons 26.2.0 (`AtomicSaver`) | Code for Windows (atomicwrites, boltons); `F_FULLFSYNC` on macOS (atomicwrites) |
| Subprocess wrappers | sh 2.4.0, plumbum 2.0.2, sarge 0.1.8, invoke 3.0.3, anyio 4.15.1 (`run_process`) | Shell-style pipelines (sh, plumbum, sarge); an `async` interface (anyio) |
| JSON reading | standard library `json`, orjson 3.13.0, msgspec 0.22.0 | Speed (orjson, msgspec); none of the three refuses a repeated key by default |
| Digests | standard library `hashlib.file_digest`, dirhash 0.5.0, checksumdir 1.3.0 | Any algorithm by name (`hashlib`); one digest over a directory tree (dirhash, checksumdir) |

The comparison found these features missing from cistula. Each is left out for
the reason given:

- **Windows.** Every guarantee of `run` rests on a process group, which
  Windows does not have (see Limits). The writers are tested on Linux only. A
  Windows port is a release of its own, and it waits until a project that
  uses cistula runs on Windows.
- **`F_FULLFSYNC` on macOS.** No test runs on macOS, so a call there would
  ship unproven. Limits states the gap.
- **Pipelines and shell syntax.** `run` takes a list of arguments and never a
  shell, so no string is parsed into a command. A caller passes the output of
  one call as `input` of the next, or runs a script under one time limit.
- **An `async` interface.** anyio needs a dependency, and cistula uses the
  standard library only. `run` blocks, and a caller in an event loop can call
  it in a worker thread (`asyncio.to_thread`).
- **Faster JSON.** orjson and msgspec are compiled dependencies, and like the
  standard library they take the last of repeated keys instead of refusing
  them. `read_json` is meant for records and configuration, not for bulk data.
- **Other digest algorithms and tree digests.** The records cistula writes
  name SHA-256 only. `hashlib.file_digest` takes any algorithm for a caller
  that needs one. A tree digest needs rules for ordering, links and
  permissions that no consumer has asked for yet.

Where cistula goes further than the compared libraries:

- `run` requires a time limit and sends the whole process group `SIGTERM` when
  the limit passes, followed by `SIGKILL` while the child is still running or a
  group member still holds its output pipes open. `env` replaces the whole
  environment of the child, and without `env` the child inherits the caller's
  full environment; `child_env` builds an environment from an allow-list for
  the caller to pass as `env`. The compared wrappers make the time limit
  optional. sh, plumbum, sarge and invoke kill only the child on a timeout; sh
  kills the group only when the caller asks for it.
- The writers flush the directory as well as the file. Of the compared
  libraries, only atomicwrites flushes the directory, boltons flushes the file
  only, and safer flushes neither.
- `staged_path` refuses to publish an empty file unless the caller allows
  one; none of the compared writers checks the size of what it publishes.
- `read_json` refuses repeated keys and the non-finite numbers `NaN` and
  `Infinity`. The standard library accepts both; orjson and msgspec refuse the
  numbers but accept repeated keys.
