# SPDX-FileCopyrightText: 2026 Lusoris <lusoris@proton.me>
# SPDX-License-Identifier: EUPL-1.2
from __future__ import annotations

import os
import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

from tool_loader import REPOSITORY, load_tool, script

if TYPE_CHECKING:
    from pathlib import Path

figures = load_tool("check_durable_figures")

TABLE = """Measured:

  | File system | Default (`durable=True`) | `durable=False` |
  | --- | --- | --- |
  | btrfs | 10 ms (18 to 36 ms in earlier runs) | 15 µs |
  | ZFS, `sync=standard` | 3 ms | 22 to 28 µs |
  | tmpfs (memory, no disk) | 11 to 13 µs | 10 to 11 µs |

  A caller that writes many files.
"""


def measurement(kind: str, durable: float, plain: float) -> str:
    return (
        f"durable\t{durable}\t{kind}\nnot-durable\t{plain}\t{kind}\n"
        f"group-fsync\t1.0\t{kind}\ngroup-syncfs\tskipped\t{kind}\tsyncfs is Linux only\n"
    )


def test_stated_reads_every_row_with_its_spans_in_microseconds() -> None:
    assert figures.stated(TABLE) == {
        "btrfs": ((10000.0, 36000.0), (15.0, 15.0)),
        "zfs": ((3000.0, 3000.0), (22.0, 28.0)),
        "tmpfs": ((11.0, 13.0), (10.0, 11.0)),
    }


def test_the_readme_of_the_repository_states_one_table() -> None:
    with open(os.path.join(REPOSITORY, "README.md"), encoding="utf-8") as file:
        rows = figures.stated(file.read())

    assert {"btrfs", "zfs", "tmpfs"} <= set(rows)
    assert figures.compare(rows, "tmpfs", 12.0, 10.0) == []


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("no table", "expected one table of durable-write figures, found 0"),
        (TABLE + TABLE, "expected one table of durable-write figures, found 2"),
        (TABLE.split("  | btrfs", maxsplit=1)[0], "the table of durable-write figures has no row"),
        (TABLE.replace("15 µs", "fast"), "no figure in the table cell 'fast'"),
        (TABLE.replace("15 µs", "15 us"), "no figure in the table cell '15 us'"),
    ],
)
def test_stated_refuses_a_missing_table_or_figure(text: str, message: str) -> None:
    with pytest.raises(ValueError, match=f"^{message}$"):
        figures.stated(text)


def test_measured_reads_the_default_and_the_write_without_flushes() -> None:
    assert figures.measured(measurement("btrfs", 10015.4, 14.3)) == ("btrfs", 10015.4, 14.3)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "durable\t1.0\tbtrfs\n",
        "durable\t1.0\tbtrfs\nnot-durable\t1.0\ttmpfs\n",
        "durable\t1.0\nnot-durable\t1.0\n",
    ],
)
def test_measured_refuses_a_measurement_without_both_lines_for_one_file_system(text: str) -> None:
    with pytest.raises(ValueError, match="no 'durable' and 'not-durable' line for one file system"):
        figures.measured(text)


def test_compare_passes_figures_inside_the_tolerance() -> None:
    rows = figures.stated(TABLE)

    assert figures.compare(rows, "btrfs", 10015.4, 14.3) == []
    assert figures.compare(rows, "zfs", 3000.0, 25.0) == []


@pytest.mark.parametrize(
    ("durable", "plain", "problems"),
    [
        (2500.0, 15.0, []),
        (144000.0, 60.0, []),
        (2499.0, 15.0, ["btrfs: measured 2499 µs for the default, the README states 10000 to 36000 µs"]),
        (144001.0, 15.0, ["btrfs: measured 144001 µs for the default, the README states 10000 to 36000 µs"]),
        (10000.0, 3.74, ["btrfs: measured 3.74 µs for durable=False, the README states 15 µs"]),
        (10000.0, 60.1, ["btrfs: measured 60.1 µs for durable=False, the README states 15 µs"]),
    ],
)
def test_compare_allows_a_factor_of_four_either_way_and_no_more(
    durable: float, plain: float, problems: list[str]
) -> None:
    assert figures.compare(figures.stated(TABLE), "btrfs", durable, plain) == problems


@pytest.mark.parametrize(
    ("durable", "plain", "problems"),
    [
        (2000.0, 100.0, []),
        (1999.0, 100.0, ["ext2/ext3: measured the default at 1999 µs, less than 20 times 100 µs without the flushes"]),
        (40.0, 30.0, ["ext2/ext3: measured the default at 40 µs, less than 20 times 30 µs without the flushes"]),
    ],
)
def test_compare_holds_a_disk_without_a_row_to_the_ratio(durable: float, plain: float, problems: list[str]) -> None:
    assert figures.compare(figures.stated(TABLE), "ext2/ext3", durable, plain) == problems


def test_compare_holds_a_file_system_in_memory_to_no_ratio() -> None:
    assert figures.compare(figures.stated(TABLE), "tmpfs", 11.0, 11.0) == []
    assert figures.compare(figures.stated(TABLE), "ramfs", 5.0, 5.0) == []


@pytest.mark.parametrize(
    ("row", "problem"),
    [
        ("| ZFS | 560 µs | 22 to 28 µs |", None),
        (
            "| ZFS | 559 µs | 22 to 28 µs |",
            "zfs: the default 559 µs is less than 20 times 22 to 28 µs without the flushes",
        ),
    ],
)
def test_compare_holds_every_disk_row_to_the_ratio(row: str, problem: str | None) -> None:
    rows = figures.stated(TABLE.replace("| ZFS, `sync=standard` | 3 ms | 22 to 28 µs |", row))

    assert figures.compare(rows, "tmpfs", 11.0, 10.0) == ([problem] if problem else [])


def test_main_passes_a_current_table(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    readme = tmp_path / "README.md"
    readme.write_text(TABLE, encoding="utf-8")
    measured = tmp_path / "durable.tsv"
    measured.write_text(measurement("ext2/ext3", 3000.0, 20.0), encoding="utf-8")

    assert figures.main([str(readme), str(measured)]) == 0
    out, err = capsys.readouterr()
    assert out.splitlines() == [
        "ext2/ext3: default 3000 µs, durable=False 20 µs; no row of the table",
        f"PASS: the durable-write figures in {readme} agree with the measurement.",
    ]
    assert err == ""


def test_main_fails_a_table_that_is_out_of_date(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    readme = tmp_path / "README.md"
    readme.write_text(TABLE, encoding="utf-8")
    measured = tmp_path / "durable.tsv"
    measured.write_text(measurement("btrfs", 900.0, 14.0), encoding="utf-8")

    assert figures.main([str(readme), str(measured)]) == 1
    assert capsys.readouterr().err.splitlines() == [
        f"FAIL: the durable-write figures in {readme} disagree with the measurement:",
        "  btrfs: measured 900 µs for the default, the README states 10000 to 36000 µs",
    ]


def test_main_fails_a_readme_without_the_table(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    readme = tmp_path / "README.md"
    readme.write_text("No table.\n", encoding="utf-8")
    measured = tmp_path / "durable.tsv"
    measured.write_text(measurement("btrfs", 1.0, 1.0), encoding="utf-8")

    assert figures.main([str(readme), str(measured)]) == 1
    assert capsys.readouterr().err == "FAIL: expected one table of durable-write figures, found 0\n"


@pytest.mark.parametrize("argv", [[], ["README.md"], ["README.md", "durable.tsv", "more"]])
def test_main_refuses_a_wrong_argument_count(argv: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    assert figures.main(argv) == 2
    assert capsys.readouterr().err == "usage: check_durable_figures.py README MEASUREMENT\n"


def test_main_reports_a_missing_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert figures.main([str(tmp_path / "README.md"), str(tmp_path / "durable.tsv")]) == 2
    assert capsys.readouterr().err.startswith("usage: check_durable_figures.py README MEASUREMENT: ")


def test_the_script_runs_as_the_gate_calls_it(tmp_path: Path) -> None:
    readme = tmp_path / "README.md"
    readme.write_text(TABLE, encoding="utf-8")
    measured = tmp_path / "durable.tsv"
    measured.write_text(measurement("btrfs", 50.0, 14.0), encoding="utf-8")

    done = subprocess.run(
        [sys.executable, "-I", script("check_durable_figures"), str(readme), str(measured)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert done.returncode == 1
    assert "btrfs: measured 50 µs for the default" in done.stderr
    assert "less than 20 times 14 µs" in done.stderr
