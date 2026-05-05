# diode

A SystemVerilog language server built on [pyslang](https://github.com/MikePopoloski/pyslang), designed for FPGA engineers.

## Install

Diode is not yet on PyPI. Install the latest from git with [pipx](https://pipx.pypa.io/):

```bash
pipx install git+https://github.com/wangahrah/diode.git
```

This puts `diode-ls` on your PATH in its own private virtualenv. Upgrade with `pipx upgrade diode-ls`.

For development:

```bash
git clone https://github.com/wangahrah/diode.git
cd diode
python -m venv .venv && source .venv/bin/activate
pip install -e .[dev]
```

## Usage

```bash
# STDIO mode (used by editors)
diode-ls

# TCP mode (for debugging)
diode-ls --tcp --port 2087
```

## Status

Phase 1 — in development. See `concept.md` for the full project vision.
