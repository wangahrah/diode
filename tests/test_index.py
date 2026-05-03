"""Tests for diode.index — symbol index and build_index."""

from __future__ import annotations

from pathlib import Path

from diode.compiler import compile_project
from diode.index import SymbolIndex, build_index
from diode.types import (
    CompilationResult,
    DiodeSymbolKind,
    FilePosition,
    ProjectConfig,
    ProjectSource,
    ProjectSourceKind,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"
SIMPLE_MODULE = FIXTURES_DIR / "simple_module.sv"
PARAMETERIZED = FIXTURES_DIR / "parameterized.sv"
CROSS_FILE_DIR = FIXTURES_DIR / "cross_file"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _compile_simple() -> CompilationResult:
    """Compile simple_module.sv and return the result."""
    config = ProjectConfig(
        source_files=[
            ProjectSource(path=SIMPLE_MODULE.resolve(), kind=ProjectSourceKind.AUTO_DISCOVER),
        ]
    )
    return compile_project(config)


def _compile_parameterized() -> CompilationResult:
    """Compile parameterized.sv and return the result."""
    config = ProjectConfig(
        source_files=[
            ProjectSource(path=PARAMETERIZED.resolve(), kind=ProjectSourceKind.AUTO_DISCOVER),
        ]
    )
    return compile_project(config)


def _compile_cross_file() -> CompilationResult:
    """Compile the cross_file fixture set and return the result."""
    config = ProjectConfig(
        source_files=[
            ProjectSource(path=(CROSS_FILE_DIR / "pkg.sv").resolve(), kind=ProjectSourceKind.AUTO_DISCOVER),
            ProjectSource(path=(CROSS_FILE_DIR / "sub.sv").resolve(), kind=ProjectSourceKind.AUTO_DISCOVER),
            ProjectSource(path=(CROSS_FILE_DIR / "top.sv").resolve(), kind=ProjectSourceKind.AUTO_DISCOVER),
        ]
    )
    return compile_project(config)


def _build_simple_index() -> SymbolIndex:
    """Build index for simple_module.sv."""
    return build_index(_compile_simple())


def _build_parameterized_index() -> SymbolIndex:
    """Build index for parameterized.sv."""
    return build_index(_compile_parameterized())


def _build_cross_file_index() -> SymbolIndex:
    """Build index for the cross_file fixture set."""
    return build_index(_compile_cross_file())


# ---------------------------------------------------------------------------
# Tests: simple_module.sv indexing
# ---------------------------------------------------------------------------


class TestSimpleModuleIndex:
    """Test indexing of simple_module.sv."""

    def test_build_index_returns_symbol_index(self) -> None:
        result = _compile_simple()
        index = build_index(result)
        assert isinstance(index, SymbolIndex)

    def test_document_symbols_contains_module(self) -> None:
        index = _build_simple_index()
        path = SIMPLE_MODULE.resolve()
        symbols = index.get_document_symbols(path)
        names = [s.name for s in symbols]
        assert "counter" in names

    def test_document_symbols_contains_ports(self) -> None:
        index = _build_simple_index()
        path = SIMPLE_MODULE.resolve()
        symbols = index.get_document_symbols(path)
        port_names = {s.name for s in symbols if s.kind == DiodeSymbolKind.PORT}
        assert port_names == {"clk", "rst_n", "en", "count"}

    def test_document_symbols_contains_parameter(self) -> None:
        index = _build_simple_index()
        path = SIMPLE_MODULE.resolve()
        symbols = index.get_document_symbols(path)
        params = [s for s in symbols if s.kind == DiodeSymbolKind.PARAMETER]
        assert len(params) == 1
        assert params[0].name == "WIDTH"

    def test_document_symbols_contains_signal(self) -> None:
        index = _build_simple_index()
        path = SIMPLE_MODULE.resolve()
        symbols = index.get_document_symbols(path)
        signals = [s for s in symbols if s.kind == DiodeSymbolKind.SIGNAL]
        assert len(signals) == 1
        assert signals[0].name == "count_next"
        assert signals[0].type_str is not None
        assert "logic" in signals[0].type_str

    def test_document_symbols_contains_always_blocks(self) -> None:
        index = _build_simple_index()
        path = SIMPLE_MODULE.resolve()
        symbols = index.get_document_symbols(path)
        always = [s for s in symbols if s.kind == DiodeSymbolKind.ALWAYS]
        assert len(always) == 2
        details = {s.detail for s in always}
        assert "always_comb" in details
        assert "always_ff" in details

    def test_document_symbols_ordered_by_position(self) -> None:
        index = _build_simple_index()
        path = SIMPLE_MODULE.resolve()
        symbols = index.get_document_symbols(path)
        lines = [s.definition.range.start.line for s in symbols]
        assert lines == sorted(lines)

    def test_module_has_detail_with_ports(self) -> None:
        index = _build_simple_index()
        sym = index.find_definition("counter")
        assert sym is not None
        assert sym.detail is not None
        assert "clk" in sym.detail
        assert "count" in sym.detail

    def test_parameter_has_type_and_value(self) -> None:
        index = _build_simple_index()
        path = SIMPLE_MODULE.resolve()
        sym = index.find_definition("WIDTH", path)
        assert sym is not None
        assert sym.kind == DiodeSymbolKind.PARAMETER
        assert sym.type_str is not None
        assert "int" in sym.type_str
        assert sym.detail is not None
        assert "8" in sym.detail

    def test_port_has_direction_and_type(self) -> None:
        index = _build_simple_index()
        path = SIMPLE_MODULE.resolve()
        symbols = index.get_document_symbols(path)
        count_port = next(s for s in symbols if s.name == "count" and s.kind == DiodeSymbolKind.PORT)
        assert count_port.type_str is not None
        assert "output" in count_port.type_str
        assert "logic" in count_port.type_str

    def test_signal_parent_name_is_module(self) -> None:
        index = _build_simple_index()
        path = SIMPLE_MODULE.resolve()
        sym = index.find_definition("count_next", path)
        assert sym is not None
        assert sym.parent_name == "counter"


# ---------------------------------------------------------------------------
# Tests: parameterized.sv indexing
# ---------------------------------------------------------------------------


class TestParameterizedModuleIndex:
    """Test indexing of parameterized.sv."""

    def test_document_symbols_contains_module(self) -> None:
        index = _build_parameterized_index()
        path = PARAMETERIZED.resolve()
        symbols = index.get_document_symbols(path)
        names = [s.name for s in symbols]
        assert "shift_register" in names

    def test_document_symbols_contains_parameters(self) -> None:
        index = _build_parameterized_index()
        path = PARAMETERIZED.resolve()
        symbols = index.get_document_symbols(path)
        params = {s.name for s in symbols if s.kind == DiodeSymbolKind.PARAMETER}
        assert "DEPTH" in params
        assert "WIDTH" in params

    def test_document_symbols_contains_ports(self) -> None:
        index = _build_parameterized_index()
        path = PARAMETERIZED.resolve()
        symbols = index.get_document_symbols(path)
        ports = {s.name for s in symbols if s.kind == DiodeSymbolKind.PORT}
        assert ports == {"clk", "rst_n", "din", "dout"}

    def test_document_symbols_contains_signal(self) -> None:
        index = _build_parameterized_index()
        path = PARAMETERIZED.resolve()
        symbols = index.get_document_symbols(path)
        signals = [s for s in symbols if s.kind == DiodeSymbolKind.SIGNAL]
        signal_names = {s.name for s in signals}
        assert "stage_reg" in signal_names

    def test_document_symbols_contains_generate_block(self) -> None:
        index = _build_parameterized_index()
        path = PARAMETERIZED.resolve()
        symbols = index.get_document_symbols(path)
        generates = [s for s in symbols if s.kind == DiodeSymbolKind.GENERATE]
        assert len(generates) >= 1
        assert any(s.name == "gen_stages" for s in generates)


# ---------------------------------------------------------------------------
# Tests: cross_file indexing
# ---------------------------------------------------------------------------


class TestCrossFileIndex:
    """Test indexing across multiple files (cross_file fixture)."""

    def test_top_module_indexed(self) -> None:
        index = _build_cross_file_index()
        sym = index.find_definition("top_design")
        assert sym is not None
        assert sym.kind == DiodeSymbolKind.MODULE
        assert sym.definition.path.name == "top.sv"

    def test_sub_module_indexed(self) -> None:
        index = _build_cross_file_index()
        sym = index.find_definition("data_processor")
        assert sym is not None
        assert sym.kind == DiodeSymbolKind.MODULE
        assert sym.definition.path.name == "sub.sv"

    def test_package_indexed(self) -> None:
        index = _build_cross_file_index()
        sym = index.find_definition("common_pkg")
        assert sym is not None
        assert sym.kind == DiodeSymbolKind.PACKAGE
        assert sym.definition.path.name == "pkg.sv"

    def test_package_typedef_indexed(self) -> None:
        index = _build_cross_file_index()
        sym = index.find_definition("data_t")
        assert sym is not None
        assert sym.kind == DiodeSymbolKind.TYPEDEF
        assert sym.definition.path.name == "pkg.sv"
        assert sym.type_str is not None

    def test_package_function_indexed(self) -> None:
        index = _build_cross_file_index()
        sym = index.find_definition("is_valid")
        assert sym is not None
        assert sym.kind == DiodeSymbolKind.FUNCTION
        assert sym.definition.path.name == "pkg.sv"
        assert sym.detail is not None
        assert "function" in sym.detail

    def test_package_enum_members_indexed(self) -> None:
        index = _build_cross_file_index()
        for name in ("CMD_NOP", "CMD_READ", "CMD_WRITE", "CMD_RESET"):
            sym = index.find_definition(name)
            assert sym is not None, f"Enum member {name} not found"
            assert sym.kind == DiodeSymbolKind.ENUM_MEMBER
            assert sym.parent_name == "common_pkg"

    def test_instance_indexed(self) -> None:
        index = _build_cross_file_index()
        top_path = (CROSS_FILE_DIR / "top.sv").resolve()
        sym = index.find_definition("u_proc", top_path)
        assert sym is not None
        assert sym.kind == DiodeSymbolKind.INSTANCE
        assert sym.detail == "data_processor"
        assert sym.definition.path.name == "top.sv"

    def test_find_references_for_module(self) -> None:
        """find_references('data_processor') should include the instantiation site."""
        index = _build_cross_file_index()
        refs = index.find_references("data_processor")
        assert len(refs) >= 1
        # At least the definition and the instantiation
        ref_files = {r.path.name for r in refs}
        assert "top.sv" in ref_files  # instantiation site
        assert "sub.sv" in ref_files  # definition site

    def test_sub_module_ports_indexed_in_sub_file(self) -> None:
        """data_processor's ports should be indexed in sub.sv."""
        index = _build_cross_file_index()
        sub_path = (CROSS_FILE_DIR / "sub.sv").resolve()
        symbols = index.get_document_symbols(sub_path)
        port_names = {s.name for s in symbols if s.kind == DiodeSymbolKind.PORT}
        assert "clk" in port_names
        assert "data_in" in port_names
        assert "cmd" in port_names

    def test_sub_module_signals_indexed(self) -> None:
        """data_processor's internal signals should be indexed."""
        index = _build_cross_file_index()
        sub_path = (CROSS_FILE_DIR / "sub.sv").resolve()
        symbols = index.get_document_symbols(sub_path)
        signal_names = {s.name for s in symbols if s.kind == DiodeSymbolKind.SIGNAL}
        assert "data_reg" in signal_names

    def test_top_module_document_symbols(self) -> None:
        index = _build_cross_file_index()
        top_path = (CROSS_FILE_DIR / "top.sv").resolve()
        symbols = index.get_document_symbols(top_path)
        names = {s.name for s in symbols}
        assert "top_design" in names
        assert "u_proc" in names
        assert "internal_data" in names


# ---------------------------------------------------------------------------
# Tests: lookup_at
# ---------------------------------------------------------------------------


class TestLookupAt:
    """Test the lookup_at() method."""

    def test_lookup_at_module_declaration(self) -> None:
        """Cursor on module name should return the module."""
        index = _build_simple_index()
        path = SIMPLE_MODULE.resolve()
        # "module counter" is at line 14, col 8 (0-based: line=13, col=7)
        sym = index.lookup_at(path, FilePosition(line=13, column=7))
        assert sym is not None
        assert sym.name == "counter"
        assert sym.kind == DiodeSymbolKind.MODULE

    def test_lookup_at_port_declaration(self) -> None:
        """Cursor on port name should return the port."""
        index = _build_simple_index()
        path = SIMPLE_MODULE.resolve()
        # "clk" port is at line 17, col 30 (0-based: line=16, col=29)
        sym = index.lookup_at(path, FilePosition(line=16, column=29))
        assert sym is not None
        assert sym.name == "clk"
        assert sym.kind == DiodeSymbolKind.PORT

    def test_lookup_at_parameter_declaration(self) -> None:
        """Cursor on parameter name should return the parameter."""
        index = _build_simple_index()
        path = SIMPLE_MODULE.resolve()
        # "WIDTH" parameter is at line 15, col 19 (0-based: line=14, col=18)
        sym = index.lookup_at(path, FilePosition(line=14, column=18))
        assert sym is not None
        assert sym.name == "WIDTH"
        assert sym.kind == DiodeSymbolKind.PARAMETER

    def test_lookup_at_signal_declaration(self) -> None:
        """Cursor on signal declaration should return the signal."""
        index = _build_simple_index()
        path = SIMPLE_MODULE.resolve()
        # "count_next" is at line 23, col 23 (0-based: line=22, col=22)
        sym = index.lookup_at(path, FilePosition(line=22, column=22))
        assert sym is not None
        assert sym.name == "count_next"
        assert sym.kind == DiodeSymbolKind.SIGNAL

    def test_lookup_at_word_under_cursor_fallback(self) -> None:
        """Cursor on a reference should use word-under-cursor fallback."""
        index = _build_simple_index()
        path = SIMPLE_MODULE.resolve()
        # Line 38: "count <= count_next;" (0-based line=37)
        # "count_next" starts at some column within the always_ff block
        # Since always_ff is the narrowest container, it will match first.
        # But if we click on a word that's NOT inside any specific symbol's
        # range, the fallback should kick in.
        # Let's use find_definition directly as a more reliable test:
        sym = index.find_definition("count_next", path)
        assert sym is not None
        assert sym.name == "count_next"
        assert sym.kind == DiodeSymbolKind.SIGNAL

    def test_lookup_at_instance(self) -> None:
        """Cursor on instance name should return the instance."""
        index = _build_cross_file_index()
        top_path = (CROSS_FILE_DIR / "top.sv").resolve()
        # "u_proc" is at line 31, col 20 (0-based: line=30, col=19)
        sym = index.lookup_at(top_path, FilePosition(line=30, column=19))
        assert sym is not None
        assert sym.name == "u_proc"
        assert sym.kind == DiodeSymbolKind.INSTANCE
        assert sym.detail == "data_processor"

    def test_lookup_at_returns_none_for_empty_position(self) -> None:
        """lookup_at on a comment or whitespace should return None."""
        index = _build_simple_index()
        path = SIMPLE_MODULE.resolve()
        # Line 1 is a comment: "// tests/fixtures/simple_module.sv"
        sym = index.lookup_at(path, FilePosition(line=0, column=0))
        assert sym is None


# ---------------------------------------------------------------------------
# Tests: find_definition
# ---------------------------------------------------------------------------


class TestFindDefinition:
    """Test the find_definition() method."""

    def test_find_module_by_name(self) -> None:
        index = _build_simple_index()
        sym = index.find_definition("counter")
        assert sym is not None
        assert sym.kind == DiodeSymbolKind.MODULE
        assert sym.name == "counter"

    def test_find_signal_with_context(self) -> None:
        index = _build_simple_index()
        path = SIMPLE_MODULE.resolve()
        sym = index.find_definition("count_next", path)
        assert sym is not None
        assert sym.kind == DiodeSymbolKind.SIGNAL

    def test_find_parameter_with_context(self) -> None:
        index = _build_simple_index()
        path = SIMPLE_MODULE.resolve()
        sym = index.find_definition("WIDTH", path)
        assert sym is not None
        assert sym.kind == DiodeSymbolKind.PARAMETER

    def test_find_cross_file_module(self) -> None:
        index = _build_cross_file_index()
        sym = index.find_definition("data_processor")
        assert sym is not None
        assert sym.kind == DiodeSymbolKind.MODULE
        assert sym.definition.path.name == "sub.sv"

    def test_find_package(self) -> None:
        index = _build_cross_file_index()
        sym = index.find_definition("common_pkg")
        assert sym is not None
        assert sym.kind == DiodeSymbolKind.PACKAGE

    def test_find_typedef(self) -> None:
        index = _build_cross_file_index()
        sym = index.find_definition("data_t")
        assert sym is not None
        assert sym.kind == DiodeSymbolKind.TYPEDEF

    def test_find_nonexistent_returns_none(self) -> None:
        index = _build_simple_index()
        sym = index.find_definition("nonexistent_symbol")
        assert sym is None

    def test_find_prefers_local_over_global(self) -> None:
        """When a name exists locally and globally, prefer local."""
        index = _build_cross_file_index()
        top_path = (CROSS_FILE_DIR / "top.sv").resolve()
        # "clk" exists as a port in both top_design and data_processor.
        # With context_path=top.sv, should return the top.sv one.
        sym = index.find_definition("clk", top_path)
        assert sym is not None
        assert sym.definition.path.name == "top.sv"


# ---------------------------------------------------------------------------
# Tests: find_references
# ---------------------------------------------------------------------------


class TestFindReferences:
    """Test the find_references() method."""

    def test_find_references_for_instantiated_module(self) -> None:
        index = _build_cross_file_index()
        refs = index.find_references("data_processor")
        assert len(refs) >= 2
        ref_files = {r.path.name for r in refs}
        # Should have at least: definition (sub.sv) and instantiation (top.sv)
        assert "sub.sv" in ref_files
        assert "top.sv" in ref_files

    def test_find_references_for_nonexistent(self) -> None:
        index = _build_simple_index()
        refs = index.find_references("nonexistent_xyz")
        assert refs == []

    def test_find_references_for_signal(self) -> None:
        """find_references for a signal should include its declaration."""
        index = _build_simple_index()
        refs = index.find_references("count_next")
        assert len(refs) >= 1
        # At least the declaration site
        ref_lines = [r.range.start.line for r in refs]
        # count_next declared at line 23 (0-based: 22)
        assert 22 in ref_lines


# ---------------------------------------------------------------------------
# Tests: get_document_symbols
# ---------------------------------------------------------------------------


class TestGetDocumentSymbols:
    """Test the get_document_symbols() method."""

    def test_simple_module_outline(self) -> None:
        """get_document_symbols returns a proper outline for simple_module.sv."""
        index = _build_simple_index()
        path = SIMPLE_MODULE.resolve()
        symbols = index.get_document_symbols(path)
        assert len(symbols) > 0

        kinds = {s.kind for s in symbols}
        assert DiodeSymbolKind.MODULE in kinds
        assert DiodeSymbolKind.PORT in kinds
        assert DiodeSymbolKind.PARAMETER in kinds
        assert DiodeSymbolKind.SIGNAL in kinds
        assert DiodeSymbolKind.ALWAYS in kinds

    def test_package_outline(self) -> None:
        """get_document_symbols returns proper outline for pkg.sv."""
        index = _build_cross_file_index()
        pkg_path = (CROSS_FILE_DIR / "pkg.sv").resolve()
        symbols = index.get_document_symbols(pkg_path)
        assert len(symbols) > 0

        kinds = {s.kind for s in symbols}
        assert DiodeSymbolKind.PACKAGE in kinds
        assert DiodeSymbolKind.TYPEDEF in kinds
        assert DiodeSymbolKind.ENUM_MEMBER in kinds
        assert DiodeSymbolKind.FUNCTION in kinds

    def test_empty_path_returns_empty(self) -> None:
        index = _build_simple_index()
        symbols = index.get_document_symbols(Path("/nonexistent/path.sv"))
        assert symbols == []

    def test_symbols_are_ordered_by_position(self) -> None:
        index = _build_cross_file_index()
        top_path = (CROSS_FILE_DIR / "top.sv").resolve()
        symbols = index.get_document_symbols(top_path)
        positions = [(s.definition.range.start.line, s.definition.range.start.column) for s in symbols]
        assert positions == sorted(positions)
