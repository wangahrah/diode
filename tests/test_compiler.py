"""Tests for diode.compiler module."""

from __future__ import annotations

from pathlib import Path

import pytest

from diode.compiler import compile_project
from diode.types import (
    CompilationResult,
    DiodeDiagnostic,
    DiodeSeverity,
    ProjectConfig,
    ProjectSource,
    ProjectSourceKind,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _make_config(paths: list[Path], **kwargs) -> ProjectConfig:
    """Helper to build a ProjectConfig from a list of file paths."""
    sources = [
        ProjectSource(path=p, kind=ProjectSourceKind.AUTO_DISCOVER) for p in paths
    ]
    return ProjectConfig(source_files=sources, **kwargs)


class TestCompileSimpleModule:
    """Test compilation of a single simple module."""

    def test_compiles_successfully(self) -> None:
        """simple_module.sv should compile with zero errors."""
        config = _make_config([FIXTURES_DIR / "simple_module.sv"])
        result = compile_project(config)

        assert isinstance(result, CompilationResult)
        assert result.success is True
        assert result.compilation is not None

    def test_no_error_diagnostics(self) -> None:
        """simple_module.sv should produce no error-level diagnostics."""
        config = _make_config([FIXTURES_DIR / "simple_module.sv"])
        result = compile_project(config)

        error_diags = [
            d for d in result.diagnostics if d.severity == DiodeSeverity.ERROR
        ]
        assert len(error_diags) == 0

    def test_source_files_populated(self) -> None:
        """CompilationResult.source_files should list the compiled file."""
        config = _make_config([FIXTURES_DIR / "simple_module.sv"])
        result = compile_project(config)

        assert len(result.source_files) == 1
        assert result.source_files[0] == (FIXTURES_DIR / "simple_module.sv").resolve()


class TestCompileCrossFile:
    """Test cross-file compilation with package, sub-module, and top-level."""

    def test_compiles_successfully(self) -> None:
        """Cross-file project (pkg, sub, top) should compile with zero errors."""
        cross_dir = FIXTURES_DIR / "cross_file"
        config = _make_config([
            cross_dir / "pkg.sv",
            cross_dir / "sub.sv",
            cross_dir / "top.sv",
        ])
        result = compile_project(config)

        assert result.success is True
        assert result.compilation is not None

    def test_no_error_diagnostics(self) -> None:
        """Cross-file compilation should produce no error-level diagnostics."""
        cross_dir = FIXTURES_DIR / "cross_file"
        config = _make_config([
            cross_dir / "pkg.sv",
            cross_dir / "sub.sv",
            cross_dir / "top.sv",
        ])
        result = compile_project(config)

        error_diags = [
            d for d in result.diagnostics if d.severity == DiodeSeverity.ERROR
        ]
        assert len(error_diags) == 0

    def test_source_files_list(self) -> None:
        """CompilationResult should contain all three source files."""
        cross_dir = FIXTURES_DIR / "cross_file"
        paths = [
            cross_dir / "pkg.sv",
            cross_dir / "sub.sv",
            cross_dir / "top.sv",
        ]
        config = _make_config(paths)
        result = compile_project(config)

        assert len(result.source_files) == 3
        resolved = [p.resolve() for p in paths]
        assert result.source_files == resolved


class TestDiagnostics:
    """Test diagnostic extraction from files with intentional errors."""

    def test_reports_errors(self) -> None:
        """bad.sv should produce at least one error diagnostic."""
        config = _make_config([FIXTURES_DIR / "bad.sv"])
        result = compile_project(config)

        assert result.success is False
        assert len(result.diagnostics) > 0

        error_diags = [
            d for d in result.diagnostics if d.severity == DiodeSeverity.ERROR
        ]
        assert len(error_diags) > 0

    def test_diagnostic_has_location(self) -> None:
        """Each diagnostic should have a valid file location."""
        config = _make_config([FIXTURES_DIR / "bad.sv"])
        result = compile_project(config)

        for diag in result.diagnostics:
            assert isinstance(diag, DiodeDiagnostic)
            assert diag.location is not None
            assert diag.location.range.start.line >= 0
            assert diag.location.range.start.column >= 0

    def test_diagnostic_has_message(self) -> None:
        """Each diagnostic should have a non-empty message string."""
        config = _make_config([FIXTURES_DIR / "bad.sv"])
        result = compile_project(config)

        for diag in result.diagnostics:
            assert diag.message
            assert len(diag.message) > 0

    def test_diagnostic_has_code(self) -> None:
        """Each diagnostic should have a code string (e.g. 'UndeclaredIdentifier')."""
        config = _make_config([FIXTURES_DIR / "bad.sv"])
        result = compile_project(config)

        for diag in result.diagnostics:
            assert diag.code is not None
            assert len(diag.code) > 0

    def test_undeclared_identifier_detected(self) -> None:
        """bad.sv should report 'nonexistent_signal' as undeclared."""
        config = _make_config([FIXTURES_DIR / "bad.sv"])
        result = compile_project(config)

        undeclared = [
            d for d in result.diagnostics if "nonexistent_signal" in d.message
        ]
        assert len(undeclared) >= 1

    def test_unknown_module_detected(self) -> None:
        """bad.sv should report 'ghost_module' as unknown."""
        config = _make_config([FIXTURES_DIR / "bad.sv"])
        result = compile_project(config)

        unknown = [d for d in result.diagnostics if "ghost_module" in d.message]
        assert len(unknown) >= 1


class TestOpenFilesOverride:
    """Test that open_files dict overrides disk content."""

    def test_override_replaces_disk_content(self) -> None:
        """When open_files provides content for a file, it should be used
        instead of reading from disk."""
        path = FIXTURES_DIR / "simple_module.sv"
        resolved = path.resolve()

        # Override with invalid content that will produce errors
        bad_content = "module override_test;\n  assign x = undefined_var;\nendmodule\n"
        config = _make_config([path])
        result = compile_project(config, open_files={resolved: bad_content})

        assert result.success is False
        error_diags = [
            d for d in result.diagnostics if d.severity == DiodeSeverity.ERROR
        ]
        assert len(error_diags) > 0

    def test_override_with_valid_content(self) -> None:
        """Overriding bad.sv with valid content should produce zero errors."""
        path = FIXTURES_DIR / "bad.sv"
        resolved = path.resolve()

        good_content = "module good_override (input logic clk);\nendmodule\n"
        config = _make_config([path])
        result = compile_project(config, open_files={resolved: good_content})

        assert result.success is True
        error_diags = [
            d for d in result.diagnostics if d.severity == DiodeSeverity.ERROR
        ]
        assert len(error_diags) == 0

    def test_override_only_affects_specified_file(self) -> None:
        """Only the file in open_files should be overridden; others read from disk."""
        cross_dir = FIXTURES_DIR / "cross_file"
        pkg_path = cross_dir / "pkg.sv"
        sub_path = cross_dir / "sub.sv"
        top_path = cross_dir / "top.sv"

        config = _make_config([pkg_path, sub_path, top_path])

        # Override only pkg.sv with valid but different content
        override_content = (
            "package common_pkg;\n"
            "  parameter int DATA_WIDTH = 32;\n"
            "  typedef logic [DATA_WIDTH-1:0] data_t;\n"
            "  typedef enum logic [1:0] {\n"
            "    CMD_NOP  = 2'b00,\n"
            "    CMD_READ = 2'b01,\n"
            "    CMD_WRITE = 2'b10,\n"
            "    CMD_RESET = 2'b11\n"
            "  } cmd_t;\n"
            "  function automatic logic is_valid(cmd_t cmd);\n"
            "    return cmd != CMD_NOP;\n"
            "  endfunction\n"
            "endpackage\n"
        )
        result = compile_project(
            config, open_files={pkg_path.resolve(): override_content}
        )

        # Should still compile successfully since override is valid
        assert result.success is True


class TestCompilationResultStructure:
    """Test the structure and invariants of CompilationResult."""

    def test_result_has_compilation_object(self) -> None:
        """CompilationResult should always have a compilation object."""
        config = _make_config([FIXTURES_DIR / "simple_module.sv"])
        result = compile_project(config)

        assert result.compilation is not None

    def test_result_diagnostics_are_list(self) -> None:
        """CompilationResult.diagnostics should be a list of DiodeDiagnostic."""
        config = _make_config([FIXTURES_DIR / "simple_module.sv"])
        result = compile_project(config)

        assert isinstance(result.diagnostics, list)
        for d in result.diagnostics:
            assert isinstance(d, DiodeDiagnostic)

    def test_empty_config_compiles(self) -> None:
        """An empty ProjectConfig (no source files) should compile without error."""
        config = _make_config([])
        result = compile_project(config)

        assert result.success is True
        assert result.compilation is not None
        assert len(result.source_files) == 0

    def test_none_open_files_accepted(self) -> None:
        """Passing None for open_files should be handled gracefully."""
        config = _make_config([FIXTURES_DIR / "simple_module.sv"])
        result = compile_project(config, open_files=None)

        assert result.success is True
