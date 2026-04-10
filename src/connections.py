import json
import os
import uuid

import keyring

CONFIG_DIR = os.path.join(os.path.expanduser('~'), '.config', 'tusk')
CONNECTIONS_FILE = os.path.join(CONFIG_DIR, 'connections.json')
FAVOURITES_FILE = os.path.join(CONFIG_DIR, 'favourites.json')
KEYRING_SERVICE = 'xyz.shapemachine.tusk-gnome'

SCHEMA_VERSION = 2

_NEW_FIELD_DEFAULTS = {
    'tags': list,
    'last_connected': None,
    'ssl_mode': 'prefer',
    'ssl_root_cert': None,
    'cloud_provider': None,
    'cloud_region': None,
    'cloud_auth_mode': 'password',
    'cloud_instance_id': None,
    'cloud_proxy_enabled': False,
    'cloud_proxy_port': None,
    'secondary_endpoint': None,
    'secondary_port': None,
}


class KeyringUnavailableError(Exception):
    pass


def _ssh_key(conn_id):
    return f'{conn_id}:ssh'


def _write_connections_file(connections, tags_registry):
    tmp = CONNECTIONS_FILE + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(
            {
                'schema_version': SCHEMA_VERSION,
                'connections': connections,
                'tags': tags_registry,
            },
            f,
            indent=2,
        )
    os.replace(tmp, CONNECTIONS_FILE)


def _apply_defaults(conn, tags_registry):
    """Add any missing new fields to a connection dict in-place."""
    if 'id' not in conn:
        conn['id'] = str(uuid.uuid4())
    for field, default in _NEW_FIELD_DEFAULTS.items():
        if field not in conn:
            conn[field] = default() if callable(default) else default
    # Convert legacy folder field to a tag
    if 'folder' in conn:
        folder = conn.pop('folder')
        if folder:
            tags_registry.setdefault(folder, {'color': '#aaaaaa', 'warn_on_connect': False})
            if folder not in conn['tags']:
                conn['tags'].append(folder)
    # Convert legacy environment/environment_color fields to a tag
    if 'environment' in conn:
        env = conn.pop('environment')
        color = conn.pop('environment_color', '#aaaaaa') or '#aaaaaa'
        if env:
            tags_registry.setdefault(env, {'color': color, 'warn_on_connect': False})
            if env not in conn['tags']:
                conn['tags'].append(env)
    elif 'environment_color' in conn:
        conn.pop('environment_color')


class ConnectionStore:
    def __init__(self):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        self._connections, self._tags_registry = self._load()

    def _load(self):
        if not os.path.exists(CONNECTIONS_FILE):
            return [], {}
        with open(CONNECTIONS_FILE) as f:
            raw = json.load(f)
        if isinstance(raw, list):
            # v1 format — bare array; migrate in-place and save
            return self._migrate_v1(raw)
        # v2+ format
        connections = raw.get('connections', [])
        tags_registry = raw.get('tags', {})
        # Still apply defaults in case new fields were added since last write
        for conn in connections:
            _apply_defaults(conn, tags_registry)
        return connections, tags_registry

    def _migrate_v1(self, old_list):
        tags_registry = {}
        for conn in old_list:
            _apply_defaults(conn, tags_registry)
        _write_connections_file(old_list, tags_registry)
        return old_list, tags_registry

    def _save(self):
        _write_connections_file(self._connections, self._tags_registry)

    def get_tags_registry(self):
        return dict(self._tags_registry)

    def set_tag(self, name, color, warn_on_connect):
        self._tags_registry[name] = {'color': color, 'warn_on_connect': warn_on_connect}
        self._save()

    def remove_tag(self, name):
        self._tags_registry.pop(name, None)
        self._save()

    def list(self):
        return list(self._connections)

    def get_password(self, conn_id):
        try:
            return keyring.get_password(KEYRING_SERVICE, conn_id) or ''
        except Exception as e:
            raise KeyringUnavailableError(str(e)) from e

    def get_ssh_passphrase(self, conn_id):
        try:
            return keyring.get_password(KEYRING_SERVICE, _ssh_key(conn_id)) or ''
        except Exception as e:
            raise KeyringUnavailableError(str(e)) from e

    def add(self, conn):
        _apply_defaults(conn, self._tags_registry)
        password = conn.pop('password', '')
        ssh_passphrase = conn.pop('ssh_passphrase', '')
        try:
            keyring.set_password(KEYRING_SERVICE, conn['id'], password)
            keyring.set_password(KEYRING_SERVICE, _ssh_key(conn['id']), ssh_passphrase)
        except Exception as e:
            raise KeyringUnavailableError(str(e)) from e
        self._connections.append(conn)
        self._save()
        return conn

    def add_after(self, after_id, conn):
        """Like add(), but inserts the new connection immediately after after_id."""
        _apply_defaults(conn, self._tags_registry)
        password = conn.pop('password', '')
        ssh_passphrase = conn.pop('ssh_passphrase', '')
        try:
            keyring.set_password(KEYRING_SERVICE, conn['id'], password)
            keyring.set_password(KEYRING_SERVICE, _ssh_key(conn['id']), ssh_passphrase)
        except Exception as e:
            raise KeyringUnavailableError(str(e)) from e
        idx = next((i for i, c in enumerate(self._connections) if c['id'] == after_id), None)
        if idx is not None:
            self._connections.insert(idx + 1, conn)
        else:
            self._connections.append(conn)
        self._save()
        return conn

    def remove(self, conn_id):
        try:
            keyring.delete_password(KEYRING_SERVICE, conn_id)
        except keyring.errors.PasswordDeleteError:
            pass
        except Exception as e:
            raise KeyringUnavailableError(str(e)) from e
        try:
            keyring.delete_password(KEYRING_SERVICE, _ssh_key(conn_id))
        except keyring.errors.PasswordDeleteError:
            pass
        except Exception as e:
            raise KeyringUnavailableError(str(e)) from e
        self._connections = [c for c in self._connections if c['id'] != conn_id]
        self._save()

    def update(self, conn):
        password = conn.pop('password', None)
        ssh_passphrase = conn.pop('ssh_passphrase', None)
        try:
            if password is not None:
                keyring.set_password(KEYRING_SERVICE, conn['id'], password)
            if ssh_passphrase is not None:
                keyring.set_password(KEYRING_SERVICE, _ssh_key(conn['id']), ssh_passphrase)
        except Exception as e:
            raise KeyringUnavailableError(str(e)) from e
        for i, c in enumerate(self._connections):
            if c['id'] == conn['id']:
                merged = {**c, **conn}
                _apply_defaults(merged, self._tags_registry)
                self._connections[i] = merged
                conn = merged
                break
        self._save()
        return conn


class FavouritesStore:
    """Persists per-connection pinned table/view favourites."""

    def __init__(self):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        self._data = self._load()  # dict: conn_id -> [{schema, table, item_type}]

    def _load(self):
        if os.path.exists(FAVOURITES_FILE):
            try:
                with open(FAVOURITES_FILE) as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    return data
            except (json.JSONDecodeError, OSError):
                pass
        return {}

    def _save(self):
        tmp = FAVOURITES_FILE + '.tmp'
        try:
            with open(tmp, 'w') as f:
                json.dump(self._data, f, indent=2)
            os.replace(tmp, FAVOURITES_FILE)
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    def get(self, conn_id):
        return list(self._data.get(conn_id, []))

    def add(self, conn_id, schema, table, item_type):
        favs = self._data.setdefault(conn_id, [])
        if not any(f['schema'] == schema and f['table'] == table for f in favs):
            favs.append({'schema': schema, 'table': table, 'item_type': item_type})
            self._save()

    def remove(self, conn_id, schema, table):
        if conn_id in self._data:
            self._data[conn_id] = [
                f for f in self._data[conn_id]
                if not (f['schema'] == schema and f['table'] == table)
            ]
            self._save()

    def is_pinned(self, conn_id, schema, table):
        return any(
            f['schema'] == schema and f['table'] == table
            for f in self._data.get(conn_id, [])
        )
