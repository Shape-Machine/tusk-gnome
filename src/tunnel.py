import select
import socket
import threading
from contextlib import contextmanager


def _free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def _forward(local_sock, transport, remote_host, remote_port):
    try:
        channel = transport.open_channel(
            'direct-tcpip',
            (remote_host, remote_port),
            local_sock.getpeername(),
        )
    except Exception:
        local_sock.close()
        return

    try:
        while True:
            r, _, _ = select.select([local_sock, channel], [], [], 1)
            if local_sock in r:
                data = local_sock.recv(4096)
                if not data:
                    break
                channel.sendall(data)
            if channel in r:
                data = channel.recv(4096)
                if not data:
                    break
                local_sock.sendall(data)
    except OSError:
        pass
    finally:
        local_sock.close()
        channel.close()


def apply_conn_settings(db, conn):
    """Apply session-level settings derived from the connection profile.

    Must be called after psycopg.connect() and before any user queries.
    Currently handles: read-only mode.
    """
    if conn.get('read_only'):
        with db.cursor() as cur:
            cur.execute('SET SESSION default_transaction_read_only = on')
        db.commit()


@contextmanager
def open_db(conn, autocommit=False):
    """Open a psycopg connection via tunnel with session settings applied.

    Preferred over calling open_tunnel + psycopg.connect directly.
    Guarantees apply_conn_settings() runs on every connection, including
    read-only enforcement.

    Pass autocommit=True for DDL that must run outside a transaction block,
    e.g. CREATE/DROP INDEX CONCURRENTLY.
    """
    import psycopg
    with open_tunnel(conn) as (host, port), psycopg.connect(
        host=host,
        port=port,
        dbname=conn['database'],
        user=conn['username'],
        password=conn['password'],
        connect_timeout=10,
    ) as db:
        apply_conn_settings(db, conn)
        if autocommit:
            db.autocommit = True
        yield db


@contextmanager
def open_tunnel(conn):
    """
    Yields (host, port) to connect Postgres to.
    Opens an SSH tunnel when conn['ssh_enabled'] is True,
    otherwise passes the original host/port straight through.
    """
    if not conn.get('ssh_enabled'):
        yield conn['host'], conn['port']
        return

    import paramiko

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    connect_kwargs = dict(
        hostname=conn['ssh_host'],
        port=conn.get('ssh_port', 22),
        username=conn.get('ssh_user', ''),
        timeout=10,
    )

    key_path = conn.get('ssh_key_path', '').strip()
    if key_path:
        connect_kwargs['key_filename'] = key_path
        passphrase = conn.get('ssh_passphrase') or None
        if passphrase:
            connect_kwargs['passphrase'] = passphrase

    client.connect(**connect_kwargs)
    transport = client.get_transport()

    local_port = _free_port()
    server_sock = socket.socket()
    try:
        server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_sock.bind(('127.0.0.1', local_port))
        server_sock.listen(5)
        server_sock.settimeout(1)
    except Exception:
        server_sock.close()
        client.close()
        raise

    stop = threading.Event()

    def accept_loop():
        while not stop.is_set():
            try:
                local_sock, _ = server_sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(
                target=_forward,
                args=(local_sock, transport, conn['host'], conn['port']),
                daemon=True,
            ).start()

    threading.Thread(target=accept_loop, daemon=True).start()

    try:
        yield '127.0.0.1', local_port
    finally:
        stop.set()
        server_sock.close()
        client.close()
