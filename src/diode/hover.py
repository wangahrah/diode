"""Hover content formatting for diode.

Pure formatting module — takes a SymbolInfo and returns GitHub-flavored
markdown suitable for LSP hover popups. No LSP imports, no side effects.
"""

from __future__ import annotations

import logging

from diode.types import DiodeSymbolKind, SymbolInfo

logger = logging.getLogger(__name__)


def format_hover(symbol: SymbolInfo) -> str:
    """Format a SymbolInfo into markdown for LSP hover response.

    Returns GitHub-flavored markdown string. Uses SystemVerilog code fences
    for code blocks.

    Args:
        symbol: The SymbolInfo to format.

    Returns:
        Markdown string for the hover popup.
    """
    kind = symbol.kind

    if kind in (DiodeSymbolKind.MODULE, DiodeSymbolKind.INTERFACE):
        return _format_module_or_interface(symbol)
    if kind == DiodeSymbolKind.PORT:
        return _format_port(symbol)
    if kind in (DiodeSymbolKind.PARAMETER, DiodeSymbolKind.LOCALPARAM):
        return _format_parameter(symbol)
    if kind == DiodeSymbolKind.SIGNAL:
        return _format_signal(symbol)
    if kind == DiodeSymbolKind.INSTANCE:
        return _format_instance(symbol)
    if kind in (DiodeSymbolKind.FUNCTION, DiodeSymbolKind.TASK):
        return _format_function_or_task(symbol)
    if kind == DiodeSymbolKind.TYPEDEF:
        return _format_typedef(symbol)
    if kind == DiodeSymbolKind.ENUM_MEMBER:
        return _format_enum_member(symbol)
    if kind == DiodeSymbolKind.PACKAGE:
        return _format_package(symbol)

    # Fallback for GENERATE, ALWAYS, CLASS, or any future kinds
    return _format_fallback(symbol)


def _format_module_or_interface(symbol: SymbolInfo) -> str:
    """Format a MODULE or INTERFACE symbol."""
    keyword = symbol.kind.value  # "module" or "interface"
    header = f"{keyword} {symbol.name}"

    if symbol.detail:
        # detail contains the port list (e.g., "input logic clk, input logic rst_n, ...")
        ports = symbol.detail
        return f"```systemverilog\n{header} (\n    {ports}\n);\n```"

    return f"```systemverilog\n{header}\n```"


def _format_port(symbol: SymbolInfo) -> str:
    """Format a PORT symbol.

    Combines direction (from detail), type (from type_str), and name.
    Example: "input logic [7:0] data"
    """
    parts: list[str] = []

    if symbol.detail:
        parts.append(symbol.detail)
    if symbol.type_str:
        parts.append(symbol.type_str)
    parts.append(symbol.name)

    line = " ".join(parts)
    return f"```systemverilog\n{line}\n```"


def _format_parameter(symbol: SymbolInfo) -> str:
    """Format a PARAMETER or LOCALPARAM symbol.

    Example: "parameter int WIDTH = 8"
    """
    keyword = symbol.kind.value  # "parameter" or "localparam"
    parts = [keyword]

    if symbol.type_str:
        parts.append(symbol.type_str)

    parts.append(symbol.name)

    if symbol.detail:
        parts.append(f"= {symbol.detail}")

    line = " ".join(parts)
    return f"```systemverilog\n{line}\n```"


def _format_signal(symbol: SymbolInfo) -> str:
    """Format a SIGNAL symbol.

    Example: "logic [7:0] count_next"
    """
    parts: list[str] = []

    if symbol.type_str:
        parts.append(symbol.type_str)

    parts.append(symbol.name)
    line = " ".join(parts)
    return f"```systemverilog\n{line}\n```"


def _format_instance(symbol: SymbolInfo) -> str:
    """Format an INSTANCE symbol.

    Shows the module being instantiated.
    """
    if symbol.detail:
        return f"Instance of **{symbol.detail}**"

    return f"Instance **{symbol.name}**"


def _format_function_or_task(symbol: SymbolInfo) -> str:
    """Format a FUNCTION or TASK symbol.

    Uses detail for the full signature if available.
    """
    if symbol.detail:
        return f"```systemverilog\n{symbol.detail}\n```"

    keyword = symbol.kind.value  # "function" or "task"
    return f"```systemverilog\n{keyword} {symbol.name}\n```"


def _format_typedef(symbol: SymbolInfo) -> str:
    """Format a TYPEDEF symbol.

    Shows the underlying type from type_str.
    """
    if symbol.type_str:
        return f"```systemverilog\ntypedef {symbol.type_str}\n```"

    return f"```systemverilog\ntypedef {symbol.name}\n```"


def _format_enum_member(symbol: SymbolInfo) -> str:
    """Format an ENUM_MEMBER symbol.

    Shows enum_type::member = value.
    """
    parts: list[str] = []

    if symbol.type_str:
        parts.append(f"{symbol.type_str}::{symbol.name}")
    else:
        parts.append(symbol.name)

    if symbol.detail:
        parts.append(f"= {symbol.detail}")

    line = " ".join(parts)
    return f"```systemverilog\n{line}\n```"


def _format_package(symbol: SymbolInfo) -> str:
    """Format a PACKAGE symbol."""
    return f"```systemverilog\npackage {symbol.name}\n```"


def _format_fallback(symbol: SymbolInfo) -> str:
    """Fallback formatter for unhandled symbol kinds (GENERATE, ALWAYS, CLASS)."""
    if symbol.detail:
        return f"```systemverilog\n{symbol.detail}\n```"

    kind_label = symbol.kind.value
    return f"```systemverilog\n{kind_label} {symbol.name}\n```"
