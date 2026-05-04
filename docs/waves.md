# Implementation Waves

How to break diode into parallelizable work. Each wave has clear
inputs, outputs, and a review gate before the next wave starts.

# Phase 1 (complete)

## Wave 0 — Blueprint (complete)

**Output**: The artifacts you're reading now.
- `types.py` — shared data structures (real code)
- `pyproject.toml` — package definition
- `CLAUDE.md` — implementation guide
- `docs/architecture.md` — interface specification
- `docs/waves.md` — this file
- `tests/fixtures/` — test SystemVerilog files

**Gate**: `python -c "from diode.types import *"` passes. `pip install -e .` succeeds.

## Wave 1 — Project config + Compiler (parallel)

Two independent modules. Neither depends on the other — both depend only on `types.py`.

### Agent A: `project.py`
**Input**: `types.py`, architecture.md section on project.py
**Output**: `src/diode/project.py` + `tests/test_project.py`
**Scope**:
- `load_project(workspace_root: Path) -> ProjectConfig`
- diode.toml parsing (TOML via tomllib/tomli)
- `.f` file parsing (paths, +incdir+, +define+, -f nesting, comments)
- Auto-discovery fallback (glob for *.sv, *.v, *.svh)
- Glob expansion for config file patterns
- Path resolution relative to config file

**Test expectations**:
- Parse a diode.toml with all fields populated
- Parse a .f file with nested -f includes
- Auto-discover .sv files when no config exists
- Handle missing/malformed config gracefully

### Agent B: `compiler.py`
**Input**: `types.py`, architecture.md section on compiler.py
**Output**: `src/diode/compiler.py` + `tests/test_compiler.py`
**Scope**:
- `compile_project(config: ProjectConfig, open_files: dict[Path, str]) -> CompilationResult`
- Create pyslang SyntaxTree per source file
- Override file content for open files (editor buffers)
- Build pyslang Compilation
- Extract diagnostics into DiodeDiagnostic list
- Return CompilationResult with compilation object

**Test expectations**:
- Compile simple_module.sv successfully
- Compile cross_file/ project with all three files
- Report diagnostics for files with errors
- Override file content from open_files dict

**Gate**: Both modules pass their tests independently. `ProjectConfig` from project.py
is the correct input shape for compiler.py.

## Wave 2 — Symbol Index

Depends on Wave 1 (needs CompilationResult shape and a working compiler to test against).

### Agent C: `index.py`
**Input**: `types.py`, architecture.md section on index.py, working `compiler.py`
**Output**: `src/diode/index.py` + `tests/test_index.py`
**Scope**:
- `SymbolIndex` class
- `build_index(result: CompilationResult) -> SymbolIndex`
- Walk pyslang compilation tree, extract symbols
- Lookup methods: `lookup_at()`, `find_definition()`, `find_references()`, `get_document_symbols()`
- Immutable snapshot pattern

**Test expectations**:
- Index simple_module.sv: find module, ports, signals, parameters
- Index cross_file/: resolve cross-file module instantiation
- `lookup_at()` returns correct symbol for known positions
- `find_definition()` resolves module names, package members
- `find_references()` finds all instantiation sites
- `get_document_symbols()` returns outline for a file

**Gate**: Index correctly resolves all expected behaviors documented in test fixture comments.

## Wave 3 — Hover + Server (parallel after index)

Both depend on index.py. They are parallelizable because hover.py formats data
from SymbolInfo (no server dependency) and server.py calls hover.py but can stub it initially.

### Agent D: `hover.py`
**Input**: `types.py`, architecture.md section on hover.py
**Output**: `src/diode/hover.py` (no separate test file — tested via test_server.py)
**Scope**:
- `format_hover(symbol: SymbolInfo) -> str`
- Markdown formatting for each symbol kind
- Module: port list + parameters in code fence
- Signal: type, width, direction
- Instance: module name, parameter bindings
- Package member: type/value

### Agent E: `server.py`
**Input**: All modules, architecture.md section on server.py
**Output**: `src/diode/server.py` + `tests/conftest.py` + `tests/test_server.py`
**Scope**:
- pygls LanguageServer setup
- All LSP handler registrations
- Compilation → index → diagnostics cycle
- Open file tracking (didOpen/didChange/didClose)
- Debounced recompilation
- Position conversion helpers
- `main()` with argument parsing
- Threading: @SERVER.thread(), compilation lock

**Test expectations**:
- Initialize server, open a file, get diagnostics
- Go-to-definition resolves module instantiation
- Hover shows port list for a module
- Find references returns instantiation sites
- Document symbols returns file outline

**Gate**: Full LSP integration test passes — open file, get diagnostics, navigate, hover.

## Wave 4 — Integration testing (parallel)

Final validation wave. All modules exist. Agents write additional integration tests
and fix any issues found during cross-module testing.

### Agent F: End-to-end tests
- Multi-file project scenarios
- Error recovery (bad files mixed with good files)
- Config precedence (diode.toml > .f file > auto-discover)
- Performance sanity check (compile time for fixture project)

### Agent G: Edge cases
- Empty project (no SV files)
- Huge file (stress test with generated SV)
- Unicode in comments/strings
- Windows-style paths (if supporting Windows)
- Nested generate blocks, complex parameterization

**Gate**: All tests pass. `diode-ls` starts and serves requests against test fixtures.

## Phase 1 review gates — what to check

Between each wave, verify:
1. All tests from the wave pass
2. No module imports from lsprotocol/pygls except server.py
3. No module creates threads or locks except server.py
4. All public functions match the signatures in architecture.md
5. types.py is unchanged from wave 0
6. No features beyond phase 1 scope

---

# Phase 2 — Intelligence

Phase 2 adds three features: **completion**, **workspace symbols**, and
**document highlight**. Phase 1 modules are complete and working. Phase 2
adds one new module (`completion.py`), extends one existing module
(`index.py`), and adds three new handlers to `server.py`.

The types contract was extended in the phase 2 blueprint with:
`CompletionContextKind`, `CompletionItemKind`, `CompletionItem`.

## Wave 5 — Index extension + Completion infrastructure

Two parallel tracks. The index extension is small; completion infrastructure
is the bulk of the work.

### Agent H: `index.py` extension
**Input**: `types.py` (updated), architecture.md phase 2 section on index.py
**Output**: Modified `src/diode/index.py`
**Scope**:
- Add `source_lines` property (expose `_source_lines` for completion module)
- Add `search_symbols(query: str, limit: int = 100) -> list[SymbolInfo]` method
- Case-insensitive substring search over `_symbols_by_name`
- Result ordering: exact match → prefix match → substring match
- Result cap at `limit` to prevent huge responses
- **Do NOT change existing methods** — they are tested and working

**Test expectations**:
- `search_symbols("counter")` finds the counter module
- `search_symbols("data")` finds data_t, data_processor, DATA_WIDTH, etc.
- `search_symbols("")` returns up to `limit` symbols (not everything)
- Case-insensitive: `search_symbols("CMD")` matches `CMD_NOP` etc.
- `source_lines` property returns the internal dict

**Gate**: Existing `test_index.py` still passes. New `search_symbols` tests pass.

### Agent I: `completion.py` — core infrastructure
**Input**: `types.py` (updated), architecture.md phase 2 section on completion.py,
working `compiler.py`, `index.py` (for SymbolIndex reference)
**Output**: `src/diode/completion.py` + `tests/test_completion.py`
**Scope**:
- `get_completions()` entry point (full signature in architecture.md)
- `_detect_context()` — backward text scanning for context detection
- `_find_scope_at_position()` — semantic tree walking
- `_find_instance_at_position()` — instance lookup for port/param completion
- All seven candidate generators:
  - `_complete_identifiers(scope, compilation)`
  - `_complete_module_names(compilation)`
  - `_complete_port_connections(compilation, path, position)`
  - `_complete_param_overrides(compilation, path, position)`
  - `_complete_package_members(compilation, package_name)`
  - `_complete_dot_members(scope, compilation, qualifier)`
  - `_complete_system_tasks(prefix)`
- Helper functions: `_map_pyslang_to_completion_kind`, `_get_type_detail`

**What NOT to implement**:
- Do NOT import pygls or lsprotocol — return `list[CompletionItem]` only
- Do NOT implement macro/backtick completion
- Do NOT implement keyword completion
- Do NOT build a pre-computed completion index — query pyslang live
- Do NOT parse SystemVerilog for context detection — use text scanning heuristics

**Test expectations** (using `completion.sv` and `completion_structs.sv` fixtures):
- Context detection:
  - Trigger `.` after identifier → DOT context
  - Trigger `:` after `pkg::` → PACKAGE_MEMBER context with qualifier
  - Trigger `$` → SYSTEM_TASK context
  - Inside `.port(` → PORT_CONNECTION context
  - Manual invoke at statement level → IDENTIFIER context
- Scope resolution:
  - `_find_scope_at_position` inside always block → returns that block's scope
  - Inside module body → returns module body scope
  - Outside any module → returns None
- Candidate generation (compile fixtures, then call generators):
  - Identifier completion returns signals, ports, params from local scope
  - Module name completion returns all definitions
  - Port connection completion excludes already-connected ports
  - Package member completion lists package contents
  - Dot completion on struct returns fields
  - System task completion filters by prefix
- Integration (via `get_completions()`):
  - Returns non-empty list for each completion context
  - Items have correct sort_group ordering
  - Items have correct CompletionItemKind

**Gate**: All test_completion.py tests pass. completion.py imports from
types.py and pyslang only — no pygls dependency.

## Wave 6 — Server integration (sequential after wave 5)

### Agent J: `server.py` — phase 2 handlers
**Input**: Working `completion.py`, `index.py` (extended), architecture.md phase 2
section on server.py
**Output**: Modified `src/diode/server.py` + extended `tests/test_server.py`
**Scope**:
- Add `_compilation_result` module-level state (store `CompilationResult` in
  `_do_recompile()` alongside `_index`)
- Register `textDocument/completion` handler with trigger characters `['.', ':', '$']`:
  - Extract trigger character from `params.context.trigger_character`
  - Call `completion.get_completions(_compilation_result.compilation, _index, ...)`
  - Convert `CompletionItem` → `lsp.CompletionItem` using `_COMPLETION_KIND_MAP`
  - Return `lsp.CompletionList(is_incomplete=False, items=...)`
- Register `workspace/symbol` handler:
  - Call `_index.search_symbols(params.query)`
  - Convert `SymbolInfo` → `lsp.SymbolInformation`
  - Return list
- Register `textDocument/documentHighlight` handler:
  - Lookup symbol at position via `_index.lookup_at()`
  - Call `_index.find_references(symbol.name)`
  - Filter to same file
  - Declaration site → `DocumentHighlightKind.Write`
  - Usage site → `DocumentHighlightKind.Read`
  - Return list of `lsp.DocumentHighlight`
- Add `_COMPLETION_KIND_MAP` dict and `_completion_kind_to_lsp()` helper
- Update `import` statement to include `completion` module

**What NOT to do**:
- Do NOT modify existing handlers (definition, hover, references, documentSymbol)
- Do NOT change the compilation/indexing flow except to store `_compilation_result`
- Do NOT add new capabilities beyond completion + workspace symbol + document highlight

**Test expectations** (LSP integration tests via pytest-lsp):
- Completion:
  - Send completion request at identifier position → get signal/port/param items
  - Send completion request with trigger '.' → get struct fields or port names
  - Send completion request with trigger ':' after pkg:: → get package members
  - Send completion request with trigger '$' → get system tasks
  - Verify CompletionItem kind, label, detail fields are populated
- Workspace symbols:
  - Query "counter" → returns counter module symbol
  - Query "data" → returns multiple matching symbols
  - Empty query → returns symbols (up to limit)
- Document highlight:
  - Request on signal name → returns multiple highlights
  - Declaration site has Write kind, usage has Read kind
  - Results are filtered to the requested file only

**Gate**: All existing test_server.py tests still pass. New completion,
workspace symbol, and document highlight integration tests pass.

## Wave 7 — Integration testing + polish (parallel after wave 6)

### Agent K: End-to-end completion tests
- Multi-file completion scenarios (cross-file imports, package members)
- Completion on partially typed identifiers (manual invoke with prefix)
- Completion at file boundaries (first line, last line, empty file)
- Completion with compilation errors (broken code around cursor)
- Verify completion works after file edit (didChange → recompile → complete)

### Agent L: Edge cases + performance
- Completion in empty module body (no signals yet)
- Completion inside generate blocks
- Dot-completion on unknown/unresolvable types → empty list, no crash
- Package member completion for nonexistent package → empty list
- Port completion when all ports are already connected → empty list
- System task completion with full match (e.g., "$display" typed fully)
- Workspace symbol with special characters in query
- Document highlight on symbol with no references
- Verify no regressions in phase 1 features

**Gate**: All tests pass. `diode-ls` starts and correctly handles completion,
workspace symbol, and document highlight requests against all test fixtures.

## Phase 2 review gates — what to check

Between each wave, verify:
1. All tests from this wave AND all previous waves pass
2. No module imports from lsprotocol/pygls except server.py
3. completion.py imports only from types.py and pyslang (not index internals)
4. All public functions match the signatures in architecture.md
5. types.py phase 2 additions are unchanged from wave 5 blueprint
6. No features beyond phase 2 scope (no code actions, rename, inlay hints)
7. Existing phase 1 behavior is unchanged (run phase 1 test suite)
