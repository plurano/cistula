# SPDX-FileCopyrightText: 2026 Lusoris <lusoris@proton.me>
# SPDX-License-Identifier: EUPL-1.2
"""Measure what a durable write costs on the file system of a directory.

Usage: measure_durable_write.py DIRECTORY [COUNT]

Writes COUNT small files (default 200) into a new directory inside DIRECTORY
four ways and prints one tab-separated line per way: its name, the median
time per file in microseconds, and the file system type when the host names
it. The new directory is removed afterwards.

- `durable`: `write_text_atomic` with the default `durable=True`.
- `not-durable`: `write_text_atomic(..., durable=False)`.
- `group-fsync`: every file written with `durable=False`, then each file and
  the directory flushed once after all renames; the time is the whole group
  divided by COUNT.
- `group-syncfs`: every file written with `durable=False`, then one `syncfs`
  of the file system; Linux only, the line says why it is skipped elsewhere.

The two group ways are measurements for a design question (issue 7), not
functions of the library.
"""

from __future__ import annotations

import ctypes
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from typing import TYPE_CHECKING

from cistula.files import write_text_atomic

if TYPE_CHECKING:
    from collections.abc import Callable

USAGE = "usage: measure_durable_write.py DIRECTORY [COUNT]"
DEFAULT_COUNT = 200
# A bound for COUNT; at about 35 ms per durable write the run stays near a minute.
MAX_COUNT = 2000
# Seconds `stat` may take to name the file system.
STAT_TIMEOUT = 10
TEXT = "a small record of about one line\n"


def _paths(directory: str, count: int) -> list[str]:
    return [os.path.join(directory, f"f{index}.txt") for index in range(count)]


def _per_call(directory: str, count: int, *, durable: bool) -> float:
    times = []
    for path in _paths(directory, count):
        start = time.perf_counter_ns()
        write_text_atomic(path, TEXT, durable=durable)
        times.append(time.perf_counter_ns() - start)
    return statistics.median(times) / 1000


def _flush(path: str) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _group_fsync(directory: str, count: int) -> float:
    paths = _paths(directory, count)
    start = time.perf_counter_ns()
    for path in paths:
        write_text_atomic(path, TEXT, durable=False)
    for path in paths:
        _flush(path)
    _flush(directory)
    return (time.perf_counter_ns() - start) / count / 1000


def _group_syncfs(directory: str, count: int) -> float:
    libc = ctypes.CDLL(None, use_errno=True)
    start = time.perf_counter_ns()
    for path in _paths(directory, count):
        write_text_atomic(path, TEXT, durable=False)
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        if libc.syncfs(descriptor) != 0:
            raise OSError(ctypes.get_errno(), "syncfs failed")
    finally:
        os.close(descriptor)
    return (time.perf_counter_ns() - start) / count / 1000


def _file_system(directory: str) -> str:
    try:
        result = subprocess.run(  # noqa: S603 - fixed program, path as one argument
            ["stat", "-f", "-c", "%T", directory],  # noqa: S607 - stat from PATH
            capture_output=True,
            text=True,
            check=True,
            timeout=STAT_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return result.stdout.strip() or "unknown"


def measure(directory: str, count: int) -> list[str]:
    """Return the four result lines for COUNT files written inside DIRECTORY."""
    if not 1 <= count <= MAX_COUNT:
        message = f"COUNT must be between 1 and {MAX_COUNT}, got {count}"
        raise ValueError(message)
    kind = _file_system(directory)
    lines: list[str] = []
    ways: list[tuple[str, Callable[[str], float]]] = [
        ("durable", lambda d: _per_call(d, count, durable=True)),
        ("not-durable", lambda d: _per_call(d, count, durable=False)),
        ("group-fsync", lambda d: _group_fsync(d, count)),
    ]
    if sys.platform == "linux":
        ways.append(("group-syncfs", lambda d: _group_syncfs(d, count)))
    else:
        lines.append(f"group-syncfs\tskipped\t{kind}\tsyncfs is Linux only")
    for name, way in ways:
        work = tempfile.mkdtemp(prefix="cistula-measure-", dir=directory)
        try:
            lines.append(f"{name}\t{way(work):.1f}\t{kind}")
        finally:
            shutil.rmtree(work)
    return lines


def main(arguments: list[str]) -> int:
    """Print the measurement for the directory in ARGUMENTS; 2 on a usage error."""
    if not 1 <= len(arguments) <= 2 or not os.path.isdir(arguments[0]):  # noqa: PLR2004 - DIRECTORY and COUNT
        sys.stderr.write(f"{USAGE}\n")
        return 2
    try:
        count = int(arguments[1]) if len(arguments) == 2 else DEFAULT_COUNT  # noqa: PLR2004 - COUNT given
        lines = measure(arguments[0], count)
    except ValueError as error:
        sys.stderr.write(f"{USAGE}: {error}\n")
        return 2
    sys.stdout.write("".join(f"{line}\n" for line in lines))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
