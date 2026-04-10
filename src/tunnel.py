import select
import shutil
import socket
import subprocess
import threading
import time
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
    Handles: read-only mode, default schema (search_path).
    """
    from psycopg import sql as pgsql
    with db.cursor() as cur:
        if conn.get('read_only'):
            cur.execute('SET SESSION default_transaction_read_only = on')
        if conn.get('default_schema'):
            cur.execute(
                pgsql.SQL('SET search_path TO {}').format(
                    pgsql.Identifier(conn['default_schema'])
                )
            )
    db.commit()


def _psycopg_kwargs(conn, host, port, password=None):
    """Build psycopg.connect keyword arguments from a connection profile."""
    kwargs = dict(
        host=host,
        port=port,
        dbname=conn['database'],
        user=conn['username'],
        password=password if password is not None else conn.get('password', ''),
        connect_timeout=10,
    )
    ssl_mode = conn.get('ssl_mode')
    if ssl_mode and ssl_mode != 'prefer':
        kwargs['sslmode'] = ssl_mode
    ssl_root_cert = conn.get('ssl_root_cert')
    if ssl_root_cert:
        kwargs['sslrootcert'] = ssl_root_cert
    return kwargs


@contextmanager
def open_db(conn, autocommit=False):
    """Open a psycopg connection via tunnel with session settings applied.

    Preferred over calling open_tunnel + psycopg.connect directly.
    Guarantees apply_conn_settings() runs on every connection, including
    read-only enforcement.

    Pass autocommit=True for DDL that must run outside a transaction block,
    e.g. CREATE/DROP INDEX CONCURRENTLY.

    Handles cloud_auth_mode='iam': fetches a fresh gcloud access token and
    uses it as the PostgreSQL password.
    """
    import psycopg

    # Resolve password (IAM token or stored password)
    password = conn.get('password', '')
    if conn.get('cloud_auth_mode') == 'iam':
        from gcp_discovery import get_iam_token
        password = get_iam_token()

    with open_tunnel(conn) as (host, port), psycopg.connect(
        **_psycopg_kwargs(conn, host, port, password=password)
    ) as db:
        apply_conn_settings(db, conn)
        if autocommit:
            db.autocommit = True
        yield db


@contextmanager
def _cloud_proxy_tunnel(conn):
    """Launch cloud-sql-proxy or alloydb-auth-proxy and yield (host, local_port).

    Selects the proxy binary based on cloud_provider:
      - 'gcp-cloudsql'  → cloud-sql-proxy  <instance_id> --port <port>
      - 'gcp-alloydb'   → alloydb-auth-proxy <instance_uri> --port <port>

    Waits up to 10 s for the proxy to accept TCP connections, then yields.
    Terminates the proxy subprocess on context exit.
    """
    provider = conn.get('cloud_provider', '')
    instance_id = conn.get('cloud_instance_id', '')
    if not instance_id:
        raise RuntimeError('cloud_instance_id is required for cloud proxy connections.')

    if provider == 'gcp-alloydb':
        binary = 'alloydb-auth-proxy'
    else:
        binary = 'cloud-sql-proxy'

    if not shutil.which(binary):
        raise RuntimeError(
            f'{binary} not found on $PATH. '
            'Install it to connect to this instance.'
        )

    local_port = _free_port()
    cmd = [binary, instance_id, '--port', str(local_port)]
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as e:
        raise RuntimeError(f'Could not start {binary}: {e}')

    try:
        # Wait for the proxy to begin listening (up to 10 s)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(('127.0.0.1', local_port), timeout=0.5):
                    break
            except OSError:
                if proc.poll() is not None:
                    raise RuntimeError(f'{binary} exited unexpectedly during startup.')
                time.sleep(0.2)
        else:
            proc.terminate()
            raise RuntimeError(f'{binary} did not start listening within 10 seconds.')
        yield '127.0.0.1', local_port
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


@contextmanager
def open_tunnel(conn):
    """
    Yields (host, port) to connect Postgres to.
    - SSH tunnel when conn['ssh_enabled'] is True
    - Cloud proxy when conn['cloud_proxy_enabled'] is True
    - Direct otherwise
    """
    if conn.get('cloud_proxy_enabled'):
        with _cloud_proxy_tunnel(conn) as (host, port):
            yield host, port
        return

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
