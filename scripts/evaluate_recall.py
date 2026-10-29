#!/usr/bin/env python3
"""Run the shared fixed recall benchmark from the command line."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from memory_slice.benchmark import main, summarize
if __name__ == '__main__':
    sys.exit(main())
