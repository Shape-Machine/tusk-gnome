#!/usr/bin/env python3
"""Dev runner — use this instead of installing: python3 run.py"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from main import main

sys.exit(main())
