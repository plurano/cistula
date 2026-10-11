# SPDX-FileCopyrightText: 2026 Lusoris <lusoris@proton.me>
# SPDX-License-Identifier: EUPL-1.2
from __future__ import annotations

import array
import json
import os
import re
import signal
import stat
import subprocess
import sys
from typing import TYPE_CHECKING, Any

import pytest

from plurano_python_library.files import (
    DuplicateKeyError,
    loads_json,
    read_json,
    write_bytes_atomic,
    write_json,
    write_text_atomic,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from plurano_python_library.files import JsonValue

TEMPORARY = re.compile(r"^\.(?P<name>.+)\.[0-9a-f]{16}\.tmp$")


@pytest.fixture
def umask_027() -> Iterator[None]:
    previous = os.umask(0o027)
    try:
        yield
    finally:
        os.umask(previous)


def mode(path: Path) -> int:
    return stat.S_IMODE(path.lstat().st_mode)


def names(directory: Path) -> list[str]:
    return sorted(entry.name for entry in directory.iterdir())


# --- loads_json ------------------------------------------------------------------


def test_loads_every_json_type() -> None:
    text = '{"n": null, "t": true, "i": 1, "f": 1.5, "s": "ä", "l": [1, {"k": []}], "o": {}}'

    assert loads_json(text) == {"n": None, "t": True, "i": 1, "f": 1.5, "s": "ä", "l": [1, {"k": []}], "o": {}}


@pytest.mark.parametrize("text", ["1", '"text"', "null", "[]", "{}"])
def test_loads_documents_that_are_not_objects(text: str) -> None:
    assert loads_json(text) == json.loads(text)


@pytest.mark.parametrize(
    ("text", "key"),
    [
        ('{"a": 1, "a": 2}', "a"),
        ('{"a": 1, "a": 1}', "a"),
        ('{"outer": {"b": 1, "c": 2, "b": 3}}', "b"),
        ('[{"x": 1}, {"y": 1, "y": 2}]', "y"),
        ('{"a": 1, "\\u0061": 2}', "a"),
        ('{"": 1, "": 2}', ""),
    ],
)
def test_repeated_key_is_refused(text: str, key: str) -> None:
    with pytest.raises(DuplicateKeyError) as caught:
        loads_json(text)

    assert caught.value.key == key
    assert isinstance(caught.value, ValueError)
    assert repr(key) in str(caught.value)


@pytest.mark.parametrize(
    "text",
    [
        '{"a": 1, "A": 2}',
        '{"a": {"a": {"a": 1}}}',
        '[{"a": 1}, {"a": 2}]',
        '{"a": 1, "a ": 2}',
    ],
)
def test_same_name_in_different_objects_or_another_spelling_is_not_a_repeat(text: str) -> None:
    assert loads_json(text) == json.loads(text)


def test_unique_keys_false_keeps_the_last_value() -> None:
    assert loads_json('{"a": 1, "a": 2}', unique_keys=False) == {"a": 2}


@pytest.mark.parametrize("text", ["", " ", "{", '{"a": 1,}', "{'a': 1}", '{"a": 1} trailing'])
def test_malformed_text_is_a_decode_error(text: str) -> None:
    with pytest.raises(json.JSONDecodeError):
        loads_json(text)


@pytest.mark.parametrize("value", [b"{}", bytearray(b"{}"), None, 1])
def test_loads_needs_text(value: Any) -> None:
    with pytest.raises(TypeError, match="needs text"):
        loads_json(value)


# --- read_json ---------------------------------------------------------------------


def test_reads_a_utf8_file_from_text_and_path(tmp_path: Path) -> None:
    path = tmp_path / "data.json"
    path.write_bytes('{"name": "Zürich", "list": [1, 2]}'.encode())

    assert read_json(path) == {"name": "Zürich", "list": [1, 2]}
    assert read_json(str(path)) == {"name": "Zürich", "list": [1, 2]}


def test_read_refuses_a_repeated_key_unless_told_otherwise(tmp_path: Path) -> None:
    path = tmp_path / "data.json"
    path.write_text('{"a": 1, "a": 2}', encoding="utf-8")

    with pytest.raises(DuplicateKeyError):
        read_json(path)
    assert read_json(path, unique_keys=False) == {"a": 2}


def test_read_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        read_json(tmp_path / "absent.json")


def test_read_directory(tmp_path: Path) -> None:
    with pytest.raises(IsADirectoryError):
        read_json(tmp_path)


def test_read_empty_file(tmp_path: Path) -> None:
    path = tmp_path / "empty.json"
    path.write_bytes(b"")

    with pytest.raises(json.JSONDecodeError):
        read_json(path)


def test_read_refuses_bytes_that_are_not_utf8(tmp_path: Path) -> None:
    path = tmp_path / "latin.json"
    path.write_bytes('{"name": "Zürich"}'.encode("latin-1"))

    with pytest.raises(UnicodeDecodeError):
        read_json(path)


def test_read_refuses_a_byte_order_mark(tmp_path: Path) -> None:
    path = tmp_path / "bom.json"
    path.write_bytes(b"\xef\xbb\xbf{}")

    with pytest.raises(json.JSONDecodeError):
        read_json(path)


def test_every_content_error_is_a_value_error(tmp_path: Path) -> None:
    for content in (b'{"a": 1, "a": 2}', b"\xff", b"{"):
        path = tmp_path / "bad.json"
        path.write_bytes(content)
        with pytest.raises(ValueError):  # noqa: PT011 - the shared base class is the point
            read_json(path)


# --- write_bytes_atomic --------------------------------------------------------------


@pytest.mark.usefixtures("umask_027")
def test_new_file_gets_content_and_default_permissions(tmp_path: Path) -> None:
    path = tmp_path / "new.bin"

    write_bytes_atomic(path, b"\x00\x01content")

    assert path.read_bytes() == b"\x00\x01content"
    assert mode(path) == 0o640
    assert names(tmp_path) == ["new.bin"]


@pytest.mark.parametrize("permissions", [0o600, 0o644, 0o755, 0o400])
def test_existing_file_is_replaced_and_keeps_its_permissions(tmp_path: Path, permissions: int) -> None:
    path = tmp_path / "old.bin"
    path.write_bytes(b"old content that is longer than the new one")
    path.chmod(permissions)

    write_bytes_atomic(path, b"new")

    assert path.read_bytes() == b"new"
    assert mode(path) == permissions
    assert names(tmp_path) == ["old.bin"]


def test_text_path_and_relative_path_without_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)

    write_bytes_atomic("plain.bin", b"relative")
    write_bytes_atomic(str(tmp_path / "absolute.bin"), b"absolute")

    assert (tmp_path / "plain.bin").read_bytes() == b"relative"
    assert (tmp_path / "absolute.bin").read_bytes() == b"absolute"
    assert names(tmp_path) == ["absolute.bin", "plain.bin"]


@pytest.mark.parametrize("size", [0, 1, 65536, 5 * 1024 * 1024 + 1])
def test_sizes_from_empty_to_larger_than_one_write(tmp_path: Path, size: int) -> None:
    path = tmp_path / "sized.bin"
    data = bytes(index % 251 for index in range(size))

    write_bytes_atomic(path, data)

    assert path.read_bytes() == data


def test_every_bytes_like_type_is_written_as_its_bytes(tmp_path: Path) -> None:
    numbers = array.array("H", [1, 2, 3])

    write_bytes_atomic(tmp_path / "a", bytearray(b"bytearray"))
    write_bytes_atomic(tmp_path / "b", memoryview(b"memoryview")[2:6])
    write_bytes_atomic(tmp_path / "c", memoryview(numbers))

    assert (tmp_path / "a").read_bytes() == b"bytearray"
    assert (tmp_path / "b").read_bytes() == b"mory"
    assert (tmp_path / "c").read_bytes() == numbers.tobytes()


def test_short_writes_are_continued(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real_write = os.write
    calls = 0

    def short_write(descriptor: int, data: bytes) -> int:
        nonlocal calls
        calls += 1
        return real_write(descriptor, bytes(data[:7]))

    monkeypatch.setattr(os, "write", short_write)

    write_bytes_atomic(tmp_path / "short.bin", b"0123456789" * 5)

    assert (tmp_path / "short.bin").read_bytes() == b"0123456789" * 5
    assert calls == 8


def test_short_writes_of_a_view_with_wide_items_are_continued(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Four items of four bytes each: the length that counts is 16 bytes, not 4 items.
    numbers = array.array("I", [1, 2, 3, 4])
    assert numbers.itemsize == 4
    real_write = os.write
    monkeypatch.setattr(os, "write", lambda descriptor, data: real_write(descriptor, bytes(data[:3])))

    write_bytes_atomic(tmp_path / "wide.bin", memoryview(numbers))

    assert (tmp_path / "wide.bin").read_bytes() == numbers.tobytes()


def test_content_and_directory_are_flushed_to_disk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real_fsync = os.fsync
    flushed: list[bool] = []

    def recording_fsync(descriptor: int) -> None:
        flushed.append(stat.S_ISDIR(os.fstat(descriptor).st_mode))
        real_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", recording_fsync)

    write_bytes_atomic(tmp_path / "durable.bin", b"data")

    # First the file, before the rename; then the directory, after it.
    assert flushed == [False, True]


@pytest.mark.usefixtures("umask_027")
def test_symbolic_link_is_replaced_and_its_target_left_alone(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.write_bytes(b"target content")
    target.chmod(0o600)
    link = tmp_path / "link"
    link.symlink_to(target)

    write_bytes_atomic(link, b"new")

    assert not link.is_symlink()
    assert link.read_bytes() == b"new"
    # The new file is a new file: it does not take over the target's permissions.
    assert mode(link) == 0o640
    assert target.read_bytes() == b"target content"
    assert mode(target) == 0o600


@pytest.mark.usefixtures("umask_027")
def test_dangling_symbolic_link_is_replaced(tmp_path: Path) -> None:
    link = tmp_path / "link"
    link.symlink_to(tmp_path / "absent")

    write_bytes_atomic(link, b"new")

    assert not link.is_symlink()
    assert link.read_bytes() == b"new"
    assert mode(link) == 0o640
    assert names(tmp_path) == ["link"]


@pytest.mark.parametrize("data", ["text", None, 5, ["a"]])
def test_write_bytes_needs_bytes(tmp_path: Path, data: Any) -> None:
    with pytest.raises(TypeError, match="needs bytes"):
        write_bytes_atomic(tmp_path / "never", data)

    assert names(tmp_path) == []


def test_missing_directory_is_refused_and_not_created(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        write_bytes_atomic(tmp_path / "absent" / "file", b"data")

    assert names(tmp_path) == []


def test_directory_as_target_is_refused(tmp_path: Path) -> None:
    (tmp_path / "directory").mkdir()

    with pytest.raises(IsADirectoryError):
        write_bytes_atomic(tmp_path / "directory", b"data")

    assert names(tmp_path) == ["directory"]


@pytest.mark.parametrize("path", ["", "directory/"])
def test_path_without_a_file_name_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, path: str) -> None:
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ValueError, match="not a file path"):
        write_bytes_atomic(path, b"data")


def test_bytes_path_is_refused(tmp_path: Path) -> None:
    path: Any = os.fsencode(tmp_path / "bytes-path")

    with pytest.raises(TypeError, match="path must be text"):
        write_bytes_atomic(path, b"data")


# --- a failure between writing and replacing ---------------------------------------


def refuse(*_: object, **__: object) -> None:
    raise OSError(28, "planted: no space left on device")


def interrupt(*_: object, **__: object) -> None:
    raise KeyboardInterrupt


@pytest.mark.parametrize("step", ["write", "fsync", "replace", "fchmod"])
@pytest.mark.parametrize("failure", [refuse, interrupt])
def test_failure_at_any_step_leaves_the_old_file_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, step: str, failure: Any
) -> None:
    path = tmp_path / "kept.txt"
    path.write_bytes(b"old content")
    path.chmod(0o600)
    monkeypatch.setattr(os, step, failure)

    with pytest.raises((OSError, KeyboardInterrupt)):
        write_bytes_atomic(path, b"new content")
    monkeypatch.undo()

    assert path.read_bytes() == b"old content"
    assert mode(path) == 0o600
    assert names(tmp_path) == ["kept.txt"]


@pytest.mark.parametrize("step", ["write", "fsync", "replace"])
def test_failure_when_creating_a_file_leaves_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, step: str
) -> None:
    monkeypatch.setattr(os, step, refuse)

    with pytest.raises(OSError, match="planted"):
        write_bytes_atomic(tmp_path / "never.txt", b"new content")
    monkeypatch.undo()

    assert names(tmp_path) == []


def test_interrupt_right_after_the_replace_keeps_the_new_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "replaced.txt"
    path.write_bytes(b"old content")
    real_replace = os.replace

    def replace_then_interrupt(source: str, destination: str) -> None:
        real_replace(source, destination)
        raise KeyboardInterrupt

    monkeypatch.setattr(os, "replace", replace_then_interrupt)

    with pytest.raises(KeyboardInterrupt):
        write_bytes_atomic(path, b"new content")
    monkeypatch.undo()

    # The rename had happened: the clean-up finds no temporary file and the
    # interrupt still reaches the caller.
    assert path.read_bytes() == b"new content"
    assert names(tmp_path) == ["replaced.txt"]


def test_new_content_is_complete_on_disk_before_the_replace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "ordered.txt"
    path.write_bytes(b"old content")
    seen: dict[str, bytes] = {}
    real_replace = os.replace

    def checking_replace(source: str, destination: str) -> None:
        with open(source, "rb") as handle:
            seen["temporary"] = handle.read()
        with open(destination, "rb") as handle:
            seen["target"] = handle.read()
        real_replace(source, destination)

    monkeypatch.setattr(os, "replace", checking_replace)

    write_bytes_atomic(path, b"new content")

    assert seen == {"temporary": b"new content", "target": b"old content"}
    assert path.read_bytes() == b"new content"


def test_process_killed_between_write_and_replace_leaves_the_old_file_whole(tmp_path: Path) -> None:
    path = tmp_path / "survivor.txt"
    path.write_bytes(b"old content")
    # The child dies, without any clean-up, at the moment it would rename.
    code = """
import os, signal, sys
from plurano_python_library import files

def die(*_):
    os.kill(os.getpid(), signal.SIGKILL)

os.replace = die
files.write_bytes_atomic(sys.argv[1], b"new content")
"""
    done = subprocess.run([sys.executable, "-c", code, str(path)], check=False, timeout=60)

    assert done.returncode == -signal.SIGKILL
    assert path.read_bytes() == b"old content"
    # A crash is the one case that leaves the temporary file, under its documented name.
    (left,) = [name for name in names(tmp_path) if name != "survivor.txt"]
    match = TEMPORARY.match(left)
    assert match is not None
    assert match.group("name") == "survivor.txt"
    assert (tmp_path / left).read_bytes() == b"new content"

    # What the crash left behind is in nobody's way: the next write has a
    # name of its own, succeeds, and does not touch the leftover.
    write_bytes_atomic(path, b"written after the crash")

    assert path.read_bytes() == b"written after the crash"
    assert names(tmp_path) == sorted([left, "survivor.txt"])
    assert (tmp_path / left).read_bytes() == b"new content"


# --- write_text_atomic ---------------------------------------------------------------


def test_text_is_written_as_utf8_with_its_line_endings(tmp_path: Path) -> None:
    path = tmp_path / "text.txt"

    write_text_atomic(path, "Zürich\r\nline\n")

    assert path.read_bytes() == "Zürich\r\nline\n".encode()


def test_empty_text_makes_an_empty_file(tmp_path: Path) -> None:
    write_text_atomic(tmp_path / "empty.txt", "")

    assert (tmp_path / "empty.txt").read_bytes() == b""


@pytest.mark.parametrize("text", [b"bytes", None, 5])
def test_write_text_needs_text(tmp_path: Path, text: Any) -> None:
    with pytest.raises(TypeError, match="needs text"):
        write_text_atomic(tmp_path / "never", text)

    assert names(tmp_path) == []


def test_text_that_cannot_be_encoded_leaves_the_old_file_whole(tmp_path: Path) -> None:
    path = tmp_path / "kept.txt"
    path.write_bytes(b"old content")

    with pytest.raises(UnicodeEncodeError):
        write_text_atomic(path, "lone surrogate \ud800")

    assert path.read_bytes() == b"old content"
    assert names(tmp_path) == ["kept.txt"]


# --- write_json ------------------------------------------------------------------------


def test_json_is_sorted_indented_unescaped_and_ends_with_a_newline(tmp_path: Path) -> None:
    path = tmp_path / "data.json"

    write_json(path, {"b": [1, 2], "a": "Zürich"})

    assert path.read_bytes() == '{\n  "a": "Zürich",\n  "b": [\n    1,\n    2\n  ]\n}\n'.encode()


def test_json_layout_options(tmp_path: Path) -> None:
    path = tmp_path / "data.json"

    write_json(path, {"b": 1, "a": 2}, indent=None, sort_keys=False)

    assert path.read_text(encoding="utf-8") == '{"b": 1, "a": 2}\n'


@pytest.mark.parametrize("value", [None, True, 0, -1.5, "", "text", [], {}, {"nested": [{"deep": [None, 1]}]}])
def test_written_json_reads_back_equal(tmp_path: Path, value: JsonValue) -> None:
    path = tmp_path / "round.json"

    write_json(path, value)

    assert read_json(path) == value


def test_value_json_cannot_represent_leaves_the_old_file_whole(tmp_path: Path) -> None:
    path = tmp_path / "kept.json"
    path.write_bytes(b'{"old": true}\n')
    unrepresentable: Any = {"set": {1, 2}}

    with pytest.raises(TypeError):
        write_json(path, unrepresentable)

    assert path.read_bytes() == b'{"old": true}\n'
    assert names(tmp_path) == ["kept.json"]
