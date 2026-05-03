# Test Fixtures

SystemVerilog files designed to exercise diode's phase 1 LSP features.
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
```

## Adding new fixtures

When adding a fixture:
1. Include comments at the top documenting expected LSP behavior
2. Use specific line references (these are part of the test contract)
3. Keep fixtures minimal — test one concept per file where possible
4. Update this README with the new file's purpose
