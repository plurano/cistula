# SPDX-FileCopyrightText: 2026 Lusoris <lusoris@proton.me>
# SPDX-License-Identifier: EUPL-1.2
from __future__ import annotations

import math
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from plurano_python_library import process
from plurano_python_library.process import (
    CommandFailedError,
    CommandNotFoundError,
    CommandNotStartedError,
    CommandTimeoutError,
    ProcessError,
    Result,
    child_env,
    run,
)

# Generous limit for children that are expected to finish on their own.
ENOUGH = 60.0

# A planted child needs this long at most to start and install its signal
# handling, even on a loaded machine; time limits that must expire are longer.
STARTED = 3.0


def python(code: str, *arguments: str) -> list[str]:
    return [sys.executable, "-c", code, *arguments]


def beating(path: Path) -> bool:
    """Tell whether the heartbeat file at `path` is still being written."""
    before = path.stat().st_size
    time.sleep(0.4)
    return path.stat().st_size != before


# Writes one byte to the file named by its argument every 20 ms, for ever.
HEARTBEAT = """
import sys, time
with open(sys.argv[1], "ab", buffering=0) as beat:
    while True:
        beat.write(b".")
        time.sleep(0.02)
"""

# Ignores the first signal a well-behaved child obeys, reports that, then beats.
IGNORES_TERM = (
    """
import signal, sys
signal.signal(signal.SIGTERM, signal.SIG_IGN)
print("ready", flush=True)
"""
    + HEARTBEAT
)


# --- a child that finishes ------------------------------------------------


def test_success_returns_output_and_status() -> None:
    result = run(python("import sys; print('out'); print('err', file=sys.stderr)"), timeout=ENOUGH)

    assert isinstance(result, Result)
    assert result.returncode == 0
    assert result.stdout == "out\n"
    assert result.stderr == "err\n"
    assert result.stdout_bytes == b"out\n"
    assert result.timed_out is False
    assert result.argv[0] == sys.executable
    assert isinstance(result.argv, tuple)
    assert 0 < result.duration < ENOUGH


def test_nonzero_exit_is_returned_without_check() -> None:
    result = run(python("raise SystemExit(3)"), timeout=ENOUGH)

    assert result.returncode == 3
    assert result.timed_out is False


def test_nonzero_exit_raises_with_check() -> None:
    with pytest.raises(CommandFailedError) as caught:
        run(python("import sys; print('partial'); sys.exit(3)"), timeout=ENOUGH, check=True)

    assert not isinstance(caught.value, CommandTimeoutError)
    assert isinstance(caught.value, ProcessError)
    assert caught.value.result.returncode == 3
    assert caught.value.result.stdout == "partial\n"
    assert "exit status 3" in str(caught.value)


def test_check_passes_a_successful_child_through() -> None:
    assert run(python("pass"), timeout=ENOUGH, check=True).returncode == 0


def test_child_ended_by_a_signal_has_a_negative_status() -> None:
    result = run(python("import os, signal; os.kill(os.getpid(), signal.SIGKILL)"), timeout=ENOUGH)

    assert result.returncode == -signal.SIGKILL
    assert result.timed_out is False


@pytest.mark.parametrize("data", ["grüße\n", "grüße\n".encode()])
def test_input_reaches_the_child(data: str | bytes) -> None:
    result = run(python("import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())"), timeout=ENOUGH, input=data)

    assert result.stdout == "grüße\n"


def test_without_input_the_child_reads_end_of_file_not_the_callers_input() -> None:
    # Planted: the caller's own standard input holds data. A child that
    # inherited it would read "leaked"; it must see the null device instead.
    readable, writable = os.pipe()
    os.write(writable, b"leaked")
    os.close(writable)
    saved = os.dup(0)
    os.dup2(readable, 0)
    os.close(readable)
    try:
        result = run(python("import sys; print(repr(sys.stdin.read()))"), timeout=ENOUGH)
    finally:
        os.dup2(saved, 0)
        os.close(saved)

    assert result.stdout == "''\n"


def test_empty_input_is_input_not_absence() -> None:
    result = run(python("import sys; print(repr(sys.stdin.read()))"), timeout=ENOUGH, input="")

    assert result.stdout == "''\n"


def test_cwd_and_path_arguments(tmp_path: Path) -> None:
    script = tmp_path / "where.py"
    script.write_text("import os; print(os.getcwd())", encoding="utf-8")

    result = run([Path(sys.executable), script], timeout=ENOUGH, cwd=tmp_path)

    assert result.stdout.strip() == str(tmp_path.resolve())
    assert result.argv == (sys.executable, str(script))


def test_env_replaces_the_whole_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PLANTED_PARENT_VARIABLE", "parent")
    code = "import os; print(os.environ.get('PLANTED_PARENT_VARIABLE'), os.environ.get('ONLY'))"

    inherited = run(python(code), timeout=ENOUGH)
    replaced = run(python(code), timeout=ENOUGH, env={"ONLY": "child"})

    assert inherited.stdout == "parent None\n"
    assert replaced.stdout == "None child\n"


def test_output_larger_than_a_pipe_buffer_is_collected_whole() -> None:
    size = 1024 * 1024
    code = f"import sys; sys.stdout.buffer.write(b'o' * {size}); sys.stderr.buffer.write(b'e' * {size})"

    result = run(python(code), timeout=ENOUGH)

    assert result.stdout_bytes == b"o" * size
    assert result.stderr_bytes == b"e" * size


def test_input_larger_than_a_pipe_buffer_is_delivered_whole() -> None:
    data = b"i" * (1024 * 1024)

    result = run(python("import sys; print(len(sys.stdin.buffer.read()))"), timeout=ENOUGH, input=data)

    assert result.stdout == f"{len(data)}\n"


def test_output_that_is_not_utf8_is_kept_as_bytes_and_replaced_in_text() -> None:
    result = run(python("import sys; sys.stdout.buffer.write(b'\\xff\\xfeok')"), timeout=ENOUGH)

    assert result.stdout_bytes == b"\xff\xfeok"
    assert result.stdout == "��ok"


# --- a command that cannot start -------------------------------------------


def test_missing_program_is_not_found(tmp_path: Path) -> None:
    with pytest.raises(CommandNotFoundError) as caught:
        run([tmp_path / "no-such-program"], timeout=ENOUGH)

    assert isinstance(caught.value, CommandNotStartedError)
    assert isinstance(caught.value, ProcessError)
    assert isinstance(caught.value.__cause__, FileNotFoundError)
    assert caught.value.argv == (str(tmp_path / "no-such-program"),)


def test_missing_working_directory_is_not_a_missing_program(tmp_path: Path) -> None:
    with pytest.raises(CommandNotStartedError) as caught:
        run(python("pass"), timeout=ENOUGH, cwd=tmp_path / "absent")

    assert not isinstance(caught.value, CommandNotFoundError)
    assert "working directory" in str(caught.value)


def test_missing_program_with_an_existing_working_directory_is_not_found(tmp_path: Path) -> None:
    with pytest.raises(CommandNotFoundError):
        run(["no-such-program-anywhere"], timeout=ENOUGH, cwd=tmp_path)


def test_file_without_execute_permission_is_not_started(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.write_text("#!/bin/sh\n", encoding="utf-8")
    plain.chmod(0o644)

    with pytest.raises(CommandNotStartedError) as caught:
        run([plain], timeout=ENOUGH)

    assert not isinstance(caught.value, CommandNotFoundError)
    assert isinstance(caught.value.__cause__, PermissionError)


# --- arguments that are refused ----------------------------------------------


@pytest.mark.parametrize("argv", ["echo hello", b"echo hello", bytearray(b"echo")])
def test_one_string_is_not_a_command(argv: Any) -> None:
    with pytest.raises(TypeError, match="not one string"):
        run(argv, timeout=ENOUGH)


@pytest.mark.parametrize("argv", [[], (), [""]])
def test_argv_must_name_a_program(argv: Any) -> None:
    with pytest.raises(ValueError, match="must name a program"):
        run(argv, timeout=ENOUGH)


@pytest.mark.parametrize("argv", [[sys.executable, 3], [sys.executable, None], [b"python"]])
def test_argv_elements_must_be_text_or_paths(argv: Any) -> None:
    with pytest.raises(TypeError):
        run(argv, timeout=ENOUGH)


def test_timeout_has_no_default() -> None:
    with pytest.raises(TypeError, match="timeout"):
        run(python("pass"))  # type: ignore[call-arg]


@pytest.mark.parametrize("timeout", [None, "5", True, False, [5]])
def test_timeout_must_be_a_number(timeout: Any) -> None:
    with pytest.raises(TypeError, match="timeout must be a number"):
        run(python("pass"), timeout=timeout)


@pytest.mark.parametrize("timeout", [0, 0.0, -1, -0.001, math.inf, -math.inf, math.nan])
def test_timeout_has_no_value_that_means_for_ever(timeout: float) -> None:
    with pytest.raises(ValueError, match="timeout must be finite and greater than zero"):
        run(python("pass"), timeout=timeout)


def test_smallest_timeout_is_accepted_and_expires() -> None:
    result = run(python("import time; time.sleep(60)"), timeout=5e-324, kill_after=0)

    assert result.timed_out is True


@pytest.mark.parametrize("kill_after", [None, "1", True])
def test_kill_after_must_be_a_number(kill_after: Any) -> None:
    with pytest.raises(TypeError, match="kill_after must be a number"):
        run(python("pass"), timeout=ENOUGH, kill_after=kill_after)


@pytest.mark.parametrize("kill_after", [-1, math.inf, math.nan])
def test_kill_after_must_be_finite_and_not_negative(kill_after: float) -> None:
    with pytest.raises(ValueError, match="kill_after must be finite and zero or more"):
        run(python("pass"), timeout=ENOUGH, kill_after=kill_after)


def test_refuses_to_run_without_process_groups(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(process, "_POSIX", False)

    with pytest.raises(NotImplementedError, match="process groups"):
        run(python("pass"), timeout=ENOUGH)


# --- the time limit ------------------------------------------------------------


def test_expired_limit_stops_an_obedient_child() -> None:
    result = run(python("import time; print('early', flush=True); time.sleep(60)"), timeout=STARTED)

    assert result.timed_out is True
    assert result.returncode == -signal.SIGTERM
    assert result.stdout == "early\n"
    assert STARTED <= result.duration < STARTED + 2.0


def test_child_that_handles_the_first_signal_keeps_its_exit_status() -> None:
    code = """
import signal, sys, time
signal.signal(signal.SIGTERM, lambda *_: sys.exit(7))
print("ready", flush=True)
time.sleep(60)
"""
    result = run(python(code), timeout=STARTED)

    assert result.stdout == "ready\n"
    assert result.timed_out is True
    assert result.returncode == 7


def test_child_that_ignores_the_first_signal_is_killed(tmp_path: Path) -> None:
    beat = tmp_path / "beat"

    result = run(python(IGNORES_TERM, str(beat)), timeout=STARTED, kill_after=0.5)

    # "ready" proves the child had installed its handler before the limit
    # expired, so the first signal really was ignored.
    assert result.stdout == "ready\n"
    assert result.timed_out is True
    assert result.returncode == -signal.SIGKILL
    assert STARTED + 0.5 <= result.duration < STARTED + 0.5 + 1.0
    assert not beating(beat)


def test_kill_after_zero_kills_at_once(tmp_path: Path) -> None:
    beat = tmp_path / "beat"

    result = run(python(IGNORES_TERM, str(beat)), timeout=STARTED, kill_after=0)

    assert result.stdout == "ready\n"
    assert result.returncode == -signal.SIGKILL
    assert result.duration < STARTED + 1.0
    assert not beating(beat)


def test_descendant_that_holds_the_output_is_killed_with_the_group(tmp_path: Path) -> None:
    beat = tmp_path / "beat"
    # The child starts a descendant that ignores the first signal and inherits
    # the output pipes, then exits at once with status 0.
    code = f"""
import subprocess, sys
subprocess.Popen([sys.executable, "-c", {IGNORES_TERM!r}, sys.argv[1]])
"""
    result = run(python(code, str(beat)), timeout=STARTED, kill_after=0.5)

    assert result.stdout == "ready\n"
    assert result.timed_out is True
    assert result.returncode == 0
    assert result.duration < STARTED + 0.5 + 1.0
    assert not beating(beat)


def test_descendant_outside_the_group_cannot_hold_the_call(tmp_path: Path) -> None:
    beat = tmp_path / "beat"
    # The descendant starts a session of its own, so the group signals do not
    # reach it, and it keeps the output pipes open.
    escaped = f"""
import os
os.setsid()
print(os.getpid(), flush=True)
{HEARTBEAT}
"""
    code = f"""
import subprocess, sys
subprocess.Popen([sys.executable, "-c", {escaped!r}, sys.argv[1]])
"""
    result = run(python(code, str(beat)), timeout=STARTED, kill_after=0.2)
    try:
        assert result.timed_out is True
        assert result.returncode == 0
        assert result.duration < STARTED + 0.2 + 1.0 + 1.0
        # Stated limit of the guarantee: the escaped descendant lives on.
        assert beating(beat)
    finally:
        os.kill(int(result.stdout), signal.SIGKILL)


def test_child_that_never_reads_its_input_is_stopped() -> None:
    data = b"i" * (1024 * 1024)

    result = run(python("import time; time.sleep(60)"), timeout=STARTED, input=data)

    assert result.timed_out is True
    assert result.duration < STARTED + 2.0


def test_expired_limit_raises_with_check() -> None:
    with pytest.raises(CommandTimeoutError) as caught:
        run(python("import time; print('early', flush=True); time.sleep(60)"), timeout=STARTED, check=True)

    assert isinstance(caught.value, CommandFailedError)
    assert caught.value.result.timed_out is True
    assert caught.value.result.stdout == "early\n"
    assert "time limit expired" in str(caught.value)


def test_interrupt_from_outside_leaves_no_child_running(tmp_path: Path) -> None:
    beat = tmp_path / "beat"
    interrupt = threading.Timer(STARTED, os.kill, (os.getpid(), signal.SIGINT))
    interrupt.start()
    try:
        with pytest.raises(KeyboardInterrupt):
            run(python(IGNORES_TERM, str(beat)), timeout=ENOUGH)
    finally:
        interrupt.cancel()

    assert beat.stat().st_size > 0
    assert not beating(beat)


def test_child_that_ends_as_the_limit_expires_is_reported_not_signalled(monkeypatch: pytest.MonkeyPatch) -> None:
    # Planted race: the child has ended and its exit status is collected at
    # the moment the limit expires, so the group no longer exists when the
    # stop signal is sent.
    real_communicate = subprocess.Popen.communicate
    limits: list[float | None] = []

    def late(child: subprocess.Popen[bytes], input: bytes | None = None, timeout: float | None = None) -> Any:  # noqa: A002
        limits.append(timeout)
        if len(limits) == 1:
            child.wait()
            raise subprocess.TimeoutExpired(child.args, ENOUGH)
        return real_communicate(child, input, timeout)

    monkeypatch.setattr(subprocess.Popen, "communicate", late)

    result = run(python("print('done')"), timeout=ENOUGH, kill_after=0.5)

    assert limits == [ENOUGH, 0.5]
    assert result.timed_out is True
    assert result.returncode == 0
    assert result.stdout == "done\n"


# --- child_env -------------------------------------------------------------------


BASE = {"PATH": "/bin", "HOME": "/home/u", "GIT_DIR": ".git", "GIT_WORK_TREE": ".", "git_lower": "x"}


def test_child_env_copies_the_callers_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PLANTED", "1")

    environment = child_env()
    environment["PLANTED"] = "changed"
    environment["ADDED"] = "1"

    assert environment is not os.environ
    assert os.environ["PLANTED"] == "1"
    assert "ADDED" not in os.environ
    assert child_env() == dict(os.environ)


def test_child_env_keep_is_an_allow_list() -> None:
    assert child_env(base=BASE, keep=["PATH", "HOME"]) == {"PATH": "/bin", "HOME": "/home/u"}
    assert child_env(base=BASE, keep=["GIT_*"]) == {"GIT_DIR": ".git", "GIT_WORK_TREE": "."}


def test_child_env_empty_keep_keeps_nothing() -> None:
    assert child_env(base=BASE, keep=[]) == {}
    assert child_env(base=BASE, keep=[], overrides={"ONLY": "1"}) == {"ONLY": "1"}


def test_child_env_drop_removes_matching_names_only() -> None:
    assert child_env(base=BASE, drop=["GIT_*"]) == {"PATH": "/bin", "HOME": "/home/u", "git_lower": "x"}
    assert child_env(base=BASE, drop=["ABSENT"]) == BASE


def test_child_env_drop_wins_over_keep_and_overrides_win_over_both() -> None:
    assert child_env(base=BASE, keep=["GIT_*"], drop=["GIT_DIR"]) == {"GIT_WORK_TREE": "."}
    assert child_env(base=BASE, keep=["PATH"], drop=["GIT_*"], overrides={"GIT_DIR": "set", "PATH": "/usr/bin"}) == {
        "PATH": "/usr/bin",
        "GIT_DIR": "set",
    }


def test_child_env_does_not_change_its_base() -> None:
    base = dict(BASE)

    child_env(base=base, drop=["*"], overrides={"NEW": "1"})

    assert base == BASE


@pytest.mark.parametrize("argument", ["keep", "drop"])
def test_child_env_refuses_one_string_as_patterns(argument: str) -> None:
    arguments: dict[str, Any] = {argument: "PATH"}

    with pytest.raises(TypeError, match="not one string"):
        child_env(base=BASE, **arguments)


@pytest.mark.parametrize("name", ["", "A=B", "A\0B"])
def test_child_env_refuses_an_invalid_override_name(name: str) -> None:
    with pytest.raises(ValueError, match="not a valid environment entry"):
        child_env(base=BASE, overrides={name: "1"})


def test_child_env_refuses_an_override_value_with_a_null_byte() -> None:
    with pytest.raises(ValueError, match="not a valid environment entry"):
        child_env(base=BASE, overrides={"A": "1\x002"})


@pytest.mark.parametrize("overrides", [{"A": 1}, {1: "A"}, {"A": None}])
def test_child_env_refuses_overrides_that_are_not_text(overrides: Any) -> None:
    with pytest.raises(TypeError, match="text names to text values"):
        child_env(base=BASE, overrides=overrides)


def test_child_env_feeds_run() -> None:
    environment = child_env(keep=["PATH"], overrides={"PLANTED": "seen"})

    result = run(
        python("import os; print(os.environ['PLANTED'], 'HOME' in os.environ)"), timeout=ENOUGH, env=environment
    )

    assert result.stdout == "seen False\n"
