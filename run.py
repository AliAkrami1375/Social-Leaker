#!/usr/bin/env python
"""Convenience launcher — equivalent to `python -m socialleaker.cli serve`."""
from __future__ import annotations

from socialleaker.cli import app

if __name__ == "__main__":
    app()
