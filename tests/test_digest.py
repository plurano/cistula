# SPDX-FileCopyrightText: 2026 Lusoris <lusoris@proton.me>
# SPDX-License-Identifier: EUPL-1.2
from __future__ import annotations

import hashlib
import tracemalloc
from typing import TYPE_CHECKING, Any

import pytest

from cistula.digest import sha256_bytes, sha256_file, sha256_text

if TYPE_CHECKING:
    from pathlib import Path

# Published test vectors (FIPS 180-4 examples).
EMPTY = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
ABC = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
TWO_BLOCKS = "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1"

# The standard library reads a file in pieces of this size.
PIECE = 2**18


def test_bytes_vectors() -> None:
    assert sha256_bytes(b"") == EMPTY
    assert sha256_bytes(b"abc") == ABC
    assert sha256_bytes(b"abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq") == TWO_BLOCKS


def test_bytes_like_types_give_the_same_digest() -> None:
    assert sha256_bytes(bytearray(b"abc")) == ABC
    assert sha256_bytes(memoryview(b"xabcx")[1:4]) == ABC


@pytest.mark.parametrize("value", ["abc", None, 5, ["abc"]])
def test_bytes_digest_refuses_other_types(value: Any) -> None:
    with pytest.raises(TypeError, match="needs bytes"):
        sha256_bytes(value)


def test_text_vectors_and_utf8() -> None:
    assert sha256_text("") == EMPTY
    assert sha256_text("abc") == ABC
    assert sha256_text("Zürich") == sha256_bytes("Zürich".encode())
    assert sha256_text("Zürich") != sha256_bytes("Zürich".encode("latin-1"))


@pytest.mark.parametrize("value", [b"abc", None, 5])
def test_text_digest_refuses_other_types(value: Any) -> None:
    with pytest.raises(TypeError, match="needs text"):
        sha256_text(value)


def test_text_that_cannot_be_encoded_is_refused() -> None:
    with pytest.raises(UnicodeEncodeError):
        sha256_text("lone surrogate \ud800")


def test_file_vectors(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.write_bytes(b"")
    abc = tmp_path / "abc"
    abc.write_bytes(b"abc")

    assert sha256_file(empty) == EMPTY
    assert sha256_file(abc) == ABC
    assert sha256_file(str(abc)) == ABC


@pytest.mark.parametrize("size", [1, PIECE - 1, PIECE, PIECE + 1, 3 * PIECE + 5])
def test_file_sizes_around_one_piece(tmp_path: Path, size: int) -> None:
    data = bytes(index % 251 for index in range(size))
    path = tmp_path / "sized"
    path.write_bytes(data)

    digest = sha256_file(path)

    assert digest == hashlib.sha256(data).hexdigest()
    assert len(digest) == 64
    assert digest == digest.lower()


def test_file_is_read_in_pieces_not_whole(tmp_path: Path) -> None:
    size = 64 * 1024 * 1024
    path = tmp_path / "large"
    with path.open("wb") as handle:
        handle.truncate(size)

    tracemalloc.start()
    try:
        digest = sha256_file(path)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert digest == hashlib.sha256(bytes(size)).hexdigest()
    assert peak < size // 8


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        sha256_file(tmp_path / "absent")


def test_directory(tmp_path: Path) -> None:
    with pytest.raises(IsADirectoryError):
        sha256_file(tmp_path)
