"""LSP server for diode.

Implements the pygls LanguageServer with handlers for didOpen, didChange,
didSave, didClose, definition, hover, references, and documentSymbol.
Orchestrates compilation, index rebuild, and diagnostics publishing.
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading
from collections import defaultdict
from pathlib import Path

import lsprotocol.types as lsp
from pygls import uris as pygls_uris
from pygls.lsp.server import LanguageServer

from diode import compiler, hover, index, project
from diode.index import SymbolIndex
from diode.types import (
    CompilationResult,
    DiodeDiagnostic,
    DiodeSeverity,
    DiodeSymbolKind,
    FileLocation,
    FilePosition,
    FileRange,
    ProjectConfig,
    SymbolInfo,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Server instance
# ---------------------------------------------------------------------------

SERVER = LanguageServer(
    "diode-ls",
    "v0.1.0",
    text_document_sync_kind=lsp.TextDocumentSyncKind.Full,
)

# ---------------------------------------------------------------------------
# Mutable state (accessed from thread pool, some under lock)
# ---------------------------------------------------------------------------

_config: ProjectConfig | None = None
_index: SymbolIndex | None = None
_open_files: dict[Path, str] = {}
_compilation_lock = threading.Lock()
_recompile_timer: threading.Timer | None = None
_previous_diag_files: set[str] = set()

# ---------------------------------------------------------------------------
# Symbol kind mapping
# ---------------------------------------------------------------------------

_SYMBOL_KIND_MAP: dict[DiodeSymbolKind, lsp.SymbolKind] = {
    DiodeSymbolKind.MODULE: lsp.SymbolKind.Module,
    DiodeSymbolKind.INTERFACE: lsp.SymbolKind.Interface,
    DiodeSymbolKind.PACKAGE: lsp.SymbolKind.Package,
    DiodeSymbolKind.PORT: lsp.SymbolKind.Property,
    DiodeSymbolKind.PARAMETER: lsp.SymbolKind.Constant,
    DiodeSymbolKind.LOCALPARAM: lsp.SymbolKind.Constant,
    DiodeSymbolKind.SIGNAL: lsp.SymbolKind.Variable,
    DiodeSymbolKind.INSTANCE: lsp.SymbolKind.Object,
    DiodeSymbolKind.FUNCTION: lsp.SymbolKind.Function,
    DiodeSymbolKind.TASK: lsp.SymbolKind.Function,
    DiodeSymbolKind.TYPEDEF: lsp.SymbolKind.Struct,
    DiodeSymbolKind.ENUM_MEMBER: lsp.SymbolKind.EnumMember,
    DiodeSymbolKind.GENERATE: lsp.SymbolKind.Namespace,
    DiodeSymbolKind.ALWAYS: lsp.SymbolKind.Event,
    DiodeSymbolKind.CLASS: lsp.SymbolKind.Class,
}

# Severity mapping from diode to LSP
_SEVERITY_MAP: dict[DiodeSeverity, lsp.DiagnosticSeverity] = {
    DiodeSeverity.ERROR: lsp.DiagnosticSeverity.Error,
    DiodeSeverity.WARNING: lsp.DiagnosticSeverity.Warning,
    DiodeSeverity.INFO: lsp.DiagnosticSeverity.Information,
    DiodeSeverity.HINT: lsp.DiagnosticSeverity.Hint,
}


# ---------------------------------------------------------------------------
# Position / range / location conversion helpers
# ---------------------------------------------------------------------------


def _to_lsp_position(pos: FilePosition) -> lsp.Position:
    """Convert internal 0-based position to LSP Position."""
    return lsp.Position(line=pos.line, character=pos.column)


def _to_lsp_range(file_range: FileRange) -> lsp.Range:
    """Convert internal FileRange to LSP Range."""
    return lsp.Range(
        start=_to_lsp_position(file_range.start),
        end=_to_lsp_position(file_range.end),
    )


def _to_lsp_location(loc: FileLocation) -> lsp.Location:
    """Convert internal FileLocation to LSP Location (with URI)."""
    return lsp.Location(
        uri=_path_to_uri(loc.path),
        range=_to_lsp_range(loc.range),
    )


def _from_lsp_position(pos: lsp.Position) -> FilePosition:
    """Convert LSP Position to internal FilePosition."""
    return FilePosition(line=pos.line, column=pos.character)


def _uri_to_path(uri: str) -> Path:
    """Convert LSP document URI to filesystem Path."""
    fs_path = pygls_uris.to_fs_path(uri)
    return Path(fs_path).resolve()


def _path_to_uri(path: Path) -> str:
    """Convert filesystem Path to LSP document URI."""
    return pygls_uris.from_fs_path(str(path))


def _symbol_kind_to_lsp(kind: DiodeSymbolKind) -> lsp.SymbolKind:
    """Map DiodeSymbolKind to LSP SymbolKind enum."""
    return _SYMBOL_KIND_MAP.get(kind, lsp.SymbolKind.Variable)


# ---------------------------------------------------------------------------
# Internal compilation / diagnostics helpers
# ---------------------------------------------------------------------------


def _schedule_recompile() -> None:
    """Schedule a debounced recompilation.

    Cancels any pending recompile timer, sets a new one for 300ms.
    When the timer fires, calls _do_recompile().
    """
    global _recompile_timer

    if _recompile_timer is not None:
        _recompile_timer.cancel()

    _recompile_timer = threading.Timer(0.3, _do_recompile)
    _recompile_timer.daemon = True
    _recompile_timer.start()


def _do_recompile() -> None:
    """Perform compilation and index rebuild.

    1. Acquire _compilation_lock (blocks if another compilation is running)
    2. Call compiler.compile_project(_config, _open_files)
    3. Call index.build_index(result)
    4. Atomic swap: _index = new_index
    5. Publish diagnostics for all open files
    6. Release lock
    """
    global _index, _recompile_timer

    if _config is None:
        return

    with _compilation_lock:
        try:
            result = compiler.compile_project(_config, _open_files)
            new_index = index.build_index(result)
            _index = new_index
            _publish_diagnostics(result)
        except Exception:
            logger.exception("Recompilation failed")

    _recompile_timer = None


def _publish_diagnostics(result: CompilationResult) -> None:
    """Publish diagnostics to the client for all affected files.

    Groups diagnostics by file, converts to LSP format, calls
    SERVER.text_document_publish_diagnostics() per file.
    Publishes empty diagnostics for files that had errors before but don't now.
    """
    global _previous_diag_files

    # Group diagnostics by file URI
    diags_by_uri: dict[str, list[lsp.Diagnostic]] = defaultdict(list)

    for diag in result.diagnostics:
        uri = _path_to_uri(diag.location.path)
        lsp_diag = lsp.Diagnostic(
            range=_to_lsp_range(diag.location.range),
            message=diag.message,
            severity=_SEVERITY_MAP.get(diag.severity, lsp.DiagnosticSeverity.Information),
            source="diode",
            code=diag.code,
        )
        diags_by_uri[uri].append(lsp_diag)

    # Publish diagnostics for files with issues
    current_diag_files: set[str] = set()
    for uri, diagnostics in diags_by_uri.items():
        SERVER.text_document_publish_diagnostics(
            lsp.PublishDiagnosticsParams(uri=uri, diagnostics=diagnostics)
        )
        current_diag_files.add(uri)

    # Publish empty diagnostics for files that previously had errors but now don't
    for uri in _previous_diag_files - current_diag_files:
        SERVER.text_document_publish_diagnostics(
            lsp.PublishDiagnosticsParams(uri=uri, diagnostics=[])
        )

    _previous_diag_files = current_diag_files


# ---------------------------------------------------------------------------
# LSP handlers: lifecycle
# ---------------------------------------------------------------------------


@SERVER.feature(lsp.INITIALIZE)
def on_initialize(params: lsp.InitializeParams) -> None:
    """Load project config from workspace root."""
    global _config

    root_uri = params.root_uri
    root_path_str = params.root_path

    workspace_root: Path | None = None

    if root_uri is not None:
        workspace_root = _uri_to_path(root_uri)
    elif root_path_str is not None:
        workspace_root = Path(root_path_str).resolve()
    elif params.workspace_folders:
        workspace_root = _uri_to_path(params.workspace_folders[0].uri)

    if workspace_root is not None:
        logger.info(f"Loading project from workspace root: {workspace_root}")
        _config = project.load_project(workspace_root)
        logger.info(
            f"Project loaded: {len(_config.source_files)} source files, "
            f"{len(_config.include_dirs)} include dirs"
        )
    else:
        logger.warning("No workspace root provided; using empty config")
        _config = ProjectConfig()


# ---------------------------------------------------------------------------
# LSP handlers: document synchronization
# ---------------------------------------------------------------------------


@SERVER.feature(lsp.TEXT_DOCUMENT_DID_OPEN)
def on_did_open(params: lsp.DidOpenTextDocumentParams) -> None:
    """Track open file content, trigger compilation."""
    path = _uri_to_path(params.text_document.uri)
    _open_files[path] = params.text_document.text
    logger.debug(f"Opened: {path}")
    _schedule_recompile()


@SERVER.feature(lsp.TEXT_DOCUMENT_DID_CHANGE)
def on_did_change(params: lsp.DidChangeTextDocumentParams) -> None:
    """Update open file content, schedule debounced recompilation."""
    path = _uri_to_path(params.text_document.uri)
    # Full sync mode: the last content change contains the full text
    if params.content_changes:
        _open_files[path] = params.content_changes[-1].text
    logger.debug(f"Changed: {path}")
    _schedule_recompile()


@SERVER.feature(lsp.TEXT_DOCUMENT_DID_SAVE)
def on_did_save(params: lsp.DidSaveTextDocumentParams) -> None:
    """Trigger immediate recompilation on save."""
    logger.debug(f"Saved: {params.text_document.uri}")
    # Cancel any pending debounced recompile and do it immediately
    global _recompile_timer
    if _recompile_timer is not None:
        _recompile_timer.cancel()
        _recompile_timer = None
    # Run recompile in thread pool
    SERVER.thread_pool.submit(_do_recompile)


@SERVER.feature(lsp.TEXT_DOCUMENT_DID_CLOSE)
def on_did_close(params: lsp.DidCloseTextDocumentParams) -> None:
    """Remove file from open_files tracking."""
    path = _uri_to_path(params.text_document.uri)
    _open_files.pop(path, None)
    logger.debug(f"Closed: {path}")


# ---------------------------------------------------------------------------
# LSP handlers: language features
# ---------------------------------------------------------------------------


@SERVER.thread()
@SERVER.feature(lsp.TEXT_DOCUMENT_DEFINITION)
def on_definition(params: lsp.DefinitionParams) -> list[lsp.Location] | None:
    """Go-to-definition handler.

    1. Convert LSP position to FilePosition
    2. Call _index.lookup_at(path, position)
    3. If found, return [Location(definition.path, definition.range)]
    4. If not found, return None
    """
    current_index = _index
    if current_index is None:
        return None

    path = _uri_to_path(params.text_document.uri)
    pos = _from_lsp_position(params.position)

    symbol = current_index.lookup_at(path, pos)
    if symbol is None:
        return None

    # If the symbol is an INSTANCE, jump to the module it instantiates
    if symbol.kind == DiodeSymbolKind.INSTANCE and symbol.detail:
        module_sym = current_index.find_definition(symbol.detail)
        if module_sym is not None:
            return [_to_lsp_location(module_sym.definition)]

    return [_to_lsp_location(symbol.definition)]


@SERVER.thread()
@SERVER.feature(lsp.TEXT_DOCUMENT_HOVER)
def on_hover(params: lsp.HoverParams) -> lsp.Hover | None:
    """Hover handler.

    1. Convert LSP position to FilePosition
    2. Call _index.lookup_at(path, position)
    3. If found, call hover.format_hover(symbol)
    4. Return Hover(contents=MarkupContent(kind=markdown, value=text))
    """
    current_index = _index
    if current_index is None:
        return None

    path = _uri_to_path(params.text_document.uri)
    pos = _from_lsp_position(params.position)

    symbol = current_index.lookup_at(path, pos)
    if symbol is None:
        return None

    text = hover.format_hover(symbol)
    return lsp.Hover(
        contents=lsp.MarkupContent(
            kind=lsp.MarkupKind.Markdown,
            value=text,
        )
    )


@SERVER.thread()
@SERVER.feature(lsp.TEXT_DOCUMENT_REFERENCES)
def on_references(params: lsp.ReferenceParams) -> list[lsp.Location] | None:
    """Find-references handler.

    1. Convert LSP position to FilePosition
    2. Call _index.lookup_at(path, position) to get the symbol name
    3. Call _index.find_references(symbol.name)
    4. Convert FileLocations to LSP Locations
    """
    current_index = _index
    if current_index is None:
        return None

    path = _uri_to_path(params.text_document.uri)
    pos = _from_lsp_position(params.position)

    symbol = current_index.lookup_at(path, pos)
    if symbol is None:
        return None

    refs = current_index.find_references(symbol.name)

    # If the client wants the declaration included, it's already in refs
    # (find_references includes declarations). If not requested, filter it out.
    if not params.context.include_declaration:
        refs = [
            r for r in refs
            if not (
                r.path == symbol.definition.path
                and r.range.start.line == symbol.definition.range.start.line
                and r.range.start.column == symbol.definition.range.start.column
            )
        ]

    if not refs:
        return None

    return [_to_lsp_location(ref) for ref in refs]


@SERVER.thread()
@SERVER.feature(lsp.TEXT_DOCUMENT_DOCUMENT_SYMBOL)
def on_document_symbol(params: lsp.DocumentSymbolParams) -> list[lsp.DocumentSymbol] | None:
    """Document symbol handler (outline view).

    1. Get file path from params
    2. Call _index.get_document_symbols(path)
    3. Convert SymbolInfo list to DocumentSymbol list
    4. Build hierarchy (children nested under parent symbols)
    """
    current_index = _index
    if current_index is None:
        return None

    path = _uri_to_path(params.text_document.uri)
    symbols = current_index.get_document_symbols(path)

    if not symbols:
        return None

    return _build_document_symbol_hierarchy(symbols)


def _build_document_symbol_hierarchy(symbols: list[SymbolInfo]) -> list[lsp.DocumentSymbol]:
    """Build a hierarchical DocumentSymbol list from flat SymbolInfo list.

    Top-level symbols (no parent_name or parent not in this file) become roots.
    Children are nested under their parent.
    """
    # Map parent_name -> children
    children_map: dict[str, list[lsp.DocumentSymbol]] = defaultdict(list)
    top_level: list[lsp.DocumentSymbol] = []

    # Track which names exist as top-level symbols in this file
    top_names = {s.name for s in symbols if s.kind in (
        DiodeSymbolKind.MODULE,
        DiodeSymbolKind.INTERFACE,
        DiodeSymbolKind.PACKAGE,
    )}

    # First pass: convert all to DocumentSymbol
    for sym in symbols:
        lsp_range = _to_lsp_range(sym.definition.range)
        doc_sym = lsp.DocumentSymbol(
            name=sym.name,
            kind=_symbol_kind_to_lsp(sym.kind),
            range=lsp_range,
            selection_range=lsp_range,
            detail=sym.type_str or sym.detail,
            children=[],
        )

        if sym.parent_name and sym.parent_name in top_names:
            children_map[sym.parent_name].append(doc_sym)
        else:
            top_level.append(doc_sym)

    # Second pass: attach children to their parents
    for doc_sym in top_level:
        if doc_sym.name in children_map:
            doc_sym.children = children_map[doc_sym.name]

    return top_level


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Entry point for the diode-ls language server.

    Parses command-line arguments:
        --tcp          Run in TCP mode instead of STDIO
        --port PORT    TCP port (default: 2087)
        --log-level    Logging level (default: WARNING)

    Starts the pygls LanguageServer.
    """
    parser = argparse.ArgumentParser(
        description="diode-ls: SystemVerilog language server"
    )
    parser.add_argument(
        "--tcp",
        action="store_true",
        help="Run in TCP mode instead of STDIO",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=2087,
        help="TCP port (default: 2087)",
    )
    parser.add_argument(
        "--log-level",
        default="WARNING",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Logging level (default: WARNING)",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
        stream=sys.stderr,
    )

    if args.tcp:
        logger.info(f"Starting diode-ls in TCP mode on port {args.port}")
        SERVER.start_tcp("127.0.0.1", args.port)
    else:
        logger.info("Starting diode-ls in STDIO mode")
        SERVER.start_io()


if __name__ == "__main__":
    main()
