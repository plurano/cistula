# SPDX-FileCopyrightText: 2026 Lusoris <lusoris@proton.me>
# SPDX-License-Identifier: EUPL-1.2
from __future__ import annotations

import hashlib
import io
import os
import struct
import subprocess
import sys
import tarfile
import zipfile
import zlib
from typing import TYPE_CHECKING, Any

import pytest

from tool_loader import load_tool, script

if TYPE_CHECKING:
    from pathlib import Path

compare = load_tool("compare_packages")

WHEEL = "pkg-1-py3-none-any.whl"
SDIST = "pkg-1.tar.gz"
DATE = (2026, 10, 11, 4, 2, 26)
SOURCE = b"VALUE = 1\n" * 50

WheelMember = tuple[str, bytes, dict[str, Any]]


def wheel_member(name: str, content: bytes = SOURCE, **fields: Any) -> WheelMember:
    return name, content, {"date": DATE, "mode": 0o100644, "compression": zipfile.ZIP_STORED, **fields}


def wheel(*members: WheelMember, comment: bytes = b"") -> bytes:
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        for name, content, fields in members:
            info = zipfile.ZipInfo(name, date_time=fields["date"])
            info.external_attr = fields["mode"] << 16
            info.compress_type = fields["compression"]
            info.extra = fields.get("extra", b"")
            archive.writestr(info, content)
        archive.comment = comment
    return data.getvalue()


def gz(data: bytes, *, level: int = 0, clock: int = 1) -> bytes:
    """Return `data` in the gzip format with a header that depends on `clock` only."""
    deflate = zlib.compressobj(level, zlib.DEFLATED, -15)
    body = deflate.compress(data) + deflate.flush()
    header = b"\x1f\x8b\x08\x00" + struct.pack("<L", clock) + b"\x00\xff"
    return header + body + struct.pack("<LL", zlib.crc32(data), len(data))


def sdist_member(name: str, content: bytes = SOURCE, **fields: Any) -> tuple[tarfile.TarInfo, bytes]:
    info = tarfile.TarInfo(name)
    info.size = len(content)
    for field, value in {"mode": 0o644, "mtime": 1_791_684_146, **fields}.items():
        setattr(info, field, value)
    return info, content


def sdist(*members: tuple[tarfile.TarInfo, bytes], level: int = 0, clock: int = 1) -> bytes:
    plain = io.BytesIO()
    with tarfile.open(fileobj=plain, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for info, content in members:
            archive.addfile(info, io.BytesIO(content))
    return gz(plain.getvalue(), level=level, clock=clock)


def build(root: Path, name: str, files: dict[str, bytes]) -> str:
    directory = root / name
    directory.mkdir()
    for file, data in files.items():
        (directory / file).write_bytes(data)
    return str(directory)


def found(tmp_path: Path, name: str, first: bytes, second: bytes) -> list[str]:
    """Return the report for one file that two builds hold with these bytes."""
    root = tmp_path / f"pair{len(os.listdir(tmp_path))}"
    root.mkdir()
    one, two = build(root, "one", {name: first}), build(root, "two", {name: second})
    lines: list[str] = compare.differences(one, two)
    if lines:
        digests = (hashlib.sha256(first).hexdigest(), hashlib.sha256(second).hexdigest())
        assert lines[0] == f"{name}: sha256 {digests[0]} in {one}, {digests[1]} in {two}"
    return lines[1:]


BASE = (wheel_member("pkg/__init__.py"), wheel_member("pkg-1.dist-info/RECORD", b"record\n"))
TREE = (sdist_member("pkg-1/a.py"), sdist_member("pkg-1/b.py", b"B = 2\n"))


# Builds that agree.


def test_two_builds_with_the_same_bytes_agree(tmp_path: Path) -> None:
    files = {WHEEL: wheel(*BASE), SDIST: sdist(*TREE)}
    assert compare.differences(build(tmp_path, "one", files), build(tmp_path, "two", files)) == []


def test_a_build_agrees_with_itself(tmp_path: Path) -> None:
    one = build(tmp_path, "one", {WHEEL: wheel(*BASE)})
    assert compare.differences(one, one) == []


def test_a_directory_beside_the_built_files_is_not_a_built_file(tmp_path: Path) -> None:
    files = {WHEEL: wheel(*BASE)}
    one, two = build(tmp_path, "one", files), build(tmp_path, "two", files)
    os.mkdir(os.path.join(two, "unpacked"))
    assert compare.differences(one, two) == []


# Wheels that differ: the report names the member and the field.


@pytest.mark.parametrize(
    ("change", "fields"),
    [
        ({"date": (2026, 10, 11, 4, 2, 28)}, "time stamp"),
        ({"mode": 0o100600}, "file mode"),
        ({"mode": 0o100755}, "file mode"),
        ({"compression": zipfile.ZIP_DEFLATED}, "compressed size, compression"),
        ({"extra": struct.pack("<HH2s", 0xCAFE, 2, b"ab")}, "extra field"),
    ],
)
def test_a_wheel_member_recorded_differently_is_named_with_the_field(
    change: dict[str, Any], fields: str, tmp_path: Path
) -> None:
    other = (wheel_member("pkg/__init__.py", **change), BASE[1])
    assert found(tmp_path, WHEEL, wheel(*BASE), wheel(*other)) == [f"  pkg/__init__.py: {fields}"]


def test_a_wheel_member_with_other_content_is_named(tmp_path: Path) -> None:
    other = (wheel_member("pkg/__init__.py", SOURCE.replace(b"1", b"2")), BASE[1])
    assert found(tmp_path, WHEEL, wheel(*BASE), wheel(*other)) == ["  pkg/__init__.py: content"]


def test_wheel_members_in_another_order_are_named(tmp_path: Path) -> None:
    assert found(tmp_path, WHEEL, wheel(*BASE), wheel(*reversed(BASE))) == [
        "  pkg-1.dist-info/RECORD: position",
        "  pkg/__init__.py: position",
    ]


def test_a_wheel_member_in_one_build_only_is_named(tmp_path: Path) -> None:
    more = (*BASE, wheel_member("pkg/extra.py"))
    assert found(tmp_path, WHEEL, wheel(*BASE), wheel(*more)) == ["  member only in one build: pkg/extra.py"]
    assert found(tmp_path, WHEEL, wheel(*more), wheel(*BASE)) == ["  member only in one build: pkg/extra.py"]


def test_wheels_that_differ_outside_their_members_are_reported(tmp_path: Path) -> None:
    assert found(tmp_path, WHEEL, wheel(*BASE), wheel(*BASE, comment=b"built there")) == [
        "  the members agree; the bytes that hold them differ"
    ]


# Source distributions that differ.


@pytest.mark.parametrize(
    ("change", "fields"),
    [
        ({"mtime": 1_791_684_147}, "time stamp"),
        ({"mtime": 1_791_684_146.5}, "time stamp, extended headers"),
        ({"mode": 0o600}, "file mode"),
        ({"uid": 1000}, "owner"),
        ({"gid": 100}, "owner"),
        ({"uname": "builder"}, "owner"),
        ({"gname": "users"}, "owner"),
        ({"type": tarfile.SYMTYPE, "size": 0, "linkname": "b.py"}, "content, type, link target"),
        ({"type": tarfile.SYMTYPE, "size": 0, "linkname": "nowhere"}, "content, type, link target"),
        ({"type": tarfile.DIRTYPE, "size": 0}, "content, type"),
        ({"pax_headers": {"comment": "built there"}}, "extended headers"),
    ],
)
def test_a_source_member_recorded_differently_is_named_with_the_field(
    change: dict[str, Any], fields: str, tmp_path: Path
) -> None:
    content = b"" if "size" in change else SOURCE
    other = (sdist_member("pkg-1/a.py", content, **change), TREE[1])
    assert found(tmp_path, SDIST, sdist(*TREE), sdist(*other)) == [f"  pkg-1/a.py: {fields}"]


def test_a_source_member_with_other_content_is_named(tmp_path: Path) -> None:
    other = (sdist_member("pkg-1/a.py", SOURCE.replace(b"1", b"2")), TREE[1])
    assert found(tmp_path, SDIST, sdist(*TREE), sdist(*other)) == ["  pkg-1/a.py: content"]


def test_source_members_in_another_order_are_named(tmp_path: Path) -> None:
    assert found(tmp_path, SDIST, sdist(*TREE), sdist(*reversed(TREE))) == [
        "  pkg-1/a.py: position",
        "  pkg-1/b.py: position",
    ]


def test_a_source_member_in_one_build_only_is_named(tmp_path: Path) -> None:
    more = (*TREE, sdist_member("pkg-1/c.py"))
    assert found(tmp_path, SDIST, sdist(*TREE), sdist(*more)) == ["  member only in one build: pkg-1/c.py"]


def test_the_time_in_the_compression_header_is_named(tmp_path: Path) -> None:
    assert found(tmp_path, SDIST, sdist(*TREE, clock=1), sdist(*TREE, clock=2)) == ["  (gzip header): bytes"]


def test_the_same_members_compressed_differently_are_reported(tmp_path: Path) -> None:
    stored, deflated = sdist(*TREE, level=0), sdist(*TREE, level=9)
    assert stored != deflated
    assert found(tmp_path, SDIST, stored, deflated) == ["  the members agree; the bytes that hold them differ"]


@pytest.mark.parametrize(("differing", "tail"), [(19, []), (20, []), (21, ["  and 1 more"]), (45, ["  and 25 more"])])
def test_a_long_report_is_cut_after_twenty_members(differing: int, tail: list[str], tmp_path: Path) -> None:
    names = [f"pkg-1/m{index:02}.py" for index in range(differing)]
    here = sdist(*(sdist_member(name) for name in names))
    there = sdist(*(sdist_member(name, mode=0o600) for name in names))
    assert found(tmp_path, SDIST, here, there) == [f"  {name}: file mode" for name in names[:20]] + tail


# Files that differ as a whole.


def test_a_file_in_one_build_only_is_named(tmp_path: Path) -> None:
    both = {WHEEL: wheel(*BASE)}
    one, two = build(tmp_path, "one", both), build(tmp_path, "two", {**both, SDIST: sdist(*TREE)})
    assert compare.differences(one, two) == [f"{SDIST}: only in one build"]
    assert compare.differences(two, one) == [f"{SDIST}: only in one build"]


def test_a_build_without_files_is_no_build(tmp_path: Path) -> None:
    empty, full = build(tmp_path, "one", {}), build(tmp_path, "two", {WHEEL: wheel(*BASE)})
    assert compare.differences(empty, full) == [f"{empty} holds no built file"]
    assert compare.differences(empty, empty) == [f"{empty} holds no built file"]
    assert compare.differences(full, empty) == [f"{WHEEL}: only in one build"]


def test_a_file_that_is_no_archive_is_compared_by_its_digest_alone(tmp_path: Path) -> None:
    assert found(tmp_path, "notes.txt", b"one\n", b"two\n") == []


def test_every_differing_file_is_reported(tmp_path: Path) -> None:
    one = build(tmp_path, "one", {WHEEL: wheel(*BASE), SDIST: sdist(*TREE)})
    two = build(tmp_path, "two", {WHEEL: wheel(*BASE, comment=b"x"), SDIST: sdist(*TREE, clock=2)})
    lines = compare.differences(one, two)
    assert [line for line in lines if line.startswith("  ")] == [
        "  the members agree; the bytes that hold them differ",
        "  (gzip header): bytes",
    ]
    assert [line.split(":")[0] for line in lines if not line.startswith("  ")] == [WHEEL, SDIST]


# The script.


def test_the_script_passes_on_equal_builds(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    files = {WHEEL: wheel(*BASE), SDIST: sdist(*TREE)}
    one, two = build(tmp_path, "one", files), build(tmp_path, "two", files)
    assert compare.main([one, two]) == 0
    assert capsys.readouterr() == (f"PASS: {two} holds the same bytes as {one}.\n", "")


def test_the_script_fails_on_one_differing_byte(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    data = wheel(*BASE)
    other = data[:-1] + bytes([data[-1] ^ 1])
    one, two = build(tmp_path, "one", {WHEEL: data}), build(tmp_path, "two", {WHEEL: other})
    assert compare.main([one, two]) == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert err.startswith(f"FAIL: the build in {two} is not the build in {one}.\n{WHEEL}: sha256 ")
    assert err.endswith("\n")


def test_the_script_fails_on_a_build_without_files(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    one, two = build(tmp_path, "one", {}), build(tmp_path, "two", {})
    assert compare.main([one, two]) == 1
    assert capsys.readouterr().err.endswith(f"{one} holds no built file\n")


@pytest.mark.parametrize("count", [0, 1, 3])
def test_the_script_takes_exactly_two_directories(
    count: int, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    one = build(tmp_path, "one", {WHEEL: wheel(*BASE)})
    assert compare.main([one] * count) == 2
    assert capsys.readouterr() == ("", "usage: compare_packages.py DIRECTORY DIRECTORY\n")


@pytest.mark.parametrize("missing", ["one", "two"])
def test_a_directory_that_is_not_there_is_no_pass(
    missing: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    present = build(tmp_path, "present", {WHEEL: wheel(*BASE)})
    absent = str(tmp_path / "absent")
    pair = [absent, present] if missing == "one" else [present, absent]
    assert compare.main(pair) == 2
    out, err = capsys.readouterr()
    assert out == ""
    assert err.startswith(f"could not compare {pair[0]} and {pair[1]}: ")


DAMAGED = [
    (WHEEL, b"cut off"),
    (WHEEL, wheel(*BASE)[:40]),
    (SDIST, b"cut off"),
    (SDIST, b""),
    (SDIST, sdist(*TREE, level=9)[:40]),  # ends inside the compressed data
    (SDIST, gz(b"")[:10] + b"\x07" + bytes(20)),  # a block of a kind the format does not have
]


@pytest.mark.parametrize(("name", "damaged"), DAMAGED)
def test_a_differing_file_that_cannot_be_read_as_an_archive_is_no_pass(
    name: str, damaged: bytes, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    whole = wheel(*BASE) if name == WHEEL else sdist(*TREE)
    one, two = build(tmp_path, "one", {name: whole}), build(tmp_path, "two", {name: damaged})
    assert compare.main([one, two]) == 2
    out, err = capsys.readouterr()
    assert out == ""
    assert err.startswith(f"could not compare {one} and {two}: ")


def run(*arguments: str) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, "-I", script("compare_packages"), *arguments]
    return subprocess.run(command, capture_output=True, text=True, timeout=60, check=False)


def test_the_script_run_as_the_gate_runs_it(tmp_path: Path) -> None:
    one = build(tmp_path, "one", {SDIST: sdist(*TREE)})
    two = build(tmp_path, "two", {SDIST: sdist(*TREE)})
    three = build(tmp_path, "three", {SDIST: sdist(*TREE, clock=2)})
    done = run(one, two)
    assert (done.returncode, done.stdout, done.stderr) == (0, f"PASS: {two} holds the same bytes as {one}.\n", "")
    done = run(one, three)
    assert done.returncode == 1
    assert done.stdout == ""
    assert done.stderr.splitlines()[0] == f"FAIL: the build in {three} is not the build in {one}."
    assert done.stderr.splitlines()[-1] == "  (gzip header): bytes"
