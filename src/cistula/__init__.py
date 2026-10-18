# SPDX-FileCopyrightText: 2026 Lusoris <lusoris@proton.me>
# SPDX-License-Identifier: EUPL-1.2
"""Cistula: shared Python helpers of the Plurano framework family.

Three modules, standard library only, none of which contacts a network:

- `process`: run a child process under a required time limit.
- `files`: strict JSON and JSON Lines reading and atomic file writing, in one call or in a block.
- `digest`: SHA-256 digests.
"""

from __future__ import annotations

from cistula.digest import sha256_bytes, sha256_file, sha256_text
from cistula.files import (
    DuplicateKeyError,
    JsonLineError,
    JsonValue,
    loads_json,
    loads_json_lines,
    open_bytes_atomic,
    open_text_atomic,
    read_json,
    read_json_lines,
    staged_path,
    write_bytes_atomic,
    write_json,
    write_text_atomic,
)
from cistula.process import (
    CommandFailedError,
    CommandNotFoundError,
    CommandNotStartedError,
    CommandTimeoutError,
    ProcessError,
    Result,
    child_env,
    run,
)

__all__ = [
    "CommandFailedError",
    "CommandNotFoundError",
    "CommandNotStartedError",
    "CommandTimeoutError",
    "DuplicateKeyError",
    "JsonLineError",
    "JsonValue",
    "ProcessError",
    "Result",
    "child_env",
    "loads_json",
    "loads_json_lines",
    "open_bytes_atomic",
    "open_text_atomic",
    "read_json",
    "read_json_lines",
    "run",
    "sha256_bytes",
    "sha256_file",
    "sha256_text",
    "staged_path",
    "write_bytes_atomic",
    "write_json",
    "write_text_atomic",
]
