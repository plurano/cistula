# SPDX-FileCopyrightText: 2026 Lusoris <lusoris@proton.me>
# SPDX-License-Identifier: EUPL-1.2
"""Fail when the durable-write figures the README states disagree with a measurement.

Usage: check_durable_figures.py README MEASUREMENT

README holds one table under "Durable by default" with a row per file system:
its name, what a default (durable) write of one small file costs, and what one
with `durable=False` costs, each as "N ms", "N µs" or "N to M µs", optionally
with earlier runs in parentheses. MEASUREMENT is the output of
`tools/measure_durable_write.py` for one directory.

Three rules, each a failure when broken:

- Every row of a file system on a disk states the default at least RATIO
  times the cost without the flushes, as the sentence above the table says
  ("milliseconds where the write alone takes microseconds").
- When the table has a row for the file system that was measured, both
  measured figures lie within a factor of TOLERANCE of the figures the row
  states. Hosts differ, so the tolerance is wide; a figure that is off by
  more is out of date.
- On a file system on a disk, the measured default costs at least FLUSHED
  times the write without the flushes. A default that costs about as much as
  the write alone did not reach the disk. The bound is lower than RATIO: a
  virtual disk with a write cache, as on the CI runner, flushes in hundreds
  of microseconds (433 µs against 48 µs, ext4, 2026-10-17).
"""

from __future__ import annotations

import re
import sys

USAGE = "usage: check_durable_figures.py README MEASUREMENT"
# How many times a write without the flushes a disk row of the table states the default at least.
RATIO = 20
# How many times a write without the flushes a measured default costs at least on a disk.
FLUSHED = 4
# How far a measured figure may lie from the stated ones, as a factor either way.
TOLERANCE = 4
# File systems held in memory; a flush there costs nothing, so the ratio does not hold.
MEMORY = frozenset({"tmpfs", "ramfs"})
HEADER = re.compile(r"^\s*\| File system \| Default \(`durable=True`\) \| `durable=False` \|\s*$")
ROW = re.compile(r"^\s*\|([^|]+)\|([^|]+)\|([^|]+)\|\s*$")
FIGURE = re.compile(r"(\d+(?:\.\d+)?)(?:\s+to\s+(\d+(?:\.\d+)?))?\s*(ms|µs)")
UNIT = {"ms": 1000.0, "µs": 1.0}

Span = tuple[float, float]


def span(cell: str) -> Span:
    """Return the lowest and the highest figure a table cell states, in microseconds."""
    values: list[float] = []
    for low, high, unit in FIGURE.findall(cell):
        values.extend(float(figure) * UNIT[unit] for figure in (low, high) if figure)
    if not values:
        message = f"no figure in the table cell {cell.strip()!r}"
        raise ValueError(message)
    return min(values), max(values)


def stated(text: str) -> dict[str, tuple[Span, Span]]:
    """Return per file system the spans of the default and of `durable=False`; one table must be there."""
    lines = text.splitlines()
    starts = [index for index, line in enumerate(lines) if HEADER.match(line)]
    if len(starts) != 1:
        message = f"expected one table of durable-write figures, found {len(starts)}"
        raise ValueError(message)
    rows: dict[str, tuple[Span, Span]] = {}
    for line in lines[starts[0] + 2 :]:
        match = ROW.match(line)
        if match is None:
            break
        name, durable, plain = match.groups()
        rows[name.split()[0].strip(",").lower()] = (span(durable), span(plain))
    if not rows:
        message = "the table of durable-write figures has no row"
        raise ValueError(message)
    return rows


def measured(text: str) -> tuple[str, float, float]:
    """Return the file system and the medians of the default and of `durable=False` from MEASUREMENT."""
    figures: dict[str, float] = {}
    kinds: set[str] = set()
    for line in text.splitlines():
        fields = line.split("\t")
        if len(fields) >= 3 and fields[0] in ("durable", "not-durable"):  # noqa: PLR2004 - way, median, file system
            figures[fields[0]] = float(fields[1])
            kinds.add(fields[2])
    if set(figures) != {"durable", "not-durable"} or len(kinds) != 1:
        message = "the measurement has no 'durable' and 'not-durable' line for one file system"
        raise ValueError(message)
    return kinds.pop(), figures["durable"], figures["not-durable"]


def _within(value: float, figures: Span) -> bool:
    return figures[0] / TOLERANCE <= value <= figures[1] * TOLERANCE


def _show(figures: Span) -> str:
    return f"{figures[0]:g} µs" if figures[0] == figures[1] else f"{figures[0]:g} to {figures[1]:g} µs"


def compare(rows: dict[str, tuple[Span, Span]], kind: str, durable: float, plain: float) -> list[str]:
    """Return one line per broken rule; empty when the table and the measurement agree."""
    problems = [
        f"{name}: the default {_show(row[0])} is less than {RATIO} times {_show(row[1])} without the flushes"
        for name, row in rows.items()
        if name not in MEMORY and row[0][0] < RATIO * row[1][1]
    ]
    row = rows.get(kind)
    if row is not None:
        problems.extend(
            f"{kind}: measured {value:g} µs for {way}, the README states {_show(figures)}"
            for way, value, figures in (("the default", durable, row[0]), ("durable=False", plain, row[1]))
            if not _within(value, figures)
        )
    if kind not in MEMORY and durable < FLUSHED * plain:
        problems.append(
            f"{kind}: measured the default at {durable:g} µs,"
            f" less than {FLUSHED} times {plain:g} µs without the flushes"
        )
    return problems


def main(arguments: list[str]) -> int:
    """Check the README against the measurement named on the command line."""
    if len(arguments) != 2:  # noqa: PLR2004 - the README and the measurement
        sys.stderr.write(f"{USAGE}\n")
        return 2
    readme, measurement = arguments
    try:
        with open(readme, encoding="utf-8") as file:
            rows = stated(file.read())
        with open(measurement, encoding="utf-8") as file:
            kind, durable, plain = measured(file.read())
    except OSError as error:
        sys.stderr.write(f"{USAGE}: {error}\n")
        return 2
    except ValueError as error:
        sys.stderr.write(f"FAIL: {error}\n")
        return 1
    problems = compare(rows, kind, durable, plain)
    if problems:
        sys.stderr.write(f"FAIL: the durable-write figures in {readme} disagree with the measurement:\n")
        sys.stderr.write("".join(f"  {problem}\n" for problem in problems))
        return 1
    row = "its row" if kind in rows else "no row of the table"
    sys.stdout.write(f"{kind}: default {durable:g} µs, durable=False {plain:g} µs; {row}\n")
    sys.stdout.write(f"PASS: the durable-write figures in {readme} agree with the measurement.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
