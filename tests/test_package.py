# SPDX-FileCopyrightText: 2026 Lusoris <lusoris@proton.me>
# SPDX-License-Identifier: EUPL-1.2
from __future__ import annotations

import importlib.resources
import inspect
import socket
import subprocess
import sys

import cistula
from cistula import digest, files, process

PUBLIC = {
    "CommandFailedError",
    "CommandNotFoundError",
    "CommandNotStartedError",
    "CommandTimeoutError",
    "DuplicateKeyError",
    "JsonLineError",
    "JsonValue",
    "ProcessError",
    "Result",
    "child_env",
    "loads_json",
    "loads_json_lines",
    "open_bytes_atomic",
    "open_text_atomic",
    "read_json",
    "read_json_lines",
    "run",
    "sha256_bytes",
    "sha256_file",
    "sha256_text",
    "staged_path",
    "write_bytes_atomic",
    "write_json",
    "write_text_atomic",
}


def test_public_interface_is_exactly_the_declared_one() -> None:
    assert set(cistula.__all__) == PUBLIC
    assert set(digest.__all__) | set(files.__all__) | set(process.__all__) == PUBLIC
    assert len(cistula.__all__) == len(PUBLIC)


def test_every_public_name_resolves_and_is_documented() -> None:
    for name in PUBLIC - {"JsonValue"}:
        member = getattr(cistula, name)
        assert inspect.getdoc(member), name


def test_type_marker_is_shipped_with_the_package() -> None:
    assert importlib.resources.files(cistula).joinpath("py.typed").is_file()


def test_import_loads_no_network_module_and_no_third_party_module() -> None:
    # A fresh interpreter in isolated mode: only the standard library and the
    # installed package are importable. No module of the package may pull in
    # the socket machinery, so no default can contact a host.
    code = """
import sys
before = set(sys.modules)
import cistula
loaded = set(sys.modules) - before
network = sorted(loaded & {"socket", "ssl", "http.client", "urllib.request", "asyncio"})
outside = sorted(
    name for name in loaded
    if name.partition(".")[0] not in sys.stdlib_module_names
    and not name.startswith("cistula")
)
print(network, outside)
"""
    done = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, check=True, timeout=60)

    assert done.stdout == "[] []\n"
    # The check above can fail: the names it looks for are real module names.
    assert socket.__name__ in sys.modules
