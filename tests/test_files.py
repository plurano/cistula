# SPDX-FileCopyrightText: 2026 Lusoris <lusoris@proton.me>
# SPDX-License-Identifier: EUPL-1.2
from __future__ import annotations

import array
import errno
import functools
import gc
import inspect
import io
import json
import math
import os
import re
import signal
import stat
import subprocess
import sys
from typing import TYPE_CHECKING, Any

import pytest

from cistula.files import (
    DuplicateKeyError,
    JsonLineError,
    loads_json,
    loads_json_lines,
    open_bytes_atomic,
    open_text_atomic,
    read_json,
    read_json_lines,
    staged_path,
    write_bytes_atomic,
    write_json,
    write_json_lines,
    write_text_atomic,
)
from cistula.process import run

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from cistula.files import JsonValue

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


@pytest.fixture
def flushed(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every flush to disk during the test, in order: "file" or "directory"."""
    real_fsync = os.fsync
    seen: list[str] = []

    def recording_fsync(descriptor: int) -> None:
        seen.append("directory" if stat.S_ISDIR(os.fstat(descriptor).st_mode) else "file")
        real_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", recording_fsync)
    return seen


def write_as_bytes(path: Path, **options: Any) -> None:
    write_bytes_atomic(path, b"new content", **options)


def write_as_text(path: Path, **options: Any) -> None:
    write_text_atomic(path, "new content", **options)


def write_as_json(path: Path, **options: Any) -> None:
    write_json(path, {"new": "content"}, **options)


def produce(temporary: str, data: bytes) -> None:
    """A producer that takes a file name: it opens the file, writes it and closes it."""
    with open(temporary, "wb") as handle:
        handle.write(data)


def write_as_open_bytes(path: Path, **options: Any) -> None:
    with open_bytes_atomic(path, **options) as handle:
        handle.write(b"new ")
        handle.write(b"content")


def write_as_open_text(path: Path, **options: Any) -> None:
    with open_text_atomic(path, **options) as text:
        text.write("new ")
        text.write("content")


def write_as_staged_path(path: Path, **options: Any) -> None:
    with staged_path(path, **options) as temporary:
        produce(temporary, b"new content")


BLOCK_WRITERS = [write_as_open_bytes, write_as_open_text, write_as_staged_path]
WRITERS = [write_as_bytes, write_as_text, write_as_json, *BLOCK_WRITERS]


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


NOT_FINITE = ["NaN", "Infinity", "-Infinity", "1e999", "-1e999", "1E400", "1.0e+400", "1.8e308", "-1.8e308"]
AROUND_A_NUMBER = ["{}", "[1, {}]", '{{"a": {}}}', '{{"a": [{{"b": [0.5, {}]}}]}}']


def same(left: object, right: object) -> bool:
    """Compare two parsed documents; unlike `==`, a NaN equals a NaN."""
    return json.dumps(left, sort_keys=True) == json.dumps(right, sort_keys=True)


@pytest.mark.parametrize("number", NOT_FINITE)
@pytest.mark.parametrize("around", AROUND_A_NUMBER)
def test_number_json_does_not_have_is_refused_when_read(around: str, number: str) -> None:
    expected = "not a JSON number" if number.lstrip("-") in {"NaN", "Infinity"} else "too large for a float"

    with pytest.raises(ValueError, match=expected) as caught:
        loads_json(around.format(number))

    assert number in str(caught.value)


@pytest.mark.parametrize("number", NOT_FINITE)
@pytest.mark.parametrize("around", AROUND_A_NUMBER)
def test_finite_numbers_false_reads_it_as_the_standard_library_does(around: str, number: str) -> None:
    text = around.format(number)

    found = loads_json(text, finite_numbers=False)

    assert same(found, json.loads(text))
    assert not math.isfinite(json.loads(number))


@pytest.mark.parametrize(
    "text",
    [
        "1.7976931348623157e308",
        "-1.7976931348623157e308",
        "5e-324",
        "1e-999",
        "-0.0",
        "0.1",
        "1" + "0" * 400,
        "-1" + "0" * 400,
        '"NaN"',
        '{"Infinity": "-Infinity", "NaN": "1e999"}',
    ],
)
def test_finite_number_at_the_limits_and_text_that_looks_like_one_are_read(text: str) -> None:
    assert loads_json(text) == json.loads(text)
    assert loads_json(text, finite_numbers=False) == json.loads(text)


def test_a_float_is_read_as_the_nearest_float_and_an_integer_stays_exact() -> None:
    found: Any = loads_json("[0.1, 1.0, 1e2, 12345678901234567890123]")

    assert found == [0.1, 1.0, 100.0, 12345678901234567890123]
    assert [type(item) for item in found] == [float, float, float, int]


@pytest.mark.parametrize("text", ["nan", "inf", "-inf", "NAN", "Inf", "+Infinity", "-NaN", "1e", "0x10", "01"])
def test_finite_numbers_false_reads_no_other_spelling(text: str) -> None:
    with pytest.raises(json.JSONDecodeError):
        loads_json(text, finite_numbers=False)
    with pytest.raises(json.JSONDecodeError):
        loads_json(text)


def test_each_keyword_lifts_its_own_check_only() -> None:
    with pytest.raises(DuplicateKeyError):
        loads_json('{"a": NaN, "a": 1}', finite_numbers=False)
    with pytest.raises(ValueError, match="not a JSON number: NaN"):
        loads_json('{"a": NaN, "a": 1}', unique_keys=False)
    with pytest.raises(ValueError, match="too large for a float: 1e999"):
        loads_json('{"a": 1e999, "a": 1}', unique_keys=False)

    assert same(loads_json('{"a": 1, "a": NaN}', unique_keys=False, finite_numbers=False), {"a": math.nan})


def test_a_long_number_is_cut_short_in_the_message() -> None:
    with pytest.raises(ValueError, match="too large for a float") as caught:
        loads_json("1" + "0" * 500 + ".5")

    assert len(str(caught.value)) < 80


def test_the_reader_keywords_are_keyword_only() -> None:
    refused: Any = loads_json

    with pytest.raises(TypeError):
        refused("1", True)  # noqa: FBT003 - the positional call is what is refused
    with pytest.raises(TypeError):
        refused("1", True, False)  # noqa: FBT003


def test_what_the_reader_accepts_by_default_the_writer_writes(tmp_path: Path) -> None:
    text = '{"big": 1.7976931348623157e308, "small": 5e-324, "long": 1' + "0" * 400 + "}"
    path = tmp_path / "out.json"

    write_json(path, loads_json(text))

    assert read_json(path) == json.loads(text)


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


@pytest.mark.parametrize("number", NOT_FINITE)
def test_read_refuses_a_number_json_does_not_have_unless_told_otherwise(tmp_path: Path, number: str) -> None:
    path = tmp_path / "data.json"
    path.write_text(f'{{"score": {number}}}', encoding="utf-8")

    with pytest.raises(ValueError, match=r"not a JSON number|too large for a float"):
        read_json(path)
    with pytest.raises(ValueError, match=r"not a JSON number|too large for a float"):
        read_json(path, unique_keys=False)
    assert same(read_json(path, finite_numbers=False), {"score": float(number)})


@pytest.mark.parametrize(
    ("content", "error"),
    [
        (b'{"a": NaN, "a": 1}', DuplicateKeyError),
        (b"\xef\xbb\xbf[NaN]", json.JSONDecodeError),
        (b"[NaN, \xff]", UnicodeDecodeError),
        (b"[NaN,]", json.JSONDecodeError),
        (b"", json.JSONDecodeError),
    ],
)
def test_read_with_finite_numbers_false_refuses_everything_else(
    tmp_path: Path, content: bytes, error: type[Exception]
) -> None:
    path = tmp_path / "data.json"
    path.write_bytes(content)

    with pytest.raises(error):
        read_json(path, finite_numbers=False)


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


# --- loads_json_lines, read_json_lines ---------------------------------------------


@pytest.mark.parametrize(
    ("text", "values"),
    [
        ("", []),
        ("\n", []),
        ('{"a": 1}', [{"a": 1}]),
        ('{"a": 1}\n', [{"a": 1}]),
        ('{"a": 1}\r\n[2, null]\r\n', [{"a": 1}, [2, None]]),
        ('1\n"two"\nnull\ntrue\n', [1, "two", None, True]),
        ('{"a": 1}\n\n  \t\r\n{"b": 2}\n\n', [{"a": 1}, {"b": 2}]),
        ('{"s": "a\u2028b\u0085c"}\n', [{"s": "a\u2028b\u0085c"}]),
    ],
)
def test_lines_reads_one_value_per_row(text: str, values: list[JsonValue]) -> None:
    assert loads_json_lines(text) == values


@pytest.mark.parametrize(
    ("text", "line", "cause"),
    [
        ("{", 1, json.JSONDecodeError),
        ('{"a": 1}\n{"b": \n{"c": 3}\n', 2, json.JSONDecodeError),
        ('{"a": 1}\n\n\n{"a": 1, "a": 2}\n', 4, DuplicateKeyError),
        ("1\n2\n[NaN]", 3, ValueError),
        ("1\n[1e999]\n", 2, ValueError),
        ('{"a":\n1}\n', 1, json.JSONDecodeError),
        ("1 2\n", 1, json.JSONDecodeError),
        ("\ufeff1\n", 1, json.JSONDecodeError),
    ],
)
def test_lines_bad_row_names_its_line_and_keeps_its_error(text: str, line: int, cause: type[ValueError]) -> None:
    with pytest.raises(JsonLineError, match=rf"^line {line}: ") as raised:
        loads_json_lines(text)

    assert raised.value.line == line
    assert isinstance(raised.value.__cause__, cause)
    assert isinstance(raised.value, ValueError)


def test_lines_keywords_lift_the_same_checks_as_loads_json() -> None:
    assert loads_json_lines('{"a": 1, "a": 2}\n', unique_keys=False) == [{"a": 2}]
    assert same(loads_json_lines("[NaN]\n", finite_numbers=False), [[math.nan]])
    with pytest.raises(JsonLineError):
        loads_json_lines('{"a": NaN, "a": 1}\n', finite_numbers=False)


def test_lines_reader_keywords_are_keyword_only() -> None:
    for function in (loads_json_lines, read_json_lines):
        parameters = inspect.signature(function).parameters
        assert parameters["unique_keys"].kind is inspect.Parameter.KEYWORD_ONLY
        assert parameters["finite_numbers"].kind is inspect.Parameter.KEYWORD_ONLY


@pytest.mark.parametrize("value", [b"1\n", bytearray(b"1\n"), None, ["1"]])
def test_lines_needs_text(value: Any) -> None:
    with pytest.raises(TypeError, match="loads_json_lines needs text"):
        loads_json_lines(value)


def test_lines_rows_written_by_write_json_read_back(tmp_path: Path) -> None:
    rows: list[JsonValue] = [{"name": "Zürich", "n": 1}, [1.5, None], "x"]
    path = tmp_path / "rows.jsonl"
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")

    assert read_json_lines(path) == rows
    assert read_json_lines(str(path)) == rows


def test_read_lines_empty_file_is_no_rows(tmp_path: Path) -> None:
    path = tmp_path / "empty.jsonl"
    path.write_bytes(b"")

    assert read_json_lines(path) == []


def test_read_lines_bad_row_names_its_line(tmp_path: Path) -> None:
    path = tmp_path / "rows.jsonl"
    path.write_text('{"a": 1}\n{"a": 1, "a": 2}\n', encoding="utf-8")

    with pytest.raises(JsonLineError, match=r"^line 2: ") as raised:
        read_json_lines(path)
    assert isinstance(raised.value.__cause__, DuplicateKeyError)
    assert read_json_lines(path, unique_keys=False) == [{"a": 1}, {"a": 2}]


def test_read_lines_refuses_bytes_that_are_not_utf8(tmp_path: Path) -> None:
    path = tmp_path / "latin.jsonl"
    path.write_bytes('{"name": "Zürich"}\n'.encode("latin-1"))

    with pytest.raises(UnicodeDecodeError):
        read_json_lines(path)


def test_read_lines_missing_file_and_directory(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        read_json_lines(tmp_path / "absent.jsonl")
    with pytest.raises(IsADirectoryError):
        read_json_lines(tmp_path)


def test_every_content_error_is_a_value_error(tmp_path: Path) -> None:
    for content in (b'{"a": 1, "a": 2}', b"\xff", b"{", b"NaN", b"[-Infinity]", b"1e999"):
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


@pytest.mark.parametrize("writer", WRITERS)
@pytest.mark.parametrize("options", [{}, {"durable": True}])
def test_content_and_directory_are_flushed_to_disk(
    tmp_path: Path, flushed: list[str], writer: Any, options: dict[str, bool]
) -> None:
    writer(tmp_path / "durable", **options)

    # First the file, before the rename; then the directory, after it.
    assert flushed == ["file", "directory"]


@pytest.mark.parametrize("writer", WRITERS)
def test_the_file_is_flushed_before_the_rename_and_the_directory_after_it(
    tmp_path: Path, flushed: list[str], monkeypatch: pytest.MonkeyPatch, writer: Any
) -> None:
    real_replace = os.replace
    at_rename: list[list[str]] = []

    def recording_replace(source: str, destination: str) -> None:
        at_rename.append(list(flushed))
        real_replace(source, destination)

    monkeypatch.setattr(os, "replace", recording_replace)

    writer(tmp_path / "ordered.bin")

    assert at_rename == [["file"]]
    assert flushed == ["file", "directory"]


@pytest.mark.parametrize("writer", WRITERS)
def test_durable_false_replaces_the_file_without_a_flush(tmp_path: Path, flushed: list[str], writer: Any) -> None:
    path = tmp_path / "fast"
    path.write_bytes(b"old content")
    path.chmod(0o640)

    writer(path, durable=False)

    assert flushed == []
    assert b"new" in path.read_bytes()
    assert b"old" not in path.read_bytes()
    assert mode(path) == 0o640
    assert names(tmp_path) == ["fast"]


@pytest.mark.usefixtures("umask_027")
def test_durable_false_gives_a_new_file_the_default_permissions(tmp_path: Path) -> None:
    write_bytes_atomic(tmp_path / "fast.bin", b"data", durable=False)

    assert (tmp_path / "fast.bin").read_bytes() == b"data"
    assert mode(tmp_path / "fast.bin") == 0o640


def test_durable_false_does_not_depend_on_a_flush_that_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "fsync", refuse)

    with pytest.raises(OSError, match="planted"):
        write_bytes_atomic(tmp_path / "durable.bin", b"data")
    write_bytes_atomic(tmp_path / "fast.bin", b"data", durable=False)
    monkeypatch.undo()

    assert names(tmp_path) == ["fast.bin"]


@pytest.mark.parametrize("step", ["write", "replace", "fchmod"])
@pytest.mark.parametrize("failure", ["refuse", "interrupt"])
def test_durable_false_failure_at_any_step_leaves_the_old_file_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, step: str, failure: str
) -> None:
    path = tmp_path / "kept.txt"
    path.write_bytes(b"old content")
    path.chmod(0o600)
    monkeypatch.setattr(os, step, {"refuse": refuse, "interrupt": interrupt}[failure])

    with pytest.raises((OSError, KeyboardInterrupt)):
        write_bytes_atomic(path, b"new content", durable=False)
    monkeypatch.undo()

    assert path.read_bytes() == b"old content"
    assert mode(path) == 0o600
    assert names(tmp_path) == ["kept.txt"]


@pytest.mark.parametrize("writer", WRITERS)
@pytest.mark.parametrize("durable", [None, 0, 1, "no", "False"])
def test_durable_needs_true_or_false(tmp_path: Path, flushed: list[str], writer: Any, durable: Any) -> None:
    path = tmp_path / "kept"
    path.write_bytes(b"old content")

    with pytest.raises(TypeError, match="durable must be True or False"):
        writer(path, durable=durable)

    assert path.read_bytes() == b"old content"
    assert names(tmp_path) == ["kept"]
    assert flushed == []


@pytest.mark.parametrize("writer", [write_bytes_atomic, write_text_atomic, write_json])
def test_durable_is_a_keyword_only(tmp_path: Path, writer: Any) -> None:
    content: Any = b"data" if writer is write_bytes_atomic else "data"

    with pytest.raises(TypeError, match="positional"):
        writer(tmp_path / "never", content, False)  # noqa: FBT003

    assert names(tmp_path) == []


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


# --- parents: creating the directory of the target -----------------------------------


@pytest.mark.parametrize("writer", WRITERS)
def test_parents_creates_every_missing_directory(tmp_path: Path, writer: Any) -> None:
    path = tmp_path / "a" / "b" / "c" / "file"

    writer(path, parents=True)

    assert b"new" in path.read_bytes()
    assert names(tmp_path) == ["a"]
    assert names(tmp_path / "a") == ["b"]
    assert names(tmp_path / "a" / "b") == ["c"]
    assert names(tmp_path / "a" / "b" / "c") == ["file"]


@pytest.mark.parametrize("writer", WRITERS)
@pytest.mark.parametrize("options", [{}, {"parents": False}])
def test_without_parents_a_missing_directory_is_refused(tmp_path: Path, writer: Any, options: dict[str, bool]) -> None:
    with pytest.raises(FileNotFoundError):
        writer(tmp_path / "absent" / "file", **options)

    assert names(tmp_path) == []


@pytest.mark.parametrize(
    "writer", [write_bytes_atomic, write_text_atomic, write_json, open_bytes_atomic, open_text_atomic, staged_path]
)
def test_parents_is_a_keyword_only_and_off_by_default(writer: Any) -> None:
    parameter = inspect.signature(writer).parameters["parents"]

    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert parameter.default is False


@pytest.mark.parametrize("writer", WRITERS)
@pytest.mark.parametrize("parents", [None, 0, 1, "no", "False"])
def test_parents_needs_true_or_false(tmp_path: Path, flushed: list[str], writer: Any, parents: Any) -> None:
    with pytest.raises(TypeError, match="parents must be True or False"):
        writer(tmp_path / "absent" / "file", parents=parents)

    assert names(tmp_path) == []
    assert flushed == []


@pytest.mark.parametrize(("umask", "directory_mode", "file_mode"), [(0o022, 0o755, 0o644), (0o027, 0o750, 0o640)])
def test_new_directories_take_their_permissions_from_the_umask(
    tmp_path: Path, umask: int, directory_mode: int, file_mode: int
) -> None:
    previous = os.umask(umask)
    try:
        write_bytes_atomic(tmp_path / "a" / "b" / "file", b"data", parents=True)
    finally:
        os.umask(previous)

    assert mode(tmp_path / "a") == directory_mode
    assert mode(tmp_path / "a" / "b") == directory_mode
    assert mode(tmp_path / "a" / "b" / "file") == file_mode


@pytest.mark.usefixtures("umask_027")
def test_parents_leaves_a_directory_that_exists_as_it_is(tmp_path: Path) -> None:
    (tmp_path / "kept").mkdir()
    (tmp_path / "kept").chmod(0o700)
    (tmp_path / "kept" / "other").write_bytes(b"other content")

    write_bytes_atomic(tmp_path / "kept" / "new" / "file", b"data", parents=True)

    assert mode(tmp_path / "kept") == 0o700
    assert mode(tmp_path / "kept" / "new") == 0o750
    assert names(tmp_path / "kept") == ["new", "other"]
    assert (tmp_path / "kept" / "other").read_bytes() == b"other content"


@pytest.mark.parametrize("writer", WRITERS)
def test_parents_changes_nothing_when_the_directory_exists(tmp_path: Path, flushed: list[str], writer: Any) -> None:
    path = tmp_path / "kept"
    path.write_bytes(b"old content")
    path.chmod(0o640)

    writer(path, parents=True)

    assert b"new" in path.read_bytes()
    assert mode(path) == 0o640
    assert names(tmp_path) == ["kept"]
    assert flushed == ["file", "directory"]


def test_parents_flushes_each_directory_that_received_a_new_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_fsync = os.fsync
    seen: list[int] = []

    def recording_fsync(descriptor: int) -> None:
        seen.append(os.fstat(descriptor).st_ino)
        real_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", recording_fsync)

    write_bytes_atomic(tmp_path / "a" / "b" / "file", b"data", parents=True)
    monkeypatch.undo()

    # The directory above each new directory, the highest first; then the file, and its directory after the rename.
    expected = [tmp_path, tmp_path / "a", tmp_path / "a" / "b" / "file", tmp_path / "a" / "b"]
    assert seen == [path.stat().st_ino for path in expected]


@pytest.mark.parametrize("writer", WRITERS)
def test_parents_with_durable_false_flushes_nothing(tmp_path: Path, flushed: list[str], writer: Any) -> None:
    writer(tmp_path / "a" / "b" / "file", parents=True, durable=False)

    assert flushed == []
    assert names(tmp_path / "a" / "b") == ["file"]


@pytest.mark.parametrize("depth", [1, 2, 40])
def test_parents_creates_one_directory_or_many(tmp_path: Path, depth: int) -> None:
    path = tmp_path.joinpath(*["d"] * depth, "file")

    write_bytes_atomic(path, b"data", parents=True, durable=False)

    assert path.read_bytes() == b"data"


@pytest.mark.parametrize("path", ["file", "a/file", "a/b/file", "./a/b/file", "a//b/file"])
def test_parents_with_a_relative_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, path: str) -> None:
    monkeypatch.chdir(tmp_path)

    write_bytes_atomic(path, b"data", parents=True)

    assert (tmp_path / path).read_bytes() == b"data"
    assert names(tmp_path) == [path.removeprefix("./").split("/")[0]]


def test_parents_reads_a_path_through_a_missing_directory_as_makedirs_does(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    os.makedirs("expected/a/../b")

    write_bytes_atomic("found/a/../b/file", b"data", parents=True)

    assert names(tmp_path / "found") == names(tmp_path / "expected") == ["a", "b"]
    assert (tmp_path / "found" / "b" / "file").read_bytes() == b"data"


def test_parents_follows_a_symbolic_link_to_a_directory(tmp_path: Path) -> None:
    (tmp_path / "real").mkdir()
    (tmp_path / "link").symlink_to(tmp_path / "real")

    write_bytes_atomic(tmp_path / "link" / "new" / "file", b"data", parents=True)

    assert (tmp_path / "link").is_symlink()
    assert (tmp_path / "real" / "new" / "file").read_bytes() == b"data"


@pytest.mark.parametrize("below", ["file", "new/file"])
def test_parents_does_not_create_the_target_of_a_dangling_symbolic_link(tmp_path: Path, below: str) -> None:
    (tmp_path / "link").symlink_to(tmp_path / "absent")

    with pytest.raises(FileNotFoundError):
        write_bytes_atomic(tmp_path / "link" / below, b"data", parents=True)

    assert names(tmp_path) == ["link"]


@pytest.mark.parametrize("below", ["file", "new/file", "new/deeper/file"])
def test_parents_refuses_a_file_where_a_directory_is_needed(tmp_path: Path, below: str) -> None:
    (tmp_path / "taken").write_bytes(b"old content")

    with pytest.raises(NotADirectoryError):
        write_bytes_atomic(tmp_path / "taken" / below, b"data", parents=True)

    assert (tmp_path / "taken").read_bytes() == b"old content"
    assert names(tmp_path) == ["taken"]


@pytest.mark.skipif(os.geteuid() == 0, reason="the superuser may write into a directory without write permission")
def test_parents_in_a_directory_without_write_permission_is_refused(tmp_path: Path) -> None:
    (tmp_path / "locked").mkdir()
    (tmp_path / "locked").chmod(0o500)

    try:
        with pytest.raises(PermissionError):
            write_bytes_atomic(tmp_path / "locked" / "new" / "file", b"data", parents=True)
        assert names(tmp_path / "locked") == []
    finally:
        (tmp_path / "locked").chmod(0o700)


def test_parents_accepts_a_directory_another_writer_created_meanwhile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_mkdir = os.mkdir

    def late_mkdir(path: str) -> None:
        real_mkdir(path)
        real_mkdir(path)

    monkeypatch.setattr(os, "mkdir", late_mkdir)

    write_bytes_atomic(tmp_path / "a" / "b" / "file", b"data", parents=True)

    assert (tmp_path / "a" / "b" / "file").read_bytes() == b"data"


def test_parents_refuses_a_file_another_writer_created_meanwhile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_mkdir = os.mkdir

    def late_mkdir(path: str) -> None:
        with open(path, "wb"):
            pass
        real_mkdir(path)

    monkeypatch.setattr(os, "mkdir", late_mkdir)

    with pytest.raises(FileExistsError):
        write_bytes_atomic(tmp_path / "a" / "file", b"data", parents=True)

    assert (tmp_path / "a").is_file()


@pytest.mark.parametrize("step", ["write", "fsync", "replace"])
def test_directories_created_stay_when_the_write_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, step: str
) -> None:
    monkeypatch.setattr(os, step, refuse)

    with pytest.raises(OSError, match="planted"):
        write_bytes_atomic(tmp_path / "a" / "b" / "file", b"data", parents=True, durable=step == "fsync")
    monkeypatch.undo()

    assert names(tmp_path) == ["a"]
    assert names(tmp_path / "a") == (["b"] if step != "fsync" else [])
    assert step == "fsync" or names(tmp_path / "a" / "b") == []


def test_parents_creates_nothing_for_a_path_without_a_file_name(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not a file path"):
        write_bytes_atomic(f"{tmp_path}/absent/directory/", b"data", parents=True)

    assert names(tmp_path) == []


def test_parents_creates_nothing_for_a_bytes_path(tmp_path: Path) -> None:
    path: Any = os.fsencode(tmp_path / "absent" / "file")

    with pytest.raises(TypeError, match="path must be text"):
        write_bytes_atomic(path, b"data", parents=True)

    assert names(tmp_path) == []


def test_parents_creates_nothing_for_content_that_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "absent" / "file"
    wrong: Any = "text"

    with pytest.raises(TypeError, match="needs bytes"):
        write_bytes_atomic(path, wrong, parents=True)
    with pytest.raises(TypeError, match="needs text"):
        write_text_atomic(path, wrong.encode(), parents=True)
    with pytest.raises(UnicodeEncodeError):
        write_text_atomic(path, "\ud800", parents=True)
    with pytest.raises(ValueError, match="Out of range float values"):
        write_json(path, math.nan, parents=True)
    with pytest.raises(TypeError, match="not JSON serializable"):
        write_json(path, wrong.encode(), parents=True)

    assert names(tmp_path) == []


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
from cistula import files

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


@pytest.mark.parametrize(("indent", "sort_keys"), [(2, True), (None, False), (4, True)])
def test_json_ensure_ascii_writes_the_bytes_of_the_standard_serialiser(
    tmp_path: Path, indent: int | None, *, sort_keys: bool
) -> None:
    path = tmp_path / "data.json"
    value: JsonValue = {"b": ["Grüße", 2], "a": "Zürich"}

    write_json(path, value, indent=indent, sort_keys=sort_keys, ensure_ascii=True)

    expected = json.dumps(value, indent=indent, sort_keys=sort_keys, ensure_ascii=True) + "\n"
    assert path.read_bytes() == expected.encode("ascii")


def test_json_default_writes_non_ascii_text_as_utf_8(tmp_path: Path) -> None:
    path = tmp_path / "data.json"

    write_json(path, {"a": "Zürich"})

    assert "Zürich".encode() in path.read_bytes()
    assert b"\\u00fc" not in path.read_bytes()


def test_json_ensure_ascii_boundaries(tmp_path: Path) -> None:
    path = tmp_path / "data.json"
    value: JsonValue = {"\u00e9cole": "", "a": "\U0001f600"}

    write_json(path, value, indent=None, ensure_ascii=True)

    assert path.read_bytes() == b'{"a": "\\ud83d\\ude00", "\\u00e9cole": ""}\n'
    assert read_json(path) == value


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


@pytest.mark.parametrize("number", [math.nan, math.inf, -math.inf])
@pytest.mark.parametrize("place", ["alone", "in a list", "in an object", "nested"])
def test_number_json_does_not_have_is_refused_and_leaves_the_old_file_whole(
    tmp_path: Path, number: float, place: str
) -> None:
    path = tmp_path / "kept.json"
    path.write_bytes(b'{"old": true}\n')
    values: dict[str, JsonValue] = {
        "alone": number,
        "in a list": [1.0, number],
        "in an object": {"score": number},
        "nested": {"runs": [{"scores": [0.5, number]}]},
    }

    with pytest.raises(ValueError, match="not JSON compliant"):
        write_json(path, values[place])

    assert path.read_bytes() == b'{"old": true}\n'
    assert names(tmp_path) == ["kept.json"]


def test_number_json_does_not_have_creates_no_file(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not JSON compliant"):
        write_json(tmp_path / "never.json", {"score": math.nan})

    assert names(tmp_path) == []


@pytest.mark.parametrize("number", [sys.float_info.max, -sys.float_info.max, sys.float_info.min, 5e-324, -0.0, 1e308])
def test_every_finite_number_is_written_and_reads_back_equal(tmp_path: Path, number: float) -> None:
    path = tmp_path / "edge.json"

    write_json(path, {"score": number})

    assert read_json(path) == {"score": number}


# --- write_json_lines ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("values", "content"),
    [
        ([], b""),
        ([{}], b"{}\n"),
        ([{"b": [1, 2], "a": "Zürich"}], '{"a":"Zürich","b":[1,2]}\n'.encode()),
        ([1, "two\nlines", None, True, [], {"k": -1.5}], b'1\n"two\\nlines"\nnull\ntrue\n[]\n{"k":-1.5}\n'),
        (["a\u2028b"], '"a\u2028b"\n'.encode()),
    ],
)
def test_lines_writes_one_compact_sorted_row_per_value(tmp_path: Path, values: list[JsonValue], content: bytes) -> None:
    path = tmp_path / "rows.jsonl"

    write_json_lines(path, values)

    assert path.read_bytes() == content


def test_lines_writer_options_and_iterables(tmp_path: Path) -> None:
    path = tmp_path / "rows.jsonl"

    write_json_lines(path, ({"b": n, "a": "é"} for n in range(2)), sort_keys=False, ensure_ascii=True)

    assert path.read_bytes() == b'{"b":0,"a":"\\u00e9"}\n{"b":1,"a":"\\u00e9"}\n'


@pytest.mark.parametrize(
    "values",
    [
        [],
        [None],
        [{"a": 1}, {"a": 1}],
        [{"nested": [{"deep": [None, 1, "Grüße"]}]}, "", 0, sys.float_info.max, 5e-324],
    ],
)
def test_written_lines_read_back_equal(tmp_path: Path, values: list[JsonValue]) -> None:
    path = tmp_path / "round.jsonl"

    write_json_lines(path, values)

    assert read_json_lines(path) == values


@pytest.mark.parametrize(
    ("bad", "error"),
    [(math.nan, ValueError), (math.inf, ValueError), ({"score": -math.inf}, ValueError), ({1, 2}, TypeError)],
)
def test_lines_value_json_cannot_represent_leaves_the_old_file_whole(
    tmp_path: Path, bad: Any, error: type[Exception]
) -> None:
    path = tmp_path / "kept.jsonl"
    path.write_bytes(b'{"old":true}\n')

    with pytest.raises(error):
        write_json_lines(path, [{"a": 1}, {"b": 2}, bad, {"c": 3}])

    assert path.read_bytes() == b'{"old":true}\n'
    assert names(tmp_path) == ["kept.jsonl"]


def test_lines_iterable_that_fails_leaves_the_old_file_whole(tmp_path: Path) -> None:
    path = tmp_path / "kept.jsonl"
    path.write_bytes(b'{"old":true}\n')

    def failing() -> Iterator[JsonValue]:
        yield {"a": 1}
        message = "producer failed"
        raise RuntimeError(message)

    with pytest.raises(RuntimeError, match="producer failed"):
        write_json_lines(path, failing())

    assert path.read_bytes() == b'{"old":true}\n'
    assert names(tmp_path) == ["kept.jsonl"]


def test_lines_value_json_cannot_represent_creates_no_file(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not JSON compliant"):
        write_json_lines(tmp_path / "never.jsonl", [{"score": math.nan}])

    assert names(tmp_path) == []


def test_lines_value_json_cannot_represent_creates_no_directory(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not JSON compliant"):
        write_json_lines(tmp_path / "new" / "never.jsonl", [1, math.inf], parents=True)

    assert names(tmp_path) == []


@pytest.mark.parametrize("values", ["[1]", b"[1]", bytearray(b"1"), memoryview(b"1"), {"a": 1}])
def test_lines_writer_refuses_one_text_bytes_or_object(tmp_path: Path, values: Any) -> None:
    with pytest.raises(TypeError, match="needs an iterable of values"):
        write_json_lines(tmp_path / "never.jsonl", values)

    assert names(tmp_path) == []


def test_lines_writer_parents_and_keywords(tmp_path: Path) -> None:
    path = tmp_path / "new" / "rows.jsonl"

    with pytest.raises(FileNotFoundError):
        write_json_lines(path, [1])
    write_json_lines(path, [1], parents=True, durable=False)

    assert path.read_bytes() == b"1\n"
    parameters = inspect.signature(write_json_lines).parameters
    for name in ("sort_keys", "ensure_ascii", "durable", "parents"):
        assert parameters[name].kind is inspect.Parameter.KEYWORD_ONLY


def test_lines_writer_flushes_when_durable(tmp_path: Path, flushed: list[str]) -> None:
    write_json_lines(tmp_path / "rows.jsonl", [1, 2])
    assert flushed == ["file", "directory"]
    flushed.clear()

    write_json_lines(tmp_path / "rows.jsonl", [1, 2], durable=False)
    assert flushed == []


# --- the block writers: open_bytes_atomic, open_text_atomic, staged_path ---------------

BLOCKS = [open_bytes_atomic, open_text_atomic, staged_path]
# The path form repeats the suffix of the target after `.tmp`.
STAGED = re.compile(r"^\.(?P<name>.+)\.[0-9a-f]{16}\.tmp(?P<suffix>\..+)?$")


class PlantedError(Exception):
    """What the block of a test raises."""


@pytest.fixture
def at_rename(monkeypatch: pytest.MonkeyPatch) -> list[bytes]:
    """What the temporary file holds at the moment of each rename."""
    real_replace = os.replace
    seen: list[bytes] = []

    def reading_replace(source: str, destination: str) -> None:
        with open(source, "rb") as handle:
            seen.append(handle.read())
        real_replace(source, destination)

    monkeypatch.setattr(os, "replace", reading_replace)
    return seen


def raise_in_open_bytes(path: Path, error: BaseException) -> None:
    with open_bytes_atomic(path) as handle:
        handle.write(b"new content")
        handle.flush()
        raise error


def raise_in_open_text(path: Path, error: BaseException) -> None:
    with open_text_atomic(path) as text:
        text.write("new content")
        text.flush()
        raise error


def raise_in_staged_path(path: Path, error: BaseException) -> None:
    with staged_path(path) as temporary:
        produce(temporary, b"new content")
        raise error


RAISING_BLOCKS = [raise_in_open_bytes, raise_in_open_text, raise_in_staged_path]


def stage(path: Path | str, producer: Any, seen: list[str], **options: Any) -> None:
    """Run `producer` on the staged path and record that path in `seen`."""
    with staged_path(path, **options) as temporary:
        seen.append(temporary)
        producer(temporary)


def kept_file(tmp_path: Path) -> Path:
    path = tmp_path / "kept.txt"
    path.write_bytes(b"old content")
    path.chmod(0o600)
    return path


@pytest.mark.usefixtures("umask_027")
def test_open_bytes_writes_in_steps_into_a_temporary_file_beside_the_target(tmp_path: Path) -> None:
    path = tmp_path / "new.bin"

    with open_bytes_atomic(path) as handle:
        handle.write(b"\x00\x01")
        handle.write(b"content")
        handle.flush()
        (temporary,) = names(tmp_path)
        written = (tmp_path / temporary).read_bytes()

    match = TEMPORARY.match(temporary)
    assert match is not None
    assert match.group("name") == "new.bin"
    assert written == b"\x00\x01content"
    assert isinstance(handle, io.BufferedWriter)
    assert path.read_bytes() == b"\x00\x01content"
    assert mode(path) == 0o640
    assert names(tmp_path) == ["new.bin"]


def test_open_text_writes_utf8_with_the_line_endings_given(tmp_path: Path) -> None:
    path = tmp_path / "new.txt"

    with open_text_atomic(path) as text:
        text.write("Zeile 1\r\n")
        text.write("Zeile 2\nohne Ende: ä€")

    assert isinstance(text, io.TextIOWrapper)
    assert text.encoding == "utf-8"
    assert path.read_bytes() == "Zeile 1\r\nZeile 2\nohne Ende: ä€".encode()
    assert names(tmp_path) == ["new.txt"]


@pytest.mark.parametrize("writer", BLOCK_WRITERS)
@pytest.mark.parametrize("permissions", [0o600, 0o644, 0o755, 0o400])
def test_block_writer_replaces_an_existing_file_and_keeps_its_permissions(
    tmp_path: Path, writer: Any, permissions: int
) -> None:
    path = tmp_path / "old.bin"
    path.write_bytes(b"old content that is longer than the new one")
    path.chmod(permissions)

    writer(path)

    assert path.read_bytes() == b"new content"
    assert mode(path) == permissions
    assert names(tmp_path) == ["old.bin"]


def test_a_reader_sees_the_old_content_until_the_block_ends(tmp_path: Path) -> None:
    path = tmp_path / "read.txt"
    path.write_bytes(b"first")
    seen: list[bytes] = []

    with open_bytes_atomic(path) as handle:
        handle.write(b"second")
        handle.flush()
        seen.append(path.read_bytes())
    with open_text_atomic(path) as text:
        text.write("third")
        text.flush()
        seen.append(path.read_bytes())
    with staged_path(path) as temporary:
        produce(temporary, b"fourth")
        seen.append(path.read_bytes())

    assert seen == [b"first", b"second", b"third"]
    assert path.read_bytes() == b"fourth"


def test_bytes_still_buffered_when_the_block_ends_are_in_the_file_before_the_rename(
    tmp_path: Path, at_rename: list[bytes]
) -> None:
    with open_bytes_atomic(tmp_path / "buffered.bin") as handle:
        handle.write(b"short enough to stay in the buffer")
        (temporary,) = names(tmp_path)
        size_in_the_block = (tmp_path / temporary).stat().st_size

    assert size_in_the_block == 0
    assert at_rename == [b"short enough to stay in the buffer"]


def test_text_still_buffered_when_the_block_ends_is_in_the_file_before_the_rename(
    tmp_path: Path, at_rename: list[bytes]
) -> None:
    # The mistake a text producer makes on a binary file: it leaves text in its buffer.
    with open_text_atomic(tmp_path / "buffered.txt") as text:
        text.write("short enough to stay in the buffer: ä\n")
        (temporary,) = names(tmp_path)
        size_in_the_block = (tmp_path / temporary).stat().st_size

    assert size_in_the_block == 0
    assert at_rename == ["short enough to stay in the buffer: ä\n".encode()]


def test_a_block_may_close_the_file_it_was_lent(tmp_path: Path, flushed: list[str]) -> None:
    with open_bytes_atomic(tmp_path / "bytes") as handle, handle:
        handle.write(b"closed by the block")
    with open_text_atomic(tmp_path / "text") as text, text:
        text.write("closed by the block")
    with open_bytes_atomic(tmp_path / "wrapped") as handle, io.TextIOWrapper(handle, encoding="utf-8") as wrapper:
        wrapper.write("closed by a wrapper")

    assert (tmp_path / "bytes").read_bytes() == b"closed by the block"
    assert (tmp_path / "text").read_bytes() == b"closed by the block"
    assert (tmp_path / "wrapped").read_bytes() == b"closed by a wrapper"
    assert names(tmp_path) == ["bytes", "text", "wrapped"]
    assert flushed == ["file", "directory"] * 3


def test_the_lent_file_is_closed_when_the_block_has_ended(tmp_path: Path) -> None:
    with open_bytes_atomic(tmp_path / "bytes") as handle:
        handle.write(b"data")
    with open_text_atomic(tmp_path / "text") as text:
        text.write("data")

    assert handle.closed
    assert text.closed
    with pytest.raises(ValueError, match="closed file"):
        handle.write(b"late")
    with pytest.raises(ValueError, match="closed file"):
        text.write("late")
    assert (tmp_path / "bytes").read_bytes() == b"data"
    assert (tmp_path / "text").read_bytes() == b"data"


@pytest.mark.parametrize("block", [open_bytes_atomic, open_text_atomic])
def test_a_block_that_writes_nothing_makes_an_empty_file(tmp_path: Path, block: Any) -> None:
    with block(tmp_path / "empty"):
        pass

    assert (tmp_path / "empty").read_bytes() == b""
    assert names(tmp_path) == ["empty"]


@pytest.mark.parametrize("size", [1, io.DEFAULT_BUFFER_SIZE, 1024 * 1024 + 1])
def test_open_bytes_sizes_from_one_byte_to_many_buffers(tmp_path: Path, size: int) -> None:
    path = tmp_path / "sized.bin"
    data = bytes(index % 251 for index in range(size))

    with open_bytes_atomic(path) as handle:
        for start in range(0, size, 65521):
            handle.write(data[start : start + 65521])

    assert path.read_bytes() == data


@pytest.mark.parametrize("block", RAISING_BLOCKS)
@pytest.mark.parametrize("kind", [PlantedError, OSError, KeyboardInterrupt])
def test_an_error_in_the_block_leaves_the_old_file_whole_and_reaches_the_caller(
    tmp_path: Path, block: Any, kind: type[BaseException]
) -> None:
    path = kept_file(tmp_path)
    error = kind("planted")

    with pytest.raises(kind) as caught:
        block(path, error)

    assert caught.value is error
    assert path.read_bytes() == b"old content"
    assert mode(path) == 0o600
    assert names(tmp_path) == ["kept.txt"]


@pytest.mark.parametrize("block", RAISING_BLOCKS)
def test_an_error_in_the_block_when_creating_a_file_leaves_nothing(tmp_path: Path, block: Any) -> None:
    with pytest.raises(PlantedError):
        block(tmp_path / "never.txt", PlantedError())

    assert names(tmp_path) == []


@pytest.mark.parametrize("writer", BLOCK_WRITERS)
@pytest.mark.parametrize("step", ["fsync", "replace", "fchmod"])
@pytest.mark.parametrize("failure", [refuse, interrupt])
def test_block_writer_failure_at_any_step_leaves_the_old_file_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, writer: Any, step: str, failure: Any
) -> None:
    path = kept_file(tmp_path)
    monkeypatch.setattr(os, step, failure)

    with pytest.raises((OSError, KeyboardInterrupt)):
        writer(path)
    monkeypatch.undo()

    assert path.read_bytes() == b"old content"
    assert mode(path) == 0o600
    assert names(tmp_path) == ["kept.txt"]


@pytest.mark.parametrize("block", RAISING_BLOCKS)
def test_a_temporary_file_that_cannot_be_removed_is_reported_with_the_first_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, block: Any
) -> None:
    path = kept_file(tmp_path)
    monkeypatch.setattr(os, "unlink", refuse)

    with pytest.raises(OSError, match="planted: no space left on device") as caught:
        block(path, PlantedError("first"))
    monkeypatch.undo()

    assert isinstance(caught.value.__context__, PlantedError)
    assert path.read_bytes() == b"old content"
    (left,) = [name for name in names(tmp_path) if name != "kept.txt"]
    assert STAGED.match(left) is not None


def lowest_free_descriptor() -> int:
    probe = os.dup(0)
    os.close(probe)
    return probe


def test_block_writers_leave_no_descriptor_open(tmp_path: Path) -> None:
    path = kept_file(tmp_path)
    before = lowest_free_descriptor()

    for writer in BLOCK_WRITERS:
        writer(path)
    for block in RAISING_BLOCKS:
        with pytest.raises(PlantedError):
            block(path, PlantedError())
    with pytest.raises(ValueError, match="an empty file"):
        stage(path, functools.partial(produce, data=b""), [])
    with pytest.raises(ValueError, match="not a regular file"):
        stage(path, os.mkfifo, [])

    assert lowest_free_descriptor() == before
    assert names(tmp_path) == ["kept.txt"]


def test_text_that_cannot_be_encoded_in_a_block_leaves_the_old_file_whole(tmp_path: Path) -> None:
    path = kept_file(tmp_path)

    def write_a_lone_surrogate() -> None:
        with open_text_atomic(path) as text:
            text.write("fine until here\n")
            text.write("\ud800")

    with pytest.raises(UnicodeEncodeError):
        write_a_lone_surrogate()

    assert path.read_bytes() == b"old content"
    assert names(tmp_path) == ["kept.txt"]


def fail_with_a_full_buffer(block: Any, path: Path, content: Any, lent: list[Any]) -> None:
    with block(path) as handle:
        handle.write(content)
        lent.append((handle.fileno(), handle))
        raise PlantedError


@pytest.mark.parametrize(("block", "content"), [(open_bytes_atomic, b"lost"), (open_text_atomic, "lost")])
def test_what_a_failed_block_left_in_the_buffer_reaches_no_other_file(tmp_path: Path, block: Any, content: Any) -> None:
    lent: list[Any] = []
    with pytest.raises(PlantedError):
        fail_with_a_full_buffer(block, tmp_path / "never", content, lent)
    number, handle = lent.pop()
    closed = handle.closed

    # The descriptor of the temporary file is closed. Its number now names another file, and
    # the file object that was lent is thrown away with content still in its buffer.
    other = os.open(tmp_path / "other", os.O_WRONLY | os.O_CREAT, 0o600)
    if other != number:
        os.dup2(other, number)
        os.close(other)
    try:
        del handle
        gc.collect()
        reached_the_other_file = (tmp_path / "other").read_bytes()
    finally:
        os.close(number)

    assert reached_the_other_file == b""
    assert closed
    assert names(tmp_path) == ["other"]


def test_a_flush_that_fails_when_the_block_ends_leaves_the_old_file_whole(tmp_path: Path) -> None:
    path = kept_file(tmp_path)
    # The child may write four bytes to a file, not more: what the block left in the
    # buffer does not fit, and the error comes from the flush at the end of the block.
    code = """
import errno, resource, sys
from cistula import files

resource.setrlimit(resource.RLIMIT_FSIZE, (4, resource.getrlimit(resource.RLIMIT_FSIZE)[1]))
try:
    with files.open_bytes_atomic(sys.argv[1]) as handle:
        handle.write(b"new content")
except OSError as error:
    sys.exit(41 if error.errno == errno.EFBIG else 40)
"""
    done = subprocess.run([sys.executable, "-c", code, str(path)], check=False, timeout=60)

    assert done.returncode == 41
    assert path.read_bytes() == b"old content"
    assert names(tmp_path) == ["kept.txt"]


def test_a_block_may_change_the_current_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "start").mkdir()
    (tmp_path / "elsewhere").mkdir()
    monkeypatch.chdir(tmp_path / "start")

    with open_bytes_atomic("bytes") as handle:
        os.chdir(tmp_path / "elsewhere")
        handle.write(b"data")
    os.chdir(tmp_path / "start")
    with open_text_atomic("new/text", parents=True) as text:
        os.chdir(tmp_path / "elsewhere")
        text.write("data")
    os.chdir(tmp_path / "start")
    with staged_path("path") as temporary:
        os.chdir(tmp_path / "elsewhere")
        produce(temporary, b"data")

    assert os.path.isabs(temporary)
    assert names(tmp_path / "start") == ["bytes", "new", "path"]
    assert names(tmp_path / "start" / "new") == ["text"]
    assert names(tmp_path / "elsewhere") == []


def change_directory_and_fail(block: Any, elsewhere: Path) -> None:
    with block("file") as lent:
        if isinstance(lent, str):
            produce(lent, b"data")
        os.chdir(elsewhere)
        raise PlantedError


@pytest.mark.parametrize("block", BLOCKS)
def test_an_error_after_the_block_changed_the_directory_removes_the_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, block: Any
) -> None:
    (tmp_path / "start").mkdir()
    (tmp_path / "elsewhere").mkdir()
    monkeypatch.chdir(tmp_path / "start")

    with pytest.raises(PlantedError):
        change_directory_and_fail(block, tmp_path / "elsewhere")

    assert names(tmp_path / "start") == []
    assert names(tmp_path / "elsewhere") == []


@pytest.mark.parametrize("writer", BLOCK_WRITERS)
def test_block_writer_reads_a_relative_path_as_the_file_system_does(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, writer: Any
) -> None:
    (tmp_path / "start").mkdir()
    (tmp_path / "elsewhere" / "deep").mkdir(parents=True)
    (tmp_path / "start" / "link").symlink_to(tmp_path / "elsewhere" / "deep")
    monkeypatch.chdir(tmp_path / "start")

    # `link/..` is the directory above the target of the link, not the directory of the link.
    writer("link/../file")

    assert names(tmp_path / "start") == ["link"]
    assert names(tmp_path / "elsewhere") == ["deep", "file"]


@pytest.mark.parametrize("block", BLOCKS)
def test_block_writer_refuses_a_path_it_cannot_write_before_it_creates_anything(tmp_path: Path, block: Any) -> None:
    def enter(path: Any) -> None:
        with block(path, parents=True):
            pass

    with pytest.raises(ValueError, match="not a file path"):
        enter(f"{tmp_path}/absent/directory/")
    with pytest.raises(TypeError, match="path must be text"):
        enter(os.fsencode(tmp_path / "absent" / "file"))

    assert names(tmp_path) == []


@pytest.mark.parametrize("block", BLOCKS)
def test_nothing_happens_before_the_block_is_entered(tmp_path: Path, block: Any) -> None:
    block(tmp_path / "absent" / "file", parents=True, durable="not checked yet")

    assert names(tmp_path) == []


@pytest.mark.parametrize("block", BLOCKS)
def test_block_writers_take_only_the_path_by_position(block: Any) -> None:
    parameters = inspect.signature(block).parameters
    by_keyword = {name for name, parameter in parameters.items() if parameter.kind is inspect.Parameter.KEYWORD_ONLY}

    assert set(parameters) - by_keyword == {"path"}
    assert parameters["durable"].default is True
    assert parameters["parents"].default is False


# --- staged_path: the producer opens the file -----------------------------------------


@pytest.mark.parametrize(
    ("name", "suffix"),
    [("table.parquet", ".parquet"), ("archive.tar.gz", ".gz"), ("plain", None), (".hidden", None)],
)
def test_staged_path_names_a_missing_file_beside_the_target_with_its_suffix(
    tmp_path: Path, name: str, suffix: str | None
) -> None:
    with staged_path(tmp_path / name) as temporary:
        there_before = os.path.lexists(temporary) or names(tmp_path) != []
        produce(temporary, b"data")

    match = STAGED.match(os.path.basename(temporary))
    assert match is not None
    assert match.group("name") == name
    assert match.group("suffix") == suffix
    assert os.path.dirname(temporary) == str(tmp_path)
    assert not there_before
    assert (tmp_path / name).read_bytes() == b"data"
    assert names(tmp_path) == [name]


@pytest.mark.parametrize("options", [{}, {"allow_empty": True}])
def test_staged_path_refuses_a_producer_that_wrote_no_file(tmp_path: Path, options: dict[str, bool]) -> None:
    path = kept_file(tmp_path)
    seen: list[str] = []

    with pytest.raises(FileNotFoundError, match="nothing was written to the staged path") as caught:
        stage(path, len, seen, **options)

    assert caught.value.filename == seen[0]
    assert path.read_bytes() == b"old content"
    assert names(tmp_path) == ["kept.txt"]


def test_staged_path_refuses_an_empty_file(tmp_path: Path) -> None:
    path = kept_file(tmp_path)

    with pytest.raises(ValueError, match=r"an empty file at the staged path.*allow_empty=True"):
        stage(path, functools.partial(produce, data=b""), [])

    assert path.read_bytes() == b"old content"
    assert names(tmp_path) == ["kept.txt"]


def test_staged_path_refuses_the_file_of_a_producer_that_has_not_written_yet(tmp_path: Path) -> None:
    path = kept_file(tmp_path)
    unclosed: list[Any] = []

    def produce_without_closing(temporary: str) -> None:
        # The planted mistake: the producer leaves its file open, the content is still in its buffer.
        unclosed.append(open(temporary, "wb"))  # noqa: SIM115
        unclosed[0].write(b"still in the buffer of the producer")

    with pytest.raises(ValueError, match="an empty file at the staged path"):
        stage(path, produce_without_closing, [])
    unclosed[0].close()

    assert path.read_bytes() == b"old content"
    assert names(tmp_path) == ["kept.txt"]


def test_staged_path_cannot_see_what_a_producer_still_holds_in_a_file_that_is_not_empty(tmp_path: Path) -> None:
    # The limit the documentation states: only the producer knows whether it is done.
    path = tmp_path / "cut.bin"
    first = b"x" * io.DEFAULT_BUFFER_SIZE
    unclosed: list[Any] = []

    def produce_without_closing(temporary: str) -> None:
        unclosed.append(open(temporary, "wb"))  # noqa: SIM115
        unclosed[0].write(first)
        unclosed[0].flush()
        unclosed[0].write(b"the end")

    stage(path, produce_without_closing, [])
    published = path.read_bytes()
    unclosed[0].close()

    assert published == first
    assert path.read_bytes() == first + b"the end"


def test_allow_empty_publishes_an_empty_file(tmp_path: Path, flushed: list[str]) -> None:
    path = kept_file(tmp_path)

    stage(path, functools.partial(produce, data=b""), [], allow_empty=True)

    assert path.read_bytes() == b""
    assert mode(path) == 0o600
    assert names(tmp_path) == ["kept.txt"]
    assert flushed == ["file", "directory"]


@pytest.mark.parametrize("allow_empty", [None, 0, 1, "no", "False"])
def test_allow_empty_needs_true_or_false(tmp_path: Path, allow_empty: Any) -> None:
    seen: list[str] = []

    with pytest.raises(TypeError, match="allow_empty must be True or False"):
        stage(tmp_path / "absent" / "file", functools.partial(produce, data=b"data"), seen, allow_empty=allow_empty)

    assert seen == []
    assert names(tmp_path) == []


def test_allow_empty_is_a_keyword_only_and_off_by_default() -> None:
    parameter = inspect.signature(staged_path).parameters["allow_empty"]

    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert parameter.default is False


@pytest.mark.usefixtures("umask_027")
def test_staged_path_new_file_keeps_the_permissions_the_producer_gave_it(tmp_path: Path) -> None:
    def produce_private(temporary: str) -> None:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.write(descriptor, b"data")
        os.close(descriptor)

    stage(tmp_path / "private", produce_private, [])
    stage(tmp_path / "plain", functools.partial(produce, data=b"data"), [])

    assert mode(tmp_path / "private") == 0o600
    assert mode(tmp_path / "plain") == 0o640


def test_staged_path_refuses_a_symbolic_link_and_leaves_its_target_alone(tmp_path: Path) -> None:
    path = kept_file(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.write_bytes(b"content elsewhere")

    with pytest.raises(OSError, match="symbolic link") as caught:
        stage(path, functools.partial(os.symlink, elsewhere), [])

    assert caught.value.errno == errno.ELOOP
    assert path.read_bytes() == b"old content"
    assert not path.is_symlink()
    assert elsewhere.read_bytes() == b"content elsewhere"
    assert names(tmp_path) == ["elsewhere", "kept.txt"]


def test_staged_path_refuses_a_pipe_without_waiting_for_its_writer(tmp_path: Path) -> None:
    path = kept_file(tmp_path)

    with pytest.raises(ValueError, match="not a regular file at the staged path"):
        stage(path, os.mkfifo, [])

    assert path.read_bytes() == b"old content"
    assert names(tmp_path) == ["kept.txt"]


def test_staged_path_refuses_a_directory_and_does_not_remove_it(tmp_path: Path) -> None:
    path = kept_file(tmp_path)
    seen: list[str] = []

    with pytest.raises(ValueError, match="not a regular file at the staged path"):
        stage(path, os.mkdir, seen)

    assert path.read_bytes() == b"old content"
    assert names(tmp_path) == sorted([os.path.basename(seen[0]), "kept.txt"])
    assert os.path.isdir(seen[0])


def test_staged_path_serves_a_child_process_that_runs_in_another_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "work").mkdir()
    monkeypatch.chdir(tmp_path)
    code = "import sys\nwith open(sys.argv[1], 'wb') as handle:\n    handle.write(b'from the child')\n"

    with staged_path("out.bin") as temporary:
        run([sys.executable, "-c", code, temporary], timeout=60, cwd=tmp_path / "work", check=True)

    assert (tmp_path / "out.bin").read_bytes() == b"from the child"
    assert names(tmp_path) == ["out.bin", "work"]
    assert names(tmp_path / "work") == []


# --- a process killed inside a block ----------------------------------------------------

KILLED_IN_A_BLOCK = {
    "open_bytes_atomic": """
with files.open_bytes_atomic(sys.argv[1]) as handle:
    handle.write(b"new content")
    handle.flush()
    os.kill(os.getpid(), signal.SIGKILL)
""",
    "open_text_atomic": """
with files.open_text_atomic(sys.argv[1]) as text:
    text.write("new content")
    text.flush()
    os.kill(os.getpid(), signal.SIGKILL)
""",
    "staged_path": """
with files.staged_path(sys.argv[1]) as temporary:
    with open(temporary, "wb") as handle:
        handle.write(b"new content")
    os.kill(os.getpid(), signal.SIGKILL)
""",
}


@pytest.mark.parametrize("block", sorted(KILLED_IN_A_BLOCK))
def test_process_killed_inside_a_block_leaves_the_old_file_whole(tmp_path: Path, block: str) -> None:
    path = tmp_path / "survivor.txt"
    path.write_bytes(b"old content")
    code = "import os, signal, sys\nfrom cistula import files\n" + KILLED_IN_A_BLOCK[block]

    done = subprocess.run([sys.executable, "-c", code, str(path)], check=False, timeout=60)

    assert done.returncode == -signal.SIGKILL
    assert path.read_bytes() == b"old content"
    # The temporary file stays, under its documented name, and is in nobody's way.
    (left,) = [name for name in names(tmp_path) if name != "survivor.txt"]
    match = STAGED.match(left)
    assert match is not None
    assert match.group("name") == "survivor.txt"
    assert match.group("suffix") == (".txt" if block == "staged_path" else None)
    assert (tmp_path / left).read_bytes() == b"new content"

    write_as_staged_path(path)
    write_as_open_bytes(path)

    assert path.read_bytes() == b"new content"
    assert names(tmp_path) == sorted([left, "survivor.txt"])
