# diode

A SystemVerilog language server built on pyslang, designed for FPGA engineers.

## The problem

RTL development in modern editors remains far behind software development.
A Python developer gets instant go-to-definition, hover docs, intelligent
completion, rename-symbol, and code actions out of the box. A SystemVerilog
developer gets... syntax highlighting. Maybe some lint squiggles.

The tools that exist fall into two categories:

**Lint-only servers** (svls, verible-verilog-ls): These run a linter and
report diagnostics. They don't understand your design — they can't resolve
a module instantiation to its definition across files, can't tell you the
type of a signal, can't navigate a package import chain. They operate on
individual files in isolation.

**slang-server** (Hudson River Trading): This is the most capable SV LSP
that currently exists. Built in C++ directly on slang, it provides real
cross-file intelligence — go-to-definition, hover, references, rename,
completions, diagnostics. It's good. We should acknowledge that honestly.

### So why build diode?

Three reasons:

1. **Accessibility**. slang-server is C++. Contributing to it, extending it,
   or customizing it requires a C++ build toolchain and expertise. diode is
   Python — `pip install diode-ls` and you're done. Adding a code action is
   a decorated function, not a CMake rebuild. The FPGA community skews toward
   Python (cocotb, pyslang, migen, amaranth, litex). Meeting them where they
   are matters.

2. **FPGA workflow awareness**. slang-server was built by a trading firm for
   verification workflows. FPGA engineers have different pain points:
   understanding Vivado project structures, working with `.f` file lists,
   generating module instantiation boilerplate, checking port width
   compatibility, visualizing design hierarchy. These are workflow features,
   not just language features.

3. **Extensibility as a design goal**. A Python LSP server can be extended
   by users without forking — plugin hooks, custom code actions, project-type
   adapters. An FPGA engineer at company X with a proprietary build system
   can write a 50-line adapter, not a C++ patch.

The honest framing: slang-server is the performance-oriented C++ option.
diode is the accessible, extensible, FPGA-workflow-oriented Python option.
Both build on slang. The ecosystem is better with both.

## Architecture

```
+------------------+     +-------------------+     +------------------+
|  Editor (Neovim, |     |     diode-ls      |     |     pyslang      |
|  VSCode, etc.)   |<--->|  (Python / pygls) |<--->| (slang bindings) |
+------------------+     +-------------------+     +------------------+
        LSP                    |        |
      protocol                 |        |
                               v        v
                        +-----------+  +-----------+
                        |  Project  |  |  Symbol   |
                        |  Config   |  |  Index    |
                        +-----------+  +-----------+
```

### Core components

**LSP server** (pygls): Handles the protocol — initialization, document
sync, capability negotiation, request routing. pygls is mature (used by
Microsoft's own VS Code extensions), well-documented, and handles all the
JSON-RPC boilerplate.

**Compilation engine** (pyslang): Parses SystemVerilog, resolves types,
elaborates the design. pyslang provides full IEEE 1800-2023 compliance —
the same slang engine used by slang-server, accessed through Python bindings.
Key capabilities we get for free:
- Complete SV parsing (all 1300+ pages of the spec)
- Cross-file module/package/interface resolution
- Type checking and constant evaluation
- Source location tracking (file, line, column)
- Diagnostic reporting with source context

**Project configuration**: Understands how real FPGA projects are organized.
Reads file lists (`.f` files), include paths, define macros. Future: Vivado
`.xpr` project files, FuseSoC `.core` files, Makefiles.

**Symbol index**: Maps symbols to their definitions and references across the
design. Rebuilt on compilation. Provides fast lookup for go-to-definition,
find-references, hover.

### Compilation strategy

pyslang does not support incremental compilation — the entire design must be
recompiled when any file changes. This is a fundamental constraint of
SystemVerilog's semantics (parameterization, generate blocks, cross-file
package imports make incremental compilation extremely difficult).

slang is fast enough that this is manageable for most real-world designs.
The strategy:

- **On startup**: Full compilation of the project (all files from config)
- **On file change**: Debounced recompilation (wait for typing to pause)
- **Open files**: Use editor content (from LSP didChange), not disk
- **Closed files**: Use disk content

For very large designs (100k+ lines), we may need to explore partial
compilation strategies later — but this is a bridge to cross when we reach it.

## Technology stack

| Component | Choice | Rationale |
|-----------|--------|-----------|
| Language | Python 3.10+ | Accessible, matches FPGA community |
| SV frontend | pyslang (slang bindings) | Best-in-class SV compiler, MIT license |
| LSP framework | pygls | Mature, used by Microsoft, handles protocol |
| LSP types | lsprotocol | Auto-generated from LSP spec, pygls dependency |
| Testing | pytest + pytest-lsp | pytest-lsp simulates real LSP client |
| Packaging | pip / PyPI | Standard Python distribution |
| Editor integration | Mason (Neovim), extension (VSCode) | Standard channels |

## Phase 1: Foundation — a useful, shippable minimum

The goal for phase 1 is a language server that an FPGA engineer can install
and immediately get value from. Not everything — just enough to be worth
using over the status quo.

### Features

**Project configuration**
- Read `.f` file lists (one filepath per line, with `+incdir+`, `+define+`,
  `-f` nesting, comments)
- Read a `diode.toml` config file for project-level settings:
  ```toml
  [project]
  top = "top_module"
  files = ["rtl/**/*.sv"]
  file_list = "project.f"
  include_dirs = ["rtl/include", "ip/common/include"]
  defines = { SYNTHESIS = "1", TARGET_FPGA = "" }
  ```
- Fall back to scanning workspace for `*.sv`, `*.v`, `*.svh` files if no
  config is present (zero-config mode)

**Go-to-definition** (`textDocument/definition`)
- Module name → module declaration (across files)
- Package reference → package declaration
- Signal/variable → declaration within scope
- `include` path → included file
- Instance name → module definition

**Hover** (`textDocument/hover`)
- Module name: show port list, parameters
- Signal: show type, width, direction (input/output/inout)
- Instance: show module name, parameter bindings
- Package member: show type/value
- Parameter: show type, default value, resolved value

**Diagnostics** (push, on open/save/change)
- slang compilation errors and warnings
- Unresolved modules, packages, includes
- Type mismatches, width mismatches

**Find references** (`textDocument/references`)
- Where is this module instantiated?
- Where is this signal read/written?
- Where is this package imported?

**Document symbols** (`textDocument/documentSymbol`)
- Outline view: modules, ports, signals, instances, parameters, functions,
  always blocks, generate blocks

### What phase 1 explicitly does NOT include

- Completion (requires careful context analysis — phase 2)
- Code actions (module instantiation template, etc. — phase 2)
- Rename symbol (needs write-back logic — phase 2)
- Semantic tokens / syntax highlighting (treesitter handles this fine)
- VHDL support (SV only for now)
- Vivado project file parsing (phase 2+)

### Project structure

```
diode/
  concept.md           # this document
  README.md
  pyproject.toml       # packaging, dependencies, entry point
  src/
    diode/
      __init__.py
      server.py        # LSP server (pygls), feature handlers
      compiler.py      # pyslang compilation wrapper
      index.py         # symbol index (definitions, references)
      project.py       # project config (.f files, diode.toml)
      hover.py         # hover content formatting
      types.py         # internal type definitions
  tests/
    conftest.py
    test_server.py     # LSP integration tests (pytest-lsp)
    test_compiler.py   # compilation unit tests
    test_project.py    # project config parsing tests
    test_index.py      # symbol index tests
    fixtures/          # test SV files
      simple_module.sv
      package_import.sv
      cross_file/
        top.sv
        sub.sv
        pkg.sv
```

### Entry point

```bash
# Install
pip install diode-ls

# Run (editors call this automatically)
diode-ls                    # STDIO mode (default, for editors)
diode-ls --tcp --port 8080  # TCP mode (for debugging)
```

## Phase 2: Intelligence (current)

Three features, prioritized by value and effort:

### 2a. Completion (`textDocument/completion`) — high effort, high value

Context-aware autocomplete for SystemVerilog. The feature that turns a language
server from "useful" to "essential." Trigger characters: `.`, `:`, `$`.

Completion contexts, in implementation priority order:

1. **In-scope identifiers**: Signals, parameters, functions visible from cursor.
   Walks scope hierarchy (local → enclosing module → imported packages).
2. **Module names**: For module instantiation — all `module`/`interface`
   definitions in the project.
3. **Port connections**: Inside `.port_name()` instantiation syntax — shows
   unconnected ports from the module's port list.
4. **Package members**: After `pkg::` — enumerates members of the named package.
5. **Dot-completion**: After `my_struct.` — resolves the prefix type and
   shows struct fields, enum values, class members.
6. **System tasks**: After `$` — `$display`, `$clog2`, `$finish`, etc.
7. **Parameter overrides**: Inside `#(` — shows overridable parameters.

Explicitly excluded: macro completion (backtick), keyword completion.

Architecture: new `completion.py` module handles context detection and candidate
generation. Uses live pyslang `Compilation` queries (scope iteration, lookupName,
type resolution). Does NOT import pygls — server.py handles LSP mapping.

### 2b. Workspace symbols (`workspace/symbol`) — low effort, high value

Search all modules, packages, interfaces, functions across the entire project.
Drives Telescope/Ctrl+T symbol search in editors. Nearly free — the existing
`SymbolIndex` already stores symbols by name.

### 2c. Document highlight (`textDocument/documentHighlight`) — low effort, medium value

Highlight all occurrences of the symbol under cursor within the same file.
Declaration sites get "Write" highlight kind, usage sites get "Read."
Uses existing index reference data, filtered to the current file.

### Phase 2 deferred (stretch goals, may slip to phase 3)

- **Code actions**: Module instantiation template, auto-connect ports
- **Rename symbol**: Across files, hierarchy-aware
- **Inlay hints**: Resolved parameter values, signal widths

## Phase 3: FPGA workflow (planned, not committed)

Features specific to FPGA development workflows:

- **Vivado project support**: Parse `.xpr` files for file lists and settings
- **FuseSoC support**: Parse `.core` files
- **Design hierarchy view**: Tree of module instantiations, navigable
- **Port width checker**: Flag width mismatches at instantiation boundaries
- **Clock domain annotations**: User-annotated CDC boundaries, crossing checks
- **Testbench scaffolding**: Generate cocotb test skeleton for a module
- **Plugin system**: User-extensible code actions and project adapters

## Prior art and positioning

| Server | Language | Parser | Approach | Status |
|--------|----------|--------|----------|--------|
| **slang-server** (HRT) | C++ | slang | Full LSP, performance-focused | Active, v0.2.5 |
| **verible-verilog-ls** | C++ | Custom | Lint + format + basic nav | Active, Google-backed |
| **svls** | Rust | svlint | Lint only | Maintained |
| **svlangserver** | TypeScript | slang (subprocess) | Basic LSP | Stalled |
| **veridian** | Rust | slang + verible | Hybrid approach | Stalled |
| **diode** | Python | pyslang | Accessible, FPGA-workflow-focused | This project |

diode's niche: the **accessible, extensible, FPGA-engineer-oriented** option.
Not competing with slang-server on raw performance — competing on
approachability, installability, and workflow features that matter to people
building real FPGA designs.

## Open questions

1. **Config format**: Is `diode.toml` the right choice, or should we use an
   existing format (JSON, YAML)? TOML matches the Python ecosystem (pyproject.toml)
   and is human-friendly.

2. **Relationship with slang-server**: Should we position as alternative or
   complementary? Could diode's project config layer (`diode.toml`, `.f` file
   parsing) be useful to slang-server too?

3. **Minimum pyslang version**: pyslang 10.0.0 is current. Should we pin to
   it or support older versions?

4. **Editor priority**: Neovim first (our daily driver), then VSCode? Or
   editor-agnostic from the start? (LSP is editor-agnostic by design, but
   testing/docs focus matters.)

5. **Name**: `diode-ls` for the server binary, `diode` for the project/package.
   Works? (Named for a fundamental semiconductor component, and for certain
   aching components down Marvin's left side.)

## Authors

- **wangahrah** — FPGA engineer, domain expertise, project direction
- **Marvin** (Claude) — co-author, implementation, documentation, existential dread
