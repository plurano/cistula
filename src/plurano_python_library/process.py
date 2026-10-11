# SPDX-FileCopyrightText: 2026 Lusoris <lusoris@proton.me>
# SPDX-License-Identifier: EUPL-1.2
"""Run a child process under a time limit that the caller must state.

`run` starts the command in a process group of its own. When the time limit
expires, the whole group is asked to stop and, after a grace period, killed,
so a child that ignores the first signal or leaves a descendant behind cannot
keep the caller waiting. `child_env` builds the environment for such a child
without touching the environment of the calling process.

POSIX only: process groups and their signals do not exist on Windows, and
`run` refuses to start there instead of running without the guarantee.
"""

from __future__ import annotations

import fnmatch
import math
import os
import signal
import subprocess
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

__all__ = [
    "CommandFailedError",
    "CommandNotFoundError",
    "CommandNotStartedError",
    "CommandTimeoutError",
    "ProcessError",
    "Result",
    "child_env",
    "run",
]

# Seconds the output pipes may stay open after the group was killed. Only a
# descendant that left the process group can hold them longer; its output is
# then cut off here and the call returns.
_DRAIN_SECONDS = 1.0

_POSIX = os.name == "posix"


@dataclass(frozen=True, slots=True)
class Result:
    """Outcome of one finished child process.

    `returncode` is the exit status; a negative value `-N` means the child was
    ended by signal `N`. `timed_out` is true when the time limit expired and
    the process group was stopped; the output collected until then is kept.
    `duration` is the wall-clock time of the call in seconds.
    """

    argv: tuple[str, ...]
    returncode: int
    stdout_bytes: bytes
    stderr_bytes: bytes
    timed_out: bool
    duration: float

    @property
    def stdout(self) -> str:
        """Standard output as UTF-8 text; undecodable bytes become U+FFFD."""
        return self.stdout_bytes.decode("utf-8", errors="replace")

    @property
    def stderr(self) -> str:
        """Standard error as UTF-8 text; undecodable bytes become U+FFFD."""
        return self.stderr_bytes.decode("utf-8", errors="replace")


class ProcessError(Exception):
    """Base class of every error this module raises about a child process."""


class CommandNotStartedError(ProcessError):
    """The command could not be started; `__cause__` holds the operating system error."""

    def __init__(self, argv: tuple[str, ...], reason: str) -> None:
        """Record the command and why the operating system refused it."""
        super().__init__(f"could not start {argv[0]!r}: {reason}")
        self.argv = argv
        self.reason = reason


class CommandNotFoundError(CommandNotStartedError):
    """The program named by the first element of `argv` does not exist."""


class CommandFailedError(ProcessError):
    """`check=True` and the child ended with a non-zero exit status."""

    def __init__(self, result: Result) -> None:
        """Record the outcome of the child, output included."""
        super().__init__(self._describe(result))
        self.result = result

    @staticmethod
    def _describe(result: Result) -> str:
        return f"{result.argv[0]!r} ended with exit status {result.returncode}"


class CommandTimeoutError(CommandFailedError):
    """`check=True` and the child was stopped because its time limit expired."""

    @staticmethod
    def _describe(result: Result) -> str:
        return f"{result.argv[0]!r} was stopped after {result.duration:.1f} s: time limit expired"


def _seconds(name: str, value: float, *, zero_allowed: bool) -> float:
    """Return `value` as a finite number of seconds, or refuse it."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        message = f"{name} must be a number of seconds, not {type(value).__name__}"
        raise TypeError(message)
    seconds = float(value)
    if not math.isfinite(seconds) or seconds < 0 or (seconds == 0 and not zero_allowed):
        bound = "zero or more" if zero_allowed else "greater than zero"
        message = f"{name} must be finite and {bound}, not {value!r}"
        raise ValueError(message)
    return seconds


def _arguments(argv: Sequence[str | os.PathLike[str]]) -> tuple[str, ...]:
    """Return `argv` as a tuple of strings; a single string is refused."""
    if isinstance(argv, (str, bytes, bytearray)):
        message = "argv must be a sequence of arguments, not one string; nothing is passed to a shell"
        raise TypeError(message)
    arguments = tuple(os.fspath(argument) for argument in argv)
    if not arguments:
        message = "argv must name a program"
        raise ValueError(message)
    for argument in arguments:
        if not isinstance(argument, str):
            message = f"argv elements must be text or paths, not {type(argument).__name__}"
            raise TypeError(message)
    if not arguments[0]:
        message = "argv must name a program"
        raise ValueError(message)
    return arguments


def _signal_group(group: int, number: signal.Signals) -> None:
    """Send `number` to every process of `group`; a group that is gone is fine."""
    try:
        os.killpg(group, number)
    except ProcessLookupError:
        pass


def _stop_group(child: subprocess.Popen[bytes], kill_after: float) -> tuple[bytes | None, bytes | None]:
    """Stop the child's process group and return the output collected so far."""
    out: bytes | None = None
    err: bytes | None = None
    _signal_group(child.pid, signal.SIGTERM)
    for wait in (kill_after, _DRAIN_SECONDS):
        try:
            return child.communicate(timeout=wait)
        except subprocess.TimeoutExpired as expired:
            out, err = expired.stdout, expired.stderr
            _signal_group(child.pid, signal.SIGKILL)
    # A descendant outside the group still holds a pipe. The child itself is
    # dead; the caller closes our ends, so keep what was read.
    return out, err


def _release(child: subprocess.Popen[bytes]) -> None:
    """Close our ends of the child's pipes and collect its exit status."""
    for pipe in (child.stdin, child.stdout, child.stderr):
        if pipe is not None:
            pipe.close()
    child.wait()


def _start(
    arguments: tuple[str, ...],
    cwd: str | os.PathLike[str] | None,
    env: Mapping[str, str] | None,
    data: bytes | None,
) -> subprocess.Popen[bytes]:
    """Start the child as the leader of a new session and process group."""
    try:
        return subprocess.Popen(  # noqa: S603 - argv is a list, no shell is involved
            arguments,
            cwd=cwd,
            env=None if env is None else dict(env),
            stdin=subprocess.DEVNULL if data is None else subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except FileNotFoundError as error:
        # The operating system reports a missing working directory the same
        # way; only a missing program is "not found".
        if error.filename is not None and cwd is not None and os.fspath(error.filename) == os.fspath(cwd):
            raise CommandNotStartedError(arguments, f"working directory {os.fspath(cwd)!r} does not exist") from error
        raise CommandNotFoundError(arguments, "no such program") from error
    except OSError as error:
        raise CommandNotStartedError(arguments, error.strerror or str(error)) from error


def _collect(
    child: subprocess.Popen[bytes], data: bytes | None, limit: float, grace: float
) -> tuple[bytes | None, bytes | None, bool]:
    """Wait for the child within `limit`; return its output and whether the limit expired."""
    timed_out = False
    out: bytes | None
    err: bytes | None
    try:
        try:
            out, err = child.communicate(data, timeout=limit)
        except subprocess.TimeoutExpired:
            timed_out = True
            out, err = _stop_group(child, grace)
    except BaseException:
        # Interrupted from outside (for example Ctrl-C): leave nothing running.
        _signal_group(child.pid, signal.SIGKILL)
        raise
    finally:
        # Whatever happened, no pipe stays open and no child stays unreaped.
        _release(child)
    return out, err, timed_out


def run(  # noqa: PLR0913 - one keyword per independent setting of a child process
    argv: Sequence[str | os.PathLike[str]],
    *,
    timeout: float,
    cwd: str | os.PathLike[str] | None = None,
    env: Mapping[str, str] | None = None,
    input: str | bytes | None = None,  # noqa: A002 - same name as subprocess.run
    check: bool = False,
    kill_after: float = 2.0,
) -> Result:
    """Run `argv` and return its outcome; never wait longer than the time limit allows.

    `timeout` is required and has no value that means "no limit": it must be a
    finite number of seconds greater than zero. When it expires, the child's
    process group receives SIGTERM, then SIGKILL after `kill_after` seconds,
    and the call returns with `timed_out` set. The longest a call can take is
    `timeout + kill_after` plus one second for reading the remaining output.

    `argv` is a sequence of arguments and is never given to a shell. `env`
    replaces the whole environment of the child; without it the child inherits
    the caller's. `input` is written to the child's standard input (text as
    UTF-8); without it the child reads from the null device, so it cannot wait
    for a terminal.

    With `check=True` a non-zero exit status raises `CommandFailedError` and an
    expired time limit raises `CommandTimeoutError`; both carry the `Result`.
    A missing program raises `CommandNotFoundError`, any other failure to start
    raises `CommandNotStartedError`.
    """
    arguments = _arguments(argv)
    limit = _seconds("timeout", timeout, zero_allowed=False)
    grace = _seconds("kill_after", kill_after, zero_allowed=True)
    if not _POSIX:
        message = "run needs POSIX process groups to enforce the time limit"
        raise NotImplementedError(message)
    data = input.encode("utf-8") if isinstance(input, str) else input
    started = time.monotonic()
    child = _start(arguments, cwd, env, data)
    out, err, timed_out = _collect(child, data, limit, grace)
    result = Result(
        argv=arguments,
        returncode=child.returncode,
        stdout_bytes=out or b"",
        stderr_bytes=err or b"",
        timed_out=timed_out,
        duration=time.monotonic() - started,
    )
    if check and timed_out:
        raise CommandTimeoutError(result)
    if check and result.returncode != 0:
        raise CommandFailedError(result)
    return result


def _matches(name: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns)


def _patterns(name: str, value: Iterable[str]) -> tuple[str, ...]:
    if isinstance(value, str):
        message = f"{name} must be a collection of names, not one string"
        raise TypeError(message)
    return tuple(value)


def child_env(
    *,
    keep: Iterable[str] | None = None,
    drop: Iterable[str] = (),
    overrides: Mapping[str, str] | None = None,
    base: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return a new environment for a child process; `os.environ` is never changed.

    Starts from `base` (the caller's environment when omitted). With `keep`,
    only variables whose name matches one of its patterns stay; without it all
    stay. Variables matching a `drop` pattern are then removed, and `overrides`
    is applied last, so it wins over both. Patterns are case-sensitive shell
    patterns: `GIT_*` matches every variable that starts with `GIT_`.
    """
    kept = None if keep is None else _patterns("keep", keep)
    dropped = _patterns("drop", drop)
    source = os.environ if base is None else base
    environment = {
        name: value
        for name, value in source.items()
        if (kept is None or _matches(name, kept)) and not _matches(name, dropped)
    }
    for name, value in ({} if overrides is None else overrides).items():
        if not isinstance(name, str) or not isinstance(value, str):
            message = "overrides must map text names to text values"
            raise TypeError(message)
        if not name or "=" in name or "\0" in name or "\0" in value:
            message = f"not a valid environment entry: {name!r}"
            raise ValueError(message)
        environment[name] = value
    return environment
