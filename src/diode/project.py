"""Project configuration loading for diode.

Discovers and parses project configuration from diode.toml, .f file lists,
or auto-discovery of SystemVerilog sources. Always returns a valid
ProjectConfig -- never raises exceptions.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ImportError:
        tomllib = None  # type: ignore[assignment]

from diode.types import ProjectConfig, ProjectSource, ProjectSourceKind

logger = logging.getLogger(__name__)

# File extensions recognised during auto-discovery
_SV_EXTENSIONS = (".sv", ".v", ".svh")


def load_project(workspace_root: Path) -> ProjectConfig:
    """Load project configuration from the workspace.

    Discovery order:
    1. Look for diode.toml in workspace_root
    2. Look for *.f files in workspace_root
    3. Fall back to auto-discovery (glob for *.sv, *.v, *.svh)

    Args:
        workspace_root: The LSP workspace root directory.

    Returns:
        ProjectConfig with resolved absolute paths.

    Raises:
        Nothing -- always returns a valid config. Logs warnings for issues.
    """
    workspace_root = workspace_root.resolve()

    # 1. Try diode.toml
    toml_path = workspace_root / "diode.toml"
    if toml_path.is_file():
        logger.info(f"Found config: {toml_path}")
        return _parse_diode_toml(toml_path)

    # 2. Try .f files
    f_files = sorted(workspace_root.glob("*.f"))
    if f_files:
        logger.info(f"Found file list(s): {[str(f) for f in f_files]}")
        all_sources: list[Path] = []
        all_incdirs: list[Path] = []
        all_defines: dict[str, str] = {}
        for f_path in f_files:
            sources, incdirs, defines = _parse_file_list(f_path)
            all_sources.extend(sources)
            all_incdirs.extend(incdirs)
            all_defines.update(defines)
        # Deduplicate while preserving order
        seen_sources: set[Path] = set()
        unique_sources: list[ProjectSource] = []
        for p in all_sources:
            resolved = p.resolve()
            if resolved not in seen_sources:
                seen_sources.add(resolved)
                unique_sources.append(
                    ProjectSource(path=resolved, kind=ProjectSourceKind.FILE_LIST)
                )
        seen_dirs: set[Path] = set()
        unique_incdirs: list[Path] = []
        for d in all_incdirs:
            resolved = d.resolve()
            if resolved not in seen_dirs:
                seen_dirs.add(resolved)
                unique_incdirs.append(resolved)
        return ProjectConfig(
            source_files=unique_sources,
            include_dirs=unique_incdirs,
            defines=all_defines,
        )

    # 3. Auto-discover
    logger.info(f"No config found, auto-discovering in {workspace_root}")
    discovered = _auto_discover(workspace_root)
    return ProjectConfig(
        source_files=[
            ProjectSource(path=p, kind=ProjectSourceKind.AUTO_DISCOVER)
            for p in discovered
        ],
    )


def _parse_diode_toml(toml_path: Path) -> ProjectConfig:
    """Parse a diode.toml configuration file.

    Expected TOML structure:
        [project]
        top = "top_module"                    # optional
        files = ["rtl/**/*.sv", "rtl/pkg.sv"] # glob patterns
        file_list = "project.f"               # path to .f file
        include_dirs = ["rtl/include"]         # include search paths
        defines = { SYNTHESIS = "1" }          # preprocessor defines

    All relative paths resolved against toml_path.parent.
    Glob patterns expanded at parse time.
    Both ``files`` and ``file_list`` can coexist -- sources are merged.
    """
    if tomllib is None:
        logger.warning(
            "Cannot parse diode.toml: tomllib not available and tomli not installed"
        )
        return ProjectConfig(config_path=toml_path)

    base_dir = toml_path.parent.resolve()

    try:
        with open(toml_path, "rb") as fh:
            data = tomllib.load(fh)
    except Exception as exc:
        logger.warning(f"Failed to parse {toml_path}: {exc}")
        return ProjectConfig(config_path=toml_path)

    project = data.get("project", {})

    # --- top module ---------------------------------------------------------
    top_module: str | None = project.get("top")

    # --- files (glob patterns) ----------------------------------------------
    source_files: list[ProjectSource] = []
    seen_paths: set[Path] = set()

    file_patterns: list[str] = project.get("files", [])
    for pattern in file_patterns:
        # Resolve relative patterns against base_dir
        matched = sorted(base_dir.glob(pattern))
        if not matched:
            logger.warning(f"File pattern '{pattern}' matched no files")
        for p in matched:
            resolved = p.resolve()
            if resolved not in seen_paths:
                seen_paths.add(resolved)
                source_files.append(
                    ProjectSource(path=resolved, kind=ProjectSourceKind.CONFIG)
                )

    # --- file_list (.f file) ------------------------------------------------
    file_list_rel: str | None = project.get("file_list")
    f_sources: list[Path] = []
    f_incdirs: list[Path] = []
    f_defines: dict[str, str] = {}

    if file_list_rel is not None:
        f_path = (base_dir / file_list_rel).resolve()
        if f_path.is_file():
            f_sources, f_incdirs, f_defines = _parse_file_list(f_path)
        else:
            logger.warning(f"file_list path does not exist: {f_path}")

    for p in f_sources:
        resolved = p.resolve()
        if resolved not in seen_paths:
            seen_paths.add(resolved)
            source_files.append(
                ProjectSource(path=resolved, kind=ProjectSourceKind.FILE_LIST)
            )

    # --- include_dirs -------------------------------------------------------
    include_dirs: list[Path] = []
    seen_dirs: set[Path] = set()

    for d in project.get("include_dirs", []):
        resolved = (base_dir / d).resolve()
        if resolved not in seen_dirs:
            seen_dirs.add(resolved)
            include_dirs.append(resolved)

    # Merge include dirs from .f file
    for d in f_incdirs:
        resolved = d.resolve()
        if resolved not in seen_dirs:
            seen_dirs.add(resolved)
            include_dirs.append(resolved)

    # --- defines ------------------------------------------------------------
    defines: dict[str, str] = {}
    raw_defines = project.get("defines", {})
    for key, value in raw_defines.items():
        defines[str(key)] = str(value)

    # Merge defines from .f file (toml defines take precedence)
    merged_defines = {**f_defines, **defines}

    return ProjectConfig(
        source_files=source_files,
        include_dirs=include_dirs,
        defines=merged_defines,
        top_module=top_module,
        config_path=toml_path,
    )


def _parse_file_list(
    f_path: Path, seen: set[Path] | None = None
) -> tuple[list[Path], list[Path], dict[str, str]]:
    """Parse a .f file list, returning (source_files, include_dirs, defines).

    Supported directives:
        path/to/file.sv          -- source file (relative to .f file location)
        +incdir+path/to/include  -- include directory
        +define+NAME=VALUE       -- preprocessor define (VALUE optional)
        +define+NAME             -- define with empty value
        -f path/to/nested.f      -- include another .f file (recursive)
        // comment               -- line comment (also # comments)
        (blank lines)            -- ignored

    Args:
        f_path: Path to the .f file.
        seen: Set of already-processed .f files (for cycle detection).

    Returns:
        Tuple of (source_files, include_dirs, defines).
    """
    if seen is None:
        seen = set()

    resolved_f = f_path.resolve()

    # Cycle detection
    if resolved_f in seen:
        logger.warning(f"Cycle detected: {f_path} already processed, skipping")
        return [], [], {}

    seen.add(resolved_f)

    base_dir = resolved_f.parent

    source_files: list[Path] = []
    include_dirs: list[Path] = []
    defines: dict[str, str] = {}

    try:
        lines = resolved_f.read_text(encoding="utf-8").splitlines()
    except Exception as exc:
        logger.warning(f"Failed to read {f_path}: {exc}")
        return [], [], {}

    for line_no, raw_line in enumerate(lines, start=1):
        # Strip whitespace
        line = raw_line.strip()

        # Skip blank lines
        if not line:
            continue

        # Skip comment lines
        if line.startswith("//") or line.startswith("#"):
            continue

        # Strip inline comments (everything after //)
        comment_pos = line.find("//")
        if comment_pos >= 0:
            line = line[:comment_pos].strip()
        comment_pos = line.find("#")
        if comment_pos >= 0:
            line = line[:comment_pos].strip()

        if not line:
            continue

        # +incdir+ directive
        if line.startswith("+incdir+"):
            dir_path = line[len("+incdir+"):]
            if dir_path:
                include_dirs.append((base_dir / dir_path).resolve())
            else:
                logger.warning(
                    f"{f_path}:{line_no}: empty +incdir+ directive"
                )
            continue

        # +define+ directive
        if line.startswith("+define+"):
            define_str = line[len("+define+"):]
            if "=" in define_str:
                name, _, value = define_str.partition("=")
                defines[name.strip()] = value.strip()
            elif define_str.strip():
                defines[define_str.strip()] = ""
            else:
                logger.warning(
                    f"{f_path}:{line_no}: empty +define+ directive"
                )
            continue

        # -f directive (nested file list)
        if line.startswith("-f ") or line.startswith("-f\t"):
            nested_path_str = line[3:].strip()
            if nested_path_str:
                nested_path = (base_dir / nested_path_str).resolve()
                if nested_path.is_file():
                    nested_sources, nested_incdirs, nested_defines = (
                        _parse_file_list(nested_path, seen)
                    )
                    source_files.extend(nested_sources)
                    include_dirs.extend(nested_incdirs)
                    defines.update(nested_defines)
                else:
                    logger.warning(
                        f"{f_path}:{line_no}: nested file not found: {nested_path}"
                    )
            else:
                logger.warning(
                    f"{f_path}:{line_no}: empty -f directive"
                )
            continue

        # Otherwise treat as a source file path
        file_path = (base_dir / line).resolve()
        source_files.append(file_path)

    return source_files, include_dirs, defines


def _auto_discover(workspace_root: Path) -> list[Path]:
    """Glob for *.sv, *.v, *.svh files in workspace_root (recursive).

    Returns sorted list of absolute paths.
    """
    results: list[Path] = []
    for ext in _SV_EXTENSIONS:
        results.extend(workspace_root.rglob(f"*{ext}"))
    # Resolve all to absolute, sort, and deduplicate
    seen: set[Path] = set()
    unique: list[Path] = []
    for p in sorted(r.resolve() for r in results):
        if p not in seen:
            seen.add(p)
            unique.append(p)
    return unique
