#!/usr/bin/env python3

from __future__ import annotations

try:
    from .plex_app import main
except ImportError:
    from plex_app import main


if __name__ == "__main__":
    main()
