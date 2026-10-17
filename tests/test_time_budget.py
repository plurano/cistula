# SPDX-FileCopyrightText: 2026 Lusoris <lusoris@proton.me>
# SPDX-License-Identifier: EUPL-1.2
from __future__ import annotations

from pathlib import Path

import pytest

CONFTEST = (Path(__file__).parent / "conftest.py").read_text(encoding="utf-8")

SLOW = """
import time

def test_slow():
    time.sleep(0.7)
"""

FAST = """
def test_fast():
    pass
"""


def suite(pytester: pytest.Pytester, budget: str, tests: str) -> pytest.RunResult:
    # In a process of its own: the budget's hang timer ends the process it runs in.
    pytester.makeconftest(CONFTEST)
    pytester.makeini(f"[pytest]\ntest_seconds = {budget}\n")
    pytester.makepyfile(tests)
    return pytester.runpytest_subprocess("-p", "no:cacheprovider", timeout=30)


def test_a_test_over_the_budget_fails(pytester: pytest.Pytester) -> None:
    result = suite(pytester, "0.5", SLOW)

    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*the test took *s, more than test_seconds = 0.5 s*"])


def test_a_test_inside_the_budget_passes(pytester: pytest.Pytester) -> None:
    suite(pytester, "5", SLOW + FAST).assert_outcomes(passed=2)


def test_a_failing_test_keeps_its_own_failure(pytester: pytest.Pytester) -> None:
    result = suite(pytester, "0.5", SLOW + "    assert False, 'own'\n")

    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*AssertionError: own*"])
    result.stdout.no_fnmatch_line("*more than test_seconds*")


def test_a_test_that_hangs_fails_at_twice_the_budget(pytester: pytest.Pytester) -> None:
    result = suite(pytester, "0.2", "import time\n\ndef test_hangs():\n    time.sleep(60)\n" + FAST)

    result.assert_outcomes(failed=1, passed=1)
    assert result.duration < 30
    result.stdout.fnmatch_lines(["*the test hung for 0.4 s, twice test_seconds, and was interrupted*"])


@pytest.mark.parametrize("budget", ["0", "-1", "nan", "inf", "soon"])
def test_a_budget_that_is_not_a_positive_number_is_refused(pytester: pytest.Pytester, budget: str) -> None:
    result = suite(pytester, budget, FAST)

    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*test_seconds must be a finite number of seconds greater than zero*"])
