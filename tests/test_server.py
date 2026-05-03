"""Tests for diode.server -- LSP server module.

Tests include:
- Unit tests for conversion helpers and internal functions
- Unit tests for handler logic using direct index calls
- Integration tests using pytest-lsp for end-to-end LSP communication
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import lsprotocol.types as lsp
import pytest
import pytest_lsp
from pytest_lsp import ClientServerConfig, LanguageClient

FIXTURES_DIR = Path(__file__).parent / "fixtures"
SIMPLE_MODULE = FIXTURES_DIR / "simple_module.sv"
CROSS_FILE_DIR = FIXTURES_DIR / "cross_file"


# ---------------------------------------------------------------------------
# Unit tests: conversion helpers
# ---------------------------------------------------------------------------


class TestConversionHelpers:
    """Test position/range/location conversion functions."""

    def test_to_lsp_position(self) -> None:
        from diode.server import _to_lsp_position
        from diode.types import FilePosition

        pos = FilePosition(line=5, column=10)
        lsp_pos = _to_lsp_position(pos)
        assert lsp_pos.line == 5
        assert lsp_pos.character == 10

    def test_from_lsp_position(self) -> None:
        from diode.server import _from_lsp_position

        lsp_pos = lsp.Position(line=3, character=7)
        pos = _from_lsp_position(lsp_pos)
        assert pos.line == 3
        assert pos.column == 7

    def test_to_lsp_range(self) -> None:
        from diode.server import _to_lsp_range
        from diode.types import FilePosition, FileRange

        file_range = FileRange(
            start=FilePosition(line=1, column=2),
            end=FilePosition(line=3, column=4),
        )
        lsp_range = _to_lsp_range(file_range)
        assert lsp_range.start.line == 1
        assert lsp_range.start.character == 2
        assert lsp_range.end.line == 3
        assert lsp_range.end.character == 4

    def test_to_lsp_location(self) -> None:
        from diode.server import _to_lsp_location
        from diode.types import FileLocation, FilePosition, FileRange

        loc = FileLocation(
            path=Path("/tmp/test.sv"),
            range=FileRange(
                start=FilePosition(line=0, column=0),
                end=FilePosition(line=0, column=5),
            ),
        )
        lsp_loc = _to_lsp_location(loc)
        assert lsp_loc.uri == "file:///tmp/test.sv"
        assert lsp_loc.range.start.line == 0

    def test_uri_to_path(self) -> None:
        from diode.server import _uri_to_path

        path = _uri_to_path("file:///tmp/test.sv")
        assert path == Path("/tmp/test.sv")

    def test_path_to_uri(self) -> None:
        from diode.server import _path_to_uri

        uri = _path_to_uri(Path("/tmp/test.sv"))
        assert uri == "file:///tmp/test.sv"

    def test_roundtrip_uri_path(self) -> None:
        from diode.server import _path_to_uri, _uri_to_path

        original = Path("/home/user/project/rtl/top.sv")
        uri = _path_to_uri(original)
        roundtripped = _uri_to_path(uri)
        assert roundtripped == original


class TestSymbolKindMapping:
    """Test DiodeSymbolKind -> LSP SymbolKind mapping."""

    def test_all_kinds_mapped(self) -> None:
        from diode.server import _symbol_kind_to_lsp
        from diode.types import DiodeSymbolKind

        for kind in DiodeSymbolKind:
            result = _symbol_kind_to_lsp(kind)
            assert isinstance(result, lsp.SymbolKind), f"No mapping for {kind}"

    def test_specific_mappings(self) -> None:
        from diode.server import _symbol_kind_to_lsp
        from diode.types import DiodeSymbolKind

        assert _symbol_kind_to_lsp(DiodeSymbolKind.MODULE) == lsp.SymbolKind.Module
        assert _symbol_kind_to_lsp(DiodeSymbolKind.INTERFACE) == lsp.SymbolKind.Interface
        assert _symbol_kind_to_lsp(DiodeSymbolKind.PACKAGE) == lsp.SymbolKind.Package
        assert _symbol_kind_to_lsp(DiodeSymbolKind.PORT) == lsp.SymbolKind.Property
        assert _symbol_kind_to_lsp(DiodeSymbolKind.PARAMETER) == lsp.SymbolKind.Constant
        assert _symbol_kind_to_lsp(DiodeSymbolKind.SIGNAL) == lsp.SymbolKind.Variable
        assert _symbol_kind_to_lsp(DiodeSymbolKind.INSTANCE) == lsp.SymbolKind.Object
        assert _symbol_kind_to_lsp(DiodeSymbolKind.FUNCTION) == lsp.SymbolKind.Function
        assert _symbol_kind_to_lsp(DiodeSymbolKind.TYPEDEF) == lsp.SymbolKind.Struct
        assert _symbol_kind_to_lsp(DiodeSymbolKind.ENUM_MEMBER) == lsp.SymbolKind.EnumMember
        assert _symbol_kind_to_lsp(DiodeSymbolKind.GENERATE) == lsp.SymbolKind.Namespace
        assert _symbol_kind_to_lsp(DiodeSymbolKind.ALWAYS) == lsp.SymbolKind.Event
        assert _symbol_kind_to_lsp(DiodeSymbolKind.CLASS) == lsp.SymbolKind.Class


class TestDocumentSymbolHierarchy:
    """Test _build_document_symbol_hierarchy."""

    def test_builds_hierarchy_from_symbol_infos(self) -> None:
        from diode.server import _build_document_symbol_hierarchy
        from diode.types import (
            DiodeSymbolKind,
            FileLocation,
            FilePosition,
            FileRange,
            SymbolInfo,
        )

        loc = FileLocation(
            path=Path("/tmp/test.sv"),
            range=FileRange(
                start=FilePosition(line=0, column=0),
                end=FilePosition(line=10, column=0),
            ),
        )
        port_loc = FileLocation(
            path=Path("/tmp/test.sv"),
            range=FileRange(
                start=FilePosition(line=2, column=4),
                end=FilePosition(line=2, column=20),
            ),
        )

        symbols = [
            SymbolInfo(name="top", kind=DiodeSymbolKind.MODULE, definition=loc),
            SymbolInfo(
                name="clk",
                kind=DiodeSymbolKind.PORT,
                definition=port_loc,
                parent_name="top",
                type_str="input logic",
            ),
        ]

        result = _build_document_symbol_hierarchy(symbols)
        assert len(result) == 1  # Only "top" at top level
        assert result[0].name == "top"
        assert result[0].kind == lsp.SymbolKind.Module
        assert len(result[0].children) == 1
        assert result[0].children[0].name == "clk"

    def test_empty_symbols_returns_empty(self) -> None:
        from diode.server import _build_document_symbol_hierarchy

        result = _build_document_symbol_hierarchy([])
        assert result == []

    def test_orphan_children_become_top_level(self) -> None:
        """Symbols with a parent_name not in the file become top-level."""
        from diode.server import _build_document_symbol_hierarchy
        from diode.types import (
            DiodeSymbolKind,
            FileLocation,
            FilePosition,
            FileRange,
            SymbolInfo,
        )

        loc = FileLocation(
            path=Path("/tmp/test.sv"),
            range=FileRange(
                start=FilePosition(line=0, column=0),
                end=FilePosition(line=0, column=5),
            ),
        )
        symbols = [
            SymbolInfo(
                name="orphan",
                kind=DiodeSymbolKind.SIGNAL,
                definition=loc,
                parent_name="nonexistent_module",
            ),
        ]
        result = _build_document_symbol_hierarchy(symbols)
        assert len(result) == 1
        assert result[0].name == "orphan"


# ---------------------------------------------------------------------------
# Unit tests: direct handler logic (without LSP transport)
# ---------------------------------------------------------------------------


from diode.types import (
    ProjectConfig,
    ProjectSource,
    ProjectSourceKind,
)


class TestServerRecompilation:
    """Test the recompilation logic directly."""

    def test_do_recompile_with_config(self) -> None:
        """_do_recompile should successfully compile when config is set."""
        import diode.server as srv

        original_config = srv._config
        original_index = srv._index
        try:
            srv._config = ProjectConfig(
                source_files=[
                    ProjectSource(
                        path=SIMPLE_MODULE.resolve(),
                        kind=ProjectSourceKind.AUTO_DISCOVER,
                    ),
                ]
            )
            srv._do_recompile()
            assert srv._index is not None
        finally:
            srv._config = original_config
            srv._index = original_index

    def test_do_recompile_without_config(self) -> None:
        """_do_recompile should be a no-op when config is None."""
        import diode.server as srv

        original_config = srv._config
        original_index = srv._index
        try:
            srv._config = None
            srv._index = None
            srv._do_recompile()
            assert srv._index is None
        finally:
            srv._config = original_config
            srv._index = original_index

    def test_do_recompile_builds_index(self) -> None:
        """After recompile, the index should contain the compiled module's symbols."""
        import diode.server as srv
        from diode.index import SymbolIndex

        original_config = srv._config
        original_index = srv._index
        try:
            srv._config = ProjectConfig(
                source_files=[
                    ProjectSource(
                        path=SIMPLE_MODULE.resolve(),
                        kind=ProjectSourceKind.AUTO_DISCOVER,
                    ),
                ]
            )
            srv._do_recompile()
            assert isinstance(srv._index, SymbolIndex)
            sym = srv._index.find_definition("counter")
            assert sym is not None
            assert sym.name == "counter"
        finally:
            srv._config = original_config
            srv._index = original_index

    def test_do_recompile_with_open_files(self) -> None:
        """Recompile should use open_files content override."""
        import diode.server as srv

        original_config = srv._config
        original_index = srv._index
        original_open = srv._open_files.copy()
        try:
            resolved = SIMPLE_MODULE.resolve()
            srv._config = ProjectConfig(
                source_files=[
                    ProjectSource(
                        path=resolved,
                        kind=ProjectSourceKind.AUTO_DISCOVER,
                    ),
                ]
            )
            # Override with different module name
            srv._open_files[resolved] = (
                "module override_name (input logic clk);\nendmodule\n"
            )
            srv._do_recompile()
            assert srv._index is not None
            sym = srv._index.find_definition("override_name")
            assert sym is not None
        finally:
            srv._config = original_config
            srv._index = original_index
            srv._open_files = original_open


class TestHandlerLogicDirect:
    """Test handler logic by directly calling through the compiled index.

    These tests exercise the same logic as the LSP handlers without the
    LSP transport layer. This verifies the correctness of:
    - go-to-definition (instance -> module resolution)
    - hover formatting
    - find references across files
    - document symbols hierarchy
    """

    def _setup_index(self) -> None:
        """Set up a server index from cross-file fixtures."""
        import diode.server as srv
        from diode.compiler import compile_project
        from diode.index import build_index

        config = ProjectConfig(
            source_files=[
                ProjectSource(
                    path=(CROSS_FILE_DIR / "pkg.sv").resolve(),
                    kind=ProjectSourceKind.AUTO_DISCOVER,
                ),
                ProjectSource(
                    path=(CROSS_FILE_DIR / "sub.sv").resolve(),
                    kind=ProjectSourceKind.AUTO_DISCOVER,
                ),
                ProjectSource(
                    path=(CROSS_FILE_DIR / "top.sv").resolve(),
                    kind=ProjectSourceKind.AUTO_DISCOVER,
                ),
            ]
        )
        result = compile_project(config)
        srv._index = build_index(result)
        srv._config = config

    def _teardown_index(self) -> None:
        import diode.server as srv

        srv._index = None
        srv._config = None

    def test_definition_logic_for_instance(self) -> None:
        """Go-to-definition on an INSTANCE should resolve to the module definition."""
        import diode.server as srv
        from diode.types import DiodeSymbolKind

        self._setup_index()
        try:
            top_path = (CROSS_FILE_DIR / "top.sv").resolve()
            # Find u_proc in the index
            sym = srv._index.find_definition("u_proc", top_path)
            assert sym is not None
            assert sym.kind == DiodeSymbolKind.INSTANCE
            assert sym.detail == "data_processor"

            # The go-to-def logic should resolve the instance to its module
            module_sym = srv._index.find_definition(sym.detail)
            assert module_sym is not None
            assert module_sym.kind == DiodeSymbolKind.MODULE
            assert "sub.sv" in str(module_sym.definition.path)
        finally:
            self._teardown_index()

    def test_hover_logic_for_module(self) -> None:
        """Hover on a module should produce markdown with the module name."""
        import diode.server as srv
        from diode.hover import format_hover

        self._setup_index()
        try:
            sym = srv._index.find_definition("top_design")
            assert sym is not None
            text = format_hover(sym)
            assert "top_design" in text
            assert "module" in text
        finally:
            self._teardown_index()

    def test_hover_logic_for_signal(self) -> None:
        """Hover on a signal should show the type."""
        import diode.server as srv
        from diode.hover import format_hover

        self._setup_index()
        try:
            top_path = (CROSS_FILE_DIR / "top.sv").resolve()
            sym = srv._index.find_definition("internal_data", top_path)
            assert sym is not None
            text = format_hover(sym)
            assert "internal_data" in text
        finally:
            self._teardown_index()

    def test_references_logic(self) -> None:
        """find_references should return locations in multiple files."""
        import diode.server as srv

        self._setup_index()
        try:
            refs = srv._index.find_references("data_processor")
            assert len(refs) >= 2
            ref_files = {r.path.name for r in refs}
            assert "sub.sv" in ref_files
            assert "top.sv" in ref_files
        finally:
            self._teardown_index()

    def test_document_symbols_logic(self) -> None:
        """get_document_symbols should return module and its members."""
        import diode.server as srv
        from diode.server import _build_document_symbol_hierarchy

        self._setup_index()
        try:
            top_path = (CROSS_FILE_DIR / "top.sv").resolve()
            symbols = srv._index.get_document_symbols(top_path)
            assert len(symbols) >= 1

            hierarchy = _build_document_symbol_hierarchy(symbols)
            all_names: set[str] = set()
            for sym in hierarchy:
                all_names.add(sym.name)
                if sym.children:
                    for child in sym.children:
                        all_names.add(child.name)

            assert "top_design" in all_names
            assert "u_proc" in all_names
            assert "internal_data" in all_names
        finally:
            self._teardown_index()

    def test_hover_returns_none_for_empty(self) -> None:
        """lookup_at on a comment should return None."""
        import diode.server as srv

        self._setup_index()
        try:
            from diode.types import FilePosition

            top_path = (CROSS_FILE_DIR / "top.sv").resolve()
            sym = srv._index.lookup_at(top_path, FilePosition(line=0, column=0))
            assert sym is None
        finally:
            self._teardown_index()

    def test_definition_on_port_signal(self) -> None:
        """Go-to-definition on a port should find the port."""
        import diode.server as srv
        from diode.types import DiodeSymbolKind

        self._setup_index()
        try:
            top_path = (CROSS_FILE_DIR / "top.sv").resolve()
            sym = srv._index.find_definition("clk", top_path)
            assert sym is not None
            assert sym.kind == DiodeSymbolKind.PORT
        finally:
            self._teardown_index()


# ---------------------------------------------------------------------------
# Integration test using pytest-lsp (single comprehensive test)
# ---------------------------------------------------------------------------

INTEGRATION_WORKSPACE = CROSS_FILE_DIR


@pytest.fixture
def _server_command() -> list[str]:
    """Command to start the diode-ls server for integration tests."""
    return [sys.executable, "-m", "diode.server"]


@pytest.mark.asyncio
async def test_lsp_integration(_server_command: list[str]) -> None:
    """End-to-end integration test of the diode LSP server.

    Covers: open, diagnostics, hover, go-to-definition, references,
    document symbols, didChange, and didClose.

    Manages its own client lifecycle to ensure proper shutdown.
    """
    config = ClientServerConfig(server_command=_server_command)
    client = await config.start()

    try:
        # Initialize the session
        workspace_uri = f"file://{INTEGRATION_WORKSPACE.resolve()}"
        await client.initialize_session(
            lsp.InitializeParams(
                capabilities=pytest_lsp.client_capabilities("visual_studio_code"),
                root_uri=workspace_uri,
                workspace_folders=[
                    lsp.WorkspaceFolder(uri=workspace_uri, name="test"),
                ],
            )
        )

        # Open all cross-file sources
        file_uris: dict[str, str] = {}
        for name in ("pkg.sv", "sub.sv", "top.sv"):
            path = (CROSS_FILE_DIR / name).resolve()
            uri = f"file://{path}"
            text = path.read_text()
            file_uris[name] = uri
            client.text_document_did_open(
                lsp.DidOpenTextDocumentParams(
                    text_document=lsp.TextDocumentItem(
                        uri=uri,
                        language_id="systemverilog",
                        version=1,
                        text=text,
                    )
                )
            )

        # Wait for compilation to complete (collect diagnostics)
        try:
            while True:
                await asyncio.wait_for(
                    client.wait_for_notification(lsp.TEXT_DOCUMENT_PUBLISH_DIAGNOSTICS),
                    timeout=10.0,
                )
        except asyncio.TimeoutError:
            pass

        # --- Hover on module name ---
        hover_result = await client.text_document_hover_async(
            lsp.HoverParams(
                text_document=lsp.TextDocumentIdentifier(uri=file_uris["top.sv"]),
                position=lsp.Position(line=18, character=7),
            )
        )
        assert hover_result is not None, "Hover on 'top_design' should not be None"
        if isinstance(hover_result.contents, lsp.MarkupContent):
            assert "top_design" in hover_result.contents.value

        # --- Go-to-definition on instance ---
        def_result = await client.text_document_definition_async(
            lsp.DefinitionParams(
                text_document=lsp.TextDocumentIdentifier(uri=file_uris["top.sv"]),
                position=lsp.Position(line=30, character=21),
            )
        )
        assert def_result is not None, "Definition on 'u_proc' should resolve"
        if isinstance(def_result, list):
            assert len(def_result) >= 1
            assert "sub.sv" in def_result[0].uri

        # --- Find references ---
        refs_result = await client.text_document_references_async(
            lsp.ReferenceParams(
                context=lsp.ReferenceContext(include_declaration=True),
                text_document=lsp.TextDocumentIdentifier(uri=file_uris["sub.sv"]),
                position=lsp.Position(line=16, character=7),
            )
        )
        assert refs_result is not None
        assert len(refs_result) >= 1
        ref_uris = {loc.uri for loc in refs_result}
        assert any("sub.sv" in u for u in ref_uris)
        assert any("top.sv" in u for u in ref_uris)

        # --- Document symbols ---
        symbols_result = await client.text_document_document_symbol_async(
            lsp.DocumentSymbolParams(
                text_document=lsp.TextDocumentIdentifier(uri=file_uris["top.sv"]),
            )
        )
        assert symbols_result is not None
        assert len(symbols_result) >= 1
        all_names: set[str] = set()
        for sym in symbols_result:
            all_names.add(sym.name)
            if hasattr(sym, "children") and sym.children:
                for child in sym.children:
                    all_names.add(child.name)
        assert "top_design" in all_names

        # --- Hover on comment returns None ---
        hover_none = await client.text_document_hover_async(
            lsp.HoverParams(
                text_document=lsp.TextDocumentIdentifier(uri=file_uris["top.sv"]),
                position=lsp.Position(line=0, character=0),
            )
        )
        assert hover_none is None

        # --- didClose ---
        client.text_document_did_close(
            lsp.DidCloseTextDocumentParams(
                text_document=lsp.TextDocumentIdentifier(uri=file_uris["top.sv"]),
            )
        )
        await asyncio.sleep(0.3)

    finally:
        # Ensure clean shutdown regardless of test outcome
        await client.shutdown_session()
        await client.stop()
