"""The native C++ core must stay optional at the CMake level (rev77).

Windows report: ``pip install -e ".[recorder]"`` died in the C++ link step with
``LNK1120: 149 unresolved externals`` (every symbol ``__imp__Py...``), so the
optional recorder extra was uninstallable on a machine whose Python/MSVC pair
does not produce a usable import library. ``FPSCOACH_SKIP_NATIVE=1`` has to skip
the native core entirely.

These tests read ``CMakeLists.txt`` as text: no CMake or compiler is available
on every machine that runs the suite, and the Windows-only branches cannot be
configured here at all. They pin the three properties the fix depends on.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CMAKE = ROOT / "CMakeLists.txt"


@pytest.fixture(scope="module")
def cmake_text() -> str:
    assert CMAKE.is_file(), "CMakeLists.txt must ship in the repo root"
    return CMAKE.read_text(encoding="utf-8")


def test_guard_uses_if_block_not_top_level_return(cmake_text: str) -> None:
    """Top-level ``return()`` needs CMake >= 3.29; the declared floor is 3.23."""
    floor = re.search(r"cmake_minimum_required\(VERSION\s+([\d.]+)", cmake_text)
    assert floor, "cmake_minimum_required(VERSION ...) is missing"
    version = tuple(int(part) for part in floor.group(1).split(".")[:2])
    if version >= (3, 29):
        pytest.skip("floor already permits top-level return()")

    # Strip comments so the explanatory prose about return() does not trip this.
    code = "\n".join(
        line for line in cmake_text.splitlines() if not line.lstrip().startswith("#")
    )
    stray = [line.strip() for line in code.splitlines() if line.strip() == "return()"]
    assert not stray, (
        "CMakeLists.txt calls return() at file scope while declaring "
        f"cmake_minimum_required(VERSION {floor.group(1)}); wrap the body in if()/endif()"
    )


def test_project_does_not_enable_cxx_eagerly(cmake_text: str) -> None:
    """``project(... LANGUAGES CXX)`` detects a compiler during configure.

    The skip-native install is only compiler-free if CXX is enabled afterwards,
    inside the branch that actually builds targets.
    """
    # Anchored to the start of a line: a comment above mentions "project() time".
    project = re.search(r"^project\([^)]*\)", cmake_text, re.DOTALL | re.MULTILINE)
    assert project, "project() is missing"
    assert "LANGUAGES NONE" in project.group(0), project.group(0)
    assert "enable_language(CXX)" in cmake_text


def test_skip_variable_covers_env_var_and_keeps_native_default(cmake_text: str) -> None:
    assert "FPSCOACH_SKIP_NATIVE" in cmake_text
    assert "ENV{FPSCOACH_SKIP_NATIVE}" in cmake_text
    # "0" and "" must leave the native build on, so the variable can be preset.
    assert 'STREQUAL "0"' in cmake_text
    # Every target must sit in the non-skip branch: the guard opens before the
    # first add_library and closes at the very end of the file.
    code = cmake_text.splitlines()
    guard = next(
        index
        for index, line in enumerate(code)
        if re.match(r"^if\(FPSCOACH_SKIP_NATIVE\)\s*$", line.strip())
    )
    first_target = next(
        index for index, line in enumerate(code) if line.startswith("    add_library(")
    )
    assert guard < first_target, "add_library() runs before the skip guard"


def test_native_target_links_python_module_explicitly(cmake_text: str) -> None:
    """LNK2001 ``__imp__Py...`` means CPython's import lib never reached the linker."""
    link = re.search(
        r"target_link_libraries\(\s*fpscoach_native\b[^)]*\)", cmake_text, re.DOTALL
    )
    assert link, "fpscoach_native has no target_link_libraries()"
    assert "Python::Module" in link.group(0), link.group(0)


def test_missing_import_library_is_reported_at_configure_time(cmake_text: str) -> None:
    """A silent empty import library is what produced 149 unresolved externals."""
    assert "${Python_LIBRARIES}" in cmake_text, "the resolved import library is not printed"
    warning = re.search(r"if\(NOT Python_LIBRARIES\)(.*?)endif\(\)", cmake_text, re.DOTALL)
    assert warning, "no early warning when no CPython import library is resolved"
    assert "FPSCOACH_SKIP_NATIVE" in warning.group(1), warning.group(1)
