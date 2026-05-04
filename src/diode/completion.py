"""Completion engine for diode.

Detects completion context from trigger character and surrounding text,
resolves the enclosing pyslang scope, and generates completion candidates
by querying the live Compilation object.

This module imports only from types.py and pyslang — no pygls/lsprotocol.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, NamedTuple

import pyslang

from diode.types import (
    CompletionContextKind,
    CompletionItem,
    CompletionItemKind,
    FilePosition,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal data structures
# ---------------------------------------------------------------------------

_IDENT_RE = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]*")


class _CompletionContext(NamedTuple):
    """Result of context detection. Internal to completion.py."""

    kind: CompletionContextKind
    prefix: str  # text typed after trigger (for filtering)
    qualifier: str  # package name, struct variable, etc. (empty if N/A)


# ---------------------------------------------------------------------------
# Curated system tasks list
# ---------------------------------------------------------------------------

_SYSTEM_TASKS: dict[str, str] = {
    "display": "Display formatted message",
    "write": "Write formatted message (no newline)",
    "monitor": "Monitor signal changes",
    "strobe": "Display at end of time step",
    "finish": "End simulation",
    "stop": "Pause simulation",
    "fatal": "Report fatal error",
    "error": "Report error",
    "warning": "Report warning",
    "info": "Report information",
    "time": "Current simulation time",
    "realtime": "Current simulation time (real)",
    "stime": "Current simulation time (short)",
    "random": "Random number generator",
    "urandom": "Unsigned random number",
    "urandom_range": "Unsigned random in range",
    "clog2": "Ceiling log base 2",
    "bits": "Number of bits",
    "size": "Array size",
    "left": "Left bound of range",
    "right": "Right bound of range",
    "high": "High bound of range",
    "low": "Low bound of range",
    "signed": "Cast to signed",
    "unsigned": "Cast to unsigned",
    "countones": "Count ones in bit vector",
    "onehot": "Check one-hot encoding",
    "onehot0": "Check one-hot or zero encoding",
    "isunknown": "Check for unknown bits",
    "readmemh": "Read hex memory file",
    "readmemb": "Read binary memory file",
    "writememh": "Write hex memory file",
    "writememb": "Write binary memory file",
    "fopen": "Open file",
    "fclose": "Close file",
    "fwrite": "Write to file",
    "fdisplay": "Display to file",
    "fscanf": "Scan from file",
    "feof": "End of file check",
    "sformat": "Format to string",
    "sformatf": "Format to string (function)",
    "value$plusargs": "Get plusarg value",
    "test$plusargs": "Test plusarg existence",
    "cast": "Dynamic type cast",
    "typename": "Get type name string",
    "assertoff": "Disable assertions",
    "asserton": "Enable assertions",
    "assertkill": "Kill assertions",
}

# pyslang ArgumentDirection map (same as index.py)
_DIRECTION_MAP = {
    pyslang.ArgumentDirection.In: "input",
    pyslang.ArgumentDirection.Out: "output",
    pyslang.ArgumentDirection.InOut: "inout",
    pyslang.ArgumentDirection.Ref: "ref",
}


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def get_completions(
    compilation: Any,  # pyslang.Compilation
    index: Any,  # SymbolIndex
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
        compilation: The current pyslang Compilation object.
        index: The current SymbolIndex.
        path: The file being edited (absolute path).
        position: Cursor position (0-based line/column).
        trigger_char: The character that triggered completion, or None.
        source_lines: Cached source text lines per file.

    Returns:
        List of CompletionItem, sorted by sort_group then label.
        Empty list if no completions are available.
    """
    if compilation is None:
        return []

    ctx = _detect_context(source_lines, path, position, trigger_char)

    try:
        if ctx.kind == CompletionContextKind.SYSTEM_TASK:
            items = _complete_system_tasks(ctx.prefix)
        elif ctx.kind == CompletionContextKind.PACKAGE_MEMBER:
            items = _complete_package_members(compilation, ctx.qualifier)
        elif ctx.kind == CompletionContextKind.DOT:
            scope = _find_scope_at_position(compilation, path, position)
            items = _complete_dot_members(scope, compilation, ctx.qualifier)
        elif ctx.kind == CompletionContextKind.PORT_CONNECTION:
            items = _complete_port_connections(compilation, path, position)
        elif ctx.kind == CompletionContextKind.PARAM_OVERRIDE:
            items = _complete_param_overrides(compilation, path, position)
        elif ctx.kind == CompletionContextKind.MODULE_NAME:
            items = _complete_module_names(compilation)
        else:
            # IDENTIFIER context
            scope = _find_scope_at_position(compilation, path, position)
            if scope is not None:
                items = _complete_identifiers(scope, compilation)
            else:
                items = _complete_module_names(compilation)
    except Exception:
        logger.debug("Completion generation failed", exc_info=True)
        items = []

    # Filter by prefix if present
    if ctx.prefix:
        prefix_lower = ctx.prefix.lower()
        items = [i for i in items if i.label.lower().startswith(prefix_lower)]

    # Sort by sort_group then label
    items.sort(key=lambda i: (i.sort_group, i.label.lower()))
    return items


# ---------------------------------------------------------------------------
# Context detection
# ---------------------------------------------------------------------------


def _detect_context(
    source_lines: dict[Path, list[str]],
    path: Path,
    position: FilePosition,
    trigger_char: str | None,
) -> _CompletionContext:
    """Analyze the trigger character and surrounding text to determine context.

    Uses backward text scanning on the current line — does NOT parse
    SystemVerilog grammar.
    """
    # Get the current line text
    resolved = path.resolve()
    lines = source_lines.get(resolved, [])
    if not lines:
        # Try unresolved path too
        lines = source_lines.get(path, [])

    if position.line >= len(lines):
        return _CompletionContext(CompletionContextKind.IDENTIFIER, "", "")

    line = lines[position.line]
    col = position.column
    pre_text = line[:col]

    # Step 2: trigger_char == '$'
    if trigger_char == "$":
        # Extract any text typed after '$'
        prefix = ""
        return _CompletionContext(CompletionContextKind.SYSTEM_TASK, prefix, "")

    # Step 3: trigger_char == ':'
    if trigger_char == ":":
        # Check if pre_text ends with '<ident>::'
        m = re.search(r"(\w+)::\s*$", pre_text)
        if m:
            qualifier = m.group(1)
            return _CompletionContext(
                CompletionContextKind.PACKAGE_MEMBER, "", qualifier
            )
        return _CompletionContext(CompletionContextKind.IDENTIFIER, "", "")

    # Step 4: trigger_char == '.'
    if trigger_char == ".":
        # Check if this is a port connection context
        # Look for pattern: we're inside an instance port list
        if _is_port_connection_context(pre_text, lines, position.line):
            return _CompletionContext(CompletionContextKind.PORT_CONNECTION, "", "")

        # Otherwise it's a dot member access — find the qualifier
        m = re.search(r"(\w+)\.\s*$", pre_text)
        qualifier = m.group(1) if m else ""
        return _CompletionContext(CompletionContextKind.DOT, "", qualifier)

    # Step 5: trigger_char is None (manual invoke)
    if trigger_char is None:
        # Extract partial word at cursor
        prefix = _extract_prefix(line, col)

        # Check for '$' prefix (system task with partial text)
        text_before_prefix = pre_text[: len(pre_text) - len(prefix)] if prefix else pre_text
        if text_before_prefix.rstrip().endswith("$"):
            return _CompletionContext(CompletionContextKind.SYSTEM_TASK, prefix, "")

        # Check for '::' (package member with partial text)
        m = re.search(r"(\w+)::\s*$", text_before_prefix)
        if m:
            return _CompletionContext(
                CompletionContextKind.PACKAGE_MEMBER, prefix, m.group(1)
            )

        # Check for '.' (dot member with partial text)
        m = re.search(r"(\w+)\.\s*$", text_before_prefix)
        if m:
            # Could be port connection or dot member
            if _is_port_connection_context(text_before_prefix, lines, position.line):
                return _CompletionContext(
                    CompletionContextKind.PORT_CONNECTION, prefix, ""
                )
            return _CompletionContext(
                CompletionContextKind.DOT, prefix, m.group(1)
            )

        # Check for '#(' param override context
        if _is_param_override_context(pre_text, lines, position.line):
            return _CompletionContext(
                CompletionContextKind.PARAM_OVERRIDE, prefix, ""
            )

        # Check for port connection context (inside instance port list)
        if _is_port_connection_context(pre_text, lines, position.line):
            return _CompletionContext(
                CompletionContextKind.PORT_CONNECTION, prefix, ""
            )

        # Default: IDENTIFIER
        return _CompletionContext(CompletionContextKind.IDENTIFIER, prefix, "")

    # Default fallback
    return _CompletionContext(CompletionContextKind.IDENTIFIER, "", "")


def _extract_prefix(line: str, col: int) -> str:
    """Extract the partial identifier before the cursor position."""
    if col <= 0 or col > len(line):
        return ""

    end = col
    start = col
    while start > 0 and re.match(r"[a-zA-Z0-9_]", line[start - 1]):
        start -= 1

    return line[start:end]


def _is_port_connection_context(
    pre_text: str, lines: list[str], current_line: int
) -> bool:
    """Determine if we're inside an instance port connection list.

    Scans backward through lines looking for the instance pattern.
    Port connection context is indicated by:
    - Being inside parentheses after an identifier (instance name)
    - Seeing '.port_name(' patterns
    """
    # Scan backward from the current position tracking paren nesting
    paren_depth = 0
    # Check current line first
    for ch in reversed(pre_text):
        if ch == ")":
            paren_depth += 1
        elif ch == "(":
            if paren_depth > 0:
                paren_depth -= 1
            else:
                # We're at an unmatched '(' - check what precedes it
                # If preceded by an identifier and possibly a dot pattern,
                # this is likely a port connection
                break
        elif ch == ";" or ch == "{":
            return False

    # Look for '.identifier(' or '.identifier' patterns in preceding text
    # and the opening '(' of the instance port list
    combined = pre_text
    # Also look at preceding lines (up to 50 lines back)
    for look_back in range(1, min(51, current_line + 1)):
        prev_line = lines[current_line - look_back]
        combined = prev_line + "\n" + combined

    # Check if we find a named port connection pattern
    # Pattern: identifier identifier ( .port ... ) — this is an instance
    # The key signal is '.identifier(' inside balanced parens
    has_dot_port = re.search(r"\.\s*\w+\s*\(", combined) is not None

    # Also check we're inside parentheses of an instantiation
    # by finding an unmatched '('
    depth = 0
    for ch in reversed(combined):
        if ch == ")":
            depth += 1
        elif ch == "(":
            if depth > 0:
                depth -= 1
            else:
                # Found unmatched '(' — we're inside parens
                if has_dot_port:
                    return True
                return False
        elif ch == ";":
            return False

    return False


def _is_param_override_context(
    pre_text: str, lines: list[str], current_line: int
) -> bool:
    """Determine if we're inside a #() parameter override list."""
    # Scan backward for '#(' pattern
    combined = pre_text
    for look_back in range(1, min(21, current_line + 1)):
        combined = lines[current_line - look_back] + "\n" + combined

    depth = 0
    for i in range(len(combined) - 1, -1, -1):
        ch = combined[i]
        if ch == ")":
            depth += 1
        elif ch == "(":
            if depth > 0:
                depth -= 1
            else:
                # Found unmatched '(' — check if preceded by '#'
                if i > 0 and combined[i - 1] == "#":
                    return True
                return False
        elif ch == ";":
            return False

    return False


# ---------------------------------------------------------------------------
# Scope walking
# ---------------------------------------------------------------------------


def _find_scope_at_position(
    compilation: Any,
    path: Path,
    position: FilePosition,
) -> Any | None:
    """Walk the semantic tree to find the deepest scope containing the cursor.

    Returns the deepest pyslang Scope (InstanceBodySymbol, PackageSymbol,
    StatementBlockSymbol, etc.) that contains the given position.
    """
    sm = compilation.sourceManager
    target_line = position.line + 1  # pyslang is 1-based
    target_col = position.column + 1
    resolved_path = path.resolve()

    def _source_range_contains(sym: Any, line: int, col: int) -> bool:
        """Check if a symbol's syntax.sourceRange contains line:col."""
        try:
            syntax = sym.syntax
            if syntax is None:
                return False
            sr = syntax.sourceRange
            start_line = sm.getLineNumber(sr.start)
            start_col = sm.getColumnNumber(sr.start)
            end_line = sm.getLineNumber(sr.end)
            end_col = sm.getColumnNumber(sr.end)

            # Check the file matches
            try:
                start_path = Path(sm.getFullPath(sr.start.buffer)).resolve()
                if start_path != resolved_path:
                    return False
            except Exception:
                return False

            if (start_line, start_col) <= (line, col) <= (end_line, end_col):
                return True
            return False
        except Exception:
            return False

    def _find_deepest(scope: Any) -> Any:
        """Recurse into child scopes to find the deepest match."""
        best = scope
        try:
            for member in scope:
                # Check InstanceSymbol.body
                if isinstance(member, pyslang.InstanceSymbol):
                    body = member.body
                    if _source_range_contains(body, target_line, target_col):
                        deeper = _find_deepest(body)
                        if deeper is not None:
                            best = deeper
                # Check StatementBlockSymbol (procedural scopes)
                elif isinstance(member, pyslang.StatementBlockSymbol):
                    if _source_range_contains(member, target_line, target_col):
                        deeper = _find_deepest(member)
                        if deeper is not None:
                            best = deeper
                # Check ProceduralBlockSymbol — may contain nested scopes
                elif isinstance(member, pyslang.ProceduralBlockSymbol):
                    if _source_range_contains(member, target_line, target_col):
                        # The procedural block itself isn't a scope we iterate,
                        # but we keep it as the best scope context
                        best = scope  # stay at parent scope (module body)
        except Exception:
            pass
        return best

    # Check top instances
    for inst in compilation.getRoot().topInstances:
        body = inst.body
        if _source_range_contains(body, target_line, target_col):
            return _find_deepest(body)

    # Check packages
    for pkg in compilation.getPackages():
        if pkg.name == "std":
            continue
        if _source_range_contains(pkg, target_line, target_col):
            return _find_deepest(pkg)

    return None


def _find_instance_at_position(
    compilation: Any,
    path: Path,
    position: FilePosition,
) -> Any | None:
    """Find the module instance surrounding the cursor position.

    Used for PORT_CONNECTION and PARAM_OVERRIDE contexts, where we need
    to know which instance's ports/params to complete.
    """
    sm = compilation.sourceManager
    target_line = position.line + 1
    target_col = position.column + 1
    resolved_path = path.resolve()

    def _syntax_contains(sym: Any) -> bool:
        try:
            syntax = sym.syntax
            if syntax is None:
                return False
            sr = syntax.sourceRange
            start_line = sm.getLineNumber(sr.start)
            start_col = sm.getColumnNumber(sr.start)
            end_line = sm.getLineNumber(sr.end)
            end_col = sm.getColumnNumber(sr.end)
            try:
                start_path = Path(sm.getFullPath(sr.start.buffer)).resolve()
                if start_path != resolved_path:
                    return False
            except Exception:
                return False
            return (start_line, start_col) <= (target_line, target_col) <= (
                end_line,
                end_col,
            )
        except Exception:
            return False

    # Walk top instances
    for inst in compilation.getRoot().topInstances:
        body = inst.body
        try:
            for member in body:
                if isinstance(member, pyslang.InstanceSymbol):
                    if _syntax_contains(member):
                        return member
        except Exception:
            pass

    return None


# ---------------------------------------------------------------------------
# Candidate generators
# ---------------------------------------------------------------------------


def _complete_identifiers(
    scope: Any,
    compilation: Any,
) -> list[CompletionItem]:
    """Generate completions for in-scope identifiers.

    Walks the scope hierarchy from the deepest scope upward.
    """
    items: list[CompletionItem] = []
    seen_names: set[str] = set()

    def _add_members(s: Any, sort_group: int) -> None:
        try:
            for member in s:
                name = getattr(member, "name", "")
                if not name or name in seen_names:
                    continue
                # Skip compiler-generated symbols
                if name.startswith("$"):
                    continue
                seen_names.add(name)
                kind = _map_pyslang_to_completion_kind(member)
                detail = _get_type_detail(member)
                items.append(
                    CompletionItem(
                        label=name,
                        kind=kind,
                        detail=detail,
                        sort_group=sort_group,
                    )
                )
        except Exception:
            pass

    # Current scope members (sort_group=0)
    _add_members(scope, sort_group=0)

    # Walk parent scopes (sort_group=1)
    current = scope
    while True:
        parent = getattr(current, "parentScope", None)
        if parent is None:
            break
        _add_members(parent, sort_group=1)
        current = parent

    return items


def _complete_module_names(compilation: Any) -> list[CompletionItem]:
    """Generate completions for module/interface names.

    Calls compilation.getDefinitions() and returns each as a CompletionItem.
    """
    items: list[CompletionItem] = []
    try:
        for defn in compilation.getDefinitions():
            name = defn.name
            if not name:
                continue
            # Determine kind
            if hasattr(defn, "definitionKind"):
                if defn.definitionKind == pyslang.DefinitionKind.Interface:
                    kind = CompletionItemKind.INTERFACE
                else:
                    kind = CompletionItemKind.MODULE
            else:
                kind = CompletionItemKind.MODULE

            items.append(
                CompletionItem(
                    label=name,
                    kind=kind,
                    detail=None,
                    sort_group=0,
                )
            )
    except Exception:
        logger.debug("Failed to enumerate definitions", exc_info=True)

    return items


def _complete_port_connections(
    compilation: Any,
    path: Path,
    position: FilePosition,
) -> list[CompletionItem]:
    """Generate completions for port connections in instantiation.

    Finds the instance, gets all ports, excludes already-connected ones.
    """
    instance = _find_instance_at_position(compilation, path, position)
    if instance is None:
        return []

    items: list[CompletionItem] = []
    try:
        sub_body = instance.body

        # Get already-connected port names
        connected: set[str] = set()
        try:
            for pc in instance.portConnections:
                port = pc.port
                connected.add(port.name)
        except Exception:
            pass

        # Generate items for unconnected ports
        for port in sub_body.portList:
            if port.name in connected:
                continue
            dir_str = _DIRECTION_MAP.get(port.direction, "")
            type_str = str(port.type)
            detail = f"{dir_str} {type_str}".strip()

            items.append(
                CompletionItem(
                    label=port.name,
                    kind=CompletionItemKind.PORT,
                    detail=detail,
                    insert_text=f".{port.name}()",
                    sort_group=0,
                )
            )
    except Exception:
        logger.debug("Failed port connection completion", exc_info=True)

    return items


def _complete_param_overrides(
    compilation: Any,
    path: Path,
    position: FilePosition,
) -> list[CompletionItem]:
    """Generate completions for parameter overrides in #().

    Finds the instance and returns its parameters.
    """
    instance = _find_instance_at_position(compilation, path, position)
    if instance is None:
        return []

    items: list[CompletionItem] = []
    try:
        sub_body = instance.body
        for param in sub_body.parameters:
            type_str = str(param.type) if hasattr(param, "type") else None
            value = str(getattr(param, "value", ""))
            detail = f"{type_str} = {value}" if type_str and value else type_str or value

            items.append(
                CompletionItem(
                    label=param.name,
                    kind=CompletionItemKind.PARAMETER,
                    detail=detail,
                    insert_text=f".{param.name}()",
                    sort_group=0,
                )
            )
    except Exception:
        logger.debug("Failed param override completion", exc_info=True)

    return items


def _complete_package_members(
    compilation: Any,
    package_name: str,
) -> list[CompletionItem]:
    """Generate completions for package members after '::'.

    Calls compilation.getPackage(package_name) and iterates members.
    """
    try:
        pkg = compilation.getPackage(package_name)
    except Exception:
        return []

    if pkg is None:
        return []

    items: list[CompletionItem] = []
    try:
        for member in pkg:
            name = getattr(member, "name", "")
            if not name:
                continue
            kind = _map_pyslang_to_completion_kind(member)
            detail = _get_type_detail(member)
            items.append(
                CompletionItem(
                    label=name,
                    kind=kind,
                    detail=detail,
                    sort_group=0,
                )
            )
    except Exception:
        logger.debug("Failed package member completion", exc_info=True)

    return items


def _complete_dot_members(
    scope: Any,
    compilation: Any,
    qualifier: str,
) -> list[CompletionItem]:
    """Generate completions for members after '.'.

    Resolves the qualifier name in the current scope, gets its type,
    and iterates the type's members.
    """
    if not qualifier or scope is None:
        return []

    # Resolve the qualifier name in scope
    sym = None
    try:
        sym = scope.lookupName(qualifier)
    except Exception:
        pass

    if sym is None:
        # Try walking parent scopes
        current = scope
        while sym is None:
            parent = getattr(current, "parentScope", None)
            if parent is None:
                break
            try:
                sym = parent.lookupName(qualifier)
            except Exception:
                pass
            current = parent

    if sym is None:
        return []

    # Get the symbol's type
    try:
        sym_type = sym.type
    except Exception:
        return []

    # Unwrap type aliases to get canonical type
    try:
        canonical = sym_type.canonicalType
    except Exception:
        canonical = sym_type

    items: list[CompletionItem] = []

    # Struct: iterate FieldSymbol members
    if getattr(canonical, "isStruct", False) and getattr(canonical, "isScope", False):
        try:
            for member in canonical:
                if isinstance(member, pyslang.FieldSymbol):
                    type_str = str(member.type) if hasattr(member, "type") else None
                    items.append(
                        CompletionItem(
                            label=member.name,
                            kind=CompletionItemKind.FIELD,
                            detail=type_str,
                            sort_group=0,
                        )
                    )
        except Exception:
            pass

    # Enum: iterate enum value members
    elif getattr(canonical, "isEnum", False) and getattr(canonical, "isScope", False):
        try:
            for member in canonical:
                items.append(
                    CompletionItem(
                        label=member.name,
                        kind=CompletionItemKind.ENUM_MEMBER,
                        detail=str(getattr(member, "value", "")),
                        sort_group=0,
                    )
                )
        except Exception:
            pass

    return items


def _complete_system_tasks(prefix: str) -> list[CompletionItem]:
    """Generate completions for system tasks/functions after '$'.

    Uses a curated list of common system tasks/functions.
    Filtered by prefix if the user has typed partial text after '$'.
    """
    items: list[CompletionItem] = []
    prefix_lower = prefix.lower()

    for name, description in _SYSTEM_TASKS.items():
        if prefix_lower and not name.lower().startswith(prefix_lower):
            continue
        items.append(
            CompletionItem(
                label=name,
                kind=CompletionItemKind.SYSTEM_TASK,
                detail=description,
                insert_text=f"${name}",
                sort_group=0,
            )
        )

    return items


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _map_pyslang_to_completion_kind(sym: Any) -> CompletionItemKind:
    """Map a pyslang symbol to a CompletionItemKind."""
    if isinstance(sym, pyslang.PortSymbol):
        return CompletionItemKind.PORT
    if isinstance(sym, pyslang.ParameterSymbol):
        return CompletionItemKind.PARAMETER
    if isinstance(sym, (pyslang.VariableSymbol, pyslang.NetSymbol)):
        return CompletionItemKind.SIGNAL
    if isinstance(sym, pyslang.SubroutineSymbol):
        sub_kind = getattr(sym, "subroutineKind", None)
        if sub_kind == pyslang.SubroutineKind.Task:
            return CompletionItemKind.TASK
        return CompletionItemKind.FUNCTION
    if isinstance(sym, pyslang.TypeAliasType):
        return CompletionItemKind.TYPEDEF
    if isinstance(sym, pyslang.TransparentMemberSymbol):
        return CompletionItemKind.ENUM_MEMBER
    if isinstance(sym, pyslang.FieldSymbol):
        return CompletionItemKind.FIELD
    if isinstance(sym, pyslang.InstanceSymbol):
        return CompletionItemKind.MODULE
    if isinstance(sym, pyslang.PackageSymbol):
        return CompletionItemKind.PACKAGE
    return CompletionItemKind.SIGNAL


def _get_type_detail(sym: Any) -> str | None:
    """Extract a human-readable type string from a pyslang symbol.

    Returns strings like 'logic [7:0]', 'input logic', 'int', etc.
    Returns None if no type information is available.
    """
    try:
        if isinstance(sym, pyslang.PortSymbol):
            dir_str = _DIRECTION_MAP.get(sym.direction, "")
            type_str = str(sym.type)
            return f"{dir_str} {type_str}".strip()
        if isinstance(sym, pyslang.ParameterSymbol):
            type_str = str(sym.type) if hasattr(sym, "type") else ""
            value = str(getattr(sym, "value", ""))
            if type_str and value:
                return f"{type_str} = {value}"
            return type_str or value or None
        if isinstance(sym, (pyslang.VariableSymbol, pyslang.NetSymbol)):
            return str(sym.type) if hasattr(sym, "type") else None
        if isinstance(sym, pyslang.FieldSymbol):
            return str(sym.type) if hasattr(sym, "type") else None
        if isinstance(sym, pyslang.SubroutineSymbol):
            ret = str(sym.returnType) if hasattr(sym, "returnType") else "void"
            return f"function → {ret}"
        if isinstance(sym, pyslang.TypeAliasType):
            ct = str(sym.canonicalType) if hasattr(sym, "canonicalType") else None
            return ct
        if isinstance(sym, pyslang.TransparentMemberSymbol):
            wrapped = getattr(sym, "wrapped", None)
            if wrapped is not None:
                return str(getattr(wrapped, "value", ""))
            return None
    except Exception:
        pass
    return None
