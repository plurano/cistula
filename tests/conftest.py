# SPDX-FileCopyrightText: 2026 Lusoris <lusoris@proton.me>
# SPDX-License-Identifier: EUPL-1.2
"""A time budget for every test.

The gate runs the suite once per interpreter and the hook that runs the gate
before a push stops it after three minutes. A test that takes longer than
`test_seconds` (pyproject.toml) fails, so a slow test is caught where it is
written and not as a push that is cut off. A test that hangs never returns to
be measured: at twice the budget the main thread receives SIGINT, and the
interrupt it raises inside the test is reported as that test's failure.
"""

from __future__ import annotations

import math
import signal
import threading
import time
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Generator

pytest_plugins = ["pytester"]


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addini("test_seconds", "the longest a single test may take, in seconds", default="10")


def _budget(config: pytest.Config) -> float:
    text = str(config.getini("test_seconds"))
    try:
        seconds = float(text)
    except ValueError:
        seconds = math.nan
    if not (math.isfinite(seconds) and seconds > 0):
        message = f"test_seconds must be a finite number of seconds greater than zero, got {text!r}"
        raise pytest.UsageError(message)
    return seconds


def pytest_configure(config: pytest.Config) -> None:
    _budget(config)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item: pytest.Item) -> Generator[None, None, None]:
    budget = _budget(item.config)
    hung = threading.Event()

    def interrupt() -> None:
        hung.set()
        signal.pthread_kill(threading.main_thread().ident or 0, signal.SIGINT)

    watchdog = threading.Timer(2 * budget, interrupt)
    started = time.monotonic()
    watchdog.start()
    try:
        result = yield
    except KeyboardInterrupt:
        if not hung.is_set():
            raise
        pytest.fail(f"the test hung for {2 * budget:g} s, twice test_seconds, and was interrupted", pytrace=True)
    finally:
        watchdog.cancel()
        watchdog.join()
    took = time.monotonic() - started
    if took > budget:
        pytest.fail(f"the test took {took:.2f} s, more than test_seconds = {budget:g} s", pytrace=False)
    return result
