# ADR-0002: Grouped flush: one flush pass for a group of durable writes

- **Status**: Proposed
- **Date**: 2026-10-17
- **Authors**: lusoris

## Context

The writers in `cistula.files` are durable by default. Each call flushes the
temporary file before the rename and the directory after it
([src/cistula/files.py](../../src/cistula/files.py)). A caller that writes
many files pays for both flushes once per file. The only cheaper call today
is `durable=False`, which gives up crash safety completely.

Measured with [tools/measure_durable_write.py](../../tools/measure_durable_write.py)
on 2026-10-17 (Linux 7.2, Python 3.11, NVMe disks). Each figure is the time
per file for 200 small files, from three runs:

| Way | btrfs | ZFS, `sync=standard` | tmpfs |
| --- | --- | --- | --- |
| `durable` (default, two flushes per file) | 9.9 to 10.1 ms | 2.9 to 3.0 ms | 11 to 13 µs |
| `not-durable` (no flush) | 15 µs | 22 to 28 µs | 10 to 11 µs |
| `group-fsync` (all renames, then one flush per file and one of the directory) | 5.0 ms | 34 to 46 µs | 9 to 13 µs |
| `group-syncfs` (all renames, then one `syncfs`) | 0.15 to 1.6 ms | 34 to 55 µs | 10 to 11 µs |

The btrfs volume was full (about 600 MB free of 1.9 TB) during the
measurement, which can raise the cost of a flush there. Earlier runs on
another btrfs volume measured 18 to 36 ms for the default call (issue 7).

Three readings follow from the table:

1. **Grouping pays on ZFS**: one flush per file after all renames costs
   about 1.5 % of the default, because ZFS commits the pending writes of
   many files in one transaction group.
2. **Grouping by file flush pays little on btrfs**: each `fsync` still
   commits the btrfs log, so the gain is one of the two flushes per file
   (half the cost). Only `syncfs` removes most of it, and its time varies
   by a factor of ten between runs.
3. **On tmpfs nothing changes**: there is no disk to flush to.

## Decision

Proposed, not decided. The question is whether `cistula.files` offers a
group whose writes share one flush pass, for example a context manager:

```python
with durable_group(directory) as group:
    for name, record in records.items():
        group.write_json(name, record)
# every file and the directory are flushed when the block ends
```

The points a design has to settle before anything is built:

- **What a caller can rely on after a crash.** Each file is still replaced
  in one step for every reader. After a power failure, which files of the
  group hold their new content is no longer one answer per call: any subset
  of the group can. The guarantee would be "all of them once the block has
  ended without an error", nothing before that.
- **`fsync` per file or `syncfs`.** `syncfs` is Linux only, and the library
  claims Linux and POSIX calls. It also flushes the writes of every other
  process on that file system, so its cost depends on them, which the
  btrfs spread above shows. The per-file flush after all renames is POSIX and
  costs only the group's own writes.
- **Which directories.** A group writing into several directories has to
  flush each of them once.
- **Errors.** A failed flush at the end of the block has to name the files
  whose durability it cannot promise.

Recommended for a later decision: the per-file flush after all renames
(`group-fsync`), because it is POSIX, flushes only the group's own files, and
gives the large gain where it was measured (ZFS). It is not built until a
consumer measures a loop where the default cost matters and `durable=False`
is not acceptable.

## Consequences

- **Positive**: A batch of files that must survive a crash would cost about
  the time of the writes on ZFS, and half the default on btrfs.
- **Negative**: A second guarantee to document and test next to the
  per-call one, with a weaker answer for a crash inside the block. The
  measurement covers one host; another disk or kernel can change the
  figures, so the tool is in the repository to run again.
