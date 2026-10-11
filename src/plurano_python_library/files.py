# SPDX-FileCopyrightText: 2026 Lusoris <lusoris@proton.me>
# SPDX-License-Identifier: EUPL-1.2
"""Read JSON strictly and write files so that a reader never sees half of one.

The readers decode UTF-8 and refuse two things the standard library accepts
silently: an object that names the same key twice (it keeps the last value)
and a number JSON does not have (NaN, an infinity, a number too large for a
float).

The writers put the new content into a temporary file beside the target, flush
it to disk and rename it over the target in one step: after a failure or a
crash the target holds either the old content or the new, never a mixture. A
writer called with `durable=False` leaves out the flush and with it the promise
about a crash of the machine.
"""

from __future__ import annotations

import json
import math
import os
import secrets
import stat
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = [
    "DuplicateKeyError",
    "JsonValue",
    "loads_json",
    "read_json",
    "write_bytes_atomic",
    "write_json",
    "write_text_atomic",
]

type JsonValue = bool | int | float | str | list[JsonValue] | dict[str, JsonValue] | None

type _Path = str | os.PathLike[str]


class DuplicateKeyError(ValueError):
    """A JSON object names the same key more than once."""

    def __init__(self, key: str) -> None:
        """Record the repeated `key`."""
        super().__init__(f"duplicate key in JSON object: {key!r}")
        self.key = key


def _unique(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
    found: dict[str, JsonValue] = {}
    for key, value in pairs:
        if key in found:
            raise DuplicateKeyError(key)
        found[key] = value
    return found


def _refuse_constant(name: str) -> float:
    message = f"not a JSON number: {name}"
    raise ValueError(message)


def _finite(text: str) -> float:
    value = float(text)
    if math.isinf(value):
        message = f"number too large for a float: {text[:40]}"
        raise ValueError(message)
    return value


def loads_json(text: str, *, unique_keys: bool = True, finite_numbers: bool = True) -> JsonValue:
    """Parse a JSON document strictly.

    An object with a repeated key raises `DuplicateKeyError`;
    `unique_keys=False` restores the standard library's behaviour of keeping
    the last value.

    `NaN`, `Infinity` and `-Infinity`, which JSON does not have, and a number
    too large for a float raise `ValueError`. `finite_numbers=False` restores
    the standard library's behaviour: the three names are read as these
    numbers and a number too large is read as an infinity. `write_json`
    refuses to write any of them, so only a document read with the default can
    always be written back.

    Each keyword lifts its one check and nothing else. Everything else is
    `json.loads`: malformed text raises `json.JSONDecodeError`. All of these
    errors are `ValueError`.
    """
    if not isinstance(text, str):
        message = f"loads_json needs text, not {type(text).__name__}; read_json reads a file"
        raise TypeError(message)
    return cast(
        "JsonValue",
        json.loads(
            text,
            object_pairs_hook=_unique if unique_keys else None,
            parse_constant=_refuse_constant if finite_numbers else None,
            parse_float=_finite if finite_numbers else None,
        ),
    )


def read_json(path: _Path, *, unique_keys: bool = True, finite_numbers: bool = True) -> JsonValue:
    """Read a UTF-8 JSON file strictly; see `loads_json` for the two keywords.

    An object with a repeated key raises `DuplicateKeyError`; NaN, an
    infinity and a number too large for a float raise `ValueError`. Bytes that
    are not UTF-8 raise `UnicodeDecodeError`, malformed JSON (including a
    byte-order mark) raises `json.JSONDecodeError`. All of these are
    `ValueError`. A missing or unreadable file raises `OSError`.
    """
    with open(path, "rb") as handle:
        text = handle.read().decode("utf-8")
    return loads_json(text, unique_keys=unique_keys, finite_numbers=finite_numbers)


def _sync_directory(directory: str) -> None:
    """Flush the directory entry of a renamed file to disk."""
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _mode_to_keep(target: str) -> int | None:
    """Return the permissions the new file takes over, or `None` for the default.

    An existing regular file keeps its permissions. A new file gets what any
    newly created file would get (the process umask applies), and so does the
    regular file that replaces a symbolic link: the link's own mode says nothing.
    """
    try:
        found = os.lstat(target).st_mode
    except FileNotFoundError:
        return None
    return stat.S_IMODE(found) if stat.S_ISREG(found) else None


def _replace(path: _Path, write: Callable[[int], None], *, durable: bool) -> None:
    """Write through `write` into a temporary file and rename it over `path`."""
    if not isinstance(durable, bool):
        message = f"durable must be True or False, not {type(durable).__name__}"
        raise TypeError(message)
    target = os.fspath(path)
    if not isinstance(target, str):
        message = f"path must be text or a text path, not {type(target).__name__}"
        raise TypeError(message)
    directory, name = os.path.split(target)
    if not name:
        message = f"not a file path: {target!r}"
        raise ValueError(message)
    directory = directory or "."
    mode = _mode_to_keep(target)
    temporary = os.path.join(directory, f".{name}.{secrets.token_hex(8)}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o666)
    try:
        try:
            if mode is not None:
                os.fchmod(descriptor, mode)
            write(descriptor)
            if durable:
                os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, target)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
    if durable:
        _sync_directory(directory)


def write_bytes_atomic(path: _Path, data: bytes | bytearray | memoryview, *, durable: bool = True) -> None:
    """Replace the file at `path` with `data` in one step.

    The directory must exist; it is not created. When writing fails, `path`
    is unchanged and no temporary file stays behind. Only a crash of the whole
    process can leave a temporary file, named `.<name>.<random>.tmp`, beside
    the unchanged target.

    Permissions: an existing file keeps its permissions. A new file gets the
    permissions of any newly created file, so the process umask decides who
    can read it (mode 644 under umask 022); no file is made private for the
    caller. A symbolic link at `path` is replaced by a regular file with the
    permissions of a new file, its target is left alone.

    `durable=True` flushes the file to disk before the rename and the
    directory after it, so the new content survives a power failure or a crash
    of the operating system once the call has returned. `durable=False`
    leaves out both flushes and is much faster on a disk. It is not
    crash-safe: the rename is still one step for every reader and after a
    failed write, but after a power failure or a crash of the operating system
    the target can hold the old content, the new content, or an empty or
    incomplete file. Use it only for files that can be made again.
    """
    if not isinstance(data, (bytes, bytearray, memoryview)):
        message = f"write_bytes_atomic needs bytes, not {type(data).__name__}"
        raise TypeError(message)
    view = memoryview(data).cast("B")

    def write(descriptor: int) -> None:
        written = 0
        while written < len(view):
            written += os.write(descriptor, view[written:])

    _replace(path, write, durable=durable)


def write_text_atomic(path: _Path, text: str, *, durable: bool = True) -> None:
    """Replace the file at `path` with `text` as UTF-8 in one step.

    Line endings are written as given. See `write_bytes_atomic` for what
    "in one step" guarantees, for the permissions of the file and for
    `durable`.
    """
    if not isinstance(text, str):
        message = f"write_text_atomic needs text, not {type(text).__name__}"
        raise TypeError(message)
    write_bytes_atomic(path, text.encode("utf-8"), durable=durable)


def write_json(
    path: _Path, value: JsonValue, *, indent: int | None = 2, sort_keys: bool = True, durable: bool = True
) -> None:
    """Replace the file at `path` with `value` as UTF-8 JSON in one step.

    The document ends with a newline; non-ASCII text is written as itself, not
    escaped. A value JSON cannot represent is refused before anything is
    written: NaN and the two infinities raise `ValueError`, a value of a type
    JSON does not have raises `TypeError`. See `write_bytes_atomic` for what
    "in one step" guarantees, for the permissions of the file and for
    `durable`.
    """
    text = json.dumps(value, indent=indent, sort_keys=sort_keys, ensure_ascii=False, allow_nan=False)
    write_text_atomic(path, text + "\n", durable=durable)
