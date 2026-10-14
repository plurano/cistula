# SPDX-FileCopyrightText: 2026 Lusoris <lusoris@proton.me>
# SPDX-License-Identifier: EUPL-1.2
from __future__ import annotations

import base64
import hashlib
import subprocess
import sys
import zipfile
from typing import TYPE_CHECKING

import pytest

from tool_loader import REPOSITORY, load_tool, script

if TYPE_CHECKING:
    from pathlib import Path

consumer = load_tool("check_consumer")

# A program that imports the probe package, finds it alone in the new
# environment and says which value it holds.
PROBE = """\
import os, sys
from importlib import metadata
import probe
assert os.path.commonpath([probe.__file__, sys.prefix]) == sys.prefix, probe.__file__
assert [entry.metadata["Name"] for entry in metadata.distributions()] == ["probe"]
print(f"probe {probe.VALUE}")
"""


def record_line(name: str, data: bytes) -> str:
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
    return f"{name},sha256={digest},{len(data)}\n"


def wheel(directory: Path, value: str = "1") -> Path:
    """Write a minimal wheel of the package `probe` into `directory`."""
    directory.mkdir(exist_ok=True)
    members = {
        "probe/__init__.py": f"VALUE = {value}\n".encode(),
        "probe-1.0.dist-info/METADATA": b"Metadata-Version: 2.1\nName: probe\nVersion: 1.0\n",
        "probe-1.0.dist-info/WHEEL": b"Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
    }
    record = "".join(record_line(name, data) for name, data in members.items()) + "probe-1.0.dist-info/RECORD,,\n"
    path = directory / "probe-1.0-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
        archive.writestr("probe-1.0.dist-info/RECORD", record)
    return path


def program(directory: Path, text: str = PROBE, name: str = "program.py") -> Path:
    path = directory / name
    path.write_text(text, encoding="utf-8")
    return path


def test_requirement_names_the_wheel_by_address_and_digest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = wheel(tmp_path / "dist")
    monkeypatch.chdir(tmp_path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    expected = f"probe @ file://{path.resolve()} --hash=sha256:{digest}\n"
    assert consumer.requirement("dist/probe-1.0-py3-none-any.whl") == expected


@pytest.mark.parametrize("count", [0, 2])
def test_one_wheel_refuses_any_other_count(tmp_path: Path, count: int) -> None:
    for number in range(count):
        (tmp_path / f"probe-{number}-py3-none-any.whl").write_bytes(b"")
    (tmp_path / "probe-1.0.tar.gz").write_bytes(b"")
    with pytest.raises(ValueError, match=rf"^expected one \*\.whl in {tmp_path}, found {count}$"):
        consumer.one_wheel(str(tmp_path))


def test_one_wheel_ignores_other_files(tmp_path: Path) -> None:
    path = wheel(tmp_path)
    (tmp_path / "probe-1.0.tar.gz").write_bytes(b"")
    assert consumer.one_wheel(str(tmp_path)) == str(path)


def test_environment_drops_what_steers_pip_and_python(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PIP_INDEX_URL", "http://index.invalid/simple")
    monkeypatch.setenv("PYTHONPATH", "/elsewhere")
    monkeypatch.setenv("CONSUMER_KEPT", "yes")
    environment = consumer.scrubbed_environment()
    assert "PIP_INDEX_URL" not in environment
    assert "PYTHONPATH" not in environment
    assert environment["CONSUMER_KEPT"] == "yes"


def test_check_installs_the_wheel_and_runs_the_program(tmp_path: Path) -> None:
    wheel(tmp_path / "dist", value="7")
    name, output = consumer.check(str(tmp_path / "dist"), str(program(tmp_path)))
    assert (name, output) == ("probe-1.0-py3-none-any.whl", "probe 7\n")


def test_check_hides_the_program_s_own_directory(tmp_path: Path) -> None:
    # A module next to the program must not be importable: only the
    # installed package is.
    wheel(tmp_path / "dist")
    program(tmp_path, "VALUE = 1\n", name="neighbour.py")
    with pytest.raises(RuntimeError, match=r"No module named 'neighbour'"):
        consumer.check(str(tmp_path / "dist"), str(program(tmp_path, "import neighbour\n")))


def test_check_ignores_the_caller_s_python_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wheel(tmp_path / "dist", value="2")
    shadow = tmp_path / "shadow" / "probe"
    shadow.mkdir(parents=True)
    (shadow / "__init__.py").write_text("VALUE = 'shadow'\n", encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "shadow"))
    assert consumer.check(str(tmp_path / "dist"), str(program(tmp_path)))[1] == "probe 2\n"


def test_check_fails_when_the_program_fails(tmp_path: Path) -> None:
    wheel(tmp_path / "dist")
    failing = program(tmp_path, "import sys\nsys.exit('consumer saw a wrong value')\n")
    with pytest.raises(RuntimeError, match=r"program\.py exited 1:\nconsumer saw a wrong value$"):
        consumer.check(str(tmp_path / "dist"), str(failing))


def test_check_fails_on_a_damaged_wheel(tmp_path: Path) -> None:
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "probe-1.0-py3-none-any.whl").write_bytes(b"not a zip archive")
    with pytest.raises(RuntimeError, match=r"pip .* exited [1-9]"):
        consumer.check(str(tmp_path / "dist"), str(program(tmp_path)))


def test_check_refuses_a_missing_program(tmp_path: Path) -> None:
    wheel(tmp_path / "dist")
    with pytest.raises(ValueError, match=r"^no program at .*missing\.py$"):
        consumer.check(str(tmp_path / "dist"), str(tmp_path / "missing.py"))


def test_check_stops_a_step_at_the_time_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wheel(tmp_path / "dist")
    monkeypatch.setattr(consumer, "TIMEOUT", 0.2)
    with pytest.raises(RuntimeError, match=r"did not finish within 0\.2 s$"):
        consumer.check(str(tmp_path / "dist"), str(program(tmp_path, "import time\ntime.sleep(30)\n")))


def test_main_passes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    wheel(tmp_path / "dist")
    assert consumer.main([str(tmp_path / "dist"), str(program(tmp_path))]) == 0
    passed = "PASS: a new environment installed probe-1.0-py3-none-any.whl by its hash and ran program.py against it.\n"
    expected = f"probe 1\n{passed}"
    assert capsys.readouterr() == (expected, "")


def test_main_fails_on_a_failing_check(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "dist").mkdir()
    assert consumer.main([str(tmp_path / "dist"), str(program(tmp_path))]) == 1
    assert capsys.readouterr().err == f"FAIL: expected one *.whl in {tmp_path / 'dist'}, found 0\n"


def test_main_reports_a_missing_directory(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert consumer.main([str(tmp_path / "absent"), str(program(tmp_path))]) == 2
    assert capsys.readouterr().err.startswith(f"could not run the consumer check on {tmp_path / 'absent'}: ")


@pytest.mark.parametrize("arguments", [[], ["dist"], ["dist", "program.py", "extra"]])
def test_main_refuses_a_wrong_argument_count(arguments: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    assert consumer.main(arguments) == 2
    assert capsys.readouterr().err == "usage: check_consumer.py DIRECTORY PROGRAM\n"


def test_script_runs_as_the_gate_calls_it(tmp_path: Path) -> None:
    wheel(tmp_path / "dist")
    result = subprocess.run(
        [sys.executable, "-I", script("check_consumer"), str(tmp_path / "dist"), str(program(tmp_path))],
        cwd=REPOSITORY,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert (result.returncode, result.stderr) == (0, "")
    assert result.stdout.endswith("ran program.py against it.\n")
