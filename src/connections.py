import json
import os
import uuid

import keyring

CONFIG_DIR = os.path.join(os.path.expanduser('~'), '.config', 'tusk')
CONNECTIONS_FILE = os.path.join(CONFIG_DIR, 'connections.json')
FAVOURITES_FILE = os.path.join(CONFIG_DIR, 'favourites.json')
KEYRING_SERVICE = 'xyz.shapemachine.tusk-gnome'


class KeyringUnavailableError(Exception):
    pass


def _ssh_key(conn_id):
    return f'{conn_id}:ssh'


class ConnectionStore:
    def __init__(self):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        self._connections = self._load()

    def _load(self):
        if os.path.exists(CONNECTIONS_FILE):
            with open(CONNECTIONS_FILE) as f:
                return json.load(f)
        return []

    def _save(self):
        tmp = CONNECTIONS_FILE + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(self._connections, f, indent=2)
        os.replace(tmp, CONNECTIONS_FILE)

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
        if 'id' not in conn:
            conn['id'] = str(uuid.uuid4())
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
        if 'id' not in conn:
            conn['id'] = str(uuid.uuid4())
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
                self._connections[i] = conn
                break
        self._save()


class FavouritesStore:
    """Persists per-connection pinned table/view favourites."""

    def __init__(self):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        self._data = self._load()  # dict: conn_id -> [{schema, table, item_type}]

    def _load(self):
        if os.path.exists(FAVOURITES_FILE):
            try:
                with open(FAVOURITES_FILE) as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def _save(self):
        tmp = FAVOURITES_FILE + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(self._data, f, indent=2)
        os.replace(tmp, FAVOURITES_FILE)

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
