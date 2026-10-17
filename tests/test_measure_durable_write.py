# SPDX-FileCopyrightText: 2026 Lusoris <lusoris@proton.me>
# SPDX-License-Identifier: EUPL-1.2
from __future__ import annotations

import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

from tool_loader import REPOSITORY, load_tool, script

if TYPE_CHECKING:
    from pathlib import Path

measure = load_tool("measure_durable_write")

WAYS = ["durable", "not-durable", "group-fsync", "group-syncfs"]


def test_measure_prints_one_line_per_way_and_leaves_nothing(tmp_path: Path) -> None:
    lines = measure.measure(str(tmp_path), 3)
    fields = [line.split("\t") for line in lines]
    assert [row[0] for row in fields] == WAYS
    assert all(float(row[1]) > 0 for row in fields)
    assert {row[2] for row in fields} != {""}
    assert list(tmp_path.iterdir()) == []


def test_measure_skips_syncfs_away_from_linux(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(measure.sys, "platform", "darwin")
    lines = measure.measure(str(tmp_path), 1)
    assert lines[0].startswith("group-syncfs\tskipped\t")
    assert [line.split("\t")[0] for line in lines[1:]] == WAYS[:3]


@pytest.mark.parametrize("count", [0, -1, measure.MAX_COUNT + 1])
def test_measure_refuses_a_count_outside_its_bound(tmp_path: Path, count: int) -> None:
    with pytest.raises(ValueError, match="COUNT must be between"):
        measure.measure(str(tmp_path), count)
    assert list(tmp_path.iterdir()) == []


def test_measure_accepts_the_smallest_count(tmp_path: Path) -> None:
    assert len(measure.measure(str(tmp_path), 1)) == len(WAYS)


@pytest.mark.parametrize(
    "arguments",
    [[], ["a", "1", "x"], ["/nonexistent/cistula"], ["{tmp}", "many"], ["{tmp}", "0"]],
)
def test_main_answers_a_usage_error_with_2(
    tmp_path: Path, arguments: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    given = [argument.replace("{tmp}", str(tmp_path)) for argument in arguments]
    assert measure.main(given) == 2
    assert "usage: measure_durable_write.py" in capsys.readouterr().err


def test_script_runs_from_the_command_line(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, script("measure_durable_write"), str(tmp_path), "2"],
        capture_output=True,
        text=True,
        check=False,
        cwd=REPOSITORY,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert [line.split("\t")[0] for line in result.stdout.splitlines()] == WAYS
