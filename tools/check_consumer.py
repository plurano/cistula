# SPDX-FileCopyrightText: 2026 Lusoris <lusoris@proton.me>
# SPDX-License-Identifier: EUPL-1.2
"""Install the built wheel the way a consumer does and run a program against it.

Usage: check_consumer.py DIRECTORY PROGRAM

DIRECTORY holds one wheel. The check creates a new virtual environment that
holds no package at all, not even pip, writes the requirements line the README
gives (the wheel by its file address and its SHA-256), and installs it with
the pip of the interpreter running this script: hashes required, no package
index, no dependencies, no source distribution, no pip configuration of the
host. PROGRAM then runs on the new environment's interpreter in isolated mode
(`-I`) from an empty directory, so it imports the installed package and
nothing from this tree. A step that exits non-zero fails the check, and the
failure quotes the end of what the step reported.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path

USAGE = "usage: check_consumer.py DIRECTORY PROGRAM"
# Lines of a failed step's output quoted in the failure.
TAIL = 20
# Seconds each step may take; creating the environment and installing one
# wheel from a file take about a second.
TIMEOUT = 300


def requirement(wheel: str) -> str:
    """Return the requirements line of the README for `wheel`: its name, file address and SHA-256."""
    path = Path(wheel).resolve()
    name = path.name.split("-", 1)[0]
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return f"{name} @ {path.as_uri()} --hash=sha256:{digest}\n"


def one_wheel(directory: str) -> str:
    """Return the path of the one wheel in `directory`."""
    names = sorted(name for name in os.listdir(directory) if name.endswith(".whl"))
    if len(names) != 1:
        message = f"expected one *.whl in {directory}, found {len(names)}"
        raise ValueError(message)
    return os.path.join(directory, names[0])


def scrubbed_environment() -> dict[str, str]:
    """Return this process's environment without the variables that steer pip or Python."""
    return {key: value for key, value in os.environ.items() if not key.startswith(("PIP_", "PYTHON"))}


def _call(argv: list[str], cwd: str) -> str:
    """Run one step and return its standard output; a non-zero exit or the time limit raises."""
    try:
        result = subprocess.run(  # noqa: S603 - the interpreters and the program are the caller's
            argv,
            cwd=cwd,
            env=scrubbed_environment(),
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        message = f"{' '.join(argv)} did not finish within {TIMEOUT} s"
        raise RuntimeError(message) from error
    if result.returncode != 0:
        tail = "\n".join((result.stdout + result.stderr).splitlines()[-TAIL:])
        message = f"{' '.join(argv)} exited {result.returncode}:\n{tail}"
        raise RuntimeError(message)
    return result.stdout


def check(directory: str, program: str) -> tuple[str, str]:
    """Install the wheel in `directory` into a new environment and run `program` there.

    Returns the wheel's file name and the program's standard output.
    """
    wheel = one_wheel(directory)
    program = os.path.abspath(program)
    if not os.path.isfile(program):
        message = f"no program at {program}"
        raise ValueError(message)
    with tempfile.TemporaryDirectory() as temporary:
        environment = os.path.join(temporary, "env")
        python = os.path.join(environment, "bin", "python")
        requirements = os.path.join(temporary, "requirements.txt")
        work = os.path.join(temporary, "work")
        os.mkdir(work)
        Path(requirements).write_text(requirement(wheel), encoding="utf-8")
        _call([sys.executable, "-m", "venv", "--without-pip", environment], temporary)
        install = ["install", "--no-index", "--no-deps", "--require-hashes", "--only-binary=:all:", "-r", requirements]
        _call(
            [sys.executable, "-m", "pip", "--isolated", "--disable-pip-version-check", "--python", python, *install],
            temporary,
        )
        output = _call([python, "-I", program], work)
    return os.path.basename(wheel), output


def main(arguments: list[str]) -> int:
    """Check the build directory and the program named on the command line."""
    if len(arguments) != 2:  # noqa: PLR2004 - the build and the program
        sys.stderr.write(f"{USAGE}\n")
        return 2
    directory, program = arguments
    try:
        wheel, output = check(directory, program)
    except (ValueError, RuntimeError) as error:
        sys.stderr.write(f"FAIL: {error}\n")
        return 1
    except OSError as error:
        sys.stderr.write(f"could not run the consumer check on {directory}: {error}\n")
        return 2
    sys.stdout.write(output)
    sys.stdout.write(
        f"PASS: a new environment installed {wheel} by its hash and ran {os.path.basename(program)} against it.\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
