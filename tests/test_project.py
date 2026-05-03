"""Tests for diode.project — project configuration loading."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from diode.project import (
    _auto_discover,
    _parse_diode_toml,
    _parse_file_list,
    load_project,
)
from diode.types import ProjectConfig, ProjectSource, ProjectSourceKind

FIXTURES_DIR = Path(__file__).parent / "fixtures"
PROJECT_DIR = FIXTURES_DIR / "project"


# ---------------------------------------------------------------------------
# _parse_diode_toml
# ---------------------------------------------------------------------------


class TestParseDiodeToml:
    """Tests for parsing diode.toml configuration files."""

    def test_all_fields_populated(self) -> None:
        """Parse a diode.toml with all fields populated."""
        config = _parse_diode_toml(PROJECT_DIR / "diode.toml")

        assert isinstance(config, ProjectConfig)
        assert config.top_module == "top_design"
        assert config.config_path == PROJECT_DIR / "diode.toml"

        # files glob should have found mod_a.sv and mod_b.sv
        source_paths = [s.path for s in config.source_files]
        assert (PROJECT_DIR / "sv_sources" / "mod_a.sv").resolve() in source_paths
        assert (PROJECT_DIR / "sv_sources" / "mod_b.sv").resolve() in source_paths

        # All sources should be CONFIG kind (from files= pattern)
        for src in config.source_files:
            assert src.kind == ProjectSourceKind.CONFIG

        # include_dirs
        assert (PROJECT_DIR / "includes").resolve() in config.include_dirs

        # defines
        assert config.defines["SYNTHESIS"] == "1"
        assert config.defines["WIDTH"] == "8"

    def test_glob_expansion(self) -> None:
        """Glob patterns in files= are expanded at parse time."""
        config = _parse_diode_toml(PROJECT_DIR / "diode.toml")
        source_paths = [s.path for s in config.source_files]
        # *.sv in sv_sources should match at least mod_a.sv and mod_b.sv
        assert len(source_paths) >= 2

    def test_files_and_filelist_merged(self) -> None:
        """Both files= and file_list= coexist and sources are merged."""
        config = _parse_diode_toml(PROJECT_DIR / "diode_with_filelist.toml")

        source_paths = [s.path for s in config.source_files]
        # mod_a.sv from files=
        assert (PROJECT_DIR / "sv_sources" / "mod_a.sv").resolve() in source_paths
        # defs.svh from nested.f (referenced by file_list=)
        assert (PROJECT_DIR / "includes" / "defs.svh").resolve() in source_paths

        # Include dirs should come from both toml and .f file
        assert (PROJECT_DIR / "includes").resolve() in config.include_dirs
        assert (PROJECT_DIR / "sv_sources").resolve() in config.include_dirs

        # Defines merged: TOP_DEFINE from toml, NESTED_DEF from .f file
        assert config.defines["TOP_DEFINE"] == "1"
        assert config.defines["NESTED_DEF"] == "42"

    def test_malformed_toml_returns_valid_config(self) -> None:
        """A malformed TOML file returns a valid (empty) ProjectConfig."""
        config = _parse_diode_toml(PROJECT_DIR / "bad.toml")
        assert isinstance(config, ProjectConfig)
        assert config.config_path == PROJECT_DIR / "bad.toml"

    def test_missing_project_section(self, tmp_path: Path) -> None:
        """A TOML file with no [project] section returns empty config."""
        toml_path = tmp_path / "diode.toml"
        toml_path.write_text("[other]\nkey = 1\n")
        config = _parse_diode_toml(toml_path)
        assert isinstance(config, ProjectConfig)
        assert config.source_files == []
        assert config.top_module is None

    def test_no_matching_glob(self, tmp_path: Path) -> None:
        """A glob pattern that matches nothing logs a warning but does not crash."""
        toml_path = tmp_path / "diode.toml"
        toml_path.write_text('[project]\nfiles = ["nonexistent/**/*.sv"]\n')
        config = _parse_diode_toml(toml_path)
        assert isinstance(config, ProjectConfig)
        assert config.source_files == []

    def test_paths_are_absolute(self) -> None:
        """All returned paths should be absolute."""
        config = _parse_diode_toml(PROJECT_DIR / "diode.toml")
        for src in config.source_files:
            assert src.path.is_absolute()
        for inc in config.include_dirs:
            assert inc.is_absolute()

    def test_source_deduplication(self, tmp_path: Path) -> None:
        """Duplicate files in multiple patterns are deduplicated."""
        sv_dir = tmp_path / "rtl"
        sv_dir.mkdir()
        (sv_dir / "a.sv").write_text("module a; endmodule\n")

        toml_path = tmp_path / "diode.toml"
        toml_path.write_text(
            '[project]\nfiles = ["rtl/a.sv", "rtl/*.sv"]\n'
        )
        config = _parse_diode_toml(toml_path)
        paths = [s.path for s in config.source_files]
        assert len(paths) == len(set(paths)), "Duplicate source files found"


# ---------------------------------------------------------------------------
# _parse_file_list
# ---------------------------------------------------------------------------


class TestParseFileList:
    """Tests for .f file list parsing."""

    def test_basic_file_list(self) -> None:
        """Parse a .f file with source files, incdir, defines, comments."""
        sources, incdirs, defines = _parse_file_list(PROJECT_DIR / "main.f")

        # Source files
        source_strs = [str(p) for p in sources]
        assert any("mod_a.sv" in s for s in source_strs)
        assert any("mod_b.sv" in s for s in source_strs)

        # Include dirs
        incdir_strs = [str(d) for d in incdirs]
        assert any("includes" in s for s in incdir_strs)

        # Defines
        assert defines["SYNTHESIS"] == "1"
        assert defines["DEBUG"] == ""

    def test_nested_f_includes(self) -> None:
        """Nested -f directives bring in sources, incdirs, and defines."""
        sources, incdirs, defines = _parse_file_list(PROJECT_DIR / "main.f")

        # From nested.f
        source_strs = [str(p) for p in sources]
        assert any("defs.svh" in s for s in source_strs)

        incdir_strs = [str(d) for d in incdirs]
        assert any("sv_sources" in s for s in incdir_strs)

        assert defines["NESTED_DEF"] == "42"

    def test_cycle_detection(self) -> None:
        """Cyclic -f includes are detected and do not cause infinite recursion."""
        sources, incdirs, defines = _parse_file_list(
            PROJECT_DIR / "cycle_a.f"
        )
        # Should get sources from both files but not loop forever
        source_strs = [str(p) for p in sources]
        assert any("mod_a.sv" in s for s in source_strs)
        assert any("mod_b.sv" in s for s in source_strs)

    def test_comments_and_blank_lines(self, tmp_path: Path) -> None:
        """Comments (// and #) and blank lines are properly skipped."""
        f_path = tmp_path / "test.f"
        f_path.write_text(
            "// comment line\n"
            "# another comment\n"
            "\n"
            "   \n"
            "some_file.sv\n"
            "another.sv // inline comment\n"
        )
        (tmp_path / "some_file.sv").write_text("module x; endmodule\n")
        (tmp_path / "another.sv").write_text("module y; endmodule\n")

        sources, _, _ = _parse_file_list(f_path)
        assert len(sources) == 2
        source_strs = [str(p) for p in sources]
        assert any("some_file.sv" in s for s in source_strs)
        assert any("another.sv" in s for s in source_strs)

    def test_missing_f_file(self, tmp_path: Path) -> None:
        """Parsing a non-existent .f file returns empty results."""
        sources, incdirs, defines = _parse_file_list(
            tmp_path / "nonexistent.f"
        )
        assert sources == []
        assert incdirs == []
        assert defines == {}

    def test_incdir_directive(self, tmp_path: Path) -> None:
        """The +incdir+ directive adds include directories."""
        f_path = tmp_path / "test.f"
        f_path.write_text("+incdir+my_includes\n+incdir+other/path\n")
        (tmp_path / "my_includes").mkdir()
        (tmp_path / "other" / "path").mkdir(parents=True)

        _, incdirs, _ = _parse_file_list(f_path)
        assert len(incdirs) == 2
        assert (tmp_path / "my_includes").resolve() in incdirs
        assert (tmp_path / "other" / "path").resolve() in incdirs

    def test_define_with_and_without_value(self, tmp_path: Path) -> None:
        """Defines with and without values are parsed correctly."""
        f_path = tmp_path / "test.f"
        f_path.write_text(
            "+define+HAS_VALUE=42\n"
            "+define+NO_VALUE\n"
            "+define+EMPTY_VALUE=\n"
        )
        _, _, defines = _parse_file_list(f_path)
        assert defines["HAS_VALUE"] == "42"
        assert defines["NO_VALUE"] == ""
        assert defines["EMPTY_VALUE"] == ""

    def test_paths_relative_to_f_file(self, tmp_path: Path) -> None:
        """Source file paths are resolved relative to the .f file location."""
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        f_path = subdir / "test.f"
        f_path.write_text("../top.sv\n")
        (tmp_path / "top.sv").write_text("module top; endmodule\n")

        sources, _, _ = _parse_file_list(f_path)
        assert len(sources) == 1
        assert sources[0] == (tmp_path / "top.sv").resolve()


# ---------------------------------------------------------------------------
# _auto_discover
# ---------------------------------------------------------------------------


class TestAutoDiscover:
    """Tests for auto-discovery of SystemVerilog files."""

    def test_discovers_sv_files(self, tmp_path: Path) -> None:
        """Auto-discover finds *.sv, *.v, and *.svh files recursively."""
        (tmp_path / "top.sv").write_text("module top; endmodule\n")
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "mod.v").write_text("module mod; endmodule\n")
        (sub / "defs.svh").write_text("`define X 1\n")
        # Non-SV file should be ignored
        (tmp_path / "readme.txt").write_text("not a sv file\n")

        results = _auto_discover(tmp_path)
        result_names = [p.name for p in results]
        assert "top.sv" in result_names
        assert "mod.v" in result_names
        assert "defs.svh" in result_names
        assert "readme.txt" not in result_names

    def test_returns_sorted_absolute_paths(self, tmp_path: Path) -> None:
        """Results are sorted and absolute."""
        (tmp_path / "b.sv").write_text("module b; endmodule\n")
        (tmp_path / "a.sv").write_text("module a; endmodule\n")

        results = _auto_discover(tmp_path)
        assert all(p.is_absolute() for p in results)
        assert results == sorted(results)

    def test_empty_directory(self, tmp_path: Path) -> None:
        """An empty directory returns an empty list."""
        results = _auto_discover(tmp_path)
        assert results == []

    def test_discovers_existing_fixtures(self) -> None:
        """Auto-discover should find the existing fixture .sv files."""
        results = _auto_discover(FIXTURES_DIR)
        result_names = [p.name for p in results]
        assert "simple_module.sv" in result_names
        assert "parameterized.sv" in result_names
        assert "top.sv" in result_names
        assert "sub.sv" in result_names
        assert "pkg.sv" in result_names


# ---------------------------------------------------------------------------
# load_project (integration)
# ---------------------------------------------------------------------------


class TestLoadProject:
    """Integration tests for load_project entry point."""

    def test_loads_diode_toml(self, tmp_path: Path) -> None:
        """load_project finds and uses diode.toml when present."""
        # Set up a workspace with a diode.toml
        sv_dir = tmp_path / "rtl"
        sv_dir.mkdir()
        (sv_dir / "mod.sv").write_text("module mod; endmodule\n")
        toml_path = tmp_path / "diode.toml"
        toml_path.write_text(
            '[project]\ntop = "mod"\nfiles = ["rtl/*.sv"]\n'
        )

        config = load_project(tmp_path)
        assert config.config_path == toml_path
        assert config.top_module == "mod"
        assert len(config.source_files) == 1
        assert config.source_files[0].kind == ProjectSourceKind.CONFIG

    def test_loads_f_file_when_no_toml(self, tmp_path: Path) -> None:
        """load_project uses .f files when no diode.toml exists."""
        (tmp_path / "a.sv").write_text("module a; endmodule\n")
        f_path = tmp_path / "project.f"
        f_path.write_text("a.sv\n+define+TEST=1\n")

        config = load_project(tmp_path)
        assert config.config_path is None
        assert len(config.source_files) == 1
        assert config.source_files[0].kind == ProjectSourceKind.FILE_LIST
        assert config.defines["TEST"] == "1"

    def test_auto_discovers_when_no_config(self, tmp_path: Path) -> None:
        """load_project falls back to auto-discovery with no config files."""
        (tmp_path / "a.sv").write_text("module a; endmodule\n")
        (tmp_path / "b.v").write_text("module b; endmodule\n")

        config = load_project(tmp_path)
        assert config.config_path is None
        assert len(config.source_files) == 2
        for src in config.source_files:
            assert src.kind == ProjectSourceKind.AUTO_DISCOVER

    def test_empty_workspace(self, tmp_path: Path) -> None:
        """An empty workspace returns a valid config with no sources."""
        config = load_project(tmp_path)
        assert isinstance(config, ProjectConfig)
        assert config.source_files == []
        assert config.include_dirs == []
        assert config.defines == {}
        assert config.top_module is None

    def test_never_raises(self, tmp_path: Path) -> None:
        """load_project never raises exceptions, even for weird inputs."""
        # Non-existent directory -- Path.resolve() won't crash
        config = load_project(tmp_path / "nonexistent")
        assert isinstance(config, ProjectConfig)

    def test_priority_toml_over_f_files(self, tmp_path: Path) -> None:
        """When both diode.toml and .f files exist, diode.toml wins."""
        (tmp_path / "mod.sv").write_text("module mod; endmodule\n")
        (tmp_path / "diode.toml").write_text(
            '[project]\ntop = "from_toml"\nfiles = ["mod.sv"]\n'
        )
        (tmp_path / "project.f").write_text("mod.sv\n+define+FROM_F=1\n")

        config = load_project(tmp_path)
        assert config.top_module == "from_toml"
        # Should NOT have the define from the .f file
        assert "FROM_F" not in config.defines

    def test_f_file_deduplication(self, tmp_path: Path) -> None:
        """Duplicate source files from multiple .f files are deduplicated."""
        (tmp_path / "mod.sv").write_text("module mod; endmodule\n")
        (tmp_path / "a.f").write_text("mod.sv\n")
        (tmp_path / "b.f").write_text("mod.sv\n")

        config = load_project(tmp_path)
        paths = [s.path for s in config.source_files]
        assert len(paths) == len(set(paths)), "Duplicate source files found"

    def test_load_project_with_fixtures(self) -> None:
        """load_project correctly processes the project fixtures directory."""
        config = load_project(PROJECT_DIR)
        assert config.config_path == (PROJECT_DIR / "diode.toml")
        assert config.top_module == "top_design"

        source_paths = [s.path for s in config.source_files]
        assert (PROJECT_DIR / "sv_sources" / "mod_a.sv").resolve() in source_paths
        assert (PROJECT_DIR / "sv_sources" / "mod_b.sv").resolve() in source_paths
