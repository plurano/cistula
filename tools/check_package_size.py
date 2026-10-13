# SPDX-FileCopyrightText: 2026 Lusoris <lusoris@proton.me>
# SPDX-License-Identifier: EUPL-1.2
"""Fail when the package size the README states is out of date.

Usage: check_package_size.py README DIRECTORY

The README states the size of the two built files in one sentence of the form
"about 277 kB and 64 kB instead of 57 kB and 24 kB": the source distribution
and the wheel as built, with their members stored uncompressed, and the size
each would have compressed. DIRECTORY holds one build. Each figure may be off
by a tenth of the measured size; one that is off by more fails, so the
sentence is updated when the package grows or shrinks.

The compressed sizes are computed here: the archive of the source
distribution compressed with gzip at level 9, and the wheel written again with
every member deflated. Two compression libraries give slightly different
sizes; the tolerance covers that.
"""

from __future__ import annotations

import copy
import gzip
import io
import os
import re
import sys
import zipfile
import zlib

SENTENCE = re.compile(
    r"about\s+(\d+)\s+kB\s+and\s+(\d+)\s+kB\s+instead\s+of\s+(\d+)\s+kB\s+and\s+(\d+)\s+kB",
)
KEYS = ("source distribution", "wheel", "source distribution compressed", "wheel compressed")
KB = 1000
USAGE = "usage: check_package_size.py README DIRECTORY"


def stated(text: str) -> dict[str, int]:
    """Return the four sizes the sentence states, in bytes; exactly one sentence must be there."""
    matches = SENTENCE.findall(text)
    if len(matches) != 1:
        message = f"expected one sentence 'about N kB and N kB instead of N kB and N kB', found {len(matches)}"
        raise ValueError(message)
    return {key: int(figure) * KB for key, figure in zip(KEYS, matches[0], strict=True)}


def _one(directory: str, suffix: str) -> str:
    names = sorted(name for name in os.listdir(directory) if name.endswith(suffix))
    if len(names) != 1:
        message = f"expected one *{suffix} in {directory}, found {len(names)}"
        raise ValueError(message)
    return os.path.join(directory, names[0])


def _deflated_size(wheel: str) -> int:
    """Return the size of the wheel written again with every member deflated."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(wheel) as source, zipfile.ZipFile(buffer, "w") as target:
        for info in source.infolist():
            deflated = copy.copy(info)
            deflated.compress_type = zipfile.ZIP_DEFLATED
            target.writestr(deflated, source.read(info))
    return len(buffer.getvalue())


def measure(directory: str) -> dict[str, int]:
    """Return the four sizes of the build in DIRECTORY, in bytes."""
    sdist = _one(directory, ".tar.gz")
    wheel = _one(directory, ".whl")
    with gzip.open(sdist) as file:
        archive = file.read()
    return {
        "source distribution": os.path.getsize(sdist),
        "wheel": os.path.getsize(wheel),
        "source distribution compressed": len(gzip.compress(archive, compresslevel=9, mtime=0)),
        "wheel compressed": _deflated_size(wheel),
    }


def _kb(size: int) -> int:
    """Return `size` in whole kilobytes, rounded half up."""
    return (size + KB // 2) // KB


def compare(stated: dict[str, int], measured: dict[str, int]) -> list[str]:
    """Return one line per figure that is off by more than a tenth of the measured size."""
    return [
        f"{key}: README states {stated[key] // KB} kB, measured {measured[key]} bytes ({_kb(measured[key])} kB)"
        for key in KEYS
        if abs(stated[key] - measured[key]) * 10 > measured[key]
    ]


def main(arguments: list[str]) -> int:
    """Check the README and the build directory named on the command line."""
    if len(arguments) != 2:  # noqa: PLR2004 - the README and the build
        sys.stderr.write(f"{USAGE}\n")
        return 2
    readme, directory = arguments
    with open(readme, encoding="utf-8") as file:
        text = file.read()
    try:
        figures = stated(text)
        sizes = measure(directory)
    except ValueError as error:
        sys.stderr.write(f"FAIL: {error}\n")
        return 1
    except (OSError, EOFError, zipfile.BadZipFile, zlib.error) as error:
        sys.stderr.write(f"could not measure the build in {directory}: {error}\n")
        return 2
    problems = compare(figures, sizes)
    if problems:
        sys.stderr.write(f"FAIL: the package size in {readme} is out of date:\n")
        sys.stderr.write("".join(f"  {problem}\n" for problem in problems))
        sys.stderr.write("Update the sentence to the measured sizes.\n")
        return 1
    sys.stdout.write("".join(f"{key}: {sizes[key]} bytes, README {figures[key] // KB} kB\n" for key in KEYS))
    sys.stdout.write(f"PASS: the package size in {readme} is within a tenth of the measured size.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
