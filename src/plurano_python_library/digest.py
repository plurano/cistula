# SPDX-FileCopyrightText: 2026 Lusoris <lusoris@proton.me>
# SPDX-License-Identifier: EUPL-1.2
"""SHA-256 digests as lower-case hexadecimal text.

`sha256_file` reads the file in pieces, so its memory use does not grow with
the size of the file.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import os

__all__ = ["sha256_bytes", "sha256_file", "sha256_text"]


def sha256_file(path: str | os.PathLike[str]) -> str:
    """Return the SHA-256 digest of the file's content. A missing file raises `OSError`."""
    with open(path, "rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def sha256_bytes(data: bytes | bytearray | memoryview) -> str:
    """Return the SHA-256 digest of `data`; text is refused, use `sha256_text`."""
    if not isinstance(data, (bytes, bytearray, memoryview)):
        message = f"sha256_bytes needs bytes, not {type(data).__name__}"
        raise TypeError(message)
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    """Return the SHA-256 digest of `text` encoded as UTF-8."""
    if not isinstance(text, str):
        message = f"sha256_text needs text, not {type(text).__name__}"
        raise TypeError(message)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
