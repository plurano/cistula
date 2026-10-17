# SPDX-FileCopyrightText: 2026 Lusoris <lusoris@proton.me>
# SPDX-License-Identifier: EUPL-1.2
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

from tool_loader import REPOSITORY, load_tool, script

if TYPE_CHECKING:
    from pathlib import Path

SCRIPT = script("check_python_versions")

check = load_tool("check_python_versions")

FILES = [
    check.PROJECT,
    check.LINT_CONFIG,
    check.MAKEFILE,
    check.LOCK,
    check.WORKFLOW,
    check.RELEASE_WORKFLOW,
]

FLOOR = 'requires-python = ">=3.11"'
OLDEST = '    "Programming Language :: Python :: 3.11",\n'
NEWEST = '    "Programming Language :: Python :: 3.15",\n'
GATE = "PYTHONS ?= python3.11 python3.12 python3.13 python3.14 python3.15"
WORKFLOW_LIST = (
    "          python-version: |\n            3.11\n            3.12\n            3.13\n            3.14\n"
    "            3.15\n"
)


@pytest.fixture
def copy(tmp_path: Path) -> str:
    """Return a directory that holds this repository's own six files."""
    for file in FILES:
        target = tmp_path / file
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(os.path.join(REPOSITORY, file), target)
    return str(tmp_path)


def change(root: str, file: str, old: str, new: str) -> None:
    path = os.path.join(root, file)
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    assert text.count(old) == 1, f"{file} holds {old!r} {text.count(old)} times"
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text.replace(old, new))


def test_this_repository_names_the_same_versions_everywhere() -> None:
    assert check.problems(REPOSITORY) == []
    assert check.supported(REPOSITORY) == ["3.11", "3.12", "3.13", "3.14", "3.15"]


def test_copy_of_this_repository_passes(copy: str) -> None:
    assert check.problems(copy) == []


@pytest.mark.parametrize(
    ("file", "old", "new", "place"),
    [
        (check.PROJECT, FLOOR, 'requires-python = ">=3.12"', "pyproject.toml: requires-python"),
        (check.PROJECT, FLOOR, 'requires-python = ">=3.10"', "pyproject.toml: requires-python"),
        (check.PROJECT, FLOOR, 'requires-python = ">=3.11,<3.16"', "pyproject.toml: requires-python"),
        (check.PROJECT, FLOOR, 'requires-python = ">3.10"', "pyproject.toml: requires-python"),
        (check.PROJECT, FLOOR, "", "pyproject.toml: requires-python"),
        (check.PROJECT, 'python_version = "3.11"', 'python_version = "3.12"', "pyproject.toml: python_version of mypy"),
        (check.PROJECT, 'python_version = "3.11"\n', "", "pyproject.toml: python_version of mypy"),
        (check.LINT_CONFIG, 'target-version = "py311"', 'target-version = "py312"', "ruff.toml: target-version"),
        (check.LINT_CONFIG, 'target-version = "py311"\n', "", "ruff.toml: target-version"),
        (check.MAKEFILE, GATE, "PYTHONS ?= python3.12 python3.13 python3.14 python3.15", "Makefile: PYTHONS"),
        (check.MAKEFILE, GATE, GATE + " python3.16", "Makefile: PYTHONS"),
        (
            check.MAKEFILE,
            GATE,
            "PYTHONS ?= python3.15 python3.14 python3.13 python3.12 python3.11",
            "Makefile: PYTHONS",
        ),
        (check.MAKEFILE, GATE, "PYTHONS ?=", "Makefile: PYTHONS"),
        (check.MAKEFILE, GATE, "INTERPRETERS ?= python3.11", "Makefile: PYTHONS"),
        (check.MAKEFILE, "--python-version=3.11", "--python-version=3.12", "Makefile: --python-version of the lock"),
        (check.MAKEFILE, " --python-version=3.11", "", "Makefile: --python-version of the lock"),
        (check.LOCK, "--python-version=3.11", "--python-version=3.12", "requirements/dev.txt: --python-version"),
        (check.LOCK, " --python-version=3.11", "", "requirements/dev.txt: --python-version"),
        (check.WORKFLOW, "            3.11\n            3.12\n", "            3.12\n", "ci.yml: python-version"),
        (check.WORKFLOW, "            3.15\n", "            3.15\n            3.16\n", "ci.yml: python-version"),
        (check.WORKFLOW, "            3.15\n", "            3.15.0\n", "ci.yml: python-version"),
        (check.WORKFLOW, WORKFLOW_LIST, '          python-version: "3.11"\n', "ci.yml: python-version"),
        (check.WORKFLOW, "          python-version: |\n", "          versions: |\n", "ci.yml: python-version"),
        (
            check.RELEASE_WORKFLOW,
            "            3.11\n            3.12\n",
            "            3.12\n",
            "release-build.yml: python-version",
        ),
        (check.RELEASE_WORKFLOW, "            3.15\n", "", "release-build.yml: python-version"),
        (
            check.RELEASE_WORKFLOW,
            "            3.15\n",
            "            3.15\n            3.16\n",
            "release-build.yml: python-version",
        ),
        (check.RELEASE_WORKFLOW, "            3.15\n", "            3.15.0\n", "release-build.yml: python-version"),
        (
            check.RELEASE_WORKFLOW,
            WORKFLOW_LIST,
            '          python-version: "3.11"\n',
            "release-build.yml: python-version",
        ),
        (
            check.RELEASE_WORKFLOW,
            "          python-version: |\n",
            "          versions: |\n",
            "release-build.yml: python-version",
        ),
    ],
)
def test_one_place_that_disagrees_is_named(copy: str, file: str, old: str, new: str, place: str) -> None:
    change(copy, file, old, new)

    found = check.problems(copy)

    assert len(found) == 1
    assert place in found[0]


def test_a_version_declared_but_not_tested_names_the_gate_and_the_workflow(copy: str) -> None:
    change(copy, check.PROJECT, NEWEST, NEWEST + NEWEST.replace("3.15", "3.16"))

    found = check.problems(copy)

    assert [text.split(":")[0] for text in found] == [check.MAKEFILE, check.WORKFLOW, check.RELEASE_WORKFLOW]
    assert "'3.16'" in found[0]


def test_a_floor_that_moves_in_the_classifiers_only_names_every_other_place(copy: str) -> None:
    change(copy, check.PROJECT, OLDEST, "")

    found = check.problems(copy)

    assert len(found) == len(check.places(copy)) - 1
    assert not any("classifiers" in text for text in found)


def test_versions_are_ordered_as_numbers_not_as_text(copy: str) -> None:
    change(copy, check.PROJECT, OLDEST, OLDEST.replace("3.11", "3.9"))

    assert check.supported(copy) == ["3.9", "3.12", "3.13", "3.14", "3.15"]


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (
            OLDEST
            + OLDEST.replace("3.11", "3.12")
            + OLDEST.replace("3.11", "3.13")
            + OLDEST.replace("3.11", "3.14")
            + NEWEST,
            "",
        ),
        ("classifiers = [", "keywords = ["),
        ("[project]", "[package]"),
    ],
)
def test_no_classifier_names_a_version(copy: str, old: str, new: str) -> None:
    change(copy, check.PROJECT, old, new)

    found = check.problems(copy)

    assert check.supported(copy) == []
    assert found[0].startswith("pyproject.toml: classifiers: names None")
    assert len(found) == len(check.places(copy))


@pytest.mark.parametrize("file", [check.WORKFLOW, check.RELEASE_WORKFLOW])
def test_a_missing_file_is_an_error_not_a_pass(copy: str, file: str) -> None:
    os.unlink(os.path.join(copy, file))

    with pytest.raises(FileNotFoundError):
        check.problems(copy)


def run_script(root: str) -> subprocess.CompletedProcess[str]:
    planted = os.path.join(root, "tools", "check_python_versions.py")
    os.makedirs(os.path.dirname(planted), exist_ok=True)
    shutil.copyfile(SCRIPT, planted)
    return subprocess.run([sys.executable, planted], capture_output=True, text=True, timeout=60, check=False)


def test_script_passes_on_a_copy_and_names_the_versions(copy: str) -> None:
    done = run_script(copy)

    assert (done.returncode, done.stderr) == (0, "")
    assert (
        done.stdout
        == "PASS: metadata, lint, type check, lock, gate and CI all name Python 3.11, 3.12, 3.13, 3.14, 3.15.\n"
    )


def test_script_fails_and_names_the_place(copy: str) -> None:
    change(copy, check.WORKFLOW, "            3.11\n            3.12\n", "            3.12\n")

    done = run_script(copy)

    assert (done.returncode, done.stdout) == (1, "")
    assert done.stderr == (
        ".github/workflows/ci.yml: python-version: names ['3.12', '3.13', '3.14', '3.15'], "
        "has to name ['3.11', '3.12', '3.13', '3.14', '3.15']\n"
    )


def test_release_workflow_leaving_version_out_is_refused(copy: str) -> None:
    change(copy, check.RELEASE_WORKFLOW, "            3.15\n", "")

    done = run_script(copy)

    assert (done.returncode, done.stdout) == (1, "")
    assert done.stderr == (
        ".github/workflows/release-build.yml: python-version: names ['3.11', '3.12', '3.13', '3.14'], "
        "has to name ['3.11', '3.12', '3.13', '3.14', '3.15']\n"
    )


def test_release_workflow_extra_version_is_refused(copy: str) -> None:
    change(copy, check.RELEASE_WORKFLOW, "            3.15\n", "            3.15\n            3.16\n")

    done = run_script(copy)

    assert (done.returncode, done.stdout) == (1, "")
    assert done.stderr == (
        ".github/workflows/release-build.yml: python-version: names ['3.11', '3.12', '3.13', '3.14', '3.15', '3.16'], "
        "has to name ['3.11', '3.12', '3.13', '3.14', '3.15']\n"
    )
