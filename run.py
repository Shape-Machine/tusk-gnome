#!/usr/bin/env python3
"""Dev runner — use this instead of installing: python3 run.py"""
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, 'src'))

# Make GTK find our icons without needing make install
_data = os.path.join(ROOT, 'data')
os.environ['XDG_DATA_DIRS'] = _data + ':' + os.environ.get('XDG_DATA_DIRS', '/usr/local/share:/usr/share')

from main import main
sys.exit(main())
