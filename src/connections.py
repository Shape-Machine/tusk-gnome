import json
import os
import uuid

import keyring

CONFIG_DIR = os.path.join(os.path.expanduser('~'), '.config', 'tusk')
CONNECTIONS_FILE = os.path.join(CONFIG_DIR, 'connections.json')
KEYRING_SERVICE = 'xyz.shapemachine.tusk-gnome'


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
        return keyring.get_password(KEYRING_SERVICE, conn_id) or ''

    def get_ssh_passphrase(self, conn_id):
        return keyring.get_password(KEYRING_SERVICE, _ssh_key(conn_id)) or ''

    def add(self, conn):
        if 'id' not in conn:
            conn['id'] = str(uuid.uuid4())
        password = conn.pop('password', '')
        ssh_passphrase = conn.pop('ssh_passphrase', '')
        keyring.set_password(KEYRING_SERVICE, conn['id'], password)
        keyring.set_password(KEYRING_SERVICE, _ssh_key(conn['id']), ssh_passphrase)
        self._connections.append(conn)
        self._save()
        return conn

    def remove(self, conn_id):
        keyring.delete_password(KEYRING_SERVICE, conn_id)
        try:
            keyring.delete_password(KEYRING_SERVICE, _ssh_key(conn_id))
        except keyring.errors.PasswordDeleteError:
            pass
        self._connections = [c for c in self._connections if c['id'] != conn_id]
        self._save()

    def update(self, conn):
        password = conn.pop('password', None)
        ssh_passphrase = conn.pop('ssh_passphrase', None)
        if password is not None:
            keyring.set_password(KEYRING_SERVICE, conn['id'], password)
        if ssh_passphrase is not None:
            keyring.set_password(KEYRING_SERVICE, _ssh_key(conn['id']), ssh_passphrase)
        for i, c in enumerate(self._connections):
            if c['id'] == conn['id']:
                self._connections[i] = conn
                break
        self._save()
