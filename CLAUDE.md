# diode — Implementation Guide

You are implementing **diode**, a SystemVerilog language server built on pyslang + pygls.
Read this entire file before writing any code.

## Architecture overview

```
Editor  <--LSP/JSON-RPC-->  server.py  -->  compiler.py  -->  pyslang
                                |                |
                                v                v
                           project.py       index.py
                                |            |
                                v            v
                           hover.py    completion.py
```

Seven modules, one shared types file:

| Module | Responsibility | Depends on |
|--------|---------------|------------|
| `types.py` | Shared dataclasses/enums | nothing |
| `project.py` | Parse diode.toml / .f files / auto-discover sources | `types.py` |
| `compiler.py` | Drive pyslang compilation, extract diagnostics | `types.py` |
| `index.py` | Walk compilation tree, build symbol lookup tables | `types.py` |
| `hover.py` | Format hover markdown from SymbolInfo | `types.py` |
| `completion.py` | Context detection, scope resolution, candidate generation | `types.py`, `index.py` |
| `server.py` | pygls server, LSP handlers, orchestration | all of the above |

## The contract: types.py

`src/diode/types.py` is the **immutable contract**. Every module imports from it.
Do not add types to individual modules — if a type is shared, it belongs in types.py.
If you need to change types.py, you are changing the contract for all modules.

Read `docs/architecture.md` for the full type definitions and public interfaces.

## Coding conventions

### Style
- Python 3.10+ — use `X | Y` union syntax, not `Union[X, Y]`
- Use `from __future__ import annotations` in every file
- Dataclasses with `frozen=True, slots=True` for value types
- Type annotations on all public function signatures
- No star imports. Always `from diode.types import SpecificType`
- f-strings for formatting, never `.format()` or `%`

### Naming
- Module-level constants: `UPPER_SNAKE`
- Classes: `PascalCase`
- Functions/methods: `snake_case`
- Private methods: `_leading_underscore`
- No abbreviations except universally understood ones (sv, lsp, cfg)

### Error handling
- Never silently swallow exceptions in production code paths
- Log warnings for recoverable issues (bad .f file line, unreadable source file)
- Let pyslang errors flow into DiodeDiagnostic — don't re-raise as Python exceptions
- Use `logging` module, logger per module: `logger = logging.getLogger(__name__)`

### What NOT to do
- **Do not write a SystemVerilog parser.** pyslang handles all parsing. Period. Context detection in `completion.py` uses simple backward text scanning on the current line — it identifies trigger patterns (`.`, `::`, `$`), not SV grammar.
- **Do not use lsprotocol types inside non-server modules.** Only server.py imports from lsprotocol/pygls. All other modules (including completion.py) use diode's own types.
- **Do not cache pyslang AST nodes across compilations.** Each recompilation creates a new tree; old references become invalid. Completion queries the live Compilation — don't store scope references.
- **Do not use threads directly.** Use `@SERVER.thread()` for handlers that do work. pygls manages the thread pool.
- **Do not import pyslang at module level in types.py.** Keep types.py dependency-free.
- **Do not implement incremental compilation.** Full recompile every time. This is intentional.
- **Do not add features beyond phase 2 scope.** Phase 2 = completion + workspace symbols + document highlight. No code actions, no rename, no inlay hints.
- **Do not pre-build a completion index.** Completion uses live pyslang queries against the retained Compilation object. The SymbolIndex is for definition/reference lookup, not completion enumeration.
- **Do not implement macro/backtick completion.** Explicitly excluded from scope.
- **Do not implement keyword completion.** Reserved for future use but not phase 2.

## Module implementation guide

### project.py
- Entry point: `load_project(workspace_root: Path) -> ProjectConfig`
- Must handle: diode.toml parsing, `.f` file parsing (recursive `-f`), auto-discovery fallback
- `.f` file format: one path per line, `+incdir+<path>`, `+define+<name>=<value>`, `-f <nested.f>`, `//` comments, blank lines
- Glob expansion for `files = ["rtl/**/*.sv"]` patterns in diode.toml
- All paths resolved relative to the config file's directory
- Return `ProjectConfig` with resolved absolute paths

### compiler.py
- Entry point: `compile_project(config: ProjectConfig, open_files: dict[Path, str]) -> CompilationResult`
- `open_files` maps file paths to their editor buffer content (overrides disk)
- Creates a `pyslang.SyntaxTree` per source file, builds a `pyslang.Compilation`
- Extracts diagnostics from the compilation into `DiodeDiagnostic` objects
- Returns the compilation object for index.py to walk

### index.py
- Entry point: `build_index(result: CompilationResult) -> SymbolIndex`
- `SymbolIndex` class with lookup methods:
  - `lookup_at(path: Path, position: FilePosition) -> SymbolInfo | None`
  - `find_definition(name: str, context_path: Path | None) -> SymbolInfo | None`
  - `find_references(name: str) -> list[FileLocation]`
  - `get_document_symbols(path: Path) -> list[SymbolInfo]`
- Walks the pyslang compilation tree, extracts declarations and references
- The index is an **immutable snapshot** — build a new one on each recompile
- Atomic swap: `server.py` holds `self._index` under a `threading.Lock`

### hover.py
- Entry point: `format_hover(symbol: SymbolInfo) -> str`
- Returns GitHub-flavored markdown
- Module hover: show port list and parameters in a code fence
- Signal hover: type, width, direction
- Instance hover: module name, parameter bindings
- Keep formatting clean and concise — this appears in a small popup

### completion.py (phase 2)
- Entry point: `get_completions(compilation, index, path, position, trigger_char, source_lines) -> list[CompletionItem]`
- Imports `types.py` and `pyslang` only — no pygls, no lsprotocol
- **Context detection** (`_detect_context`): backward text scanning to determine what kind of completion (identifier, dot, package member, port connection, param override, system task, module name)
- **Scope resolution** (`_find_scope_at_position`): walk pyslang semantic tree to find deepest scope containing cursor — this is the key new infrastructure
- **Candidate generators** — one per context kind:
  - `_complete_identifiers`: walk scope + parent scopes, enumerate members
  - `_complete_module_names`: `compilation.getDefinitions()`
  - `_complete_port_connections`: `inst.body.portList` minus already-connected
  - `_complete_package_members`: `compilation.getPackage(name)` iteration
  - `_complete_dot_members`: resolve prefix type, iterate struct/enum/class members
  - `_complete_system_tasks`: curated list of common `$` tasks
  - `_complete_param_overrides`: `inst.body.parameters`
- Uses `LookupFlags.NoUndeclaredError` to avoid polluting diagnostics during completion
- Returns `list[CompletionItem]` sorted by `sort_group` then `label`
- See `docs/architecture.md` phase 2 section for full function signatures

### server.py
- Creates the pygls `LanguageServer` instance
- Registers LSP handlers: `textDocument/didOpen`, `didChange`, `didSave`, `didClose`, `definition`, `hover`, `references`, `documentSymbol`, `textDocument/completion`, `workspace/symbol`, `textDocument/documentHighlight`
- Orchestrates compilation → index rebuild → diagnostics publish cycle
- Uses `@SERVER.thread()` for handlers that call compiler/index/completion
- Holds mutable state: `_config: ProjectConfig`, `_index: SymbolIndex | None`, `_compilation_result: CompilationResult | None`, `_compilation_lock: threading.Lock`
- Open file tracking: maintains `dict[Path, str]` of editor buffer contents
- Debounce: on `didChange`, schedule recompilation after 300ms idle (cancel previous timer)
- Completion handler: passes `_compilation_result.compilation` and `_index` to `completion.get_completions()`, converts result to LSP types
- Workspace symbol handler: calls `_index.search_symbols(query)`
- Document highlight handler: calls `_index.find_references()`, filters to current file
- `main()` function: argument parsing (--tcp, --port), starts server

## Threading model

- pygls runs the LSP message loop on the main thread (asyncio)
- `@SERVER.thread()` handlers run in a thread pool
- Compilation is serialized: one compilation at a time, guarded by `_compilation_lock`
- Index swap is atomic: build new index, then `self._index = new_index` under lock
- Feature handlers (hover, go-to-def) read `self._index` — no lock needed for reads (Python GIL + atomic reference assignment)

## Position handling

- **Internal (diode)**: 0-based line, 0-based column (byte offset within line)
- **LSP protocol**: 0-based line, 0-based column (UTF-16 code units)
- **pyslang**: 0-based line, 0-based column (byte offset)
- Conversion happens in server.py only, in helper functions:
  - `_to_lsp_position(pos: FilePosition) -> lsprotocol.Position`
  - `_to_lsp_range(range: FileRange) -> lsprotocol.Range`
  - `_from_lsp_position(pos: lsprotocol.Position) -> FilePosition`
- For phase 1, assume ASCII (byte offset == UTF-16 offset). UTF-16 handling is a phase 2 concern.

## Testing

- Use `pytest` with `pytest-lsp` for integration tests, `pytest-asyncio` for async
- Test files live in `tests/`, fixtures in `tests/fixtures/`
- One test file per module: `test_project.py`, `test_compiler.py`, `test_index.py`, `test_completion.py`, `test_server.py`
- Unit tests call module functions directly with fixture data
- Integration tests (test_server.py) use pytest-lsp to simulate a real LSP client
- Test the contract, not the implementation — if the interface is satisfied, the test passes
- Fixtures are real `.sv` files with comments documenting expected behavior (see `tests/fixtures/README.md`)

## File layout

```
diode/
  CLAUDE.md              # this file
  concept.md             # project concept and rationale
  pyproject.toml         # package definition
  docs/
    architecture.md      # precise interface specification
    waves.md             # implementation wave breakdown
  src/
    diode/
      __init__.py        # version string only
      types.py           # shared data structures (THE CONTRACT)
      project.py         # project config loading
      compiler.py        # pyslang compilation wrapper
      index.py           # symbol index
      hover.py           # hover content formatting
      completion.py      # completion context detection + candidates (phase 2)
      server.py          # pygls LSP server
  tests/
    conftest.py          # shared fixtures
    test_project.py
    test_compiler.py
    test_index.py
    test_completion.py   # completion unit tests (phase 2)
    test_server.py
    fixtures/
      simple_module.sv
      parameterized.sv
      completion.sv          # completion-specific fixture (phase 2)
      completion_structs.sv  # struct/enum dot-completion fixture (phase 2)
      cross_file/
        top.sv
        sub.sv
        pkg.sv
      README.md
```
