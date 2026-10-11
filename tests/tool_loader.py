# SPDX-FileCopyrightText: 2026 Lusoris <lusoris@proton.me>
# SPDX-License-Identifier: EUPL-1.2
from __future__ import annotations

import importlib.util
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types import ModuleType

REPOSITORY = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def script(name: str) -> str:
    """Return the path of the gate script `tools/<name>.py`."""
    return os.path.join(REPOSITORY, "tools", f"{name}.py")


def load_tool(name: str) -> ModuleType:
    """Return the gate script `tools/<name>.py` as a module; the scripts are not a package."""
    spec = importlib.util.spec_from_file_location(name, script(name))
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
