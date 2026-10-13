# SPDX-FileCopyrightText: 2026 Lusoris <lusoris@proton.me>
# SPDX-License-Identifier: EUPL-1.2
from __future__ import annotations

import gzip
import hashlib
import io
import os
import subprocess
import sys
import tarfile
import zipfile
from typing import TYPE_CHECKING

import pytest

from tool_loader import REPOSITORY, load_tool, script

if TYPE_CHECKING:
    from pathlib import Path

size = load_tool("check_package_size")

# About 65 kB of hexadecimal text: large enough that a figure rounded to whole
# kilobytes is within a tenth of every size, and compressed to about half,
# so the stored and the compressed sizes differ by far more than a tenth.
SOURCE = b"".join(hashlib.sha256(bytes([n % 256, n // 256])).hexdigest().encode() + b"\n" for n in range(1000))


def build(directory: Path) -> Path:
    """Write a source distribution and a wheel with their members stored uncompressed."""
    directory.mkdir()
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w") as tar:
        info = tarfile.TarInfo("pkg-1/pkg.py")
        info.size = len(SOURCE)
        tar.addfile(info, io.BytesIO(SOURCE))
    (directory / "pkg-1.tar.gz").write_bytes(gzip.compress(archive.getvalue(), compresslevel=0, mtime=0))
    with zipfile.ZipFile(directory / "pkg-1-py3-none-any.whl", "w") as wheel:
        wheel.writestr("pkg.py", SOURCE)
        wheel.writestr("pkg-1.dist-info/RECORD", b"pkg.py,,\n")
    return directory


def sentence(figures: dict[str, int]) -> str:
    kb = [str(figures[key] // 1000) for key in size.KEYS]
    return f"The price is size: about {kb[0]} kB and {kb[1]} kB\n  instead of {kb[2]} kB and {kb[3]} kB.\n"


def rounded(measured: dict[str, int]) -> dict[str, int]:
    return {key: (value + 500) // 1000 * 1000 for key, value in measured.items()}


def test_stated_reads_the_four_figures_across_a_line_break() -> None:
    text = "about 277 kB and 64 kB\n  instead of 56 kB and\n24 kB."

    assert size.stated(text) == {
        "source distribution": 277000,
        "wheel": 64000,
        "source distribution compressed": 56000,
        "wheel compressed": 24000,
    }


@pytest.mark.parametrize(
    ("text", "found"),
    [
        ("no figure at all", 0),
        ("about 277 kB and 64 kB instead of 56 kB", 0),
        ("about 277 KB and 64 kB instead of 56 kB and 24 kB", 0),
        ("about 1 kB and 2 kB instead of 3 kB and 4 kB; about 1 kB and 2 kB instead of 3 kB and 4 kB", 2),
    ],
)
def test_stated_refuses_anything_but_one_sentence(text: str, found: int) -> None:
    with pytest.raises(ValueError, match=f"found {found}$"):
        size.stated(text)


def test_the_readme_of_the_repository_states_the_size_once() -> None:
    with open(os.path.join(REPOSITORY, "README.md"), encoding="utf-8") as file:
        assert set(size.stated(file.read())) == set(size.KEYS)


def test_measure_reads_the_stored_sizes_and_computes_the_compressed_ones(tmp_path: Path) -> None:
    dist = build(tmp_path / "dist")
    measured = size.measure(str(dist))

    assert measured["source distribution"] == (dist / "pkg-1.tar.gz").stat().st_size
    assert measured["wheel"] == (dist / "pkg-1-py3-none-any.whl").stat().st_size
    # Stored, each file holds the source whole; compressed, little more than half of it.
    assert measured["source distribution"] > len(SOURCE) > 1.5 * measured["source distribution compressed"]
    assert measured["wheel"] > len(SOURCE) > 1.5 * measured["wheel compressed"]


@pytest.mark.parametrize(("suffix", "count"), [(".tar.gz", 0), (".tar.gz", 2), (".whl", 0), (".whl", 2)])
def test_measure_refuses_a_directory_without_exactly_one_file_of_each(tmp_path: Path, suffix: str, count: int) -> None:
    dist = build(tmp_path / "dist")
    (only,) = dist.glob(f"*{suffix}")
    if count == 0:
        only.unlink()
    else:
        (dist / f"other-1{suffix}").write_bytes(only.read_bytes())

    with pytest.raises(ValueError, match=rf"expected one \*{suffix} in .*, found {count}$"):
        size.measure(str(dist))


@pytest.mark.parametrize(
    ("figure", "problems"),
    [
        (90000, []),
        (110000, []),
        (89999, ["wheel: README states 89 kB, measured 100000 bytes (100 kB)"]),
        (110001, ["wheel: README states 110 kB, measured 100000 bytes (100 kB)"]),
    ],
)
def test_compare_allows_a_tenth_of_the_measured_size_and_no_more(figure: int, problems: list[str]) -> None:
    measured = dict.fromkeys(size.KEYS, 100000)

    assert size.compare({**measured, "wheel": figure}, measured) == problems


def test_compare_names_every_figure_that_is_off() -> None:
    measured = dict.fromkeys(size.KEYS, 64499)

    problems = size.compare(dict.fromkeys(size.KEYS, 46000), measured)

    assert problems == [f"{key}: README states 46 kB, measured 64499 bytes (64 kB)" for key in size.KEYS]


def test_main_passes_a_current_sentence(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    dist = build(tmp_path / "dist")
    measured = size.measure(str(dist))
    readme = tmp_path / "README.md"
    readme.write_text(sentence(rounded(measured)), encoding="utf-8")

    assert size.main([str(readme), str(dist)]) == 0
    out, err = capsys.readouterr()
    assert out.splitlines()[-1] == f"PASS: the package size in {readme} is within a tenth of the measured size."
    assert out.count(" bytes, README ") == 4
    assert err == ""


def test_main_fails_a_sentence_that_is_out_of_date(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    dist = build(tmp_path / "dist")
    figures = rounded(size.measure(str(dist)))
    readme = tmp_path / "README.md"
    readme.write_text(sentence({**figures, "wheel": figures["wheel"] * 2}), encoding="utf-8")

    assert size.main([str(readme), str(dist)]) == 1
    out, err = capsys.readouterr()
    assert out == ""
    lines = err.splitlines()
    assert lines[0] == f"FAIL: the package size in {readme} is out of date:"
    assert len(lines) == 3
    assert lines[1].startswith("  wheel: README states ")
    assert lines[2] == "Update the sentence to the measured sizes."


def test_main_fails_a_readme_without_the_sentence(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    dist = build(tmp_path / "dist")
    readme = tmp_path / "README.md"
    readme.write_text("No size here.\n", encoding="utf-8")

    assert size.main([str(readme), str(dist)]) == 1
    assert capsys.readouterr().err == (
        "FAIL: expected one sentence 'about N kB and N kB instead of N kB and N kB', found 0\n"
    )


@pytest.mark.parametrize("argv", [[], ["README.md"], ["README.md", "dist", "more"]])
def test_main_refuses_a_wrong_argument_count(argv: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    assert size.main(argv) == 2
    assert capsys.readouterr().err == "usage: check_package_size.py README DIRECTORY\n"


@pytest.mark.parametrize("damaged", ["pkg-1.tar.gz", "pkg-1-py3-none-any.whl"])
def test_main_reports_a_damaged_build(tmp_path: Path, capsys: pytest.CaptureFixture[str], damaged: str) -> None:
    dist = build(tmp_path / "dist")
    (dist / damaged).write_bytes((dist / damaged).read_bytes()[:100])
    readme = tmp_path / "README.md"
    readme.write_text(sentence(dict.fromkeys(size.KEYS, 1000)), encoding="utf-8")

    assert size.main([str(readme), str(dist)]) == 2
    assert capsys.readouterr().err.startswith(f"could not measure the build in {dist}: ")


def test_the_script_runs_as_the_gate_calls_it(tmp_path: Path) -> None:
    dist = build(tmp_path / "dist")
    readme = tmp_path / "README.md"
    readme.write_text(sentence(dict.fromkeys(size.KEYS, 999000)), encoding="utf-8")

    done = subprocess.run(
        [sys.executable, "-I", script("check_package_size"), str(readme), str(dist)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert done.returncode == 1
    assert done.stdout == ""
    assert done.stderr.startswith(f"FAIL: the package size in {readme} is out of date:\n")
