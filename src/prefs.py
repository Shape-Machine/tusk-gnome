import json
import os

CONFIG_DIR = os.path.join(os.path.expanduser('~'), '.config', 'tusk')
PREFS_FILE = os.path.join(CONFIG_DIR, 'prefs.json')


def get(key, default=None):
    try:
        with open(PREFS_FILE) as f:
            return json.load(f).get(key, default)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def put(key, value):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    try:
        with open(PREFS_FILE) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    data[key] = value
    with open(PREFS_FILE, 'w') as f:
        json.dump(data, f, indent=2)
