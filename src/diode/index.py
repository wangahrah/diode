"""Symbol index for diode.

Walks a pyslang compilation tree, extracts all declarations, and builds
lookup tables for go-to-definition, hover, find-references, and document
symbols. The index is an immutable snapshot -- server.py builds a new one
on each recompilation and atomically swaps.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

import pyslang

from diode.types import (
    CompilationResult,
    DiodeSymbolKind,
    FileLocation,
    FilePosition,
    FileRange,
    SymbolInfo,
)

logger = logging.getLogger(__name__)

_IDENT_RE = re.compile(r"[a-zA-Z0-9_]")

# Map pyslang ArgumentDirection to human-readable strings
_DIRECTION_MAP = {
    pyslang.ArgumentDirection.In: "input",
    pyslang.ArgumentDirection.Out: "output",
    pyslang.ArgumentDirection.InOut: "inout",
    pyslang.ArgumentDirection.Ref: "ref",
}

# Map pyslang ProceduralBlockKind to human-readable strings
_PROCEDURE_KIND_MAP = {
    pyslang.ProceduralBlockKind.Always: "always",
    pyslang.ProceduralBlockKind.AlwaysComb: "always_comb",
    pyslang.ProceduralBlockKind.AlwaysFF: "always_ff",
    pyslang.ProceduralBlockKind.AlwaysLatch: "always_latch",
}


class SymbolIndex:
    """Immutable symbol index built from a compilation result.

    Thread-safe for concurrent reads. Never mutated after construction --
    server.py builds a new index on each recompilation and atomically swaps.
    """

    def __init__(
        self,
        symbols_by_file: dict[Path, list[SymbolInfo]],
        symbols_by_name: dict[str, list[SymbolInfo]],
        references_by_name: dict[str, list[FileLocation]],
        source_lines: dict[Path, list[str]],
    ) -> None:
        self._symbols_by_file = symbols_by_file
        self._symbols_by_name = symbols_by_name
        self._references_by_name = references_by_name
        self._source_lines = source_lines

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
        resolved = path.resolve()
        file_symbols = self._symbols_by_file.get(resolved, [])

        # Step 1-3: Find symbols whose range contains the position
        candidates: list[SymbolInfo] = []
        for sym in file_symbols:
            r = sym.definition.range
            if _range_contains(r, position):
                candidates.append(sym)

        if candidates:
            # Pick the narrowest (most specific) range
            candidates.sort(key=lambda s: _range_size(s.definition.range))
            return candidates[0]

        # Step 4: Word-under-cursor fallback
        word = self._extract_word_at(resolved, position)
        if word:
            return self.find_definition(word, resolved)

        return None

    def find_definition(self, name: str, context_path: Path | None = None) -> SymbolInfo | None:
        """Find the definition of a symbol by name.

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
        candidates = self._symbols_by_name.get(name, [])
        if not candidates:
            return None

        if context_path is not None:
            resolved = context_path.resolve()
            # 1. Look for local definitions in the same file
            local = [s for s in candidates if s.definition.path.resolve() == resolved]
            if local:
                # Prefer non-module/non-package/non-instance definitions (local signals etc.)
                specific = [
                    s for s in local
                    if s.kind not in (
                        DiodeSymbolKind.MODULE,
                        DiodeSymbolKind.PACKAGE,
                        DiodeSymbolKind.INTERFACE,
                    )
                ]
                if specific:
                    return specific[0]
                return local[0]

        # 2/3. Return global definitions -- prefer modules/packages/interfaces
        global_defs = [
            s for s in candidates
            if s.kind in (
                DiodeSymbolKind.MODULE,
                DiodeSymbolKind.PACKAGE,
                DiodeSymbolKind.INTERFACE,
            )
        ]
        if global_defs:
            return global_defs[0]

        # Fall back to any match
        return candidates[0]

    def find_references(self, name: str) -> list[FileLocation]:
        """Find all references to a symbol by name.

        Returns all locations where the symbol name appears as a
        declaration or usage.

        Args:
            name: Symbol name to search for.

        Returns:
            List of FileLocation for all reference sites.
        """
        refs = list(self._references_by_name.get(name, []))

        # Also include declaration sites from symbols
        for sym in self._symbols_by_name.get(name, []):
            refs.append(sym.definition)

        return refs

    def get_document_symbols(self, path: Path) -> list[SymbolInfo]:
        """Get all symbols defined in a file, ordered by position.

        Used for textDocument/documentSymbol (outline view).
        Returns top-level and nested symbols (ports inside modules, etc.).

        Args:
            path: Absolute file path.

        Returns:
            List of SymbolInfo, ordered by definition position.
        """
        resolved = path.resolve()
        symbols = self._symbols_by_file.get(resolved, [])
        return sorted(symbols, key=lambda s: (s.definition.range.start.line, s.definition.range.start.column))

    def _extract_word_at(self, path: Path, position: FilePosition) -> str | None:
        """Extract the identifier word at the given position.

        Reads the source line and scans left/right from the column
        for [a-zA-Z0-9_] characters.
        """
        lines = self._source_lines.get(path, [])
        if position.line >= len(lines):
            return None

        line = lines[position.line]
        col = position.column
        if col >= len(line) or not _IDENT_RE.match(line[col]):
            return None

        # Scan left
        start = col
        while start > 0 and _IDENT_RE.match(line[start - 1]):
            start -= 1

        # Scan right
        end = col
        while end < len(line) and _IDENT_RE.match(line[end]):
            end += 1

        word = line[start:end]
        return word if word else None


def build_index(result: CompilationResult) -> SymbolIndex:
    """Build a symbol index from a compilation result.

    Walks the pyslang compilation tree (result.compilation.getRoot()),
    extracts all declarations, and records their locations and metadata.

    Args:
        result: Successful CompilationResult with a valid compilation object.

    Returns:
        SymbolIndex populated with all discovered symbols.
    """
    builder = _IndexBuilder(result.compilation)
    builder.walk()
    return SymbolIndex(
        symbols_by_file=dict(builder.symbols_by_file),
        symbols_by_name=dict(builder.symbols_by_name),
        references_by_name=dict(builder.references_by_name),
        source_lines=builder.source_lines,
    )


class _IndexBuilder:
    """Internal builder that walks the pyslang tree and collects symbols."""

    def __init__(self, compilation: Any) -> None:
        self._compilation = compilation
        self._sm: Any = compilation.sourceManager
        self._no_location = pyslang.SourceLocation.NoLocation

        self.symbols_by_file: dict[Path, list[SymbolInfo]] = defaultdict(list)
        self.symbols_by_name: dict[str, list[SymbolInfo]] = defaultdict(list)
        self.references_by_name: dict[str, list[FileLocation]] = defaultdict(list)
        self.source_lines: dict[Path, list[str]] = {}

        # Track which port names exist in the current module body,
        # so we skip the duplicate VariableSymbol that pyslang creates
        # for each port's internal storage.
        self._current_port_names: set[str] = set()

        # Track which module definitions we've fully walked to avoid
        # infinite recursion and duplicate indexing.
        self._walked_modules: set[str] = set()

        # Build source line cache from all buffers
        self._build_source_line_cache()

    def _build_source_line_cache(self) -> None:
        """Cache source file lines for word-under-cursor extraction."""
        for buf in self._sm.getAllBuffers():
            try:
                text = self._sm.getSourceText(buf)
                path = Path(self._sm.getFullPath(buf))
                self.source_lines[path] = text.split("\n")
            except Exception:
                logger.debug("Failed to read source text for a buffer")

    def walk(self) -> None:
        """Walk the entire compilation, extracting symbols."""
        root = self._compilation.getRoot()

        # Walk packages
        for pkg in self._compilation.getPackages():
            if pkg.name == "std":
                continue
            self._walk_package(pkg)

        # Walk top instances (modules/interfaces)
        for instance in root.topInstances:
            self._walked_modules.add(instance.definition.name)
            self._walk_instance(instance, parent_name=None)

        # Also walk non-top-level definitions so they appear in the index.
        # These are modules that are only instantiated (never a top instance)
        # but weren't reached via instance recursion.
        for defn in self._compilation.getDefinitions():
            if defn.name not in self._walked_modules:
                self._walk_definition(defn)

    def _get_location(self, sym: Any) -> FileLocation | None:
        """Extract FileLocation from a pyslang symbol's location."""
        try:
            loc = sym.location
            if loc == self._no_location:
                return None

            line = self._sm.getLineNumber(loc) - 1  # convert to 0-based
            col = self._sm.getColumnNumber(loc) - 1

            buf_id = loc.buffer
            try:
                full_path = self._sm.getFullPath(buf_id)
                file_path = Path(full_path)
            except Exception:
                fname = self._sm.getFileName(loc)
                file_path = Path(fname) if fname else None
                if file_path is None:
                    return None

            start = FilePosition(line=max(0, line), column=max(0, col))

            # Try to get end position from syntax
            end = self._get_syntax_end(sym, start)

            return FileLocation(
                path=file_path,
                range=FileRange(start=start, end=end),
            )
        except Exception:
            logger.debug(f"Failed to get location for symbol: {getattr(sym, 'name', '?')}")
            return None

    def _get_syntax_end(self, sym: Any, start: FilePosition) -> FilePosition:
        """Get the end position from a symbol's syntax node, or fall back to
        estimating from the symbol name."""
        try:
            syntax = sym.syntax
            if syntax is not None:
                sr = syntax.sourceRange
                end_line = self._sm.getLineNumber(sr.end) - 1
                end_col = self._sm.getColumnNumber(sr.end) - 1
                return FilePosition(line=max(0, end_line), column=max(0, end_col))
        except Exception:
            pass

        # Fallback: use the name length to estimate end column
        name = getattr(sym, "name", "")
        if name:
            return FilePosition(line=start.line, column=start.column + len(name))
        return start

    def _add_symbol(self, info: SymbolInfo) -> None:
        """Register a symbol in all lookup tables."""
        self.symbols_by_file[info.definition.path].append(info)
        if info.name:
            self.symbols_by_name[info.name].append(info)

    def _add_reference(self, name: str, location: FileLocation) -> None:
        """Register a reference location for a given symbol name."""
        self.references_by_name[name].append(location)

    # --- Walk functions for different symbol types ---

    def _walk_definition(self, defn: Any) -> None:
        """Walk a DefinitionSymbol that is not a top instance.

        Registers the module/interface definition so find_definition works
        for non-top-level modules. We extract just the definition-level
        symbol (not its body, since that requires an instantiation context
        which we may not have for uninstantiated modules).
        """
        location = self._get_location(defn)
        if location is None:
            return

        if hasattr(defn, "definitionKind"):
            if defn.definitionKind == pyslang.DefinitionKind.Interface:
                kind = DiodeSymbolKind.INTERFACE
            else:
                kind = DiodeSymbolKind.MODULE
        else:
            kind = DiodeSymbolKind.MODULE

        info = SymbolInfo(
            name=defn.name,
            kind=kind,
            definition=location,
            parent_name=None,
            type_str=None,
            detail=None,
        )
        self._add_symbol(info)

    def _walk_package(self, pkg: Any) -> None:
        """Walk a package symbol and extract its members."""
        location = self._get_location(pkg)
        if location is None:
            return

        # Build detail string listing package members
        members: list[str] = []
        pkg_members = list(pkg)
        for sym in pkg_members:
            name = getattr(sym, "name", "")
            if name:
                members.append(name)

        detail = ", ".join(members) if members else None

        pkg_info = SymbolInfo(
            name=pkg.name,
            kind=DiodeSymbolKind.PACKAGE,
            definition=location,
            parent_name=None,
            type_str=None,
            detail=detail,
        )
        self._add_symbol(pkg_info)

        # Walk package members
        for sym in pkg_members:
            self._walk_package_member(sym, parent_name=pkg.name)

    def _walk_package_member(self, sym: Any, parent_name: str) -> None:
        """Walk a single member of a package."""
        if isinstance(sym, pyslang.ParameterSymbol):
            self._extract_parameter(sym, parent_name)
        elif isinstance(sym, pyslang.TypeAliasType):
            self._extract_typedef(sym, parent_name)
        elif isinstance(sym, pyslang.TransparentMemberSymbol):
            self._extract_enum_member(sym, parent_name)
        elif isinstance(sym, pyslang.SubroutineSymbol):
            self._extract_subroutine(sym, parent_name)
        elif isinstance(sym, pyslang.VariableSymbol):
            # Skip return-value variables for functions (same location as subroutine)
            pass
        elif isinstance(sym, pyslang.FormalArgumentSymbol):
            # Skip formal arguments (they belong to subroutines)
            pass

    def _walk_instance(self, instance: Any, parent_name: str | None) -> None:
        """Walk a module/interface instance and all its children."""
        body = instance.body
        defn = instance.definition

        # Determine the symbol kind based on definition
        if hasattr(defn, "definitionKind"):
            if defn.definitionKind == pyslang.DefinitionKind.Interface:
                kind = DiodeSymbolKind.INTERFACE
            else:
                kind = DiodeSymbolKind.MODULE
        else:
            kind = DiodeSymbolKind.MODULE

        # Use the definition's location (where the module is declared)
        location = self._get_location(defn)
        if location is None:
            location = self._get_location(body)
        if location is None:
            return

        module_name = defn.name

        # Build port list detail string
        port_strs: list[str] = []
        for port in body.portList:
            dir_str = _DIRECTION_MAP.get(port.direction, "")
            type_str = str(port.type)
            port_strs.append(f"{dir_str} {type_str} {port.name}")

        # Build parameter list
        param_strs: list[str] = []
        for param in body.parameters:
            val = getattr(param, "value", "")
            param_strs.append(f"{param.name} = {val}")

        detail_parts: list[str] = []
        if param_strs:
            detail_parts.append(f"#({', '.join(param_strs)})")
        if port_strs:
            detail_parts.append(f"({', '.join(port_strs)})")

        detail = " ".join(detail_parts) if detail_parts else None

        module_info = SymbolInfo(
            name=module_name,
            kind=kind,
            definition=location,
            parent_name=parent_name,
            type_str=None,
            detail=detail,
        )
        self._add_symbol(module_info)

        # Track port names for this module
        self._current_port_names = {p.name for p in body.portList}

        # Walk ports
        for port in body.portList:
            self._extract_port(port, parent_name=module_name)

        # Walk parameters
        for param in body.parameters:
            self._extract_parameter(param, parent_name=module_name)

        # Walk body members using direct iteration (no recursion into sub-instances)
        for sym in body:
            if isinstance(sym, pyslang.PortSymbol):
                continue  # already walked above via body.portList
            if isinstance(sym, pyslang.ParameterSymbol):
                continue  # already walked above via body.parameters
            self._walk_body_member(sym, parent_name=module_name)

        # Clear port names
        self._current_port_names = set()

    def _walk_body_member(self, sym: Any, parent_name: str) -> None:
        """Walk a single member of a module/interface body."""
        if isinstance(sym, pyslang.VariableSymbol):
            # Skip port-internal variables
            if sym.name in self._current_port_names:
                return
            self._extract_signal(sym, parent_name)
        elif isinstance(sym, pyslang.NetSymbol):
            if sym.name in self._current_port_names:
                return
            self._extract_signal(sym, parent_name)
        elif isinstance(sym, pyslang.InstanceSymbol):
            self._extract_instance(sym, parent_name)
        elif isinstance(sym, pyslang.ProceduralBlockSymbol):
            self._extract_procedural_block(sym, parent_name)
        elif isinstance(sym, pyslang.GenerateBlockArraySymbol):
            self._extract_generate_block(sym, parent_name)
        elif isinstance(sym, pyslang.GenerateBlockSymbol):
            # Individual generate block iterations -- skip duplicates
            # (they're walked via GenerateBlockArraySymbol)
            pass
        elif isinstance(sym, pyslang.SubroutineSymbol):
            self._extract_subroutine(sym, parent_name)
        elif isinstance(sym, pyslang.TypeAliasType):
            self._extract_typedef(sym, parent_name)
        elif isinstance(sym, pyslang.TransparentMemberSymbol):
            self._extract_enum_member(sym, parent_name)
        elif isinstance(sym, pyslang.ContinuousAssignSymbol):
            # Continuous assignments (assign statements) -- no named symbol
            pass
        elif isinstance(sym, pyslang.GenvarSymbol):
            # Genvars are internal to generate blocks
            pass

    # --- Extraction functions for specific symbol types ---

    def _extract_port(self, port: Any, parent_name: str) -> None:
        """Extract a port declaration as SymbolInfo."""
        location = self._get_location(port)
        if location is None:
            return

        dir_str = _DIRECTION_MAP.get(port.direction, "")
        type_str = f"{dir_str} {port.type}" if dir_str else str(port.type)

        info = SymbolInfo(
            name=port.name,
            kind=DiodeSymbolKind.PORT,
            definition=location,
            parent_name=parent_name,
            type_str=type_str,
            detail=None,
        )
        self._add_symbol(info)

    def _extract_parameter(self, param: Any, parent_name: str) -> None:
        """Extract a parameter/localparam declaration as SymbolInfo."""
        location = self._get_location(param)
        if location is None:
            return

        is_local = getattr(param, "isLocalParam", False)
        kind = DiodeSymbolKind.LOCALPARAM if is_local else DiodeSymbolKind.PARAMETER

        type_str = str(param.type) if hasattr(param, "type") else None
        value = str(getattr(param, "value", ""))
        detail = f"{type_str} = {value}" if type_str and value else value or type_str

        info = SymbolInfo(
            name=param.name,
            kind=kind,
            definition=location,
            parent_name=parent_name,
            type_str=type_str,
            detail=detail,
        )
        self._add_symbol(info)

    def _extract_signal(self, sym: Any, parent_name: str) -> None:
        """Extract a variable/net declaration as SymbolInfo."""
        location = self._get_location(sym)
        if location is None:
            return

        type_str = str(sym.type) if hasattr(sym, "type") else None

        info = SymbolInfo(
            name=sym.name,
            kind=DiodeSymbolKind.SIGNAL,
            definition=location,
            parent_name=parent_name,
            type_str=type_str,
            detail=None,
        )
        self._add_symbol(info)

    def _extract_instance(self, instance: Any, parent_name: str) -> None:
        """Extract a module instantiation as SymbolInfo."""
        location = self._get_location(instance)
        if location is None:
            return

        module_name = instance.definition.name

        info = SymbolInfo(
            name=instance.name,
            kind=DiodeSymbolKind.INSTANCE,
            definition=location,
            parent_name=parent_name,
            type_str=None,
            detail=module_name,
        )
        self._add_symbol(info)

        # Record a reference to the instantiated module
        self._add_reference(module_name, location)

        # Recurse into the sub-instance to index its body symbols.
        # This ensures cross-file modules have their ports and signals indexed.
        if module_name not in self._walked_modules:
            self._walked_modules.add(module_name)
            self._walk_instance(instance, parent_name=None)

    def _extract_procedural_block(self, sym: Any, parent_name: str) -> None:
        """Extract an always/always_ff/always_comb block as SymbolInfo."""
        location = self._get_location(sym)
        if location is None:
            return

        proc_kind = getattr(sym, "procedureKind", None)
        detail = _PROCEDURE_KIND_MAP.get(proc_kind, "always")

        # Only index always-type blocks, not initial/final
        if proc_kind not in _PROCEDURE_KIND_MAP:
            return

        info = SymbolInfo(
            name=detail,
            kind=DiodeSymbolKind.ALWAYS,
            definition=location,
            parent_name=parent_name,
            type_str=None,
            detail=detail,
        )
        self._add_symbol(info)

    def _extract_generate_block(self, sym: Any, parent_name: str) -> None:
        """Extract a generate block as SymbolInfo."""
        location = self._get_location(sym)
        if location is None:
            return

        name = sym.name if sym.name else "generate"

        info = SymbolInfo(
            name=name,
            kind=DiodeSymbolKind.GENERATE,
            definition=location,
            parent_name=parent_name,
            type_str=None,
            detail=None,
        )
        self._add_symbol(info)

    def _extract_subroutine(self, sym: Any, parent_name: str) -> None:
        """Extract a function/task declaration as SymbolInfo."""
        location = self._get_location(sym)
        if location is None:
            return

        sub_kind = getattr(sym, "subroutineKind", None)
        if sub_kind == pyslang.SubroutineKind.Task:
            kind = DiodeSymbolKind.TASK
        else:
            kind = DiodeSymbolKind.FUNCTION

        # Build signature string
        return_type = str(sym.returnType) if hasattr(sym, "returnType") else "void"
        args: list[str] = []
        if hasattr(sym, "arguments"):
            for arg in sym.arguments:
                dir_str = _DIRECTION_MAP.get(arg.direction, "")
                arg_type = str(arg.type) if hasattr(arg, "type") else ""
                args.append(f"{dir_str} {arg_type} {arg.name}".strip())

        keyword = "task" if kind == DiodeSymbolKind.TASK else "function"
        signature = f"{keyword} {return_type} {sym.name}({', '.join(args)})"

        info = SymbolInfo(
            name=sym.name,
            kind=kind,
            definition=location,
            parent_name=parent_name,
            type_str=return_type,
            detail=signature,
        )
        self._add_symbol(info)

    def _extract_typedef(self, sym: Any, parent_name: str) -> None:
        """Extract a typedef declaration as SymbolInfo."""
        location = self._get_location(sym)
        if location is None:
            return

        # Get the canonical (resolved) type
        canonical = str(sym.canonicalType) if hasattr(sym, "canonicalType") else None

        info = SymbolInfo(
            name=sym.name,
            kind=DiodeSymbolKind.TYPEDEF,
            definition=location,
            parent_name=parent_name,
            type_str=canonical,
            detail=canonical,
        )
        self._add_symbol(info)

    def _extract_enum_member(self, sym: Any, parent_name: str) -> None:
        """Extract an enum member (via TransparentMemberSymbol) as SymbolInfo."""
        location = self._get_location(sym)
        if location is None:
            return

        # Get the actual EnumValue from the wrapped symbol
        wrapped = getattr(sym, "wrapped", None)
        value_str = None
        type_str = None
        if wrapped is not None:
            value_str = str(getattr(wrapped, "value", ""))
            type_str = str(wrapped.type) if hasattr(wrapped, "type") else None

        info = SymbolInfo(
            name=sym.name,
            kind=DiodeSymbolKind.ENUM_MEMBER,
            definition=location,
            parent_name=parent_name,
            type_str=type_str,
            detail=value_str,
        )
        self._add_symbol(info)


# --- Helper functions ---


def _range_contains(r: FileRange, pos: FilePosition) -> bool:
    """Check if a FileRange contains a FilePosition."""
    if pos.line < r.start.line or pos.line > r.end.line:
        return False
    if pos.line == r.start.line and pos.column < r.start.column:
        return False
    if pos.line == r.end.line and pos.column > r.end.column:
        return False
    return True


def _range_size(r: FileRange) -> tuple[int, int]:
    """Return a sortable size for a range (line span, column span).

    Smaller ranges sort first, allowing us to pick the most specific match.
    """
    line_span = r.end.line - r.start.line
    if line_span == 0:
        col_span = r.end.column - r.start.column
    else:
        col_span = 0
    return (line_span, col_span)
