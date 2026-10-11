# SPDX-FileCopyrightText: 2026 Lusoris <lusoris@proton.me>
# SPDX-License-Identifier: EUPL-1.2
"""Fail when two builds of the package differ in a single byte.

Usage: compare_packages.py DIRECTORY DIRECTORY

Each directory holds the files of one build. Both must hold files of the same
names with the same bytes. For a file that differs, the report names the
archive members and the fields in which the two builds disagree, so the cause
(a time stamp, a file mode, an owner, the compressed size) is in the log.
"""

from __future__ import annotations

import hashlib
import os
import sys
import tarfile
import zipfile
import zlib

Fields = dict[str, object]

# Printed per differing file; a longer list is cut off with a count.
MAX_LINES = 20


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _wheel_members(path: str) -> dict[str, Fields]:
    """Return every member of a wheel with the fields the archive records for it."""
    members: dict[str, Fields] = {}
    with zipfile.ZipFile(path) as archive:
        for position, info in enumerate(archive.infolist()):
            members[info.filename] = {
                "position": position,
                "content": _sha256(archive.read(info)),
                "compressed size": info.compress_size,
                "compression": info.compress_type,
                "time stamp": info.date_time,
                "file mode": oct(info.external_attr >> 16),
                "extra field": info.extra.hex(),
            }
    return members


def _sdist_members(path: str) -> dict[str, Fields]:
    """Return every member of a source distribution, the compression header first."""
    with open(path, "rb") as file:
        members: dict[str, Fields] = {"(gzip header)": {"bytes": file.read(10).hex()}}
    with tarfile.open(path, mode="r:gz") as archive:
        for position, member in enumerate(archive.getmembers()):
            # Only a file has content of its own; a link is not followed.
            content = archive.extractfile(member) if member.isreg() else None
            members[member.name] = {
                "position": position,
                "content": "" if content is None else _sha256(content.read()),
                "type": member.type,
                "link target": member.linkname,
                "file mode": oct(member.mode),
                "owner": (member.uid, member.gid, member.uname, member.gname),
                "time stamp": member.mtime,
                "extended headers": sorted(member.pax_headers),
            }
    return members


def members(path: str) -> dict[str, Fields]:
    """Return the members of one built file; a file of another kind has none."""
    if path.endswith(".whl"):
        return _wheel_members(path)
    if path.endswith(".tar.gz"):
        return _sdist_members(path)
    return {}


def _member_differences(first: dict[str, Fields], second: dict[str, Fields]) -> list[str]:
    """Return one line per member that the two archives record differently."""
    if not first and not second:
        return []
    lines = [f"member only in one build: {name}" for name in sorted(set(first) ^ set(second))]
    for name in sorted(set(first) & set(second)):
        fields = [field for field in first[name] if first[name][field] != second[name].get(field)]
        if fields:
            lines.append(f"{name}: {', '.join(fields)}")
    if not lines:
        lines.append("the members agree; the bytes that hold them differ")
    if len(lines) > MAX_LINES:
        lines[MAX_LINES:] = [f"and {len(lines) - MAX_LINES} more"]
    return lines


def _files(directory: str) -> dict[str, str]:
    """Return the digest of every file of one build by file name."""
    digests: dict[str, str] = {}
    for name in sorted(os.listdir(directory)):
        path = os.path.join(directory, name)
        if os.path.isfile(path):
            with open(path, "rb") as file:
                digests[name] = _sha256(file.read())
    return digests


def differences(first: str, second: str) -> list[str]:
    """Return one line per disagreement between the builds in two directories."""
    ours, theirs = _files(first), _files(second)
    if not ours:
        return [f"{first} holds no built file"]
    lines = [f"{name}: only in one build" for name in sorted(set(ours) ^ set(theirs))]
    for name in sorted(set(ours) & set(theirs)):
        if ours[name] == theirs[name]:
            continue
        lines.append(f"{name}: sha256 {ours[name]} in {first}, {theirs[name]} in {second}")
        found = _member_differences(members(os.path.join(first, name)), members(os.path.join(second, name)))
        lines.extend(f"  {line}" for line in found)
    return lines


def main(arguments: list[str]) -> int:
    """Compare the two directories named on the command line."""
    if len(arguments) != 2:  # noqa: PLR2004 - the two builds to compare
        sys.stderr.write("usage: compare_packages.py DIRECTORY DIRECTORY\n")
        return 2
    first, second = arguments
    try:
        found = differences(first, second)
    except (OSError, EOFError, tarfile.TarError, zipfile.BadZipFile, zlib.error) as error:
        sys.stderr.write(f"could not compare {first} and {second}: {error}\n")
        return 2
    if found:
        sys.stderr.write(f"FAIL: the build in {second} is not the build in {first}.\n")
        sys.stderr.write("".join(f"{line}\n" for line in found))
        return 1
    sys.stdout.write(f"PASS: {second} holds the same bytes as {first}.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
