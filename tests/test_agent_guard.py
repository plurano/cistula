# SPDX-FileCopyrightText: 2026 Lusoris <lusoris@proton.me>
# SPDX-License-Identifier: EUPL-1.2
"""Run the shipped agent command guard against the commands it must refuse and admit.

The guard `.config/agent/hooks/block_evasion.py` is written by the engine's adoption and
is not called from any hook in this repository, so nothing else would show a guard that
stopped refusing. Each test runs the script the way a PreToolUse hook does: one JSON
object on stdin, exit 2 for a refusal.

The refused commands are built from pieces, so that this file never holds them whole and
the guard in front of the agent that edits it does not refuse the edit.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from tool_loader import REPOSITORY

GUARD = os.path.join(REPOSITORY, ".config", "agent", "hooks", "block_evasion.py")
REFUSED = 2

NO_VERIFY = "--no-" + "verify"
ADMIN = "--ad" + "min"
MERGE_PATH = "pul" + "ls/10/merge"
HOOKS_DIR = ".git/" + "hooks"
SHORT_N = "-" + "n"
# One line of exactly this many characters is the longest the guard scans.
LINE_BOUND = 2048


def guard(
    command: str | None = None,
    *,
    payload: bytes | None = None,
    args: tuple[str, ...] = (),
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[bytes]:
    """Run the guard on `command` as a PreToolUse payload, or on `payload` or `args`."""
    if payload is None:
        payload = json.dumps({"tool_input": {"command": command}}).encode()
    # The hook settings of the shell running the tests must not decide the result.
    env = {key: value for key, value in os.environ.items() if not key.startswith("LEFTHOOK")}
    env.update(extra_env or {})
    return subprocess.run(
        [sys.executable, "-I", GUARD, *args],
        input=payload,
        capture_output=True,
        timeout=30,
        check=False,
        env=env,
    )


def assert_refused(result: subprocess.CompletedProcess[bytes]) -> None:
    """Assert that the guard refused the call and said why."""
    assert result.returncode == REFUSED, result.stderr
    assert result.stderr.startswith(b"[BLOCKED BY"), result.stderr


def assert_admitted(result: subprocess.CompletedProcess[bytes]) -> None:
    """Assert that the guard admitted the call without a message."""
    assert (result.returncode, result.stderr) == (0, b"")


@pytest.mark.parametrize(
    "command",
    [
        f"git commit -m x {NO_VERIFY}",
        f"git commit -m x {SHORT_N}",
        "LEFT" + "HOOK=0 git push",
        "git config core.hooksPath /tmp/hooks",
        f"rm -rf {HOOKS_DIR}",
        "lefthook uninstall",
        f"gh pr merge 10 --squash {ADMIN}",
        f"gh api -X PUT repos/owner/name/{MERGE_PATH}",
        f"gh pr merge 10 --squash \\\n  {ADMIN}",
        f"curl -X PUT https://api.github.com/repos/owner/name/{MERGE_PATH}",
    ],
    ids=[
        "commit-long-flag",
        "commit-short-flag",
        "hooks-off",
        "hooks-path",
        "hooks-removed",
        "hooks-uninstalled",
        "merge-flag",
        "merge-endpoint",
        "merge-flag-continued",
        "merge-endpoint-curl",
    ],
)
def test_refuses_an_evasion(command: str) -> None:
    assert_refused(guard(command))


@pytest.mark.parametrize(
    "command",
    [
        "git commit -s -m x",
        "gh pr merge 10 --squash --match-head-commit abc",
        "gh pr view 10 --json mergeStateStatus",
        f"git log {SHORT_N} 3",
        # The short flag belongs to the read-only command after the commit.
        f"git commit -s -m x && git log {SHORT_N} 3",
    ],
    ids=["commit", "merge", "view", "log", "commit-then-log"],
)
def test_admits_a_normal_command(command: str) -> None:
    assert_admitted(guard(command))


@pytest.mark.parametrize(
    "payload",
    [
        b"not json",
        b"[]",
        json.dumps({"tool_input": {"command": "   "}}).encode(),
        json.dumps({"tool_input": {}}).encode(),
        json.dumps({"command": "git status"}).encode(),
    ],
    ids=["not-json", "not-an-object", "blank-command", "no-command", "no-tool-input"],
)
def test_refuses_input_of_another_shape(payload: bytes) -> None:
    assert_refused(guard(payload=payload))


def test_scans_a_line_up_to_the_bound() -> None:
    command = "echo " + "a" * (LINE_BOUND - len("echo "))
    assert len(command) == LINE_BOUND
    assert_admitted(guard(command))


def test_refuses_a_line_past_the_bound() -> None:
    assert_refused(guard("echo " + "a" * (LINE_BOUND - len("echo ") + 1)))


@pytest.mark.parametrize(
    "variable",
    [{"LEFTHOOK": "0"}, {"LEFTHOOK": "false"}, {"LEFTHOOK_SKIP": "pre-push"}],
    ids=["off", "false", "narrowed"],
)
def test_refuses_hooks_switched_off_in_the_environment(variable: dict[str, str]) -> None:
    assert_refused(guard("git status", extra_env=variable))


def test_reads_the_command_from_arguments() -> None:
    assert_refused(guard(args=("git", "commit", "-m", "x", NO_VERIFY), payload=b""))
    assert_admitted(guard(args=("git", "status"), payload=b""))
