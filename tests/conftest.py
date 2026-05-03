"""Shared fixtures for diode tests."""

from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"
CROSS_FILE_DIR = FIXTURES_DIR / "cross_file"


@pytest.fixture
def fixtures_dir() -> Path:
    """Return the path to the test fixtures directory."""
    return FIXTURES_DIR


@pytest.fixture
def cross_file_dir() -> Path:
    """Return the path to the cross_file test fixtures directory."""
    return CROSS_FILE_DIR


@pytest.fixture
def simple_module_path() -> Path:
    """Return the resolved path to simple_module.sv."""
    return (FIXTURES_DIR / "simple_module.sv").resolve()


@pytest.fixture
def simple_module_uri() -> str:
    """Return the file URI for simple_module.sv."""
    from pygls import uris as pygls_uris

    return pygls_uris.from_fs_path(str((FIXTURES_DIR / "simple_module.sv").resolve()))


@pytest.fixture
def cross_file_uris() -> dict[str, str]:
    """Return a dict of name -> URI for the cross_file fixtures."""
    from pygls import uris as pygls_uris

    return {
        "pkg": pygls_uris.from_fs_path(str((CROSS_FILE_DIR / "pkg.sv").resolve())),
        "sub": pygls_uris.from_fs_path(str((CROSS_FILE_DIR / "sub.sv").resolve())),
        "top": pygls_uris.from_fs_path(str((CROSS_FILE_DIR / "top.sv").resolve())),
    }
