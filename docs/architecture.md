# Architecture — Interface Specification

Precise module interfaces for diode phase 1. Implementation agents should treat
this document as the source of truth for function signatures, data flow, and
invariants.

## Module dependency graph

```
                    types.py
                   /   |   \    \
                  /    |    \    \
           project.py  |  compiler.py  hover.py
                       |       |
                    index.py   |
                       |       |
                       +---+---+
                           |
                        server.py
```

- `types.py` — imported by everything, imports nothing from diode
- `project.py` — imports only `types.py`
- `compiler.py` — imports only `types.py` (and pyslang)
- `index.py` — imports only `types.py` (and pyslang for tree walking)
- `hover.py` — imports only `types.py`
- `server.py` — imports all of the above, plus pygls/lsprotocol

**Rule**: No module except `server.py` imports from pygls or lsprotocol.

## types.py — Complete contract

See `src/diode/types.py` for the definitive code. Summary of all exports:

### Enums
- `DiodeSeverity` — ERROR, WARNING, INFO, HINT (values 1-4, matching LSP)
- `DiodeSymbolKind` — MODULE, INTERFACE, PACKAGE, PORT, PARAMETER, LOCALPARAM, SIGNAL, INSTANCE, FUNCTION, TASK, TYPEDEF, ENUM_MEMBER, GENERATE, ALWAYS, CLASS
- `ProjectSourceKind` — FILE_LIST, CONFIG, AUTO_DISCOVER

### Position types (all frozen, slotted)
- `FilePosition(line: int, column: int)` — 0-based
- `FileRange(start: FilePosition, end: FilePosition)`
- `FileLocation(path: Path, range: FileRange)`

### Project types
- `ProjectSource(path: Path, kind: ProjectSourceKind)` — frozen, slotted
- `ProjectConfig(source_files, include_dirs, defines, top_module, config_path)` — mutable dataclass

### Compilation types
- `DiodeDiagnostic(location, severity, message, code)` — frozen, slotted
- `CompilationResult(compilation, diagnostics, source_files, success)` — mutable dataclass

### Symbol types
- `SymbolInfo(name, kind, definition, parent_name, type_str, detail, references)` — frozen, slotted

## project.py — Project configuration

### Public interface

```python
def load_project(workspace_root: Path) -> ProjectConfig:
    """Load project configuration from the workspace.

    Discovery order:
    1. Look for diode.toml in workspace_root
    2. Look for *.f files in workspace_root
    3. Fall back to auto-discovery (glob for *.sv, *.v, *.svh)

    Args:
        workspace_root: The LSP workspace root directory.

    Returns:
        ProjectConfig with resolved absolute paths.

    Raises:
        Nothing — always returns a valid config. Logs warnings for issues.
    """
```

### Internal functions

```python
def _parse_diode_toml(toml_path: Path) -> ProjectConfig:
    """Parse a diode.toml configuration file.

    Expected TOML structure:
        [project]
        top = "top_module"                    # optional
        files = ["rtl/**/*.sv", "rtl/pkg.sv"] # glob patterns
        file_list = "project.f"               # path to .f file
        include_dirs = ["rtl/include"]         # include search paths
        defines = { SYNTHESIS = "1" }          # preprocessor defines

    All relative paths resolved against toml_path.parent.
    Glob patterns expanded at parse time.
    Both `files` and `file_list` can coexist — sources are merged.
    """

def _parse_file_list(f_path: Path, seen: set[Path] | None = None) -> tuple[list[Path], list[Path], dict[str, str]]:
    """Parse a .f file list, returning (source_files, include_dirs, defines).

    Supported directives:
        path/to/file.sv          — source file (relative to .f file location)
        +incdir+path/to/include  — include directory
        +define+NAME=VALUE       — preprocessor define (VALUE optional)
        +define+NAME             — define with empty value
        -f path/to/nested.f      — include another .f file (recursive)
        // comment               — line comment (also # comments)
        (blank lines)            — ignored

    Args:
        f_path: Path to the .f file.
        seen: Set of already-processed .f files (for cycle detection).

    Returns:
        Tuple of (source_files, include_dirs, defines).
    """

def _auto_discover(workspace_root: Path) -> list[Path]:
    """Glob for *.sv, *.v, *.svh files in workspace_root (recursive).

    Returns sorted list of absolute paths.
    """
```

## compiler.py — Compilation engine

### Public interface

```python
def compile_project(
    config: ProjectConfig,
    open_files: dict[Path, str] | None = None,
) -> CompilationResult:
    """Compile the project using pyslang.

    Creates a pyslang SyntaxTree for each source file in config.source_files.
    For files present in open_files, uses the string content instead of disk.
    Builds a pyslang Compilation from all trees.

    Args:
        config: Project configuration with source files, includes, defines.
        open_files: Map of file path → editor buffer content. Files in this
            dict use the provided string; all others read from disk.

    Returns:
        CompilationResult containing the pyslang Compilation object and
        extracted diagnostics.
    """
```

### Internal functions

```python
def _create_syntax_tree(
    path: Path,
    content: str | None,
    include_dirs: list[Path],
    defines: dict[str, str],
) -> Any:  # pyslang.SyntaxTree
    """Create a pyslang SyntaxTree for a single file.

    If content is provided, parse from string. Otherwise, read from disk.
    """

def _extract_diagnostics(compilation: Any) -> list[DiodeDiagnostic]:
    """Extract all diagnostics from a pyslang Compilation.

    Maps pyslang diagnostic severity to DiodeSeverity.
    Maps pyslang source locations to FileLocation.
    """
```

### pyslang API usage pattern

```python
import pyslang

# Create syntax trees
tree = pyslang.SyntaxTree.fromText(content, name=str(path))
# or
tree = pyslang.SyntaxTree.fromFile(str(path))

# Build compilation
compilation = pyslang.Compilation()
for tree in trees:
    compilation.addSyntaxTree(tree)

# Get diagnostics
for diag in compilation.getAllDiagnostics():
    # diag.severity, diag.location, diag.message, etc.
    pass

# Get root — the compilation root for tree walking
root = compilation.getRoot()
```

**Note**: The exact pyslang API may vary. The implementation agent should consult
`python -c "import pyslang; help(pyslang)"` and test interactively. The pattern
above is the expected shape — adjust method names if pyslang uses different
conventions (e.g., `from_text` vs `fromText`).

## index.py — Symbol index

### Public interface

```python
class SymbolIndex:
    """Immutable symbol index built from a compilation result.

    Thread-safe for concurrent reads. Never mutated after construction —
    server.py builds a new index on each recompilation and atomically swaps.
    """

    def lookup_at(self, path: Path, position: FilePosition) -> SymbolInfo | None:
        """Find the symbol at a specific file position.

        Used for hover and go-to-definition. Returns the most specific
        symbol whose range contains the given position.

        Algorithm:
        1. Find all symbols in the given file
        2. Filter to those whose definition range contains position
        3. Return the most specific (innermost/narrowest range)
        4. If no exact match, try word-under-cursor fallback:
           extract the identifier at position, search by name

        Args:
            path: Absolute file path.
            position: 0-based line/column position.

        Returns:
            SymbolInfo if found, None otherwise.
        """

    def find_definition(self, name: str, context_path: Path | None = None) -> SymbolInfo | None:
        """Find the definition of a symbol by name.

        Used for go-to-definition when we have a name but not a precise
        source position in the index.

        Lookup order:
        1. Exact match in the context file's scope (local signals, params)
        2. Exact match in enclosing module/package scope
        3. Global match (modules, packages, interfaces)

        Args:
            name: Symbol name to look up.
            context_path: File where the reference occurs (for scoping).

        Returns:
            SymbolInfo if found, None otherwise.
        """

    def find_references(self, name: str) -> list[FileLocation]:
        """Find all references to a symbol by name.

        Returns all locations where the symbol name appears as a
        declaration or usage.

        Args:
            name: Symbol name to search for.

        Returns:
            List of FileLocation for all reference sites.
        """

    def get_document_symbols(self, path: Path) -> list[SymbolInfo]:
        """Get all symbols defined in a file, ordered by position.

        Used for textDocument/documentSymbol (outline view).
        Returns top-level and nested symbols (ports inside modules, etc.).

        Args:
            path: Absolute file path.

        Returns:
            List of SymbolInfo, ordered by definition position.
        """


def build_index(result: CompilationResult) -> SymbolIndex:
    """Build a symbol index from a compilation result.

    Walks the pyslang compilation tree (result.compilation.getRoot()),
    extracts all declarations, and records their locations and metadata.

    The walk visits:
    - Module/interface/package declarations → SymbolInfo with kind, ports in detail
    - Port declarations → SymbolInfo with type_str, direction
    - Parameter/localparam declarations → SymbolInfo with type_str, value in detail
    - Variable/net declarations → SymbolInfo with type_str
    - Module instantiations → SymbolInfo with INSTANCE kind, module name in detail
    - Function/task declarations → SymbolInfo with signature in detail
    - Typedef declarations → SymbolInfo with underlying type in type_str
    - Enum members → SymbolInfo with value in detail
    - Generate blocks → SymbolInfo with GENERATE kind
    - Always blocks → SymbolInfo with ALWAYS kind, block type in detail

    For references: the walk also records every name reference, linking
    it back to the declaration it resolves to. Stored as FileLocation tuples
    in SymbolInfo.references.

    Args:
        result: Successful CompilationResult with a valid compilation object.

    Returns:
        SymbolIndex populated with all discovered symbols.
    """
```

### Internal data structures

```python
# Inside SymbolIndex, the implementation should maintain:
_symbols_by_file: dict[Path, list[SymbolInfo]]      # for document symbols, lookup_at
_symbols_by_name: dict[str, list[SymbolInfo]]        # for find_definition
_references_by_name: dict[str, list[FileLocation]]   # for find_references
```

### Position-to-symbol resolution algorithm

Used by `lookup_at()`:

1. Get all symbols in the target file from `_symbols_by_file`
2. Filter to symbols whose `definition.range` contains the query position
3. If multiple matches, pick the narrowest range (most specific scope)
4. If no range match, extract the word under cursor:
   a. Read the source line at `position.line`
   b. Scan left/right from `position.column` for `[a-zA-Z0-9_]` characters
   c. Use the extracted identifier to call `find_definition(name, path)`
5. Return the found SymbolInfo or None

Step 4 is the **word-under-cursor fallback** — it handles cases where pyslang's
source locations don't perfectly align with the cursor position (e.g., clicking
on a signal name in an expression rather than its declaration).

## hover.py — Hover content formatting

### Public interface

```python
def format_hover(symbol: SymbolInfo) -> str:
    """Format a SymbolInfo into markdown for LSP hover response.

    Returns GitHub-flavored markdown string. Uses SystemVerilog code fences
    for code blocks.

    Formatting by symbol kind:
    - MODULE/INTERFACE: "module <name>" header, then port list and parameters
      in a ```systemverilog code fence
    - PORT: direction, type, name on one line (e.g., "input logic [7:0] data")
    - PARAMETER/LOCALPARAM: "parameter <type> <name> = <value>"
    - SIGNAL: type and name (e.g., "logic [WIDTH-1:0] count_next")
    - INSTANCE: "Instance of <module_name>" with port connections if available
    - FUNCTION/TASK: signature in a code fence
    - TYPEDEF: "typedef <underlying_type>" in a code fence
    - ENUM_MEMBER: "<enum_type>::<member> = <value>"
    - PACKAGE: "package <name>" header
    - Others: name and kind as fallback

    Args:
        symbol: The SymbolInfo to format.

    Returns:
        Markdown string for the hover popup.
    """
```

## server.py — LSP server

### Public interface

```python
def main() -> None:
    """Entry point for the diode-ls language server.

    Parses command-line arguments:
        --tcp          Run in TCP mode instead of STDIO
        --port PORT    TCP port (default: 2087)
        --log-level    Logging level (default: WARNING)

    Starts the pygls LanguageServer.
    """
```

### Server class

```python
import threading
from pygls.server import LanguageServer

SERVER = LanguageServer("diode-ls", "v0.1.0")

# Mutable state (all accessed from thread pool, some under lock)
_config: ProjectConfig | None = None
_index: SymbolIndex | None = None
_open_files: dict[Path, str] = {}          # path → editor buffer content
_compilation_lock = threading.Lock()
_recompile_timer: threading.Timer | None = None
```

### LSP handler registrations

```python
@SERVER.feature("initialize")
async def on_initialize(params):
    """Load project config from workspace root."""

@SERVER.feature("textDocument/didOpen")
def on_did_open(params):
    """Track open file content, trigger compilation."""

@SERVER.feature("textDocument/didChange")
def on_did_change(params):
    """Update open file content, schedule debounced recompilation."""

@SERVER.feature("textDocument/didSave")
def on_did_save(params):
    """Trigger immediate recompilation."""

@SERVER.feature("textDocument/didClose")
def on_did_close(params):
    """Remove file from open_files tracking."""

@SERVER.thread()
@SERVER.feature("textDocument/definition")
def on_definition(params) -> list[Location] | None:
    """Go-to-definition handler.

    1. Convert LSP position to FilePosition
    2. Call _index.lookup_at(path, position)
    3. If found, return [Location(definition.path, definition.range)]
    4. If not found, return None
    """

@SERVER.thread()
@SERVER.feature("textDocument/hover")
def on_hover(params) -> Hover | None:
    """Hover handler.

    1. Convert LSP position to FilePosition
    2. Call _index.lookup_at(path, position)
    3. If found, call hover.format_hover(symbol)
    4. Return Hover(contents=MarkupContent(kind=markdown, value=text))
    """

@SERVER.thread()
@SERVER.feature("textDocument/references")
def on_references(params) -> list[Location] | None:
    """Find-references handler.

    1. Convert LSP position to FilePosition
    2. Call _index.lookup_at(path, position) to get the symbol name
    3. Call _index.find_references(symbol.name)
    4. Convert FileLocations to LSP Locations
    """

@SERVER.thread()
@SERVER.feature("textDocument/documentSymbol")
def on_document_symbol(params) -> list[DocumentSymbol]:
    """Document symbol handler (outline view).

    1. Get file path from params
    2. Call _index.get_document_symbols(path)
    3. Convert SymbolInfo list to DocumentSymbol list
    4. Build hierarchy (children nested under parent symbols)
    """
```

### Internal functions

```python
def _schedule_recompile() -> None:
    """Schedule a debounced recompilation.

    Cancels any pending recompile timer, sets a new one for 300ms.
    When the timer fires, calls _do_recompile().
    """

@SERVER.thread()
def _do_recompile() -> None:
    """Perform compilation and index rebuild.

    1. Acquire _compilation_lock (blocks if another compilation is running)
    2. Call compiler.compile_project(_config, _open_files)
    3. Call index.build_index(result)
    4. Atomic swap: _index = new_index
    5. Publish diagnostics for all open files
    6. Release lock
    """

def _publish_diagnostics(result: CompilationResult) -> None:
    """Publish diagnostics to the client for all affected files.

    Groups diagnostics by file, converts to LSP format, calls
    SERVER.publish_diagnostics(uri, diagnostics) per file.
    Publishes empty diagnostics for files that had errors before but don't now.
    """

def _to_lsp_position(pos: FilePosition) -> Position:
    """Convert internal 0-based position to LSP Position."""

def _to_lsp_range(range: FileRange) -> Range:
    """Convert internal FileRange to LSP Range."""

def _to_lsp_location(loc: FileLocation) -> Location:
    """Convert internal FileLocation to LSP Location (with URI)."""

def _from_lsp_position(pos: Position) -> FilePosition:
    """Convert LSP Position to internal FilePosition."""

def _uri_to_path(uri: str) -> Path:
    """Convert LSP document URI to filesystem Path."""

def _path_to_uri(path: Path) -> str:
    """Convert filesystem Path to LSP document URI."""

def _symbol_kind_to_lsp(kind: DiodeSymbolKind) -> SymbolKind:
    """Map DiodeSymbolKind to LSP SymbolKind enum.

    Mapping:
        MODULE → Module, INTERFACE → Interface, PACKAGE → Package,
        PORT → Property, PARAMETER → Constant, LOCALPARAM → Constant,
        SIGNAL → Variable, INSTANCE → Object, FUNCTION → Function,
        TASK → Function, TYPEDEF → Struct, ENUM_MEMBER → EnumMember,
        GENERATE → Namespace, ALWAYS → Event, CLASS → Class
    """
```

## Data flow traces

### Open file → diagnostics

```
1. Editor sends textDocument/didOpen { uri, text }
2. server.on_did_open():
   a. Store text in _open_files[path]
   b. Call _schedule_recompile()
3. After 300ms debounce, _do_recompile():
   a. Lock _compilation_lock
   b. result = compiler.compile_project(_config, _open_files)
   c. new_index = index.build_index(result)
   d. _index = new_index  (atomic swap)
   e. _publish_diagnostics(result)
   f. Unlock
4. _publish_diagnostics():
   a. Group result.diagnostics by file
   b. For each file: convert DiodeDiagnostic → LSP Diagnostic
   c. SERVER.publish_diagnostics(uri, lsp_diagnostics)
5. Editor displays diagnostics as squiggles/markers
```

### Ctrl-click → go-to-definition

```
1. Editor sends textDocument/definition { uri, position }
2. server.on_definition() [runs in thread pool]:
   a. path = _uri_to_path(params.text_document.uri)
   b. pos = _from_lsp_position(params.position)
   c. symbol = _index.lookup_at(path, pos)
   d. If symbol is None: return None
   e. If symbol is an INSTANCE: look up the module it instantiates
      via _index.find_definition(symbol.detail)  [detail = module name]
   f. Return [_to_lsp_location(symbol.definition)]
3. Editor navigates to the target file/position
```

### Hover

```
1. Editor sends textDocument/hover { uri, position }
2. server.on_hover() [runs in thread pool]:
   a. path, pos = convert params
   b. symbol = _index.lookup_at(path, pos)
   c. If symbol is None: return None
   d. text = hover.format_hover(symbol)
   e. Return Hover(contents=MarkupContent(kind="markdown", value=text))
3. Editor shows hover popup
```

### Find references

```
1. Editor sends textDocument/references { uri, position, context }
2. server.on_references() [runs in thread pool]:
   a. path, pos = convert params
   b. symbol = _index.lookup_at(path, pos)
   c. If symbol is None: return None
   d. refs = _index.find_references(symbol.name)
   e. If context.includeDeclaration: prepend symbol.definition
   f. Return [_to_lsp_location(ref) for ref in refs]
3. Editor shows reference list
```

## Threading model and safety invariants

1. **Main thread**: asyncio event loop, LSP message dispatch (pygls)
2. **Thread pool**: `@SERVER.thread()` handlers (definition, hover, references, documentSymbol, recompilation)
3. **Compilation lock**: `_compilation_lock` serializes recompilation — only one `_do_recompile()` runs at a time
4. **Index reads are lock-free**: `_index` is replaced atomically (Python reference assignment is atomic under GIL). Feature handlers read `_index` without locking. They may read a slightly stale index during recompilation — this is acceptable.
5. **`_open_files` access**: Modified by didOpen/didChange/didClose (could be from thread pool). Read by `_do_recompile()`. Since dict operations in CPython are GIL-protected and we only do simple get/set/del, this is safe without additional locking in phase 1. If contention becomes an issue, add a `threading.Lock` in a future phase.
6. **No shared mutable state in index/compiler/hover/project**: These modules are pure functions or frozen data. Thread safety comes from immutability.

## Phase 2 additions

The following sections document the phase 2 extensions to the architecture.
Phase 1 interfaces above remain unchanged unless noted.

### Updated module dependency graph

```
                    types.py
                   /   |   \    \     \
                  /    |    \    \     \
           project.py  |  compiler.py  hover.py  completion.py
                       |       |                      |
                    index.py   |                      |
                       |       |                      |
                       +---+---+----------------------+
                           |
                        server.py
```

- `completion.py` — imports `types.py` and `pyslang` only (same rule as index.py)
- `index.py` — gains one new public method (`search_symbols`)
- `server.py` — gains three new handlers (completion, workspace symbol, document highlight)

---

## completion.py — Completion engine

### Design principles

1. **Context-first**: Detect the completion context from trigger character and
   surrounding text BEFORE generating candidates. Different contexts use different
   pyslang APIs.
2. **Live queries**: Query the retained `Compilation` object directly. No
   pre-computed completion index — the symbol index is optimized for definition
   lookup, not completion enumeration.
3. **Scope-walking**: The key new infrastructure is `find_scope_at_position()`,
   which walks the semantic tree to find the deepest scope containing the cursor.
4. **No LSP types**: This module returns `list[CompletionItem]` (diode's own type).
   Server.py converts to `lsp.CompletionItem`.

### Public interface

```python
def get_completions(
    compilation: Any,  # pyslang.Compilation
    index: SymbolIndex,
    path: Path,
    position: FilePosition,
    trigger_char: str | None,
    source_lines: dict[Path, list[str]],
) -> list[CompletionItem]:
    """Generate completion candidates for the given position.

    This is the single entry point. Determines completion context, resolves
    the enclosing scope, generates candidates, and returns them sorted by
    priority.

    Args:
        compilation: The current pyslang Compilation object (retained from
            CompilationResult). Used for scope walking and live queries.
        index: The current SymbolIndex. Used for module name completion
            and as fallback for identifier lookup.
        path: The file being edited (absolute path).
        position: Cursor position (0-based line/column).
        trigger_char: The character that triggered completion ('.' / ':' / '$'),
            or None if completion was manually invoked (e.g., Ctrl+Space).
        source_lines: Cached source text lines per file, for text analysis.
            Available from SymbolIndex._source_lines (exposed via property).

    Returns:
        List of CompletionItem, sorted by sort_group then label.
        Empty list if no completions are available.
    """
```

### Internal functions

```python
def _detect_context(
    source_lines: dict[Path, list[str]],
    path: Path,
    position: FilePosition,
    trigger_char: str | None,
) -> _CompletionContext:
    """Analyze the trigger character and surrounding text to determine context.

    Returns a _CompletionContext namedtuple with:
        kind: CompletionContextKind enum value
        prefix: text already typed after the trigger (for filtering)
        qualifier: additional context (package name for ::, variable for .)

    Detection logic:
        1. If trigger_char == '$': SYSTEM_TASK context
        2. If trigger_char == ':' and preceded by another ':' and an identifier:
           PACKAGE_MEMBER context, qualifier = package name
        3. If trigger_char == '.' and preceded by an identifier:
           - If inside a port connection list (detected by enclosing parens
             after an instance name): PORT_CONNECTION context
           - Otherwise: DOT context, qualifier = variable/expression name
        4. If trigger_char is None (manual invoke):
           - If cursor is inside '#(' parameter list: PARAM_OVERRIDE context
           - If cursor is inside '.(' port connection: PORT_CONNECTION context
           - If preceding text suggests module instantiation context
             (after a module name keyword or at statement level with no
             assignment): MODULE_NAME context
           - Otherwise: IDENTIFIER context
        5. Default: IDENTIFIER context

    The text analysis uses simple backward scanning on the current line.
    It does NOT parse SystemVerilog — it looks for patterns like:
        'pkg_name::' → PACKAGE_MEMBER
        'variable.'  → DOT
        '.port_name(' → PORT_CONNECTION
        '#('         → PARAM_OVERRIDE

    Args:
        source_lines: Source text lines per file.
        path: Current file path.
        position: Cursor position.
        trigger_char: Trigger character or None.

    Returns:
        _CompletionContext with kind, prefix, and qualifier.
    """


def _find_scope_at_position(
    compilation: Any,
    path: Path,
    position: FilePosition,
) -> Any | None:
    """Walk the semantic tree to find the deepest scope containing the cursor.

    Scopes in pyslang: InstanceBodySymbol, StatementBlockSymbol,
    GenerateBlockSymbol, ProceduralBlockSymbol, Scope (base class).

    Algorithm:
    1. Get source manager from compilation
    2. For each top instance in compilation.getRoot().topInstances:
       a. Get the instance body's source range
       b. If the cursor position falls within this range:
          - Record this scope as the current best
          - Iterate the body's members looking for nested scopes
          - Recurse into any nested scope that contains the cursor
    3. Also check packages (for completion inside package bodies)
    4. Return the deepest (most nested) scope found

    Source range comparison:
        Uses sm.getLineNumber(loc) and sm.getColumnNumber(loc) to convert
        pyslang SourceLocation to line/column for comparison with FilePosition.
        The sourceRange property gives (start, end) as SourceLocation pairs.

    Args:
        compilation: pyslang Compilation object.
        path: File path to constrain scope search to.
        position: Cursor position (0-based).

    Returns:
        The deepest pyslang Scope containing the position, or None.
    """


def _find_instance_at_position(
    compilation: Any,
    path: Path,
    position: FilePosition,
) -> Any | None:
    """Find the module instance surrounding the cursor position.

    Used for PORT_CONNECTION and PARAM_OVERRIDE contexts, where we need
    to know which instance's ports/params to complete.

    Similar to _find_scope_at_position but specifically looks for
    InstanceSymbol nodes whose instantiation syntax range contains the cursor.

    Args:
        compilation: pyslang Compilation object.
        path: File path.
        position: Cursor position.

    Returns:
        The pyslang InstanceSymbol at the cursor, or None.
    """


# --- Per-context candidate generators ---


def _complete_identifiers(
    scope: Any,
    compilation: Any,
) -> list[CompletionItem]:
    """Generate completions for in-scope identifiers.

    Walks the scope hierarchy from the deepest scope upward:
    1. Iterate members of the current scope → sort_group=0 (local)
    2. Walk parentScope chain → sort_group=1 (enclosing)
    3. For module body scopes, also include wildcard-imported package members
       (resolved via the compilation's import tracking) → sort_group=2

    For each member, creates a CompletionItem with:
        label: symbol name
        kind: mapped from pyslang symbol type
        detail: type string (e.g., "logic [7:0]")
        sort_group: proximity-based priority

    Filters out:
        - Compiler-generated symbols (empty names, $ prefixes)
        - Duplicate names (keeps closest scope)

    Args:
        scope: The pyslang Scope at the cursor position.
        compilation: For resolving imported names.

    Returns:
        List of CompletionItem for visible identifiers.
    """


def _complete_module_names(compilation: Any) -> list[CompletionItem]:
    """Generate completions for module/interface names.

    Calls compilation.getDefinitions() and returns each as a CompletionItem:
        label: definition name
        kind: MODULE or INTERFACE
        detail: port list summary
        sort_group: 0

    Args:
        compilation: pyslang Compilation object.

    Returns:
        List of CompletionItem for all definitions.
    """


def _complete_port_connections(
    compilation: Any,
    path: Path,
    position: FilePosition,
) -> list[CompletionItem]:
    """Generate completions for port connections in instantiation.

    1. Find the InstanceSymbol at the cursor position
    2. Get inst.body.portList for all port names
    3. Get inst.portConnections to find already-connected ports
    4. Return unconnected ports as CompletionItem:
        label: port name
        kind: PORT
        detail: direction + type (e.g., "input logic [7:0]")
        insert_text: ".port_name()" with cursor between parens
        sort_group: 0

    Args:
        compilation: pyslang Compilation object.
        path: File path containing the instantiation.
        position: Cursor position inside the port list.

    Returns:
        List of CompletionItem for unconnected ports.
    """


def _complete_param_overrides(
    compilation: Any,
    path: Path,
    position: FilePosition,
) -> list[CompletionItem]:
    """Generate completions for parameter overrides in #().

    1. Find the InstanceSymbol at the cursor position
    2. Get inst.body.parameters for all parameter names
    3. Return each as CompletionItem:
        label: parameter name
        kind: PARAMETER
        detail: type + default value
        insert_text: ".PARAM_NAME()" for named override syntax
        sort_group: 0

    Args:
        compilation: pyslang Compilation object.
        path: File path.
        position: Cursor position inside #().

    Returns:
        List of CompletionItem for parameters.
    """


def _complete_package_members(
    compilation: Any,
    package_name: str,
) -> list[CompletionItem]:
    """Generate completions for package members after '::'.

    1. Call compilation.getPackage(package_name)
    2. If not found, return empty list
    3. Iterate package scope members → CompletionItem:
        label: member name
        kind: mapped from member type (PARAMETER, TYPEDEF, FUNCTION, etc.)
        detail: type or signature string
        sort_group: 0

    Args:
        compilation: pyslang Compilation object.
        package_name: Name of the package before '::'.

    Returns:
        List of CompletionItem for package members.
    """


def _complete_dot_members(
    scope: Any,
    compilation: Any,
    qualifier: str,
) -> list[CompletionItem]:
    """Generate completions for members after '.'.

    1. Resolve the qualifier name in the current scope:
       scope.lookupName(qualifier) or walk parent scopes
    2. Get the resolved symbol's type: symbol.type
    3. Unwrap type aliases: type.canonicalType
    4. Based on the canonical type:
       - Struct: iterate for FieldSymbol → FIELD items
       - Enum: iterate for EnumValueSymbol → ENUM_MEMBER items
       - Class: iterate for properties and methods → SIGNAL/FUNCTION items
       - Interface: iterate for modport members
    5. Return CompletionItem for each member:
        label: member name
        kind: mapped from member type
        detail: type string
        sort_group: 0

    Args:
        scope: Current scope (for resolving the qualifier).
        compilation: pyslang Compilation object.
        qualifier: The identifier before '.'.

    Returns:
        List of CompletionItem for type members.
    """


def _complete_system_tasks(prefix: str) -> list[CompletionItem]:
    """Generate completions for system tasks/functions after '$'.

    Uses a curated list of common system tasks/functions rather than
    enumerating all 286 KnownSystemName entries (most are rarely used).

    Common system tasks included:
        $display, $write, $monitor, $strobe,
        $finish, $stop, $fatal, $error, $warning, $info,
        $time, $realtime, $stime,
        $random, $urandom, $urandom_range,
        $clog2, $bits, $size, $left, $right, $high, $low,
        $signed, $unsigned,
        $countones, $onehot, $onehot0, $isunknown,
        $readmemh, $readmemb, $writememh, $writememb,
        $fopen, $fclose, $fwrite, $fdisplay, $fscanf, $feof,
        $sformat, $sformatf,
        $value$plusargs, $test$plusargs,
        $cast, $typename,
        $assertoff, $asserton, $assertkill

    Each returned as CompletionItem:
        label: task name without '$' prefix
        kind: SYSTEM_TASK
        detail: brief description
        insert_text: full name with '$' prefix
        sort_group: 0

    Filtered by prefix if the user has typed partial text after '$'.

    Args:
        prefix: Text typed after '$' (may be empty).

    Returns:
        List of CompletionItem for matching system tasks.
    """


# --- Helper functions ---


def _map_pyslang_to_completion_kind(sym: Any) -> CompletionItemKind:
    """Map a pyslang symbol to a CompletionItemKind.

    Mapping:
        PortSymbol → PORT
        ParameterSymbol → PARAMETER
        VariableSymbol / NetSymbol → SIGNAL
        SubroutineSymbol (Task) → TASK
        SubroutineSymbol (Function) → FUNCTION
        TypeAliasType → TYPEDEF
        TransparentMemberSymbol (enum) → ENUM_MEMBER
        FieldSymbol (struct) → FIELD
        InstanceSymbol → MODULE
        DefinitionSymbol (Interface) → INTERFACE
        DefinitionSymbol (Module) → MODULE
        PackageSymbol → PACKAGE
        Default → SIGNAL
    """


def _get_type_detail(sym: Any) -> str | None:
    """Extract a human-readable type string from a pyslang symbol.

    Returns strings like 'logic [7:0]', 'input logic', 'int', etc.
    Returns None if no type information is available.
    """
```

### Internal data structures

```python
from typing import NamedTuple

class _CompletionContext(NamedTuple):
    """Result of context detection. Internal to completion.py."""
    kind: CompletionContextKind
    prefix: str         # text typed after trigger (for filtering)
    qualifier: str      # package name, struct variable, etc. (empty if N/A)
```

### Context detection algorithm — detailed

The `_detect_context` function performs backward text scanning on the current
line. It does NOT parse SystemVerilog — it uses heuristic pattern matching.

```
Given: line text, cursor column, trigger character

Step 1: Extract the text before the cursor on the current line
        pre_text = line[:cursor_column]

Step 2: If trigger_char == '$':
        → SYSTEM_TASK, prefix = text after '$'

Step 3: If trigger_char == ':':
        Check if pre_text ends with '<ident>::'
        If yes → PACKAGE_MEMBER, qualifier = ident, prefix = ""
        If no → IDENTIFIER (single colon, not relevant)

Step 4: If trigger_char == '.':
        Scan backward from '.' to find preceding identifier
        If inside parentheses that follow '.port(' pattern:
            → PORT_CONNECTION
        Else:
            → DOT, qualifier = preceding identifier

Step 5: If trigger_char is None (manual invoke):
        Scan backward to detect context:
        a. If inside '#(' at instantiation level → PARAM_OVERRIDE
        b. If inside '(' following '.ident' → PORT_CONNECTION
        c. If at statement level after type/module keyword → MODULE_NAME
        d. Default → IDENTIFIER, prefix = partial word at cursor

The heuristic for PORT_CONNECTION vs DOT:
    - Track parenthesis nesting backward from cursor
    - If we find '.ident(' before we find ';' or 'begin',
      we're in a port connection
    - Otherwise it's a dot member access
```

### Scope walking algorithm — detailed

```
find_scope_at_position(compilation, path, position):

    sm = compilation.sourceManager
    target_line = position.line + 1      # pyslang is 1-based
    target_col = position.column + 1

    def _source_range_contains(sym, line, col):
        """Check if a symbol's syntax.sourceRange contains line:col."""
        syntax = sym.syntax
        if syntax is None:
            return False
        sr = syntax.sourceRange
        start_line = sm.getLineNumber(sr.start)
        start_col = sm.getColumnNumber(sr.start)
        end_line = sm.getLineNumber(sr.end)
        end_col = sm.getColumnNumber(sr.end)
        # Also check the file matches
        start_path = Path(sm.getFullPath(sr.start.buffer))
        if start_path.resolve() != path.resolve():
            return False
        return (start_line, start_col) <= (line, col) <= (end_line, end_col)

    def _find_deepest(scope):
        """Recurse into child scopes to find the deepest match."""
        best = scope
        for member in scope:
            if hasattr(member, '__iter__'):  # is a Scope
                if _source_range_contains(member, target_line, target_col):
                    deeper = _find_deepest(member)
                    if deeper is not None:
                        best = deeper
            # Also check InstanceSymbol.body, ProceduralBlockSymbol, etc.
            if isinstance(member, pyslang.InstanceSymbol):
                body = member.body
                if _source_range_contains(body, target_line, target_col):
                    deeper = _find_deepest(body)
                    if deeper is not None:
                        best = deeper
        return best

    # Check top instances
    for inst in compilation.getRoot().topInstances:
        body = inst.body
        if _source_range_contains(body, target_line, target_col):
            return _find_deepest(body)

    # Check packages
    for pkg in compilation.getPackages():
        if pkg.name == 'std':
            continue
        if _source_range_contains(pkg, target_line, target_col):
            return _find_deepest(pkg)

    return None
```

### Data flow: completion request

```
1. Editor sends textDocument/completion { uri, position, context }
2. server.on_completion() [runs in thread pool]:
   a. path = _uri_to_path(params.text_document.uri)
   b. pos = _from_lsp_position(params.position)
   c. trigger = params.context.trigger_character (or None)
   d. comp = _compilation.compilation  (retained pyslang Compilation)
   e. items = completion.get_completions(comp, _index, path, pos, trigger, _index.source_lines)
   f. Convert each CompletionItem to lsp.CompletionItem:
      - label → label
      - kind → _completion_kind_to_lsp(item.kind)
      - detail → detail
      - insert_text → insertText (or label if None)
      - sort_group → sortText (f"{item.sort_group:02d}_{item.label}")
      - documentation → documentation (MarkupContent if present)
   g. Return lsp.CompletionList(is_incomplete=False, items=lsp_items)
3. Editor shows completion popup
```

---

## index.py — Phase 2 additions

### New public method

```python
class SymbolIndex:
    # ... existing methods unchanged ...

    @property
    def source_lines(self) -> dict[Path, list[str]]:
        """Expose cached source lines for use by completion module.

        Returns the internal source line cache. Read-only access.
        """
        return self._source_lines

    def search_symbols(self, query: str, limit: int = 100) -> list[SymbolInfo]:
        """Search for symbols whose name contains the query string.

        Used for workspace/symbol requests. Case-insensitive substring match.

        Args:
            query: Search string (case-insensitive).
            limit: Maximum number of results (default 100).

        Returns:
            List of matching SymbolInfo, ordered by:
            1. Exact match first
            2. Prefix match second
            3. Substring match third
            Within each group, alphabetical by name.
        """
```

---

## server.py — Phase 2 handler additions

### New LSP handler registrations

```python
@SERVER.thread()
@SERVER.feature(
    lsp.TEXT_DOCUMENT_COMPLETION,
    lsp.CompletionOptions(
        trigger_characters=[".", ":", "$"],
    ),
)
def on_completion(params: lsp.CompletionParams) -> lsp.CompletionList | None:
    """Completion handler.

    1. Get compilation and index (bail if either is None)
    2. Convert LSP position to FilePosition
    3. Extract trigger character from params.context
    4. Call completion.get_completions(...)
    5. Convert diode CompletionItems to LSP CompletionItems
    6. Return CompletionList(is_incomplete=False, items=...)
    """


@SERVER.thread()
@SERVER.feature(lsp.WORKSPACE_SYMBOL)
def on_workspace_symbol(params: lsp.WorkspaceSymbolParams) -> list[lsp.SymbolInformation] | None:
    """Workspace symbol handler (Ctrl+T / Telescope symbol search).

    1. Get query string from params
    2. Call _index.search_symbols(query)
    3. Convert each SymbolInfo to lsp.SymbolInformation:
        name: symbol name
        kind: mapped from DiodeSymbolKind
        location: mapped from symbol.definition
    4. Return list of SymbolInformation
    """


@SERVER.thread()
@SERVER.feature(lsp.TEXT_DOCUMENT_DOCUMENT_HIGHLIGHT)
def on_document_highlight(params: lsp.DocumentHighlightParams) -> list[lsp.DocumentHighlight] | None:
    """Document highlight handler.

    1. Convert position, lookup symbol via _index.lookup_at()
    2. Call _index.find_references(symbol.name)
    3. Filter to same file only
    4. For each reference in the same file:
       - If it matches the symbol's definition location: kind = Write
       - Otherwise: kind = Read
    5. Return list of DocumentHighlight
    """
```

### New helper functions

```python
_COMPLETION_KIND_MAP: dict[CompletionItemKind, lsp.CompletionItemKind] = {
    CompletionItemKind.MODULE: lsp.CompletionItemKind.Module,
    CompletionItemKind.INTERFACE: lsp.CompletionItemKind.Interface,
    CompletionItemKind.PACKAGE: lsp.CompletionItemKind.Module,
    CompletionItemKind.PORT: lsp.CompletionItemKind.Property,
    CompletionItemKind.PARAMETER: lsp.CompletionItemKind.Constant,
    CompletionItemKind.SIGNAL: lsp.CompletionItemKind.Variable,
    CompletionItemKind.FUNCTION: lsp.CompletionItemKind.Function,
    CompletionItemKind.TASK: lsp.CompletionItemKind.Function,
    CompletionItemKind.TYPEDEF: lsp.CompletionItemKind.Struct,
    CompletionItemKind.ENUM_MEMBER: lsp.CompletionItemKind.EnumMember,
    CompletionItemKind.FIELD: lsp.CompletionItemKind.Field,
    CompletionItemKind.SYSTEM_TASK: lsp.CompletionItemKind.Function,
    CompletionItemKind.KEYWORD: lsp.CompletionItemKind.Keyword,
}


def _completion_kind_to_lsp(kind: CompletionItemKind) -> lsp.CompletionItemKind:
    """Map CompletionItemKind to LSP CompletionItemKind."""
    return _COMPLETION_KIND_MAP.get(kind, lsp.CompletionItemKind.Variable)
```

### Updated mutable state

```python
# Add to existing module-level state:
_compilation_result: CompilationResult | None = None  # retained for completion access

# In _do_recompile(), after building the index:
#   _compilation_result = result  (store alongside _index)
```

Note: The existing code already retains `_compilation` implicitly because
`CompilationResult.compilation` holds the pyslang Compilation object and
`_pyslang_keep_alive` keeps the C++ objects alive. Phase 2 simply needs
to store the `CompilationResult` at module level so the completion handler
can access `result.compilation`.

---

## Open file vs disk file handling

- `_open_files: dict[Path, str]` tracks content of files open in the editor
- On `didOpen`: add `path → text` to `_open_files`
- On `didChange`: update `_open_files[path]` with latest content (full sync mode)
- On `didClose`: remove path from `_open_files`
- `compiler.compile_project()` receives `_open_files` as parameter
  - For each source file, if `path in open_files`: parse from `open_files[path]`
  - Otherwise: parse from disk
- This ensures the language server always sees what the user is typing, even before save
