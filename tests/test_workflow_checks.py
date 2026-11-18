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
import re
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
PAGES_HEAD = ("pages.yml", "Stop when main has moved on")

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


def workflow_lines(workflow: str) -> list[str]:
    with open(os.path.join(REPOSITORY, ".github", "workflows", workflow), encoding="utf-8") as handle:
        return handle.read().splitlines()


def run_block(workflow: str, step: str) -> str:
    """Return the `run: |` text of the one step named `step` in `.github/workflows/<workflow>`."""
    lines = workflow_lines(workflow)
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


def run_make(tmp_path: Path, target: str, *variables: str) -> subprocess.CompletedProcess[str]:
    """Run `target` of a copy of the Makefile in `tmp_path/work`, which the caller has filled."""
    copy = tmp_path / "work"
    shutil.copyfile(os.path.join(REPOSITORY, "Makefile"), copy / "Makefile")
    return subprocess.run(
        [str(PROGRAMS["make"]), "-s", "-f", "Makefile", target, *variables],
        cwd=copy,
        env={**ENV, "HOME": str(tmp_path)},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def verify_engine_pin(tmp_path: Path, workflow: str | None) -> subprocess.CompletedProcess[str]:
    copy = tmp_path / "work"
    copy.mkdir()
    if workflow is not None:
        (copy / ".github" / "workflows").mkdir(parents=True)
        (copy / ".github" / "workflows" / "ci.yml").write_text(workflow, encoding="utf-8")
    return run_make(tmp_path, "verify-engine-pin")


def test_verify_engine_pin_admits_a_workflow_that_pins_the_engine(tmp_path: Path) -> None:
    assert verify_engine_pin(tmp_path, 'env:\n  PRAETOR_REF: "abc"\n').returncode == 0


@pytest.mark.parametrize("workflow", [None, "env:\n  OTHER: x\n", "# PRAETOR_REF: x\n"])
def test_verify_engine_pin_refuses_a_missing_pin(tmp_path: Path, workflow: str | None) -> None:
    result = verify_engine_pin(tmp_path, workflow)
    assert result.returncode != 0
    assert "no workflow declares PRAETOR_REF" in result.stderr


HOOK_SCRIPTS = ("checkpoint.py", "common.py")
CODACY_HELD = '# Entered 2026-11-18, MEL C, ends 2026-11-28.\nexclude_paths:\n  - "AGENTS.md"\n  - ".codex/rules.md"\n'


def verify_codacy(tmp_path: Path, text: str | None, today: str) -> subprocess.CompletedProcess[str]:
    copy = tmp_path / "work"
    copy.mkdir()
    if text is not None:
        (copy / ".codacy.yml").write_text(text, encoding="utf-8")
    return run_make(tmp_path, "verify-codacy", f"CODACY_TODAY={today}")


def test_verify_codacy_admits_the_committed_file_on_its_last_day(tmp_path: Path) -> None:
    with open(os.path.join(REPOSITORY, ".codacy.yml"), encoding="utf-8") as handle:
        committed = handle.read()
    ends = re.findall(r"MEL C, ends (\d{4}-\d{2}-\d{2})", committed)
    assert len(ends) == 1, f".codacy.yml names {len(ends)} end dates"
    result = verify_codacy(tmp_path, committed, ends[0])
    assert result.returncode == 0, result.stderr


def test_verify_codacy_admits_a_tree_without_the_file(tmp_path: Path) -> None:
    assert verify_codacy(tmp_path, None, "2026-11-18").returncode == 0


def test_verify_codacy_refuses_the_day_after_the_end(tmp_path: Path) -> None:
    result = verify_codacy(tmp_path, CODACY_HELD, "2026-11-29")
    assert result.returncode != 0
    assert "ended on 2026-11-28" in result.stderr


@pytest.mark.parametrize(
    ("line", "message"),
    [
        ('  - "src/cistula/process.py"\n', "excludes src/cistula/process.py, which no check"),
        ('  - "tools/**"\n', "excludes tools/**, which no check"),
        ("engines: {}\n", "a line other than a comment"),
    ],
)
def test_verify_codacy_refuses_an_exclusion_it_does_not_hold(tmp_path: Path, line: str, message: str) -> None:
    result = verify_codacy(tmp_path, CODACY_HELD + line, "2026-11-18")
    assert result.returncode != 0
    assert message in result.stderr


@pytest.mark.parametrize(
    "text",
    [
        CODACY_HELD.replace("MEL C, ends 2026-11-28", "MEL C"),
        CODACY_HELD + "# MEL C, ends 2026-11-30\n",
    ],
)
def test_verify_codacy_refuses_a_file_without_one_end_date(tmp_path: Path, text: str) -> None:
    result = verify_codacy(tmp_path, text, "2026-11-18")
    assert result.returncode != 0
    assert "names no single end date" in result.stderr


@pytest.fixture
def engine_scripts(tmp_path: Path) -> Path:
    """Copy the committed hook scripts into `tmp_path/work` and into an engine checkout; return the checkout."""
    for root in (tmp_path / "work", tmp_path / "engine"):
        scripts = root / ".config" / "lefthook" / "scripts"
        scripts.mkdir(parents=True)
        for name in HOOK_SCRIPTS:
            shutil.copyfile(os.path.join(REPOSITORY, ".config", "lefthook", "scripts", name), scripts / name)
    return tmp_path / "engine"


def test_verify_hook_scripts_admits_the_scripts_of_the_engine(tmp_path: Path, engine_scripts: Path) -> None:
    result = run_make(tmp_path, "verify-hook-scripts", f"PRAETOR_SRC={engine_scripts}")
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("name", HOOK_SCRIPTS)
def test_verify_hook_scripts_refuses_a_script_that_differs(tmp_path: Path, engine_scripts: Path, name: str) -> None:
    with open(engine_scripts / ".config" / "lefthook" / "scripts" / name, "a", encoding="utf-8") as handle:
        handle.write("# planted\n")
    assert run_make(tmp_path, "verify-hook-scripts", f"PRAETOR_SRC={engine_scripts}").returncode != 0


def test_verify_hook_scripts_refuses_a_script_the_engine_lacks(tmp_path: Path, engine_scripts: Path) -> None:
    (engine_scripts / ".config" / "lefthook" / "scripts" / "common.py").unlink()
    assert run_make(tmp_path, "verify-hook-scripts", f"PRAETOR_SRC={engine_scripts}").returncode != 0


@pytest.mark.usefixtures("engine_scripts")
def test_verify_hook_scripts_refuses_a_missing_engine_checkout(tmp_path: Path) -> None:
    result = run_make(tmp_path, "verify-hook-scripts", f"PRAETOR_SRC={tmp_path / 'none'}")
    assert result.returncode != 0
    assert "PRAETOR_SRC names no engine checkout" in result.stderr


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


def callee_job_names() -> dict[str, list[str]]:
    # Reviewed names for default inputs, keyed by the entire pinned reference.
    # A pin bump requires reviewing the callee again, never a network fetch here.
    with open(os.path.join(REPOSITORY, "tests", "reusable_workflow_jobs.json"), encoding="utf-8") as handle:
        names: dict[str, list[str]] = json.load(handle)
    return names


def required_checks() -> list[str]:
    with open(os.path.join(REPOSITORY, ".github", "rulesets", "main.json"), encoding="utf-8") as handle:
        rules = json.load(handle)["rules"]
    contexts = [
        check["context"]
        for rule in rules
        if rule["type"] == "required_status_checks"
        for check in rule["parameters"]["required_status_checks"]
    ]
    assert contexts
    return contexts


def workflows() -> dict[str, list[str]]:
    folder = os.path.join(REPOSITORY, ".github", "workflows")
    return {name: workflow_lines(name) for name in sorted(os.listdir(folder)) if name.endswith((".yml", ".yaml"))}


def workflow_jobs(lines: list[str]) -> list[dict[str, str]]:
    """Read job-level names and uses with the workflow reader's indentation rules."""
    jobs: list[dict[str, str]] = []
    for line in lines[lines.index("jobs:") + 1 :]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if _indent(line) == 0:
            break
        if _indent(line) == 2 and line.rstrip().endswith(":"):
            jobs.append({"name": line.strip()[:-1]})
        elif _indent(line) == 4 and jobs:
            key, _, value = line.strip().partition(":")
            if key in {"name", "uses"}:
                jobs[-1][key] = value.partition(" #")[0].strip().strip("\"'")
    return jobs


def reported_names(job: dict[str, str], context: str, callee_names: dict[str, list[str]]) -> list[str]:
    if "uses" not in job:
        return [job["name"]]
    # Only callers that could supply this required context need a reviewed entry.
    if not context.startswith(job["name"] + " / "):
        return []
    reference = job["uses"]
    assert reference in callee_names, f"missing reviewed callee job names for {reference!r}"
    return [job["name"] + " / " + name for name in callee_names[reference]]


def assert_required_checks(
    contexts: list[str], workflow_sources: dict[str, list[str]], callee_names: dict[str, list[str]]
) -> None:
    for context in contexts:
        found = [
            name
            for name, lines in workflow_sources.items()
            for job in workflow_jobs(lines)
            for reported in reported_names(job, context, callee_names)
            if reported == context
        ]
        assert len(found) == 1, f"{len(found)} workflows run the required check {context!r}"
        triggers = _on_block(workflow_sources[found[0]])
        assert any(line.startswith("merge_group:") for line in triggers), (
            f"{found[0]} runs the required check {context!r} but not in a merge queue"
        )


def test_every_required_check_reports_in_a_merge_queue() -> None:
    assert_required_checks(required_checks(), workflows(), callee_job_names())


def test_reusable_workflow_reports_the_composed_check_name() -> None:
    assert_required_checks(["Licence / REUSE lint"], {"reuse.yml": workflow_lines("reuse.yml")}, callee_job_names())


def test_literal_job_still_reports_its_check_name() -> None:
    assert_required_checks(["Verification gate"], {"ci.yml": workflow_lines("ci.yml")}, {})


def test_reusable_workflow_reports_each_callee_job() -> None:
    names = {reference: ["First", "Second"] for reference in callee_job_names()}
    assert_required_checks(["Licence / First", "Licence / Second"], {"reuse.yml": workflow_lines("reuse.yml")}, names)


@pytest.mark.parametrize("context", ["No job reports this", "Licence / Not a callee job", "Licence"])
def test_required_check_refuses_an_unreported_context(context: str) -> None:
    with pytest.raises(AssertionError, match=f"0 workflows run the required check {context!r}"):
        assert_required_checks([context], {"reuse.yml": workflow_lines("reuse.yml")}, callee_job_names())


def test_required_check_refuses_an_unreviewed_reusable_pin() -> None:
    reference = "plurano/horistes/.github/workflows/licence.yml@" + "f" * 40
    lines = [f"    uses: {reference}" if line.startswith("    uses:") else line for line in workflow_lines("reuse.yml")]
    with pytest.raises(AssertionError, match="missing reviewed callee job names") as caught:
        assert_required_checks(["Licence / REUSE lint"], {"reuse.yml": lines}, callee_job_names())
    assert reference in str(caught.value)


def test_required_check_refuses_a_different_caller_name() -> None:
    lines = [line.replace("name: Licence", "name: Other") for line in workflow_lines("reuse.yml")]
    with pytest.raises(AssertionError, match="0 workflows run the required check 'Licence / REUSE lint'"):
        assert_required_checks(["Licence / REUSE lint"], {"reuse.yml": lines}, callee_job_names())


def test_required_check_refuses_duplicate_jobs_in_one_workflow() -> None:
    lines = workflow_lines("reuse.yml")
    lines += ["  duplicate:", "    name: Licence / REUSE lint", "    runs-on: ubuntu-latest"]
    with pytest.raises(AssertionError, match="2 workflows run the required check 'Licence / REUSE lint'"):
        assert_required_checks(["Licence / REUSE lint"], {"reuse.yml": lines}, callee_job_names())


def test_required_check_refuses_a_workflow_without_merge_group() -> None:
    lines = [line for line in workflow_lines("reuse.yml") if line.strip() != "merge_group:"]
    with pytest.raises(AssertionError, match="but not in a merge queue"):
        assert_required_checks(["Licence / REUSE lint"], {"reuse.yml": lines}, callee_job_names())


def pages_head(tmp_path: Path, sha: str, remote: str) -> subprocess.CompletedProcess[str]:
    """Run the Pages head check with a stubbed remote response."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (tmp_path / "work").mkdir()
    stub = bin_dir / "git"
    stub.write_text(
        f"#!/bin/sh\nprintf '%s\\n' \"$*\" > '{tmp_path / 'arguments'}'\nprintf '%s' '{remote}'\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    (tmp_path / "output").touch()
    return shell(
        run_block(*PAGES_HEAD),
        tmp_path,
        PATH=f"{bin_dir}{os.pathsep}{ENV['PATH']}",
        REPO="plurano/cistula",
        SHA=sha,
        GITHUB_OUTPUT=str(tmp_path / "output"),
    )


def test_pages_head_admits_the_current_commit(tmp_path: Path) -> None:
    """The current main commit is admitted for deployment."""
    sha = "a" * 40
    result = pages_head(tmp_path, sha, f"{sha}\trefs/heads/main\n")
    assert result.returncode == 0
    assert (tmp_path / "output").read_text(encoding="utf-8") == "current=true\n"
    assert (tmp_path / "arguments").read_text(encoding="utf-8") == (
        "ls-remote https://github.com/plurano/cistula.git refs/heads/main\n"
    )


def test_pages_head_skips_an_old_commit(tmp_path: Path) -> None:
    """An old commit is skipped with a notice."""
    result = pages_head(tmp_path, "a" * 40, f"{'b' * 40}\trefs/heads/main\n")
    assert result.returncode == 0
    assert (tmp_path / "output").read_text(encoding="utf-8") == "current=false\n"
    assert any(line.startswith("::notice::") for line in result.stdout.splitlines())


def test_pages_head_refuses_an_empty_response(tmp_path: Path) -> None:
    """An empty remote response fails without declaring a current commit."""
    result = pages_head(tmp_path, "a" * 40, "")
    assert result.returncode != 0
    assert "::error::Could not read the head of main" in result.stdout
    assert not any(
        line.startswith("current=") for line in (tmp_path / "output").read_text(encoding="utf-8").splitlines()
    )


def test_pages_head_refuses_a_sha_prefix(tmp_path: Path) -> None:
    """A commit prefix does not match the full remote head."""
    sha = "a" * 39
    result = pages_head(tmp_path, sha, f"{sha}b\trefs/heads/main\n")
    assert result.returncode == 0
    assert (tmp_path / "output").read_text(encoding="utf-8") == "current=false\n"


def test_pages_head_accepts_a_space_separator(tmp_path: Path) -> None:
    """A space separates the remote head from its reference."""
    sha = "a" * 40
    result = pages_head(tmp_path, sha, f"{sha} refs/heads/main\n")
    assert result.returncode == 0
    assert (tmp_path / "output").read_text(encoding="utf-8") == "current=true\n"


def test_pages_deploy_follows_and_requires_the_head_check() -> None:
    """The deploy job runs the head check before its guarded deploy step."""
    lines = workflow_lines("pages.yml")
    start = lines.index("  deploy:")
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i].startswith("  ") and _indent(lines[i]) == 2), len(lines)
    )
    job = lines[start:end]
    head = job.index("      - name: Stop when main has moved on")
    deploy = job.index("      - name: Deploy")
    assert head < deploy
    assert "        id: head" in job[head + 1 : deploy]
    assert job[deploy + 1] == "        if: steps.head.outputs.current == 'true'"


def pages_push_problems(lines: list[str]) -> list[str]:
    """Find problems in the Pages push trigger."""
    start = lines.index("on:")
    push = None
    for number in range(start + 1, len(lines)):
        line = lines[number]
        if line.strip() and not line.lstrip().startswith("#") and _indent(line) == 0:
            break
        if line == "  push:":
            push = number
            break
    if push is None:
        message = "no push trigger in on block"
        raise ValueError(message)
    block = []
    for line in lines[push + 1 :]:
        if line.strip() and not line.lstrip().startswith("#") and _indent(line) <= 2:
            break
        block.append(line.strip())
    problems = []
    if any(line.startswith(("paths:", "paths-ignore:")) for line in block):
        problems.append("push trigger must not filter paths")
    if "branches: [main]" not in block:
        problems.append("push trigger must name the main branch")
    return problems


def test_pages_push_builds_every_change() -> None:
    """Every push to main starts a Pages build."""
    assert pages_push_problems(workflow_lines("pages.yml")) == []


@pytest.mark.parametrize("key", ["paths:", "paths-ignore:"])
def test_pages_push_refuses_a_path_filter(key: str) -> None:
    """A path filter is refused on the push trigger."""
    lines = workflow_lines("pages.yml")
    position = lines.index("    branches: [main]") + 1
    lines[position:position] = [f"    {key}", '      - "docs/**"']
    assert pages_push_problems(lines)


def test_pages_push_needs_the_main_branch() -> None:
    """The push trigger must name the main branch."""
    lines = [line for line in workflow_lines("pages.yml") if line != "    branches: [main]"]
    assert pages_push_problems(lines)


def test_pages_push_needs_a_push_trigger() -> None:
    """A missing push trigger is refused."""
    with pytest.raises(ValueError, match="no push trigger in on block"):
        pages_push_problems(["on:", "  workflow_dispatch:", "jobs:"])
