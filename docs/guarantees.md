<!--
SPDX-FileCopyrightText: 2026 Lusoris <lusoris@proton.me>
SPDX-License-Identifier: EUPL-1.2
-->

# Guarantees

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
  directory ([tests/test_files.py](https://github.com/plurano/cistula/blob/main/tests/test_files.py),
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

  `make verify` measures the file system of the checkout and fails when the
  table is out of date (`tools/check_durable_figures.py`): every disk row must
  state the default at least 20 times the cost without the flushes, a
  measured figure must lie within a factor of 4 of its row, and on a disk the
  measured default must cost at least 4 times the write alone. The last bound
  is lower because a virtual disk with a write cache flushes fast: ext4 on
  the CI runner measured 433 µs against 48 µs.

  A caller that writes many files it can make again (caches, per-item
  results) passes `durable=False`. That call is **not crash-safe**: after a
  power failure or a crash of the operating system the target can hold the
  old content, the new content, or an empty or incomplete file. The replace
  is still one step for every reader. A flush shared by a group of files is
  not offered; [ADR 0002](adr/0002-grouped-flush.md) proposes one.
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
  directories ([tests/test_files.py](https://github.com/plurano/cistula/blob/main/tests/test_files.py),
  `test_new_directories_take_their_permissions_from_the_umask`,
  `test_directories_created_stay_when_the_write_fails`).
- **Strict JSON.** A repeated key, a byte-order mark and invalid UTF-8 are
  errors when reading. So are NaN, the two infinities and a number too large
  for a float, none of which JSON has: `json.loads` reads them silently, the
  readers raise `ValueError`. `write_json` refuses the same values and leaves
  the old file whole. A caller that must read a file holding such a number
  passes `finite_numbers=False`; that lifts this one check and no other, as
  `unique_keys=False` does for a repeated key
  ([tests/test_files.py](https://github.com/plurano/cistula/blob/main/tests/test_files.py),
  `test_each_keyword_lifts_its_own_check_only`).
