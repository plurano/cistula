# SPDX-FileCopyrightText: 2026 Lusoris <lusoris@proton.me>
# SPDX-License-Identifier: EUPL-1.2
"""Check the declared exception list of this repository.

`exceptions.toml` names every file that is left out of a check: one file, one
rule, a reason and an expiry per entry. This script fails the gate when an
entry has expired, when an entry no longer matches the tree, and when
`ruff.toml` leaves a file out of the lint without an entry. An exception that
nobody renews therefore ends by itself, and one that is no longer needed
cannot stay.
"""

from __future__ import annotations

import datetime
import os
import subprocess
import sys
import tomllib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

LIST = "exceptions.toml"
LINT_CONFIG = "ruff.toml"
LINT_RULE = "python-lint"
MAX_DAYS = 90
FIELDS = frozenset({"file", "rule", "reason", "expires"})

_LINT_SECONDS = 120
_LINT_COMMANDS = (
    ("check", "--no-cache", "--quiet"),
    ("format", "--check", "--no-cache", "--quiet"),
)
_FOUND_SOMETHING = 1


def passes_lint(root: str, file: str) -> bool:
    """Report whether `file` passes the lint and the format check of the repository at `root`.

    The file is named on the command line, so the exclusion in `ruff.toml` does
    not apply to this run. A linter that cannot run is an error, never a
    result.
    """
    linter = os.path.join(os.path.dirname(sys.executable), "ruff")
    for arguments in _LINT_COMMANDS:
        done = subprocess.run(  # noqa: S603 - fixed argv, no shell is involved
            [linter, *arguments, "--", file],
            cwd=root,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=_LINT_SECONDS,
            check=False,
        )
        if done.returncode == _FOUND_SOMETHING:
            return False
        if done.returncode != 0:
            message = f"ruff {arguments[0]} could not run on {file}: {done.stderr.decode(errors='replace').strip()}"
            raise RuntimeError(message)
    return True


def _path_problem(root: str, file: str) -> str | None:
    """Return what is wrong with `file` as the path of one existing file, or None."""
    if os.path.isabs(file) or ".." in file.split("/") or any(sign in file for sign in "*?[\\"):
        return f"{file}: must be the path of one file inside the repository, not a pattern"
    if not os.path.isfile(os.path.join(root, file)):
        return f"{file}: no such file; remove the entry and the exclusion"
    return None


def _entry_problems(
    root: str, entry: dict[str, object], today: datetime.date, lint: Callable[[str, str], bool]
) -> list[str]:
    """Return what is wrong with one entry of the list."""
    if set(entry) != FIELDS:
        return [f"needs exactly the fields {sorted(FIELDS)}, found {sorted(entry)}"]
    file, rule, reason, expires = entry["file"], entry["rule"], entry["reason"], entry["expires"]
    if not isinstance(file, str) or not isinstance(reason, str) or type(expires) is not datetime.date:
        return ["file and reason must be text and expires a date"]
    found = []
    if rule != LINT_RULE:
        found.append(f"{file}: unknown rule {rule!r}; this list knows {LINT_RULE!r}")
    if not reason.strip():
        found.append(f"{file}: the reason is empty")
    if expires < today:
        found.append(f"{file}: expired on {expires.isoformat()}")
    if (expires - today).days > MAX_DAYS:
        found.append(f"{file}: expires {expires.isoformat()}, more than {MAX_DAYS} days ahead")
    broken = _path_problem(root, file)
    if broken is not None:
        found.append(broken)
    elif lint(root, file):
        found.append(f"{file}: passes the lint now; remove the entry and the exclusion")
    return found


def problems(root: str, today: datetime.date, lint: Callable[[str, str], bool] = passes_lint) -> list[str]:
    """Return every reason why the exception list of the repository at `root` does not hold on `today`."""
    with open(os.path.join(root, LIST), "rb") as stream:
        declared = tomllib.load(stream)
    with open(os.path.join(root, LINT_CONFIG), "rb") as stream:
        excluded = set(tomllib.load(stream).get("extend-exclude", []))
    found = [f"unknown table {name!r}" for name in sorted(set(declared) - {"exception"})]
    files: list[str] = []
    for number, entry in enumerate(declared.get("exception", []), start=1):
        found.extend(f"entry {number}: {text}" for text in _entry_problems(root, entry, today, lint))
        if isinstance(entry.get("file"), str):
            files.append(entry["file"])
    named = set(files)
    found.extend(f"{file}: named by more than one entry" for file in sorted(named) if files.count(file) > 1)
    found.extend(f"{file}: left out of the lint in {LINT_CONFIG} without an entry" for file in sorted(excluded - named))
    found.extend(f"{file}: has an entry but {LINT_CONFIG} does not leave it out" for file in sorted(named - excluded))
    return found


def main() -> int:
    """Check the repository this script lives in; exit status 1 when the list does not hold."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    found = problems(root, datetime.datetime.now(tz=datetime.UTC).date())
    for text in found:
        sys.stderr.write(f"{LIST}: {text}\n")
    if found:
        return 1
    sys.stdout.write(f"PASS: every entry of {LIST} is in date and still needed.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
