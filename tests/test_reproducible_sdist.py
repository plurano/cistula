# SPDX-FileCopyrightText: 2026 Lusoris <lusoris@proton.me>
# SPDX-License-Identifier: EUPL-1.2
from __future__ import annotations

import gzip
import io
import os
import struct
import subprocess
import sys
import tarfile
import zlib
from typing import TYPE_CHECKING, Any

import pytest

from tool_loader import load_tool, script

if TYPE_CHECKING:
    from pathlib import Path

sdist = load_tool("reproducible_sdist")

EPOCH = 1_791_684_146
BLOCK = 65535
HEADER = 10
BLOCK_HEADER = 5
TRAILER = 8

Member = tuple[tarfile.TarInfo, bytes]


def member(name: str, content: bytes = b"", **fields: Any) -> Member:
    """Return a member as a build host writes it: its own owner, its own clock, its own mode."""
    info = tarfile.TarInfo(name)
    info.size = len(content)
    host = {"mode": 0o600, "mtime": 1_600_000_000.25, "uid": 1000, "gid": 100, "uname": "builder", "gname": "users"}
    for field, value in {**host, **fields}.items():
        setattr(info, field, value)
    return info, content


def packed(*members: Member, level: int = 9, clock: int = 1_600_000_123) -> bytes:
    plain = io.BytesIO()
    with tarfile.open(fileobj=plain, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for info, content in members:
            archive.addfile(info, io.BytesIO(content))
    return gzip.compress(plain.getvalue(), compresslevel=level, mtime=clock)


def listing(data: bytes) -> list[dict[str, Any]]:
    rows = []
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        for info in archive.getmembers():
            content = archive.extractfile(info)
            rows.append(
                {
                    "name": info.name,
                    "type": info.type,
                    "mode": info.mode,
                    "mtime": info.mtime,
                    "owner": (info.uid, info.gid, info.uname, info.gname),
                    "extended": dict(info.pax_headers),
                    "content": None if content is None else content.read(),
                }
            )
    return rows


def one(data: bytes, name: str) -> dict[str, Any]:
    (row,) = [row for row in listing(data) if row["name"] == name]
    return row


TREE = (
    member("pkg-1", type=tarfile.DIRTYPE, mode=0o700),
    member("pkg-1/b.py", b"B = 2\n"),
    member("pkg-1/a.py", b"A = 1\n", mode=0o664),
    member("pkg-1/run.sh", b"#!/bin/sh\n", mode=0o700),
)


# The compression container.


@pytest.mark.parametrize("size", [0, 1, BLOCK - 1, BLOCK, BLOCK + 1, 2 * BLOCK, 2 * BLOCK + 1])
def test_stored_data_reads_back_and_takes_the_fewest_blocks(size: int) -> None:
    data = bytes(range(256)) * (size // 256) + bytes(size % 256)
    out = sdist.gzip_stored(data, EPOCH)
    assert gzip.decompress(out) == data
    blocks = max(1, -(-size // BLOCK))
    assert len(out) == HEADER + blocks * BLOCK_HEADER + size + TRAILER


def test_stored_data_is_in_the_output_as_it_is() -> None:
    data = b"compressible " * 1000
    assert data in sdist.gzip_stored(data, EPOCH)


def test_empty_data_gives_exactly_one_empty_final_block() -> None:
    expected = bytes.fromhex("1f8b08000100000000ff010000ffff0000000000000000")
    assert sdist.gzip_stored(b"", 1) == expected


@pytest.mark.parametrize("epoch", [1, EPOCH, 2**32 - 1])
def test_the_header_holds_the_date_and_nothing_of_the_host(epoch: int) -> None:
    out = sdist.gzip_stored(b"x", epoch)
    assert out[:4] == b"\x1f\x8b\x08\x00"  # gzip, deflate, no file name, no comment
    assert struct.unpack("<L", out[4:8]) == (epoch,)
    assert out[8:10] == b"\x00\xff"  # no compression level, operating system unknown
    assert struct.unpack("<LL", out[-8:]) == (zlib.crc32(b"x"), 1)


def test_only_the_last_block_is_marked_final() -> None:
    out = sdist.gzip_stored(bytes(BLOCK + 1), EPOCH)
    first, second = HEADER, HEADER + BLOCK_HEADER + BLOCK
    assert out[first : first + BLOCK_HEADER] == b"\x00\xff\xff\x00\x00"
    assert out[second : second + BLOCK_HEADER] == b"\x01\x01\x00\xfe\xff"


# What the rewrite fixes.


def test_two_hosts_give_the_same_bytes() -> None:
    here = packed(*TREE)
    there = packed(
        member("pkg-1/run.sh", b"#!/bin/sh\n", mode=0o755, uid=0, gid=0, uname="root", gname="root", mtime=7),
        member("pkg-1/a.py", b"A = 1\n", mode=0o644, uid=501, uname="other", mtime=1_700_000_000),
        member("pkg-1", type=tarfile.DIRTYPE, mode=0o755, mtime=1.5),
        member("pkg-1/b.py", b"B = 2\n", mode=0o640, pax_headers={"comment": "built there"}),
        level=1,
        clock=5,
    )
    assert here != there
    assert sdist.repack(here, EPOCH) == sdist.repack(there, EPOCH)


def test_a_rewritten_archive_is_rewritten_to_itself() -> None:
    once = sdist.repack(packed(*TREE), EPOCH)
    assert sdist.repack(once, EPOCH) == once


def test_members_are_sorted_by_name_and_keep_their_content() -> None:
    rows = listing(sdist.repack(packed(*TREE), EPOCH))
    assert [(row["name"], row["content"]) for row in rows] == [
        ("pkg-1", None),
        ("pkg-1/a.py", b"A = 1\n"),
        ("pkg-1/b.py", b"B = 2\n"),
        ("pkg-1/run.sh", b"#!/bin/sh\n"),
    ]
    assert [row["type"] for row in rows] == [tarfile.DIRTYPE, tarfile.REGTYPE, tarfile.REGTYPE, tarfile.REGTYPE]


def test_every_member_is_owned_by_nobody_and_dated_by_the_commit() -> None:
    for row in listing(sdist.repack(packed(*TREE), EPOCH)):
        assert row["owner"] == (0, 0, "", "")
        assert row["mtime"] == EPOCH
        assert isinstance(row["mtime"], int)
        assert row["extended"] == {}


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        (0o600, 0o644),
        (0o664, 0o644),
        (0o666, 0o644),
        (0o000, 0o644),
        (0o4644, 0o644),  # no set-user-id bit survives
        (0o677, 0o644),  # executable for others only: the owner's bit decides
        (0o100, 0o755),
        (0o700, 0o755),
        (0o4755, 0o755),
    ],
)
def test_a_file_mode_is_one_of_two(mode: int, expected: int) -> None:
    out = sdist.repack(packed(member("pkg-1/tool", b"x", mode=mode)), EPOCH)
    assert one(out, "pkg-1/tool")["mode"] == expected


@pytest.mark.parametrize("mode", [0o700, 0o755, 0o000, 0o644])
def test_a_directory_can_always_be_entered(mode: int) -> None:
    out = sdist.repack(packed(member("pkg-1", type=tarfile.DIRTYPE, mode=mode)), EPOCH)
    assert one(out, "pkg-1")["mode"] == 0o755


def test_the_content_and_the_date_still_change_the_bytes() -> None:
    base = sdist.repack(packed(member("pkg-1/a.py", b"A = 1\n")), EPOCH)
    assert sdist.repack(packed(member("pkg-1/a.py", b"A = 2\n")), EPOCH) != base
    assert sdist.repack(packed(member("pkg-1/b.py", b"A = 1\n")), EPOCH) != base
    assert sdist.repack(packed(member("pkg-1/a.py", b"A = 1\n")), EPOCH + 1) != base


def test_a_long_name_and_a_name_outside_ascii_are_kept() -> None:
    long = "pkg-1/" + "d" * 120 + "/module.py"
    names = sorted([long, "pkg-1/größe.py"])
    here = packed(*(member(name, b"x") for name in names))
    there = packed(*(member(name, b"x", mtime=3, uname="other") for name in reversed(names)))
    out = sdist.repack(here, EPOCH)
    assert [row["name"] for row in listing(out)] == names
    assert out == sdist.repack(there, EPOCH)


def test_an_archive_without_members_stays_one() -> None:
    out = sdist.repack(packed(), EPOCH)
    assert listing(out) == []
    assert out == sdist.repack(packed(level=1, clock=9), EPOCH)


def test_a_large_member_spans_stored_blocks_and_reads_back() -> None:
    content = bytes(range(256)) * 600
    out = sdist.repack(packed(member("pkg-1/data.bin", content)), EPOCH)
    assert one(out, "pkg-1/data.bin")["content"] == content
    assert len(out) > len(content) + 2 * BLOCK_HEADER


def test_the_archive_is_in_the_pax_format_of_the_packaging_specification() -> None:
    long = "pkg-1/" + "d" * 120 + "/module.py"
    plain = gzip.decompress(sdist.repack(packed(member("pkg-1/a.py", b"x"), member(long, b"x")), EPOCH))
    assert plain[257:265] == b"ustar\x0000"
    assert plain[156:157] == tarfile.REGTYPE  # a short name needs no extended header before it
    assert plain[1024 + 156 : 1024 + 157] == tarfile.XHDTYPE  # the long name is in one


# What the rewrite refuses.


@pytest.mark.parametrize("kind", [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE, tarfile.CHRTYPE])
def test_a_member_that_is_neither_file_nor_directory_is_refused(kind: bytes) -> None:
    source = packed(member("pkg-1/a.py", b"x"), member("pkg-1/link", type=kind, linkname="a.py"))
    with pytest.raises(ValueError, match=r"^pkg-1/link is neither a file nor a directory"):
        sdist.repack(source, EPOCH)


def test_a_name_that_occurs_twice_is_refused() -> None:
    source = packed(member("pkg-1/a.py", b"first"), member("pkg-1/b.py"), member("pkg-1/a.py", b"second"))
    with pytest.raises(ValueError, match="a member name occurs more than once"):
        sdist.repack(source, EPOCH)


@pytest.mark.parametrize("epoch", [0, -1, 2**32, True, 1.0, "1", None])
def test_a_date_outside_the_header_field_is_refused(epoch: Any) -> None:
    with pytest.raises(ValueError, match="the date must be a whole number of seconds from 1 to 4294967295"):
        sdist.repack(packed(*TREE), epoch)


@pytest.mark.parametrize("epoch", [1, 2**32 - 1])
def test_the_first_and_the_last_date_of_the_header_field_are_accepted(epoch: int) -> None:
    out = sdist.repack(packed(*TREE), epoch)
    assert {row["mtime"] for row in listing(out)} == {epoch}
    assert struct.unpack("<L", out[4:8]) == (epoch,)


def test_data_that_is_not_compressed_is_refused() -> None:
    with pytest.raises(gzip.BadGzipFile):
        sdist.repack(b"not an archive", EPOCH)


def test_compressed_data_that_is_no_archive_is_refused() -> None:
    with pytest.raises(tarfile.TarError):
        sdist.repack(gzip.compress(b"not an archive" * 100), EPOCH)


def test_a_wrong_checksum_at_the_end_is_refused() -> None:
    whole = packed(*TREE)
    damaged = whole[:-8] + bytes([whole[-8] ^ 1]) + whole[-7:]
    with pytest.raises(gzip.BadGzipFile):
        sdist.repack(damaged, EPOCH)


# The script.


def write(tmp_path: Path, data: bytes) -> str:
    path = tmp_path / "pkg-1.tar.gz"
    path.write_bytes(data)
    return str(path)


def test_the_script_rewrites_the_file_in_place(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = packed(*TREE)
    path = write(tmp_path, source)
    monkeypatch.setenv("SOURCE_DATE_EPOCH", str(EPOCH))
    assert sdist.main([path]) == 0
    with open(path, "rb") as file:
        assert file.read() == sdist.repack(source, EPOCH)
    assert os.listdir(tmp_path) == ["pkg-1.tar.gz"]


@pytest.mark.parametrize("value", [None, "", "now", "-1", "1.5", " 1", "1 ", "0", "4294967296", chr(0x661)])
def test_the_script_never_takes_the_clock_for_a_missing_date(
    value: str | None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    source = packed(*TREE)
    path = write(tmp_path, source)
    if value is None:
        monkeypatch.delenv("SOURCE_DATE_EPOCH", raising=False)
    else:
        monkeypatch.setenv("SOURCE_DATE_EPOCH", value)
    assert sdist.main([path]) == 1
    assert capsys.readouterr().err.startswith(f"could not rewrite {path}: ")
    with open(path, "rb") as file:
        assert file.read() == source
    assert os.listdir(tmp_path) == ["pkg-1.tar.gz"]


def test_the_script_names_the_variable_it_misses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("SOURCE_DATE_EPOCH", raising=False)
    assert sdist.main([write(tmp_path, packed(*TREE))]) == 1
    assert "SOURCE_DATE_EPOCH must be set to a whole number of seconds, found ''" in capsys.readouterr().err


@pytest.mark.parametrize("keep", [0, 9, 40, -9, -1])
def test_a_cut_off_archive_fails_and_is_left_as_it_was(
    keep: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    whole = packed(*TREE)
    source = whole[:keep]
    path = write(tmp_path, source)
    monkeypatch.setenv("SOURCE_DATE_EPOCH", str(EPOCH))
    assert sdist.main([path]) == 1
    assert capsys.readouterr().err.startswith(f"could not rewrite {path}: ")
    with open(path, "rb") as file:
        assert file.read() == source
    assert os.listdir(tmp_path) == ["pkg-1.tar.gz"]


def test_compressed_data_the_format_does_not_allow_fails_the_script(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    source = sdist.gzip_stored(b"", EPOCH)[:HEADER] + b"\x07" + bytes(20)
    path = write(tmp_path, source)
    monkeypatch.setenv("SOURCE_DATE_EPOCH", str(EPOCH))
    assert sdist.main([path]) == 1
    assert capsys.readouterr().err.startswith(f"could not rewrite {path}: ")
    with open(path, "rb") as file:
        assert file.read() == source


def test_a_refused_member_fails_the_script(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = write(tmp_path, packed(member("pkg-1/link", type=tarfile.SYMTYPE, linkname="a.py")))
    monkeypatch.setenv("SOURCE_DATE_EPOCH", str(EPOCH))
    assert sdist.main([path]) == 1
    assert "pkg-1/link is neither a file nor a directory" in capsys.readouterr().err


def test_a_file_that_is_not_there_fails_the_script(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("SOURCE_DATE_EPOCH", str(EPOCH))
    assert sdist.main([str(tmp_path / "missing.tar.gz")]) == 1
    assert "could not rewrite" in capsys.readouterr().err
    assert os.listdir(tmp_path) == []


@pytest.mark.parametrize("count", [0, 2])
def test_the_script_takes_exactly_one_file(count: int, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = write(tmp_path, packed(*TREE))
    assert sdist.main([path] * count) == 2
    assert capsys.readouterr().err == "usage: reproducible_sdist.py FILE\n"


def run(path: str, epoch: str | None) -> subprocess.CompletedProcess[str]:
    environment = {name: value for name, value in os.environ.items() if name != "SOURCE_DATE_EPOCH"}
    if epoch is not None:
        environment["SOURCE_DATE_EPOCH"] = epoch
    command = [sys.executable, "-I", script("reproducible_sdist"), path]
    return subprocess.run(command, capture_output=True, text=True, timeout=60, env=environment, check=False)


def test_the_script_run_as_the_gate_runs_it(tmp_path: Path) -> None:
    source = packed(*TREE)
    path = write(tmp_path, source)
    done = run(path, str(EPOCH))
    assert (done.returncode, done.stdout, done.stderr) == (0, "", "")
    with open(path, "rb") as file:
        assert file.read() == sdist.repack(source, EPOCH)


def test_the_script_run_without_a_date_exits_1_and_changes_nothing(tmp_path: Path) -> None:
    source = packed(*TREE)
    path = write(tmp_path, source)
    done = run(path, None)
    assert done.returncode == 1
    assert "SOURCE_DATE_EPOCH must be set" in done.stderr
    with open(path, "rb") as file:
        assert file.read() == source
