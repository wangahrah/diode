# Implementation Waves

How to break diode's phase 1 into parallelizable work. Each wave has clear
inputs, outputs, and a review gate before the next wave starts.

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

## Review gates — what to check

Between each wave, verify:
1. All tests from the wave pass
2. No module imports from lsprotocol/pygls except server.py
3. No module creates threads or locks except server.py
4. All public functions match the signatures in architecture.md
5. types.py is unchanged from wave 0
6. No features beyond phase 1 scope
