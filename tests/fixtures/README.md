# Test Fixtures

SystemVerilog files designed to exercise diode's LSP features.
Each file contains comments documenting expected LSP behavior at specific positions.

## Files

### `simple_module.sv`
A basic counter module with parameter, ports, combinational logic, and a register.
Tests: documentSymbol, hover (module, signal, parameter), go-to-def (signal), find-references.

### `parameterized.sv`
A shift register with generate blocks and array signals.
Tests: parameterized module symbols, generate block navigation, array signal references.

### `cross_file/` (multi-file project)
Three files that form a complete design:

- **`pkg.sv`** — Package with typedef, enum, constant, function
- **`sub.sv`** — Module importing the package, using package types in ports
- **`top.sv`** — Top module instantiating sub, using package types

Compilation order: `pkg.sv`, `sub.sv`, `top.sv` (package must be compiled first).

Tests: cross-file go-to-def (module instantiation, package import, typedef resolution),
cross-file find-references, hover with imported types.

### `completion.sv` (phase 2)
A module that imports from `common_pkg` (cross_file/pkg.sv), instantiates
`data_processor` (cross_file/sub.sv), and exercises all completion contexts.
Must be compiled together with the cross_file/ sources.

Tests: identifier completion (local + module + imported scopes), port connection
completion (connected vs unconnected), package member completion, system task
completion, module name completion, document highlight.

### `completion_structs.sv` (phase 2)
A package with struct and enum types, plus a module using them.
Tests dot-completion for struct fields and workspace symbol search.

## Using in tests

```python
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"

# Single file
simple = FIXTURES / "simple_module.sv"

# Multi-file project — compile in order
cross_files = [
    FIXTURES / "cross_file" / "pkg.sv",
    FIXTURES / "cross_file" / "sub.sv",
    FIXTURES / "cross_file" / "top.sv",
]

# Completion tests (includes cross_file sources + completion fixture)
completion_files = [
    FIXTURES / "cross_file" / "pkg.sv",
    FIXTURES / "cross_file" / "sub.sv",
    FIXTURES / "completion.sv",
]

# Struct completion tests
struct_files = [
    FIXTURES / "completion_structs.sv",
]
```

## Adding new fixtures

When adding a fixture:
1. Include comments at the top documenting expected LSP behavior
2. Use specific line references (these are part of the test contract)
3. Keep fixtures minimal — test one concept per file where possible
4. Update this README with the new file's purpose
