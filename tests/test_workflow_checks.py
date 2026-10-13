# SPDX-FileCopyrightText: 2026 Lusoris <lusoris@proton.me>
# SPDX-License-Identifier: EUPL-1.2
"""Run the checks written as shell text in the workflows and the Makefile against planted defects.

CI runs these checks only on a pull request, a tag or a release, so a check
that stopped refusing would pass there without anyone seeing it. Each test
takes the exact text from the file it lives in, runs it the way the runner
does, and sees it refuse the defect and admit the good case.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import textwrap
from typing import TYPE_CHECKING

import pytest

from tool_loader import REPOSITORY

if TYPE_CHECKING:
    from pathlib import Path

PROGRAMS = {name: shutil.which(name) for name in ("bash", "git", "make")}

pytestmark = pytest.mark.skipif(
    None in PROGRAMS.values(),
    reason="the checks are shell text that needs bash, git and make on PATH",
)

SIGN_OFF = ("ci.yml", "Sign-off on pull request commits")
TAG_CHECK = ("release-build.yml", "Read the version and check the tag")
PACKAGE_PICK = ("release.yml", "Pick the two package files")
ENGINE_BUILD = ("ci.yml", "Build pinned praetorctl from the published Praetor commit")
ENGINE_PIN_TEST = 'rev-parse HEAD)" = "$PRAETOR_REF"'

TRAILER = "\n\nSigned-off-by: Tester <tester@example.com>"

ENV = {
    "PATH": os.environ.get("PATH", ""),
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "Tester",
    "GIT_AUTHOR_EMAIL": "tester@example.com",
    "GIT_COMMITTER_NAME": "Tester",
    "GIT_COMMITTER_EMAIL": "tester@example.com",
}


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def run_block(workflow: str, step: str) -> str:
    """Return the `run: |` text of the one step named `step` in `.github/workflows/<workflow>`."""
    with open(os.path.join(REPOSITORY, ".github", "workflows", workflow), encoding="utf-8") as handle:
        lines = handle.read().splitlines()
    starts = [number for number, line in enumerate(lines) if line.strip() == f"- name: {step}"]
    assert len(starts) == 1, f"{workflow} names the step {step!r} {len(starts)} times"
    rest = lines[starts[0] + 1 :]
    keys = [number for number, line in enumerate(rest) if line.strip() == "run: |"]
    assert keys, f"the step {step!r} has no run block"
    before = rest[: keys[0]]
    assert not any(line.strip().startswith("- name:") for line in before), f"the step {step!r} has no run block"
    body = []
    for line in rest[keys[0] + 1 :]:
        if line.strip() and _indent(line) <= _indent(rest[keys[0]]):
            break
        body.append(line)
    return textwrap.dedent("\n".join(body)) + "\n"


def shell(text: str, tmp_path: Path, **env: str) -> subprocess.CompletedProcess[str]:
    """Run `text` in `tmp_path/work` the way the runner runs a `run` block."""
    script = tmp_path / "step.sh"
    script.write_text(text, encoding="utf-8")
    return subprocess.run(
        [str(PROGRAMS["bash"]), "--noprofile", "--norc", "-eo", "pipefail", str(script)],
        cwd=tmp_path / "work",
        env={**ENV, "HOME": str(tmp_path), **env},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def git(repository: Path, *arguments: str) -> str:
    done = subprocess.run(
        [str(PROGRAMS["git"]), *arguments],
        cwd=repository,
        env=ENV,
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    return done.stdout.strip()


def commit(repository: Path, message: str) -> str:
    git(repository, "commit", "-q", "--allow-empty", "-m", message)
    return git(repository, "rev-parse", "HEAD")


@pytest.fixture
def work(tmp_path: Path) -> Path:
    """Return an empty Git repository at `tmp_path/work` with one commit."""
    repository = tmp_path / "work"
    repository.mkdir()
    git(repository, "init", "-q", "-b", "main")
    commit(repository, "Start" + TRAILER)
    return repository


def sign_off(tmp_path: Path, base: str, head: str) -> subprocess.CompletedProcess[str]:
    return shell(run_block(*SIGN_OFF), tmp_path, BASE_SHA=base, HEAD_SHA=head)


def test_sign_off_admits_signed_commits(tmp_path: Path, work: Path) -> None:
    base = git(work, "rev-parse", "HEAD")
    commit(work, "One" + TRAILER)
    head = commit(work, "Two" + TRAILER)
    assert sign_off(tmp_path, base, head).returncode == 0


def test_sign_off_refuses_a_commit_without_the_trailer(tmp_path: Path, work: Path) -> None:
    base = git(work, "rev-parse", "HEAD")
    unsigned = commit(work, "One")
    head = commit(work, "Two" + TRAILER)
    result = sign_off(tmp_path, base, head)
    assert result.returncode == 1
    assert f"commit {unsigned} lacks" in result.stdout


def test_sign_off_refuses_the_trailer_inside_a_line(tmp_path: Path, work: Path) -> None:
    base = git(work, "rev-parse", "HEAD")
    head = commit(work, "One\n\nsee Signed-off-by: Tester <tester@example.com>")
    assert sign_off(tmp_path, base, head).returncode == 1


def test_sign_off_skips_merge_commits(tmp_path: Path, work: Path) -> None:
    base = git(work, "rev-parse", "HEAD")
    git(work, "switch", "-q", "-c", "side")
    commit(work, "Side" + TRAILER)
    git(work, "switch", "-q", "main")
    commit(work, "Main" + TRAILER)
    git(work, "merge", "-q", "--no-ff", "-m", "Merge side", "side")
    assert sign_off(tmp_path, base, git(work, "rev-parse", "HEAD")).returncode == 0


@pytest.fixture
def release(work: Path) -> Path:
    """Return the work repository holding version 1.2.3, with `origin/main` at its head."""
    (work / "pyproject.toml").write_text('[project]\nversion = "1.2.3"\n', encoding="utf-8")
    git(work, "add", "pyproject.toml")
    commit(work, "Version")
    git(work, "update-ref", "refs/remotes/origin/main", "HEAD")
    return work


def tag_check(tmp_path: Path, *, is_tag: bool, tag: str) -> subprocess.CompletedProcess[str]:
    output = tmp_path / "output"
    return shell(
        run_block(*TAG_CHECK),
        tmp_path,
        IS_TAG=str(is_tag).lower(),
        TAG=tag,
        GITHUB_OUTPUT=str(output),
        GITHUB_SHA="0" * 40,
    )


@pytest.mark.usefixtures("release")
def test_tag_check_admits_the_version_tag_on_main(tmp_path: Path) -> None:
    assert tag_check(tmp_path, is_tag=True, tag="v1.2.3").returncode == 0
    assert (tmp_path / "output").read_text(encoding="utf-8") == "version=1.2.3\n"


@pytest.mark.usefixtures("release")
def test_tag_check_refuses_a_tag_naming_another_version(tmp_path: Path) -> None:
    result = tag_check(tmp_path, is_tag=True, tag="v1.2.4")
    assert result.returncode == 1
    assert "tag v1.2.4 is not v1.2.3" in result.stdout


def test_tag_check_refuses_a_commit_not_on_main(tmp_path: Path, release: Path) -> None:
    commit(release, "Not on main")
    result = tag_check(tmp_path, is_tag=True, tag="v1.2.3")
    assert result.returncode == 1
    assert "which is not on main" in result.stdout


@pytest.mark.usefixtures("release")
def test_tag_check_reads_only_the_version_without_a_tag(tmp_path: Path) -> None:
    assert tag_check(tmp_path, is_tag=False, tag="v9.9.9").returncode == 0
    assert (tmp_path / "output").read_text(encoding="utf-8") == "version=1.2.3\n"


def pick(tmp_path: Path, *names: str) -> subprocess.CompletedProcess[str]:
    dist = tmp_path / "work" / "dist"
    dist.mkdir(parents=True)
    for name in names:
        (dist / name).write_bytes(b"")
    return shell(run_block(*PACKAGE_PICK), tmp_path)


def test_package_pick_takes_the_wheel_and_the_sdist_only(tmp_path: Path) -> None:
    files = ["cistula-1.2.3-py3-none-any.whl", "cistula-1.2.3.tar.gz", "SHA256SUMS", "cistula.spdx.json"]
    assert pick(tmp_path, *files).returncode == 0
    assert sorted(os.listdir(tmp_path / "work" / "pypi-dist")) == sorted(files[:2])


def test_package_pick_refuses_a_second_wheel(tmp_path: Path) -> None:
    files = ["cistula-1.2.3-py3-none-any.whl", "other-1.0-py3-none-any.whl", "cistula-1.2.3.tar.gz"]
    assert pick(tmp_path, *files).returncode != 0


def test_package_pick_refuses_a_missing_sdist(tmp_path: Path) -> None:
    assert pick(tmp_path, "cistula-1.2.3-py3-none-any.whl").returncode != 0


def engine_pin(tmp_path: Path, reference: str) -> subprocess.CompletedProcess[str]:
    found = [line for line in run_block(*ENGINE_BUILD).splitlines() if ENGINE_PIN_TEST in line]
    assert len(found) == 1, f"the engine build step holds the pin test {len(found)} times"
    return shell(found[0] + "\n", tmp_path, PRAETOR_REF=reference)


@pytest.fixture
def engine(tmp_path: Path) -> str:
    """Return the head of a repository checked out at `tmp_path/work/praetor-src`."""
    source = tmp_path / "work" / "praetor-src"
    source.mkdir(parents=True)
    git(source, "init", "-q", "-b", "main")
    return commit(source, "Engine")


def test_engine_pin_admits_the_pinned_commit(tmp_path: Path, engine: str) -> None:
    assert engine_pin(tmp_path, engine).returncode == 0


@pytest.mark.usefixtures("engine")
@pytest.mark.parametrize("reference", ["f" * 40, ""])
def test_engine_pin_refuses_another_commit(tmp_path: Path, reference: str) -> None:
    assert engine_pin(tmp_path, reference).returncode == 1


def verify_engine_pin(tmp_path: Path, workflow: str | None) -> subprocess.CompletedProcess[str]:
    copy = tmp_path / "work"
    copy.mkdir()
    shutil.copyfile(os.path.join(REPOSITORY, "Makefile"), copy / "Makefile")
    if workflow is not None:
        (copy / ".github" / "workflows").mkdir(parents=True)
        (copy / ".github" / "workflows" / "ci.yml").write_text(workflow, encoding="utf-8")
    return subprocess.run(
        [str(PROGRAMS["make"]), "-s", "-f", "Makefile", "verify-engine-pin"],
        cwd=copy,
        env={**ENV, "HOME": str(tmp_path)},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_verify_engine_pin_admits_a_workflow_that_pins_the_engine(tmp_path: Path) -> None:
    assert verify_engine_pin(tmp_path, 'env:\n  PRAETOR_REF: "abc"\n').returncode == 0


@pytest.mark.parametrize("workflow", [None, "env:\n  OTHER: x\n", "# PRAETOR_REF: x\n"])
def test_verify_engine_pin_refuses_a_missing_pin(tmp_path: Path, workflow: str | None) -> None:
    result = verify_engine_pin(tmp_path, workflow)
    assert result.returncode != 0
    assert "no workflow declares PRAETOR_REF" in result.stderr


def _on_block(lines: list[str]) -> list[str]:
    """Return the lines of the `on:` block: up to the next line that starts without indentation."""
    # YAML 1.1 reads a bare `on` as true, so a generated workflow quotes the key.
    starts = [number for number, line in enumerate(lines) if line in {"on:", "'on':", '"on":'}]
    assert len(starts) == 1, f"{len(starts)} trigger blocks"
    block = []
    for line in lines[starts[0] + 1 :]:
        if line.strip() and not line[0].isspace():
            break
        block.append(line.strip())
    return block


def test_every_required_check_reports_in_a_merge_queue() -> None:
    with open(os.path.join(REPOSITORY, ".github", "rulesets", "main.json"), encoding="utf-8") as handle:
        rules = json.load(handle)["rules"]
    contexts = [
        check["context"]
        for rule in rules
        if rule["type"] == "required_status_checks"
        for check in rule["parameters"]["required_status_checks"]
    ]
    assert contexts
    folder = os.path.join(REPOSITORY, ".github", "workflows")
    workflows = {}
    for name in sorted(os.listdir(folder)):
        with open(os.path.join(folder, name), encoding="utf-8") as handle:
            workflows[name] = handle.read().splitlines()
    for context in contexts:
        found = [name for name, lines in workflows.items() if f"    name: {context}" in lines]
        assert len(found) == 1, f"{len(found)} workflows run the required check {context!r}"
        triggers = _on_block(workflows[found[0]])
        assert any(line.startswith("merge_group:") for line in triggers), (
            f"{found[0]} runs the required check {context!r} but not in a merge queue"
        )
