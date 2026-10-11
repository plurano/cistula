# SPDX-FileCopyrightText: 2026 Lusoris <lusoris@proton.me>
# SPDX-License-Identifier: EUPL-1.2
from __future__ import annotations

import datetime
import os
import shutil
import subprocess
import sys
from typing import TYPE_CHECKING, Any

import pytest

from tool_loader import REPOSITORY, load_tool, script

if TYPE_CHECKING:
    from pathlib import Path

SCRIPT = script("check_exceptions")
TODAY = datetime.date(2026, 10, 11)

# Planted files, judged by ruff's default rules in the planted repository.
FAILS_LINT = "import os\n"
FAILS_FORMAT = "VALUE  =  1\n"
CLEAN = "VALUE = 1\n"


check = load_tool("check_exceptions")


def entry(file: str | None = "hook.py", **changes: Any) -> dict[str, Any]:
    fields = {"file": file, "rule": "python-lint", "reason": "engine issue 1", "expires": TODAY, **changes}
    return {name: value for name, value in fields.items() if value is not None}


def _toml(value: Any) -> str:
    if isinstance(value, datetime.date):
        return value.isoformat()
    if isinstance(value, str):
        return '"' + value.replace("\\", "\\\\") + '"'
    return str(value)


def plant(
    root: Path,
    entries: list[dict[str, Any]],
    *,
    excluded: list[str] | None = None,
    files: dict[str, str] | None = None,
    extra: str = "",
) -> str:
    """Write a repository with an exception list; by default every entry is excluded and fails the lint."""
    names = [item["file"] for item in entries if isinstance(item.get("file"), str)]
    listed = names if excluded is None else excluded
    (root / "ruff.toml").write_text("extend-exclude = [" + ", ".join(_toml(name) for name in listed) + "]\n")
    text = extra
    for item in entries:
        text += "[[exception]]\n" + "".join(f"{name} = {_toml(value)}\n" for name, value in item.items())
    (root / "exceptions.toml").write_text(text)
    for name, content in (dict.fromkeys(names, FAILS_LINT) if files is None else files).items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(content)
    return str(root)


def never_passes(_root: str, _file: str) -> bool:
    return False


# The list of this repository.


def test_the_list_of_this_repository_holds_today() -> None:
    # The real list, the real linter, the real date: this test fails on the day
    # after an entry expires and as soon as an entry is no longer needed.
    assert check.problems(REPOSITORY, datetime.datetime.now(tz=datetime.UTC).date()) == []


def test_the_list_of_this_repository_names_exact_files_with_a_reason_that_links_an_issue() -> None:
    import tomllib  # noqa: PLC0415 - only this test reads the list itself

    with open(os.path.join(REPOSITORY, "exceptions.toml"), "rb") as stream:
        entries = tomllib.load(stream)["exception"]
    assert entries
    for item in entries:
        assert os.path.isfile(os.path.join(REPOSITORY, item["file"]))
        assert "https://github.com/" in item["reason"]


# Planted lists: what holds.


def test_an_entry_in_date_for_a_file_that_fails_the_lint_holds(tmp_path: Path) -> None:
    assert check.problems(plant(tmp_path, [entry()]), TODAY) == []


def test_a_file_that_fails_only_the_format_check_still_needs_its_entry(tmp_path: Path) -> None:
    assert check.problems(plant(tmp_path, [entry()], files={"hook.py": FAILS_FORMAT}), TODAY) == []


def test_an_empty_list_holds_when_nothing_is_left_out(tmp_path: Path) -> None:
    assert check.problems(plant(tmp_path, []), TODAY) == []


def test_a_file_in_a_directory_is_named_by_its_path(tmp_path: Path) -> None:
    assert check.problems(plant(tmp_path, [entry(".config/hooks/hook.py")]), TODAY) == []


# Planted lists: expiry.


def test_an_entry_that_expires_today_still_holds(tmp_path: Path) -> None:
    assert check.problems(plant(tmp_path, [entry(expires=TODAY)]), TODAY, never_passes) == []


def test_an_expired_entry_fails(tmp_path: Path) -> None:
    yesterday = TODAY - datetime.timedelta(days=1)
    assert check.problems(plant(tmp_path, [entry(expires=yesterday)]), TODAY, never_passes) == [
        "entry 1: hook.py: expired on 2026-10-10"
    ]


def test_an_expiry_ninety_days_ahead_holds(tmp_path: Path) -> None:
    limit = TODAY + datetime.timedelta(days=90)
    assert check.problems(plant(tmp_path, [entry(expires=limit)]), TODAY, never_passes) == []


def test_an_expiry_more_than_ninety_days_ahead_fails(tmp_path: Path) -> None:
    beyond = TODAY + datetime.timedelta(days=91)
    assert check.problems(plant(tmp_path, [entry(expires=beyond)]), TODAY, never_passes) == [
        "entry 1: hook.py: expires 2027-01-10, more than 90 days ahead"
    ]


# Planted lists: stale entries.


def test_an_entry_whose_file_passes_the_lint_again_fails(tmp_path: Path) -> None:
    assert check.problems(plant(tmp_path, [entry()], files={"hook.py": CLEAN}), TODAY) == [
        "entry 1: hook.py: passes the lint now; remove the entry and the exclusion"
    ]


def test_an_entry_whose_file_is_gone_fails(tmp_path: Path) -> None:
    assert check.problems(plant(tmp_path, [entry()], files={}), TODAY, never_passes) == [
        "entry 1: hook.py: no such file; remove the entry and the exclusion"
    ]


def test_a_file_left_out_of_the_lint_without_an_entry_fails(tmp_path: Path) -> None:
    root = plant(tmp_path, [entry()], excluded=["hook.py", "other.py"])
    assert check.problems(root, TODAY, never_passes) == ["other.py: left out of the lint in ruff.toml without an entry"]


def test_an_entry_whose_file_is_not_left_out_of_the_lint_fails(tmp_path: Path) -> None:
    assert check.problems(plant(tmp_path, [entry()], excluded=[]), TODAY, never_passes) == [
        "hook.py: has an entry but ruff.toml does not leave it out"
    ]


def test_two_entries_for_one_file_fail(tmp_path: Path) -> None:
    assert check.problems(plant(tmp_path, [entry(), entry()]), TODAY, never_passes) == [
        "hook.py: named by more than one entry"
    ]


# Planted lists: an entry names one file, one known rule, a reason and a date.


@pytest.mark.parametrize("file", ["hooks/*.py", "hooks/hook?.py", "hooks/[a-z].py", "../hook.py", "/etc/hook.py"])
def test_a_pattern_or_a_path_outside_the_repository_is_refused(tmp_path: Path, file: str) -> None:
    root = plant(tmp_path, [entry(file)], files={})
    assert check.problems(root, TODAY, never_passes) == [
        f"entry 1: {file}: must be the path of one file inside the repository, not a pattern"
    ]


def test_a_directory_is_not_a_file(tmp_path: Path) -> None:
    root = plant(tmp_path, [entry("hooks")], files={"hooks/hook.py": FAILS_LINT})
    assert check.problems(root, TODAY, never_passes) == [
        "entry 1: hooks: no such file; remove the entry and the exclusion"
    ]


def test_an_unknown_rule_is_refused(tmp_path: Path) -> None:
    assert check.problems(plant(tmp_path, [entry(rule="types")]), TODAY, never_passes) == [
        "entry 1: hook.py: unknown rule 'types'; this list knows 'python-lint'"
    ]


@pytest.mark.parametrize("reason", ["", "  "])
def test_an_empty_reason_is_refused(tmp_path: Path, reason: str) -> None:
    assert check.problems(plant(tmp_path, [entry(reason=reason)]), TODAY, never_passes) == [
        "entry 1: hook.py: the reason is empty"
    ]


@pytest.mark.parametrize("missing", ["file", "rule", "reason", "expires"])
def test_a_missing_field_is_refused(tmp_path: Path, missing: str) -> None:
    without: dict[str, Any] = {missing: None}
    root = plant(tmp_path, [entry(**without)], excluded=["hook.py"], files={"hook.py": FAILS_LINT})
    found = check.problems(root, TODAY, never_passes)
    assert found[0].startswith("entry 1: needs exactly the fields ['expires', 'file', 'reason', 'rule'], found ")
    assert missing not in found[0].split("found ")[1]


def test_an_extra_field_is_refused(tmp_path: Path) -> None:
    found = check.problems(plant(tmp_path, [entry(owner="nobody")]), TODAY, never_passes)
    fields = "['expires', 'file', 'owner', 'reason', 'rule']"
    assert found == [f"entry 1: needs exactly the fields ['expires', 'file', 'reason', 'rule'], found {fields}"]


@pytest.mark.parametrize(
    "changes",
    [
        {"expires": "2026-10-11"},
        {"expires": datetime.datetime(2026, 10, 11, 12, 0, tzinfo=datetime.UTC)},
        {"reason": 7},
    ],
)
def test_a_field_of_the_wrong_type_is_refused(tmp_path: Path, changes: dict[str, Any]) -> None:
    assert check.problems(plant(tmp_path, [entry(**changes)]), TODAY, never_passes) == [
        "entry 1: file and reason must be text and expires a date"
    ]


def test_a_table_of_another_name_is_refused(tmp_path: Path) -> None:
    root = plant(tmp_path, [entry()], extra='[waiver]\nfile = "other.py"\n')
    assert check.problems(root, TODAY, never_passes) == ["unknown table 'waiver'"]


def test_every_problem_is_reported_not_only_the_first(tmp_path: Path) -> None:
    yesterday = TODAY - datetime.timedelta(days=1)
    root = plant(tmp_path, [entry(expires=yesterday), entry("gone.py")], files={"hook.py": CLEAN})
    assert check.problems(root, TODAY) == [
        "entry 1: hook.py: expired on 2026-10-10",
        "entry 1: hook.py: passes the lint now; remove the entry and the exclusion",
        "entry 2: gone.py: no such file; remove the entry and the exclusion",
    ]


# The linter.


def test_the_linter_judges_a_file_although_the_configuration_leaves_it_out(tmp_path: Path) -> None:
    root = plant(tmp_path, [entry("fails.py"), entry("format.py"), entry("clean.py")], files={})
    for name, content in (("fails.py", FAILS_LINT), ("format.py", FAILS_FORMAT), ("clean.py", CLEAN)):
        (tmp_path / name).write_text(content)
    assert check.passes_lint(root, "fails.py") is False
    assert check.passes_lint(root, "format.py") is False
    assert check.passes_lint(root, "clean.py") is True


def test_a_linter_that_cannot_run_is_an_error_not_a_result(tmp_path: Path) -> None:
    root = plant(tmp_path, [entry()])
    (tmp_path / "ruff.toml").write_text('extend-exclude = ["hook.py"]\nno-such-setting = 1\n')
    with pytest.raises(RuntimeError, match=r"ruff check could not run on hook\.py"):
        check.problems(root, TODAY)


# The script as the gate runs it.


def _run_script(root: Path) -> subprocess.CompletedProcess[str]:
    (root / "tools").mkdir()
    script = shutil.copy(SCRIPT, root / "tools" / "check_exceptions.py")
    return subprocess.run(
        [sys.executable, str(script)],
        cwd=os.sep,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def test_the_script_passes_on_a_list_that_holds(tmp_path: Path) -> None:
    soon = datetime.datetime.now(tz=datetime.UTC).date() + datetime.timedelta(days=30)
    plant(tmp_path, [entry(expires=soon)])
    done = _run_script(tmp_path)
    assert (done.returncode, done.stderr) == (0, "")
    assert done.stdout == "PASS: every entry of exceptions.toml is in date and still needed.\n"


def test_the_script_fails_on_an_expired_entry(tmp_path: Path) -> None:
    yesterday = datetime.datetime.now(tz=datetime.UTC).date() - datetime.timedelta(days=1)
    plant(tmp_path, [entry(expires=yesterday)])
    done = _run_script(tmp_path)
    assert (done.returncode, done.stdout) == (1, "")
    assert done.stderr == f"exceptions.toml: entry 1: hook.py: expired on {yesterday.isoformat()}\n"


def test_the_script_fails_on_a_stale_entry(tmp_path: Path) -> None:
    soon = datetime.datetime.now(tz=datetime.UTC).date() + datetime.timedelta(days=30)
    plant(tmp_path, [entry(expires=soon)], files={"hook.py": CLEAN})
    done = _run_script(tmp_path)
    assert (done.returncode, done.stdout) == (1, "")
    assert done.stderr == "exceptions.toml: entry 1: hook.py: passes the lint now; remove the entry and the exclusion\n"
