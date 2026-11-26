#!/usr/bin/env python3
"""Entry point: `python run.py [serve|sync|check|config] ...`  (see `python run.py -h`)."""
import sys

if sys.version_info < (3, 11):  # noqa: UP036 – friendly message on old interpreters
    sys.exit(f"Ticket Radar דורש Python 3.11 ומעלה (מותקן: {sys.version.split()[0]})")

from ticket_radar.cli import main

if __name__ == "__main__":
    sys.exit(main())
