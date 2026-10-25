# SPDX-FileCopyrightText: 2026 Lusoris <lusoris@proton.me>
# SPDX-License-Identifier: EUPL-1.2
"""Run the two declared scanners of the gate: semgrep and trivy.

    fetch-db          download the pinned trivy vulnerability database into the
                      cache directory, unless its stamp names the pinned digest
    selftest semgrep  planted findings in a scratch directory must trigger every
                      rule of the semgrep configuration
    selftest trivy    a planted vulnerable lock in a scratch directory must
                      report the expected advisory
    check semgrep     scan the tracked files against the pinned rules
    check trivy       scan the locks under requirements/ offline against the
                      cached database; each lock has to be scanned

The database digest is pinned in one place, TRIVY_DB_DIGEST below, which the
update bot reads (renovate.json). The vulnerability database is cached under
`<XDG_CACHE_HOME or ~/.cache>/plurano/trivy-db/<digest>`, so one download per
digest is shared by every checkout and gate copy. Ported from plurano/amussis
tools/scanners.py, for Python and shell sources and pip locks.

Exit status: 0 clean or selftest passed, 1 findings or selftest failed, 2 the
check itself could not run.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

MAX_LINES = 1000
VERSION_SECONDS = 60
SCAN_SECONDS = 600
FETCH_SECONDS = 600

TRIVY_DB_REPOSITORY = "ghcr.io/aquasecurity/trivy-db"
TRIVY_DB_DIGEST = "sha256:44b3f4dfbfe98b280b725f6e4ec7ada9b13774aefbad553a19afc4b919c6a281"

# The pip analyzer reads only files named requirements.txt by default; the
# locks of this repository have other names.
LOCKS = ("requirements/dev.txt", "requirements/semgrep.txt")
LOCK_PATTERN = r"pip:^requirements/[a-z]+\.txt$"
PLANTED_LOCK = "requests==2.19.1\n"
EXPECTED_CVE = "CVE-2018-18074"

PLANTS = {
    "planted.sh": '#!/usr/bin/env bash\ncmd="ls"\neval "$cmd"\n',
    "planted.py": (
        "import subprocess\nimport urllib.request\n\n"
        "def planted():\n"
        '    eval("1 + 1")\n'
        '    subprocess.run("ls", shell=True, timeout=10)\n'
        '    subprocess.run(["ls"])\n'
        '    urllib.request.urlopen("http://127.0.0.1:1")\n'
    ),
}
SEMGREP_FLAGS = ("--error", "--metrics=off", "--disable-version-check", "--json")
TRIVY_FLAGS = ("--skip-db-update", "--skip-java-db-update", "--offline-scan", "--scanners", "vuln", "--quiet")
FOUND_SEMGREP = 1
FOUND_TRIVY = 3


class CheckError(Exception):
    """The check itself could not run."""


def out(text: str) -> None:
    """Write one line of the result."""
    sys.stdout.write(f"{text}\n")


def err(text: str) -> None:
    """Write one line of a finding or failure."""
    sys.stderr.write(f"{text}\n")


def run(argv: list[str], timeout: int, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Run one command with a deadline; a command that cannot start or ends late is a CheckError."""
    env = dict(os.environ)
    if "--cache-dir" in argv:  # trivy reads registry credentials from DOCKER_CONFIG; none are needed
        env.setdefault("DOCKER_CONFIG", argv[argv.index("--cache-dir") + 1])
    try:
        return subprocess.run(  # noqa: S603 - fixed argv, no shell is involved
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        message = f"{' '.join(argv)}: {error}"
        raise CheckError(message) from error


def say_version(tool: str) -> None:
    """Print the first line of the version of `tool`, so the log names what ran."""
    done = run([tool, "--version"], VERSION_SECONDS)
    lines = (done.stdout or done.stderr).strip().splitlines()
    if done.returncode != 0 or not lines:
        message = f"{tool} --version exited {done.returncode}: {done.stderr.strip()}"
        raise CheckError(message)
    out(f"{tool}: {lines[0]}")


def parse(done: subprocess.CompletedProcess[str], tool: str) -> dict[str, Any]:
    """Read the JSON report a scanner wrote to its standard output."""
    try:
        data = json.loads(done.stdout)
    except json.JSONDecodeError as error:
        message = f"{tool} wrote no JSON report (exit {done.returncode}): {done.stderr.strip()[:2000]}"
        raise CheckError(message) from error
    if not isinstance(data, dict):
        message = f"{tool} report is not a JSON object"
        raise CheckError(message)
    return data


def default_cache() -> Path:
    """Return the shared cache directory for the pinned trivy database."""
    base = os.environ.get("XDG_CACHE_HOME")
    cache_home = Path(base) if base else Path.home() / ".cache"
    digest_hex = TRIVY_DB_DIGEST.removeprefix("sha256:")
    return cache_home / "plurano" / "trivy-db" / digest_hex


def stamp_of(cache: Path) -> str:
    """Return the digest the cached database was fetched at, empty when none was."""
    stamp = cache / "db.stamp"
    return stamp.read_text(encoding="utf-8").strip() if stamp.is_file() else ""


def fetch_db(cache: Path, trivy: str) -> int:
    """Download the pinned database unless the cache already holds it."""
    if stamp_of(cache) == TRIVY_DB_DIGEST:
        out(f"trivy database already at {TRIVY_DB_DIGEST}")
        return 0
    say_version(trivy)
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(dir=cache.parent, prefix=".fetch-"))
    except OSError as error:
        message = f"could not prepare cache directory in {cache.parent}: {error}"
        raise CheckError(message) from error
    renamed = False
    try:
        argv = [trivy, "fs", "--cache-dir", str(tmp), "--db-repository", f"{TRIVY_DB_REPOSITORY}@{TRIVY_DB_DIGEST}"]
        # trivy stops the download at its own --timeout (default 5m), so it gets the same bound
        done = run([*argv, "--download-db-only", "--timeout", f"{FETCH_SECONDS}s"], FETCH_SECONDS)
        if done.returncode != 0:
            message = f"trivy database download exited {done.returncode}: {done.stderr.strip()}"
            raise CheckError(message)
        (tmp / "db.stamp").write_text(f"{TRIVY_DB_DIGEST}\n", encoding="utf-8")
        if cache.exists() and stamp_of(cache) != TRIVY_DB_DIGEST:
            if cache.is_dir():
                shutil.rmtree(cache)
            else:
                cache.unlink()
        try:
            os.rename(tmp, cache)
            renamed = True
        except OSError as error:
            if stamp_of(cache) == TRIVY_DB_DIGEST:
                out(f"PASS: trivy database fetched at {TRIVY_DB_DIGEST}")
                return 0
            message = f"could not rename {tmp} to {cache}: {error}"
            raise CheckError(message) from error
    finally:
        if not renamed and tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)
    out(f"PASS: trivy database fetched at {TRIVY_DB_DIGEST}")
    return 0


def rule_ids(config: Path) -> set[str]:
    """Return the rule ids of the semgrep configuration; one without rules is a CheckError."""
    if not config.is_file():
        message = f"semgrep configuration not found: {config}"
        raise CheckError(message)
    lines = config.read_text(encoding="utf-8").splitlines()[:MAX_LINES]
    ids = {line.split("id:", 1)[1].strip() for line in lines if line.lstrip().startswith("- id:")}
    if not ids:
        message = f"no rules in {config}"
        raise CheckError(message)
    return ids


def semgrep_scan(semgrep: str, config: Path, where: Path) -> tuple[int, dict[str, Any]]:
    """Run one semgrep scan of `where`; return its exit status and report."""
    say_version(semgrep)
    done = run([semgrep, "scan", "--config", str(config.resolve()), *SEMGREP_FLAGS, "."], SCAN_SECONDS, where)
    if done.returncode not in (0, FOUND_SEMGREP):
        message = f"semgrep exited {done.returncode}: {done.stderr.strip()[:2000]}"
        raise CheckError(message)
    return done.returncode, parse(done, "semgrep")


def selftest_semgrep(config: Path, semgrep: str) -> int:
    """Require every rule to fire on the planted files, with semgrep exiting 1."""
    wanted = rule_ids(config)
    with tempfile.TemporaryDirectory(prefix="semgrep-selftest-") as scratch:
        for name, text in PLANTS.items():
            (Path(scratch) / name).write_text(text, encoding="utf-8")
        status, report = semgrep_scan(semgrep, config, Path(scratch))
    fired = {str(r.get("check_id", "")).rsplit(".", 1)[-1] for r in report.get("results", [])}
    silent = sorted(wanted - fired)
    if status != FOUND_SEMGREP or silent:
        err(f"FAIL: semgrep selftest: exit {status}, rules without a finding: {silent}")
        return 1
    out(f"PASS: semgrep selftest (all {len(wanted)} rules fired on the planted files)")
    return 0


def check_semgrep(config: Path, semgrep: str) -> int:
    """Scan the tracked files; a scan that reads no file of the package is a CheckError."""
    status, report = semgrep_scan(semgrep, config, Path())
    scanned = report.get("paths", {}).get("scanned", [])
    if not any(str(p).startswith("src/cistula/") for p in scanned):
        message = "semgrep scanned no file under src/cistula/"
        raise CheckError(message)
    for r in report.get("results", []):
        err(f"{r.get('path')}:{r.get('start', {}).get('line')}: {r.get('extra', {}).get('message')}")
    if status == FOUND_SEMGREP:
        err("FAIL: semgrep found HISS violations")
        return 1
    out(f"PASS: semgrep scan of {len(scanned)} files (0 findings)")
    return 0


def trivy_scan(trivy: str, cache: Path, where: Path) -> tuple[int, dict[str, Any]]:
    """Run one offline trivy scan of the pip locks under `where`; return its exit status and report."""
    if stamp_of(cache) != TRIVY_DB_DIGEST:
        message = f"the trivy cache holds no database at {TRIVY_DB_DIGEST}; run make fetch"
        raise CheckError(message)
    say_version(trivy)
    argv = [trivy, "fs", "--cache-dir", str(cache.resolve()), *TRIVY_FLAGS, "--file-patterns", LOCK_PATTERN]
    argv += ["--skip-dirs", ".workingdir,build,dist,.venv", "--exit-code", str(FOUND_TRIVY), "--format", "json", "."]
    done = run(argv, SCAN_SECONDS, where)
    if done.returncode not in (0, FOUND_TRIVY):
        message = f"trivy exited {done.returncode}: {done.stderr.strip()[:2000]}"
        raise CheckError(message)
    return done.returncode, parse(done, "trivy")


def advisories(report: dict[str, Any]) -> dict[str, list[str]]:
    """Map each scanned target to the advisories trivy reported for it."""
    return {
        str(r.get("Target")): [f"{v.get('VulnerabilityID')} {v.get('PkgName')}" for v in r.get("Vulnerabilities") or []]
        for r in report.get("Results") or []
    }


def selftest_trivy(cache: Path, trivy: str) -> int:
    """Require the planted lock to report the expected advisory, with trivy exiting 3."""
    with tempfile.TemporaryDirectory(prefix="trivy-selftest-") as scratch:
        (Path(scratch) / "requirements").mkdir()
        (Path(scratch) / "requirements" / "planted.txt").write_text(PLANTED_LOCK, encoding="utf-8")
        status, report = trivy_scan(trivy, cache, Path(scratch))
    found = [a for found in advisories(report).values() for a in found]
    if status != FOUND_TRIVY or not any(a.startswith(f"{EXPECTED_CVE} ") for a in found):
        err(f"FAIL: trivy selftest: exit {status}, {EXPECTED_CVE} not among {found}")
        return 1
    out(f"PASS: trivy selftest ({EXPECTED_CVE} reported for the planted lock, exit {FOUND_TRIVY})")
    return 0


def check_trivy(cache: Path, trivy: str) -> int:
    """Scan the locks; a lock that trivy did not read is a CheckError."""
    status, report = trivy_scan(trivy, cache, Path())
    found = advisories(report)
    unread = sorted(set(LOCKS) - set(found))
    if unread:
        message = f"trivy did not scan {unread}"
        raise CheckError(message)
    for target, items in found.items():
        for item in items:
            err(f"{target}: {item}")
    if status == FOUND_TRIVY:
        err("FAIL: trivy found known vulnerabilities")
        return 1
    out(f"PASS: trivy scan of {', '.join(LOCKS)} (0 vulnerabilities)")
    return 0


def main() -> int:
    """Parse the command line and run one action."""
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("action", choices=("fetch-db", "selftest", "check"))
    parser.add_argument("target", nargs="?", choices=("semgrep", "trivy"))
    parser.add_argument("--cache-dir", type=Path, default=default_cache())
    parser.add_argument("--config", type=Path, default=Path(".config/semgrep/hiss.yml"))
    args = parser.parse_args()
    if (args.action == "fetch-db") == (args.target is not None):
        parser.error("fetch-db takes no target; selftest and check take semgrep or trivy")
    semgrep = os.environ.get("SEMGREP", "semgrep")
    trivy = os.environ.get("TRIVY", "trivy")
    actions: dict[tuple[str, str | None], Callable[[], int]] = {
        ("fetch-db", None): lambda: fetch_db(args.cache_dir, trivy),
        ("selftest", "semgrep"): lambda: selftest_semgrep(args.config, semgrep),
        ("selftest", "trivy"): lambda: selftest_trivy(args.cache_dir, trivy),
        ("check", "semgrep"): lambda: check_semgrep(args.config, semgrep),
        ("check", "trivy"): lambda: check_trivy(args.cache_dir, trivy),
    }
    try:
        return actions[args.action, args.target]()
    except CheckError as error:
        err(f"ERROR: {error}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
