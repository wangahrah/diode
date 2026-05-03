"""Shared data structures for diode.

This module is the contract between all other modules. Implementation agents
treat it as immutable — changes here require coordination across all modules.

All internal positions are 0-based (line, column). Conversion to/from LSP's
0-based lines / 0-based UTF-16 columns happens at the server boundary only.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class DiodeSeverity(enum.Enum):
    """Diagnostic severity levels, mirroring LSP DiagnosticSeverity."""

    ERROR = 1
    WARNING = 2
    INFO = 3
    HINT = 4


class DiodeSymbolKind(enum.Enum):
    """Symbol kinds tracked by the index.

    Intentionally a small, SV-specific set — not the full LSP SymbolKind enum.
    Mapped to LSP SymbolKind at the server boundary.
    """

    MODULE = "module"
    INTERFACE = "interface"
    PACKAGE = "package"
    PORT = "port"
    PARAMETER = "parameter"
    LOCALPARAM = "localparam"
    SIGNAL = "signal"          # wire, logic, reg, var
    INSTANCE = "instance"      # module/interface instantiation
    FUNCTION = "function"
    TASK = "task"
    TYPEDEF = "typedef"
    ENUM_MEMBER = "enum_member"
    GENERATE = "generate"
    ALWAYS = "always"          # always, always_ff, always_comb, always_latch
    CLASS = "class"


# ---------------------------------------------------------------------------
# Position / Range / Location
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FilePosition:
    """A 0-based position within a file."""

    line: int
    column: int


@dataclass(frozen=True, slots=True)
class FileRange:
    """A range within a single file, defined by start and end positions."""

    start: FilePosition
    end: FilePosition


@dataclass(frozen=True, slots=True)
class FileLocation:
    """A range anchored to a specific file path."""

    path: Path
    range: FileRange


# ---------------------------------------------------------------------------
# Project configuration
# ---------------------------------------------------------------------------


class ProjectSourceKind(enum.Enum):
    """How a source file was discovered."""

    FILE_LIST = "file_list"       # from a .f file
    CONFIG = "config"             # from diode.toml files/globs
    AUTO_DISCOVER = "auto_discover"  # workspace scan fallback


@dataclass(frozen=True, slots=True)
class ProjectSource:
    """A single source file in the project, with its origin."""

    path: Path
    kind: ProjectSourceKind


@dataclass
class ProjectConfig:
    """Resolved project configuration.

    Built by project.py from diode.toml, .f files, or auto-discovery.
    Consumed by compiler.py to know what to compile.
    """

    source_files: list[ProjectSource] = field(default_factory=list)
    include_dirs: list[Path] = field(default_factory=list)
    defines: dict[str, str] = field(default_factory=dict)
    top_module: str | None = None
    config_path: Path | None = None  # path to diode.toml, if one was found


# ---------------------------------------------------------------------------
# Compilation results
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DiodeDiagnostic:
    """A single diagnostic from compilation.

    Stored in our own format; converted to LSP Diagnostic at the server boundary.
    """

    location: FileLocation
    severity: DiodeSeverity
    message: str
    code: str | None = None  # slang diagnostic code, e.g. "UnknownModule"


@dataclass
class CompilationResult:
    """Output of a single compilation pass.

    Holds the pyslang compilation object (for tree queries) plus extracted
    diagnostics. The compilation object is opaque to most modules — only
    index.py walks it to build the symbol table.
    """

    compilation: Any  # pyslang.Compilation — typed as Any to avoid import
    diagnostics: list[DiodeDiagnostic] = field(default_factory=list)
    source_files: list[Path] = field(default_factory=list)
    success: bool = True


# ---------------------------------------------------------------------------
# Symbol index
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SymbolInfo:
    """A symbol in the index.

    Represents a named entity in the design with its definition location,
    kind, and optional metadata. The index stores these and provides
    lookup by name, location, and kind.
    """

    name: str
    kind: DiodeSymbolKind
    definition: FileLocation
    parent_name: str | None = None   # enclosing module/package/interface name
    type_str: str | None = None      # human-readable type (e.g. "logic [7:0]")
    detail: str | None = None        # extra info for hover (port list, value, etc.)
    references: tuple[FileLocation, ...] = ()  # usage sites (populated by index)
