# SPDX-FileCopyrightText: 2026 Lusoris <lusoris@proton.me>
# SPDX-License-Identifier: EUPL-1.2
"""Run the usage examples of the README against the installed package.

Each Python block of the README runs here as written. A renamed function or
argument then fails a test instead of leaving the first thing a new user
copies wrong.
"""

from __future__ import annotations

import hashlib
import json
import os
import runpy
import shutil
import subprocess
import textwrap
from typing import TYPE_CHECKING

from cistula import open_text_atomic, read_json, staged_path
from tool_loader import REPOSITORY

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

FENCE = "```"
# The first example runs git; without it the test fails, it is not skipped.
GIT = shutil.which("git")


def python_blocks() -> list[str]:
    """Return the Python blocks of the README in their order, indentation removed."""
    with open(os.path.join(REPOSITORY, "README.md"), encoding="utf-8") as file:
        lines = file.read().splitlines(keepends=True)
    blocks: list[str] = []
    current: list[str] | None = None
    for line in lines:
        fence = line.strip()
        if current is None:
            if fence == f"{FENCE}python":
                current = []
        elif fence == FENCE:
            blocks.append(textwrap.dedent("".join(current)))
            current = None
        else:
            current.append(line)
    assert current is None, "a Python block of the README is not closed"
    return blocks


def run_block(block: str, directory: Path, names: dict[str, object] | None = None) -> None:
    """Run one block as a script in `directory`, with `names` already defined."""
    path = directory / "example.py"
    path.write_text(block, encoding="utf-8")
    runpy.run_path(str(path), init_globals=names, run_name="__main__")


def git(directory: Path, *arguments: str) -> str:
    # No configuration of the host: no signing, no hooks, a fixed identity.
    assert GIT is not None, "git is not on PATH"
    environment = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    done = subprocess.run(
        [GIT, "-c", "user.name=Test", "-c", "user.email=test@example.org", *arguments],
        cwd=directory,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    return done.stdout.strip()


def test_the_readme_holds_the_examples_this_file_runs() -> None:
    # A new example fails here until a test below runs it.
    blocks = python_blocks()

    assert len(blocks) == 2
    assert blocks[0].startswith("from cistula import ")
    assert blocks[1].startswith("with open_text_atomic(")


def test_the_first_example_records_the_commit_and_the_digest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    git(tmp_path, "init", "--quiet")
    git(tmp_path, "commit", "--quiet", "--allow-empty", "--message=start")
    (tmp_path / "record.json").write_text('{"name": "artifact"}\n', encoding="utf-8")
    (tmp_path / "artifact.bin").write_bytes(b"\x00\x01artifact")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.chdir(tmp_path)

    run_block(python_blocks()[0], tmp_path)

    assert read_json(tmp_path / "record.json") == {
        "name": "artifact",
        "commit": git(tmp_path, "rev-parse", "HEAD"),
        "digest": hashlib.sha256(b"\x00\x01artifact").hexdigest(),
    }


class Frame:
    """Stands in for a data frame: a producer that opens the file by its name."""

    def to_parquet(self, path: str) -> None:
        with open(path, "wb") as file:
            file.write(b"PAR1 table PAR1")


def test_the_second_example_writes_rows_in_a_block_and_a_file_by_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The block is a fragment: it uses names the surrounding code defines.
    records = [{"id": 1}, {"id": 2, "tags": ["a", "ä"]}]
    names: dict[str, object] = {
        "json": json,
        "open_text_atomic": open_text_atomic,
        "staged_path": staged_path,
        "records": records,
        "frame": Frame(),
    }
    monkeypatch.chdir(tmp_path)

    run_block(python_blocks()[1], tmp_path, names)

    rows = (tmp_path / "rows.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(row) for row in rows] == records
    assert (tmp_path / "table.parquet").read_bytes() == b"PAR1 table PAR1"
    assert sorted(os.listdir(tmp_path)) == ["example.py", "rows.jsonl", "table.parquet"]
