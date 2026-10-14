# SPDX-FileCopyrightText: 2026 Lusoris <lusoris@proton.me>
# SPDX-License-Identifier: EUPL-1.2
"""A consumer of the installed package, run by tools/check_consumer.py.

It runs on an environment that holds the wheel and nothing else, from an
empty directory, and calls the public interface the way a consumer does:
every exported name, a child process, the JSON reader and writer, the atomic
writers and the digests. Each check that does not hold ends the program with
exit status 1 and names what differed; the source tree is never imported.
"""

from __future__ import annotations

import os
import sys
import tempfile
from importlib import metadata
from typing import TYPE_CHECKING

import cistula
from cistula import (
    CommandFailedError,
    DuplicateKeyError,
    loads_json,
    open_text_atomic,
    read_json,
    run,
    sha256_file,
    sha256_text,
    staged_path,
    write_json,
    write_text_atomic,
)

if TYPE_CHECKING:
    from collections.abc import Callable


def same(what: str, actual: object, expected: object) -> None:
    """End the program unless `actual` equals `expected`."""
    if actual != expected:
        sys.exit(f"FAIL: {what}: expected {expected!r}, got {actual!r}")


def refused(
    what: str, error: type[Exception], call: Callable[..., object], *arguments: object, **keywords: object
) -> None:
    """End the program unless calling `call` raises `error`."""
    try:
        call(*arguments, **keywords)
    except error:
        return
    sys.exit(f"FAIL: {what}: {error.__name__} was not raised")


def installed() -> None:
    """The package comes from the new environment, alone, with its type marker and every name it lists."""
    same("package location", os.path.commonpath([cistula.__file__, sys.prefix]), sys.prefix)
    same("distributions installed", sorted(entry.metadata["Name"] for entry in metadata.distributions()), ["cistula"])
    marker = os.path.join(os.path.dirname(cistula.__file__), "py.typed")
    same("type marker", os.path.isfile(marker), True)  # noqa: FBT003 - the expected value
    same("names missing from the package", [name for name in cistula.__all__ if not hasattr(cistula, name)], [])


def child() -> None:
    """A child process runs, and a failing one raises when asked to."""
    result = run([sys.executable, "-c", "print('consumer')"], timeout=30, check=True)
    same("child output", result.stdout, "consumer\n")
    refused(
        "failing child", CommandFailedError, run, [sys.executable, "-c", "raise SystemExit(3)"], timeout=30, check=True
    )


def files(directory: str) -> None:
    """The JSON and the atomic writers write what they are given and leave no temporary file."""
    record = os.path.join(directory, "record.json")
    write_json(record, {"name": "Grüße", "values": [1, 2.5, None]})
    same("JSON read back", read_json(record), {"name": "Grüße", "values": [1, 2.5, None]})
    refused("duplicate key", DuplicateKeyError, loads_json, '{"a": 1, "a": 2}')
    text = os.path.join(directory, "text.txt")
    write_text_atomic(text, "consumer\n")
    same("digest of the written file", sha256_file(text), sha256_text("consumer\n"))
    rows = os.path.join(directory, "rows.jsonl")
    with open_text_atomic(rows) as handle:
        handle.write('{"row": 1}\n')
    staged = os.path.join(directory, "staged.bin")
    with staged_path(staged) as temporary, open(temporary, "wb") as handle:
        handle.write(b"staged")
    with open(rows, encoding="utf-8") as handle:
        same("rows", handle.read(), '{"row": 1}\n')
    with open(staged, "rb") as handle:
        same("staged file", handle.read(), b"staged")
    same("files left", sorted(os.listdir(directory)), ["record.json", "rows.jsonl", "staged.bin", "text.txt"])


def main() -> None:
    """Run every check; the first that does not hold ends the program."""
    installed()
    child()
    with tempfile.TemporaryDirectory() as directory:
        files(directory)
    sys.stdout.write(f"consumer: cistula from {os.path.dirname(cistula.__file__)}: every check held\n")


if __name__ == "__main__":
    main()
