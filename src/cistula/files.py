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
about a crash of the machine. A writer called with `parents=True` first creates
the directory of the target where it is missing.

Three of the writers are blocks for content that is not one value in memory.
`open_bytes_atomic` and `open_text_atomic` lend an open file to write into
step by step; `staged_path` lends the name of the temporary file to a producer
that opens the file itself. All writers share one implementation of the
temporary file, the flush, the rename and the clean-up.
"""

from __future__ import annotations

import contextlib
import errno
import io
import json
import math
import os
import secrets
import stat
from typing import TYPE_CHECKING, NamedTuple, TypeAlias, cast

if TYPE_CHECKING:
    from collections.abc import Iterator

__all__ = [
    "DuplicateKeyError",
    "JsonValue",
    "loads_json",
    "open_bytes_atomic",
    "open_text_atomic",
    "read_json",
    "staged_path",
    "write_bytes_atomic",
    "write_json",
    "write_text_atomic",
]

# Not `type` statements: Python 3.11 does not have them.
JsonValue: TypeAlias = bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"] | None

_Path: TypeAlias = str | os.PathLike[str]


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
    """Flush the entries of `directory` to disk: a renamed file, a new directory."""
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


def _switch(name: str, value: object) -> None:
    """Refuse a value other than `True` or `False` for the keyword `name`."""
    if not isinstance(value, bool):
        message = f"{name} must be True or False, not {type(value).__name__}"
        raise TypeError(message)


def _create_directories(directory: str, *, durable: bool) -> None:
    """Create `directory` and every missing directory above it, the highest first.

    A new directory gets the permissions of any newly created directory. With
    `durable`, the directory above each new one is flushed to disk; `directory`
    itself is flushed by the caller, after the rename.
    """
    missing: list[str] = []
    current = directory
    # Every step up removes at least one separator, so this bound is never short.
    for _ in range(directory.count(os.sep) + 1):
        if os.path.lexists(current):
            break
        missing.append(current)
        current = os.path.dirname(current) or "."
    for new in reversed(missing):
        try:
            os.mkdir(new)
        except FileExistsError:
            # Another writer was faster, or the path names a directory a second way (`a/..`).
            if not os.path.isdir(new):
                raise
        if durable:
            _sync_directory(os.path.dirname(new) or ".")


class _Stage(NamedTuple):
    """One write: the target, its directory and the temporary file beside it."""

    target: str
    directory: str
    temporary: str
    mode: int | None
    durable: bool


def _stage(path: _Path, *, durable: bool, parents: bool, block: bool = False, suffix: bool = False) -> _Stage:
    """Check the arguments, create the directory if asked to and name the temporary file.

    `block` is for a write that runs code of the caller before it is done: a
    relative path is tied to the current directory now, so that this code may
    change the directory. `suffix` repeats the suffix of the target at the end
    of the temporary name, for a producer that reads the format from the name.
    """
    _switch("durable", durable)
    _switch("parents", parents)
    target = os.fspath(path)
    if not isinstance(target, str):
        message = f"path must be text or a text path, not {type(target).__name__}"
        raise TypeError(message)
    directory, name = os.path.split(target)
    if not name:
        message = f"not a file path: {target!r}"
        raise ValueError(message)
    directory = directory or "."
    if block and not os.path.isabs(directory):
        # Joined, not normalised: `a/..` keeps the meaning the file system gives it.
        directory = os.path.join(os.getcwd(), directory)
        target = os.path.join(directory, name)
    if parents:
        _create_directories(directory, durable=durable)
    mode = _mode_to_keep(target)
    ending = os.path.splitext(name)[1] if suffix else ""
    temporary = os.path.join(directory, f".{name}.{secrets.token_hex(8)}.tmp{ending}")
    return _Stage(target, directory, temporary, mode, durable)


def _remove(temporary: str) -> None:
    """Remove the temporary file after a failure; a directory a producer left there stays."""
    try:
        os.unlink(temporary)
    except FileNotFoundError:
        pass
    except OSError:
        # Which error a directory gives depends on the system; the caller gets the first error.
        if not os.path.isdir(temporary):
            raise


@contextlib.contextmanager
def _published(stage: _Stage) -> Iterator[None]:
    """Rename the temporary file over the target after the block; remove it when anything fails."""
    try:
        yield
        os.replace(stage.temporary, stage.target)
    except BaseException:
        _remove(stage.temporary)
        raise
    if stage.durable:
        _sync_directory(stage.directory)


@contextlib.contextmanager
def _temporary_file(stage: _Stage) -> Iterator[int]:
    """Create the temporary file and lend its descriptor; then flush, close and publish it."""
    descriptor = os.open(stage.temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o666)
    with _published(stage):
        try:
            if stage.mode is not None:
                os.fchmod(descriptor, stage.mode)
            yield descriptor
            if stage.durable:
                os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _seal(stage: _Stage, *, allow_empty: bool) -> None:
    """Check the file a producer left at the temporary path and make it ready for the rename."""
    # Not followed and not waited for: a link or a pipe left there is refused below, not opened.
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
    try:
        descriptor = os.open(stage.temporary, flags)
    except FileNotFoundError as error:
        message = "nothing was written to the staged path"
        raise FileNotFoundError(errno.ENOENT, message, stage.temporary) from error
    try:
        found = os.fstat(descriptor)
        if not stat.S_ISREG(found.st_mode):
            message = f"not a regular file at the staged path: {stage.temporary!r}"
            raise ValueError(message)
        if found.st_size == 0 and not allow_empty:
            message = f"an empty file at the staged path: {stage.temporary!r}; allow_empty=True publishes it"
            raise ValueError(message)
        if stage.mode is not None:
            os.fchmod(descriptor, stage.mode)
        if stage.durable:
            os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_bytes_atomic(
    path: _Path, data: bytes | bytearray | memoryview, *, durable: bool = True, parents: bool = False
) -> None:
    """Replace the file at `path` with `data` in one step.

    When writing fails, `path` is unchanged and no temporary file stays
    behind. Only a crash of the whole process can leave a temporary file,
    named `.<name>.<random>.tmp`, beside the unchanged target.

    The directory of `path` must exist: a missing one raises
    `FileNotFoundError`. `parents=True` creates it first, together with every
    missing directory above it, as `os.makedirs` does. Directories created
    stay when the write then fails; the one step is the file, not its
    directories. Nothing is created for a call that is refused for its
    arguments.

    Permissions: an existing file keeps its permissions. A new file gets the
    permissions of any newly created file, so the process umask decides who
    can read it (mode 644 under umask 022); no file is made private for the
    caller. A symbolic link at `path` is replaced by a regular file with the
    permissions of a new file, its target is left alone. A directory created
    for `parents=True` follows the same rule: it gets the permissions of any
    newly created directory (mode 755 under umask 022), and a directory that
    exists is left as it is.

    `durable=True` flushes the file to disk before the rename and the
    directory after it, and the directory above each directory it created, so
    the new content survives a power failure or a crash of the operating
    system once the call has returned. `durable=False` leaves out every flush
    and is much faster on a disk. It is not
    crash-safe: the rename is still one step for every reader and after a
    failed write, but after a power failure or a crash of the operating system
    the target can hold the old content, the new content, or an empty or
    incomplete file. Use it only for files that can be made again.
    """
    if not isinstance(data, (bytes, bytearray, memoryview)):
        message = f"write_bytes_atomic needs bytes, not {type(data).__name__}"
        raise TypeError(message)
    view = memoryview(data).cast("B")
    with _temporary_file(_stage(path, durable=durable, parents=parents)) as descriptor:
        written = 0
        while written < len(view):
            written += os.write(descriptor, view[written:])


def write_text_atomic(path: _Path, text: str, *, durable: bool = True, parents: bool = False) -> None:
    """Replace the file at `path` with `text` as UTF-8 in one step.

    Line endings are written as given. See `write_bytes_atomic` for what
    "in one step" guarantees, for the permissions of the file and for
    `durable` and `parents`.
    """
    if not isinstance(text, str):
        message = f"write_text_atomic needs text, not {type(text).__name__}"
        raise TypeError(message)
    write_bytes_atomic(path, text.encode("utf-8"), durable=durable, parents=parents)


def write_json(  # noqa: PLR0913 - one keyword per independent setting of the written file
    path: _Path,
    value: JsonValue,
    *,
    indent: int | None = 2,
    sort_keys: bool = True,
    ensure_ascii: bool = False,
    durable: bool = True,
    parents: bool = False,
) -> None:
    """Replace the file at `path` with `value` as UTF-8 JSON in one step.

    The document ends with a newline. Non-ASCII text is written as itself;
    `ensure_ascii=True` writes each non-ASCII character as a Unicode escape,
    as the standard serialiser does. A value JSON cannot represent is refused
    before anything is written: NaN and the two infinities raise `ValueError`,
    a value of a type JSON does not have raises `TypeError`. See
    `write_bytes_atomic` for what "in one step" guarantees, for the
    permissions of the file and for `durable` and `parents`.
    """
    text = json.dumps(value, indent=indent, sort_keys=sort_keys, ensure_ascii=ensure_ascii, allow_nan=False)
    write_text_atomic(path, text + "\n", durable=durable, parents=parents)


@contextlib.contextmanager
def open_bytes_atomic(path: _Path, *, durable: bool = True, parents: bool = False) -> Iterator[io.BufferedWriter]:
    """Lend a binary file to write into; replace the file at `path` with it in one step.

    For content that is written in steps or is too large for one value:

        with open_bytes_atomic("table.bin") as handle:
            for row in rows:
                handle.write(row)

    The block writes into a temporary file beside `path`. When the block ends,
    everything written is in the file before the rename: the block need not
    flush or close, and may close. When the block raises, `path` is unchanged
    and no temporary file stays behind. Nothing happens before the `with`
    statement is entered. A relative `path` means the directory that was
    current then; the block may change it.

    See `write_bytes_atomic` for what "in one step" guarantees, for the
    permissions of the file and for `durable` and `parents`. The temporary
    file exists for as long as the block runs, so a process killed inside the
    block leaves it behind.
    """
    with _temporary_file(_stage(path, durable=durable, parents=parents, block=True)) as descriptor:
        handle = os.fdopen(descriptor, "wb", closefd=False)
        try:
            yield handle
            handle.close()
        finally:
            # After a failure: drops what is still buffered. The descriptor is closed next, and
            # bytes written later would reach whatever file gets its number.
            handle.raw.close()


@contextlib.contextmanager
def open_text_atomic(path: _Path, *, durable: bool = True, parents: bool = False) -> Iterator[io.TextIOWrapper]:
    """Lend a text file to write into; replace the file at `path` with it in one step.

    The text is written as UTF-8 and line endings are written as given, as
    `write_text_atomic` does. Text that cannot be encoded raises
    `UnicodeEncodeError` where it is written, and `path` is unchanged. Text
    still buffered when the block ends is written before the rename. See
    `open_bytes_atomic` for everything else.
    """
    with open_bytes_atomic(path, durable=durable, parents=parents) as handle:
        text = io.TextIOWrapper(handle, encoding="utf-8", newline="")
        yield text
        text.close()


@contextlib.contextmanager
def staged_path(
    path: _Path, *, durable: bool = True, parents: bool = False, allow_empty: bool = False
) -> Iterator[str]:
    """Lend the name of a temporary file to a producer; replace the file at `path` with it in one step.

    For a producer that takes a file name and opens the file itself: a library
    call, a child process.

        with staged_path("table.parquet") as temporary:
            frame.to_parquet(temporary)

    The name is an absolute path beside `path`, of a file that does not exist
    yet, and it ends with the suffix of `path`. When the block ends, the file
    the producer wrote there replaces `path`. When the block raises, `path` is
    unchanged and the temporary file is removed.

    The producer must have closed the file before the block ends: this
    function cannot see content that a producer still holds in memory. Two
    signs of that mistake are refused, with `path` unchanged: no file raises
    `FileNotFoundError`, an empty file raises `ValueError`. `allow_empty=True`
    publishes an empty file. Anything but a regular file is refused too, and
    a directory left there is not removed.

    A new file keeps the permissions the producer gave it; an existing file's
    permissions are applied when the block ends. See `write_bytes_atomic` for
    `durable` and `parents`.
    """
    _switch("allow_empty", allow_empty)
    stage = _stage(path, durable=durable, parents=parents, block=True, suffix=True)
    with _published(stage):
        yield stage.temporary
        _seal(stage, allow_empty=allow_empty)
