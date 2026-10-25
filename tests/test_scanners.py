# SPDX-FileCopyrightText: 2026 Lusoris <lusoris@proton.me>
# SPDX-License-Identifier: EUPL-1.2
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from tool_loader import load_tool

scanners = load_tool("scanners")


@pytest.fixture
def fake_trivy(tmp_path: Path) -> Path:
    script_path = tmp_path / "bin" / "trivy"
    script_path.parent.mkdir(parents=True, exist_ok=True)
    body = f"""#!{sys.executable}
import os
import sys
from pathlib import Path

log_path = os.environ.get("FAKE_TRIVY_LOG")
if log_path:
    with open(log_path, "a", encoding="utf-8") as file:
        file.write(" ".join(sys.argv[1:]) + "\\n")

if "--version" in sys.argv:
    sys.stdout.write("Version: 0.76.0\\n")
    sys.exit(0)

if os.environ.get("FAKE_TRIVY_FAIL") == "1":
    sys.stderr.write("fake trivy download failed\\n")
    sys.exit(1)

race_target = os.environ.get("FAKE_TRIVY_RACE_TARGET")
if race_target:
    target = Path(race_target)
    target.mkdir(parents=True, exist_ok=True)
    (target / "db").mkdir(parents=True, exist_ok=True)
    (target / "db" / "trivy.db").write_bytes(b"")
    stamp = os.environ.get("FAKE_TRIVY_RACE_STAMP", "")
    if stamp:
        (target / "db.stamp").write_text(f"{{stamp}}\\n", encoding="utf-8")

if "--download-db-only" in sys.argv:
    if "--cache-dir" in sys.argv:
        idx = sys.argv.index("--cache-dir")
        cache_dir = Path(sys.argv[idx + 1])
        db_dir = cache_dir / "db"
        db_dir.mkdir(parents=True, exist_ok=True)
        (db_dir / "trivy.db").write_bytes(b"")
    sys.exit(0)

sys.exit(0)
"""
    script_path.write_text(body, encoding="utf-8")
    script_path.chmod(0o755)
    return script_path


def test_default_cache_under_xdg_cache_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    xdg = tmp_path / "custom-cache"
    monkeypatch.setenv("XDG_CACHE_HOME", str(xdg))
    cache = scanners.default_cache()
    expected_hex = scanners.TRIVY_DB_DIGEST.removeprefix("sha256:")
    assert cache == xdg / "plurano" / "trivy-db" / expected_hex
    assert cache.name == expected_hex
    assert str(cache).endswith(expected_hex)


def test_default_cache_without_xdg_cache_home(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    cache = scanners.default_cache()
    expected_hex = scanners.TRIVY_DB_DIGEST.removeprefix("sha256:")
    assert cache == Path.home() / ".cache" / "plurano" / "trivy-db" / expected_hex
    assert cache.name == expected_hex
    assert str(cache).endswith(expected_hex)


def test_fetch_writes_stamp_and_second_fetch_skips(
    tmp_path: Path, fake_trivy: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log_file = tmp_path / "trivy.log"
    monkeypatch.setenv("FAKE_TRIVY_LOG", str(log_file))
    cache = tmp_path / "cache"

    status = scanners.fetch_db(cache, str(fake_trivy))
    assert status == 0
    assert (cache / "db.stamp").is_file()
    assert (cache / "db.stamp").read_text(encoding="utf-8").strip() == scanners.TRIVY_DB_DIGEST
    assert (cache / "db" / "trivy.db").is_file()
    calls_after_first = log_file.read_text(encoding="utf-8").splitlines()
    assert len(calls_after_first) == 2

    status_second = scanners.fetch_db(cache, str(fake_trivy))
    assert status_second == 0
    calls_after_second = log_file.read_text(encoding="utf-8").splitlines()
    assert len(calls_after_second) == len(calls_after_first)


def test_planted_cache_with_other_digest_refuses_and_redownloads(
    tmp_path: Path, fake_trivy: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log_file = tmp_path / "trivy.log"
    monkeypatch.setenv("FAKE_TRIVY_LOG", str(log_file))
    cache = tmp_path / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    other_digest = "sha256:0000000000000000000000000000000000000000000000000000000000000000"
    (cache / "db.stamp").write_text(f"{other_digest}\n", encoding="utf-8")
    (cache / "db").mkdir(parents=True, exist_ok=True)
    (cache / "db" / "trivy.db").write_bytes(b"old")

    with pytest.raises(scanners.CheckError, match="holds no database"):
        scanners.trivy_scan(str(fake_trivy), cache, tmp_path)

    status = scanners.fetch_db(cache, str(fake_trivy))
    assert status == 0
    assert (cache / "db.stamp").read_text(encoding="utf-8").strip() == scanners.TRIVY_DB_DIGEST
    calls = log_file.read_text(encoding="utf-8").splitlines()
    assert any("--download-db-only" in call for call in calls)


def test_failing_download_cleans_up_and_leaves_no_half_cache(
    tmp_path: Path, fake_trivy: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_TRIVY_FAIL", "1")
    cache = tmp_path / "cache_dir"

    with pytest.raises(scanners.CheckError, match="download exited 1"):
        scanners.fetch_db(cache, str(fake_trivy))

    assert not cache.exists()
    assert not list(tmp_path.glob(".fetch-*"))


def test_rename_race_resolves_cleanly_when_final_cache_created(
    tmp_path: Path, fake_trivy: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "cache_dir"
    monkeypatch.setenv("FAKE_TRIVY_RACE_TARGET", str(cache))
    monkeypatch.setenv("FAKE_TRIVY_RACE_STAMP", scanners.TRIVY_DB_DIGEST)

    status = scanners.fetch_db(cache, str(fake_trivy))
    assert status == 0
    assert cache.is_dir()
    assert (cache / "db.stamp").read_text(encoding="utf-8").strip() == scanners.TRIVY_DB_DIGEST
    assert not list(tmp_path.glob(".fetch-*"))
