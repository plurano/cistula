# SPDX-FileCopyrightText: 2026 Lusoris <lusoris@proton.me>
# SPDX-License-Identifier: EUPL-1.2
"""Hold renovate.json to updates Renovate can raise.

The shared preset, default.json of plurano/horistes, makes every update wait
three days from its release timestamp, and Renovate's default
minimumReleaseAgeBehaviour, timestamp-required, holds an update without a
timestamp for good. The git-refs datasource gives none, so each custom manager
on it needs a rule that lifts the release age for its dependency, and no later
rule may set the age again.
"""

from __future__ import annotations

import copy
import os
from typing import Any

import pytest

from cistula.files import read_json
from tool_loader import REPOSITORY

# Datasources whose releases carry no timestamp (Renovate lib/modules/datasource/git-refs).
UNTIMED = frozenset({"git-refs"})
PIN = "praetor-workflow-engine-pin"

Config = dict[str, Any]


def committed() -> Config:
    config = read_json(os.path.join(REPOSITORY, "renovate.json"))
    assert isinstance(config, dict)
    return config


def lifts(rule: Config, name: str) -> bool:
    """Tell whether a rule matched by dependency name alone lifts the release age for `name`."""
    if {key for key in rule if key.startswith(("match", "exclude"))} != {"matchDepNames"}:
        return False
    lifted = "minimumReleaseAge" in rule and rule["minimumReleaseAge"] is None
    optional = rule.get("minimumReleaseAgeBehaviour") == "timestamp-optional"
    return name in rule["matchDepNames"] and (lifted or optional)


def imposes(rule: Config) -> bool:
    """Tell whether a rule sets a release age or requires its timestamp again."""
    age = rule.get("minimumReleaseAge")
    return age is not None or rule.get("minimumReleaseAgeBehaviour") == "timestamp-required"


def held_for_good(config: Config) -> list[str]:
    """Return each dependency of an untimed custom manager whose release age no last rule lifts."""
    held = []
    for manager in config.get("customManagers", []):
        if manager.get("datasourceTemplate") not in UNTIMED:
            continue
        name = manager["depNameTemplate"]
        lifted = False
        for rule in config.get("packageRules", []):
            if lifts(rule, name):
                lifted = True
            elif imposes(rule):
                lifted = False
        if not lifted:
            held.append(name)
    return held


def without_pin_rule(config: Config) -> Config:
    changed = copy.deepcopy(config)
    changed["packageRules"] = [rule for rule in changed["packageRules"] if PIN not in rule.get("matchDepNames", [])]
    return changed


def pin_rule(config: Config) -> Config:
    rules: list[Config] = config["packageRules"]
    return next(rule for rule in rules if PIN in rule.get("matchDepNames", []))


def test_the_committed_file_lifts_the_release_age_of_every_untimed_manager() -> None:
    config = committed()
    assert [m["depNameTemplate"] for m in config["customManagers"] if m.get("datasourceTemplate") in UNTIMED] == [PIN]
    assert held_for_good(config) == []


def test_a_file_without_the_pin_rule_holds_the_pin() -> None:
    assert held_for_good(without_pin_rule(committed())) == [PIN]


def test_a_later_rule_that_sets_the_age_again_holds_the_pin() -> None:
    config = copy.deepcopy(committed())
    config["packageRules"].append({"matchDatasources": ["git-refs"], "minimumReleaseAge": "3 days"})
    assert held_for_good(config) == [PIN]


@pytest.mark.parametrize("matcher", ["matchDatasources", "matchFileNames", "excludeDepNames"])
def test_a_pin_rule_narrowed_by_another_matcher_does_not_count(matcher: str) -> None:
    config = copy.deepcopy(committed())
    pin_rule(config)[matcher] = ["other"]
    assert held_for_good(config) == [PIN]


def test_a_pin_rule_with_an_age_does_not_count() -> None:
    config = copy.deepcopy(committed())
    pin_rule(config)["minimumReleaseAge"] = "1 day"
    assert held_for_good(config) == [PIN]


def test_a_pin_rule_that_makes_the_timestamp_optional_counts() -> None:
    config = copy.deepcopy(committed())
    rule = pin_rule(config)
    del rule["minimumReleaseAge"]
    rule["minimumReleaseAgeBehaviour"] = "timestamp-optional"
    assert held_for_good(config) == []
