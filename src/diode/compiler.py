"""Compilation engine for diode.

Drives pyslang to compile SystemVerilog source files, extracts diagnostics
into diode's own types. This module is the bridge between project configuration
and the pyslang compilation pipeline.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import pyslang

from diode.types import (
    CompilationResult,
    DiodeDiagnostic,
    DiodeSeverity,
    FileLocation,
    FilePosition,
    FileRange,
    ProjectConfig,
)

logger = logging.getLogger(__name__)

_DIAG_CODE_RE = re.compile(r"DiagCode\((\w+)\)")

_SEVERITY_MAP: dict[pyslang.DiagnosticSeverity, DiodeSeverity] = {
    pyslang.DiagnosticSeverity.Error: DiodeSeverity.ERROR,
    pyslang.DiagnosticSeverity.Fatal: DiodeSeverity.ERROR,
    pyslang.DiagnosticSeverity.Warning: DiodeSeverity.WARNING,
    pyslang.DiagnosticSeverity.Note: DiodeSeverity.INFO,
}


def compile_project(
    config: ProjectConfig,
    open_files: dict[Path, str] | None = None,
) -> CompilationResult:
    """Compile the project using pyslang.

    Creates a pyslang SyntaxTree for each source file in config.source_files.
    For files present in open_files, uses the string content instead of disk.
    Builds a pyslang Compilation from all trees.

    Args:
        config: Project configuration with source files, includes, defines.
        open_files: Map of file path -> editor buffer content. Files in this
            dict use the provided string; all others read from disk.

    Returns:
        CompilationResult containing the pyslang Compilation object and
        extracted diagnostics.
    """
    if open_files is None:
        open_files = {}

    source_manager = pyslang.SourceManager()

    # Build preprocessor options with defines and include directories
    preproc_options = pyslang.PreprocessorOptions()
    if config.defines:
        preproc_options.predefines = [
            f"{name}={value}" if value else name
            for name, value in config.defines.items()
        ]
    if config.include_dirs:
        preproc_options.additionalIncludePaths = list(config.include_dirs)

    bag = pyslang.Bag([preproc_options])

    # Register include directories with the source manager as well
    for inc_dir in config.include_dirs:
        source_manager.addUserDirectories(str(inc_dir))

    source_paths: list[Path] = []
    trees: list[Any] = []

    for project_source in config.source_files:
        path = project_source.path
        resolved = path.resolve()
        content = open_files.get(resolved)
        if content is None:
            content = open_files.get(path)
        source_paths.append(resolved)

        try:
            tree = _create_syntax_tree(
                path=resolved,
                content=content,
                source_manager=source_manager,
                options=bag,
            )
            trees.append(tree)
        except Exception:
            logger.warning(f"Failed to create syntax tree for {resolved}")
            continue

    # Build compilation from all trees
    compilation = pyslang.Compilation()
    for tree in trees:
        compilation.addSyntaxTree(tree)

    # Extract diagnostics
    diagnostics = _extract_diagnostics(compilation, source_manager)

    # Determine success: no error-level diagnostics
    has_errors = any(d.severity == DiodeSeverity.ERROR for d in diagnostics)

    return CompilationResult(
        compilation=compilation,
        diagnostics=diagnostics,
        source_files=source_paths,
        success=not has_errors,
    )


def _create_syntax_tree(
    path: Path,
    content: str | None,
    source_manager: Any,
    options: Any,
) -> Any:
    """Create a pyslang SyntaxTree for a single file.

    If content is provided, parse from string (editor buffer override).
    Otherwise, read from disk.

    Args:
        path: Absolute path to the source file.
        content: Optional string content to use instead of reading from disk.
        source_manager: Shared pyslang SourceManager instance.
        options: pyslang Bag containing preprocessor options.

    Returns:
        A pyslang SyntaxTree instance.
    """
    if content is not None:
        return pyslang.SyntaxTree.fromFileInMemory(
            content,
            source_manager,
            name=path.name,
            path=str(path),
            options=options,
        )
    return pyslang.SyntaxTree.fromFile(str(path), source_manager, options)


def _extract_diagnostics(
    compilation: Any,
    source_manager: Any,
) -> list[DiodeDiagnostic]:
    """Extract all diagnostics from a pyslang Compilation.

    Maps pyslang diagnostic severity to DiodeSeverity.
    Maps pyslang source locations to FileLocation.

    Args:
        compilation: A pyslang Compilation object.
        source_manager: The pyslang SourceManager used during compilation.

    Returns:
        List of DiodeDiagnostic objects extracted from the compilation.
    """
    raw_diags = compilation.getAllDiagnostics()
    engine = pyslang.DiagnosticEngine(source_manager)
    no_location = pyslang.SourceLocation.NoLocation

    result: list[DiodeDiagnostic] = []

    for diag in raw_diags:
        # Skip diagnostics with no source location
        if diag.location == no_location:
            logger.debug("Skipping diagnostic with no source location")
            continue

        # Map severity
        pyslang_severity = engine.getSeverity(diag.code, diag.location)
        if pyslang_severity == pyslang.DiagnosticSeverity.Ignored:
            continue

        diode_severity = _SEVERITY_MAP.get(pyslang_severity, DiodeSeverity.INFO)

        # Get message text
        message = engine.formatMessage(diag)

        # Extract diagnostic code name from DiagCode string representation
        code_str = str(diag.code)
        code_match = _DIAG_CODE_RE.match(code_str)
        code = code_match.group(1) if code_match else code_str

        # Map source location to FileLocation
        file_name = source_manager.getFileName(diag.location)
        # pyslang getLineNumber/getColumnNumber are 1-based; diode is 0-based
        line = source_manager.getLineNumber(diag.location) - 1
        column = source_manager.getColumnNumber(diag.location) - 1

        # Resolve file path: try to get the full path from the buffer ID
        buf_id = diag.location.buffer
        try:
            full_path = source_manager.getFullPath(buf_id)
            file_path = Path(full_path)
        except Exception:
            file_path = Path(file_name) if file_name else Path("<unknown>")

        # Build a range: point range at the diagnostic location
        # For a more precise range, we could use diag.ranges, but a point
        # range is sufficient for phase 1
        start = FilePosition(line=max(0, line), column=max(0, column))
        end = start
        if diag.ranges:
            try:
                src_range = diag.ranges[0]
                end_loc = src_range.end
                end_line = source_manager.getLineNumber(end_loc) - 1
                end_col = source_manager.getColumnNumber(end_loc) - 1
                end = FilePosition(line=max(0, end_line), column=max(0, end_col))
            except Exception:
                logger.debug(f"Failed to extract range for diagnostic: {message}")

        location = FileLocation(
            path=file_path,
            range=FileRange(start=start, end=end),
        )

        result.append(
            DiodeDiagnostic(
                location=location,
                severity=diode_severity,
                message=message,
                code=code,
            )
        )

    return result
