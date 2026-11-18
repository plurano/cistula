<!--
SPDX-FileCopyrightText: 2026 Lusoris <lusoris@proton.me>
SPDX-License-Identifier: EUPL-1.2
-->

# Limits

- Tested on Linux only: CI runs Ubuntu 24.04
  ([ci.yml](https://github.com/plurano/cistula/blob/main/.github/workflows/ci.yml)). The code uses POSIX interfaces only,
  but no test runs on macOS or a BSD, and the guarantees can differ there. On
  macOS, for one, `fsync` hands the data to the drive without asking the drive
  to store it; that takes `F_FULLFSYNC`
  ([fsync(2)](https://developer.apple.com/library/archive/documentation/System/Conceptual/ManPages_iPhoneOS/man2/fsync.2.html)),
  which the writers do not call. Process groups do not exist on Windows; `run`
  raises `NotImplementedError` there instead of running without the guarantee.
- A descendant that moves itself into another session leaves the group and
  outlives the kill. `run` still returns within `timeout + kill_after` plus
  one second for reading the remaining output.
- When a timed-out child exits on `SIGTERM` and no group member holds its
  output pipes open, `run` returns at once without sending `SIGKILL`; a group
  member that ignores `SIGTERM` and writes its output elsewhere keeps running
  ([issue 82](https://github.com/plurano/cistula/issues/82)). `run` still
  returns within the same bound.
- When the whole process is killed between write and replace, or inside a
  block, the old file is whole, but the temporary file
  `.<name>.<16 hex digits>.tmp` stays in the directory. The temporary file of
  `staged_path` has the suffix of the target after `.tmp`.
- `staged_path` cannot refuse a file that is neither empty nor complete: a
  producer that wrote a part and still holds the rest gets that part
  published
  (`test_staged_path_cannot_see_what_a_producer_still_holds_in_a_file_that_is_not_empty`).
- Python 3.11 or newer.
