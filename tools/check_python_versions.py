# SPDX-FileCopyrightText: 2026 Lusoris <lusoris@proton.me>
# SPDX-License-Identifier: EUPL-1.2
"""Check that every file naming the supported Python versions names the same ones.

The classifiers of `pyproject.toml` are the list of supported versions; the
oldest is the floor. The gate runs the tests on the interpreters the Makefile
names, CI and the release build install the interpreters their workflows
(`ci.yml` and `release-build.yml`) name, the lint and the type check read the
floor from their own settings, and the lock is compiled for the floor. This
script fails the gate when one of these places disagrees with the
classifiers, so a version cannot be declared as supported without being
tested, and the floor cannot move in one place only.
"""

from __future__ import annotations

import os
import re
import sys
import tomllib

PROJECT = "pyproject.toml"
LINT_CONFIG = "ruff.toml"
MAKEFILE = "Makefile"
LOCK = "requirements/dev.txt"
WORKFLOW = ".github/workflows/ci.yml"
RELEASE_WORKFLOW = ".github/workflows/release-build.yml"

_CLASSIFIER = re.compile(r"^Programming Language :: Python :: (3\.\d+)$")
_FLOOR = re.compile(r"^>=(3\.\d+)$")
_GATE_INTERPRETERS = re.compile(r"^PYTHONS \?=(.*)$", re.MULTILINE)
_LOCK_TARGET = re.compile(r"--python-version=(\S+)")
_WORKFLOW_LIST = re.compile(r"^ +python-version: \|\n((?: +\S+\n)+)", re.MULTILINE)


def _text(root: str, file: str) -> str:
    with open(os.path.join(root, file), encoding="utf-8") as handle:
        return handle.read()


def _table(root: str, file: str) -> dict[str, object]:
    return tomllib.loads(_text(root, file))


def _inside(table: object, *keys: str) -> object:
    """Return the value under `keys`, or None where a table or a key is missing."""
    found = table
    for key in keys:
        found = found.get(key) if isinstance(found, dict) else None
    return found


def _order(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def supported(root: str) -> list[str]:
    """Return the versions the classifiers of `pyproject.toml` name, oldest first."""
    classifiers = _inside(_table(root, PROJECT), "project", "classifiers")
    names = classifiers if isinstance(classifiers, list) else []
    matches = [_CLASSIFIER.match(name) for name in names if isinstance(name, str)]
    return sorted((match.group(1) for match in matches if match), key=_order)


def _floor(root: str) -> str:
    """Return the floor `requires-python` states; any other form is returned as written."""
    stated = str(_inside(_table(root, PROJECT), "project", "requires-python"))
    match = _FLOOR.match(stated)
    return match.group(1) if match else stated


def _gate_interpreters(root: str) -> list[str]:
    found = _GATE_INTERPRETERS.findall(_text(root, MAKEFILE))
    return [name.removeprefix("python") for line in found for name in line.split()]


def _workflow_interpreters(root: str, file: str = WORKFLOW) -> list[str]:
    return [line.strip() for block in _WORKFLOW_LIST.findall(_text(root, file)) for line in block.splitlines()]


def places(root: str) -> list[tuple[str, object, object]]:
    """Return every place that names versions: where, what it names, what it has to name."""
    versions = supported(root)
    floor = versions[0] if versions else None
    return [
        (f"{PROJECT}: classifiers", versions or None, "at least one version"),
        (f"{PROJECT}: requires-python", _floor(root), floor),
        (f"{PROJECT}: python_version of mypy", _inside(_table(root, PROJECT), "tool", "mypy", "python_version"), floor),
        (f"{LINT_CONFIG}: target-version", _table(root, LINT_CONFIG).get("target-version"), _lint_target(floor)),
        (f"{MAKEFILE}: PYTHONS", _gate_interpreters(root), versions),
        (f"{MAKEFILE}: --python-version of the lock", _LOCK_TARGET.findall(_text(root, MAKEFILE)), [floor]),
        (f"{LOCK}: --python-version in the header", _LOCK_TARGET.findall(_text(root, LOCK)), [floor]),
        (f"{WORKFLOW}: python-version", _workflow_interpreters(root, WORKFLOW), versions),
        (f"{RELEASE_WORKFLOW}: python-version", _workflow_interpreters(root, RELEASE_WORKFLOW), versions),
    ]


def _lint_target(floor: str | None) -> str | None:
    return None if floor is None else "py" + floor.replace(".", "")


def problems(root: str) -> list[str]:
    """Return every place whose versions differ from the classifiers; empty when all agree."""
    found = []
    for place, named, expected in places(root):
        agrees = named is not None if expected == "at least one version" else named == expected
        if not agrees:
            found.append(f"{place}: names {named!r}, has to name {expected!r}")
    return found


def main() -> int:
    """Check the repository this script lives in; exit status 1 when a place disagrees."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    found = problems(root)
    for text in found:
        sys.stderr.write(f"{text}\n")
    if found:
        return 1
    versions = ", ".join(supported(root))
    sys.stdout.write(f"PASS: metadata, lint, type check, lock, gate and CI all name Python {versions}.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
