# SPDX-FileCopyrightText: 2026 Lusoris <lusoris@proton.me>
# SPDX-License-Identifier: EUPL-1.2
"""Rewrite a source distribution so that its bytes depend on its content only.

Usage: reproducible_sdist.py FILE

The build backend records in the archive what it finds on the building host:
the time of the build, the owner and the mode of each file, and it leaves the
compression to the library the interpreter happens to be linked against. This
script writes the same members again with all of that fixed: sorted by name,
owned by nobody, mode 644 (755 for directories and executables), dated
SOURCE_DATE_EPOCH, and stored without compression, because two compression
libraries give two different outputs for the same input.

SOURCE_DATE_EPOCH must be set; the wall clock is never used in its place.
"""

from __future__ import annotations

import gzip
import io
import os
import struct
import sys
import tarfile
import zlib

# Largest block the deflate format can store without compressing it.
STORED_BLOCK = 65535

# The time field of the gzip header is 32 bits wide; 0 means "no time".
MAX_EPOCH = 2**32 - 1

_FILE_MODE = 0o644
_EXECUTABLE_MODE = 0o755


def gzip_stored(data: bytes, epoch: int) -> bytes:
    """Return `data` in the gzip format with every block stored, not compressed.

    The header carries `epoch`, no file name and no operating system, so the
    output is the same on every host and with every compression library.
    """
    parts = [b"\x1f\x8b\x08\x00", struct.pack("<L", epoch), b"\x00\xff"]
    starts = range(0, len(data), STORED_BLOCK) if data else range(1)
    for start in starts:
        block = data[start : start + STORED_BLOCK]
        last = start + STORED_BLOCK >= len(data)
        parts.append(struct.pack("<BHH", last, len(block), len(block) ^ 0xFFFF))
        parts.append(block)
    parts.append(struct.pack("<LL", zlib.crc32(data), len(data) % 2**32))
    return b"".join(parts)


def _fixed(member: tarfile.TarInfo, epoch: int) -> tarfile.TarInfo:
    """Return a member of the same name, kind and size with nothing of the host in it."""
    if not (member.isreg() or member.isdir()):
        message = f"{member.name} is neither a file nor a directory; a source distribution holds nothing else"
        raise ValueError(message)
    fixed = tarfile.TarInfo(member.name)
    fixed.type = tarfile.DIRTYPE if member.isdir() else tarfile.REGTYPE
    fixed.size = member.size
    fixed.mtime = epoch
    fixed.mode = _EXECUTABLE_MODE if member.isdir() or member.mode & 0o100 else _FILE_MODE
    return fixed


def repack(source: bytes, epoch: int) -> bytes:
    """Return the source distribution `source` rewritten with fixed metadata."""
    if isinstance(epoch, bool) or not isinstance(epoch, int) or not 0 < epoch <= MAX_EPOCH:
        message = f"the date must be a whole number of seconds from 1 to {MAX_EPOCH}, not {epoch!r}"
        raise ValueError(message)
    # Unpacked as a whole first: that checks the length and the checksum at
    # the end of the compressed data, which reading member by member stops
    # short of, so a cut-off file is refused and not quietly made whole.
    unpacked = io.BytesIO(gzip.decompress(source))
    plain = io.BytesIO()
    with (
        tarfile.open(fileobj=unpacked, mode="r:") as old,
        tarfile.open(fileobj=plain, mode="w", format=tarfile.PAX_FORMAT) as new,
    ):
        members = sorted(old.getmembers(), key=lambda member: member.name)
        names = [member.name for member in members]
        if len(set(names)) != len(names):
            message = "a member name occurs more than once"
            raise ValueError(message)
        for member in members:
            new.addfile(_fixed(member, epoch), old.extractfile(member))
    return gzip_stored(plain.getvalue(), epoch)


def _epoch() -> int:
    """Return SOURCE_DATE_EPOCH as a number; refuse to go on without it."""
    text = os.environ.get("SOURCE_DATE_EPOCH", "")
    if not text.isascii() or not text.isdigit():
        message = f"SOURCE_DATE_EPOCH must be set to a whole number of seconds, found {text!r}"
        raise ValueError(message)
    return int(text)


def main(arguments: list[str]) -> int:
    """Rewrite the file named on the command line in place."""
    if len(arguments) != 1:
        sys.stderr.write("usage: reproducible_sdist.py FILE\n")
        return 2
    path = arguments[0]
    try:
        with open(path, "rb") as file:
            packed = repack(file.read(), _epoch())
        # Written beside the file and moved over it, so a failure leaves the
        # backend's archive in place instead of half of the new one.
        with open(f"{path}.new", "wb") as file:
            file.write(packed)
        os.replace(f"{path}.new", path)
    except (OSError, ValueError, EOFError, tarfile.TarError, zlib.error) as error:
        sys.stderr.write(f"could not rewrite {path}: {error}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
