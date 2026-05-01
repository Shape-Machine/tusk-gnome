import select
import shutil
import socket
import subprocess
import threading
import time
from contextlib import contextmanager


class ProxyNotFoundError(RuntimeError):
    """Raised when a cloud proxy binary is required but not on $PATH."""
    def __init__(self, binary):
        self.binary = binary
        super().__init__(
            f'{binary} not found on $PATH — install it to connect to this instance.'
        )


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
    When active_endpoint='secondary' (Aurora reader), read-only is forced on.
    """
    from psycopg import sql as pgsql
    with db.cursor() as cur:
        if conn.get('read_only') or conn.get('active_endpoint') == 'secondary':
            cur.execute('SET SESSION default_transaction_read_only = on')
        if conn.get('default_schema'):
            cur.execute(
                pgsql.SQL('SET search_path TO {}').format(
                    pgsql.Identifier(conn['default_schema'])
                )
            )
    db.commit()


def _psycopg_kwargs(conn, host, port, password=None, skip_ssl=False):
    """Build psycopg.connect keyword arguments from a connection profile.

    skip_ssl should be True when connecting through a cloud proxy — the proxy
    handles SSL to Cloud SQL internally; the local psycopg→proxy leg is plain TCP.
    """
    kwargs = dict(
        host=host,
        port=port,
        dbname=conn['database'],
        user=conn['username'],
        password=password if password is not None else conn.get('password', ''),
        connect_timeout=10,
    )
    if not skip_ssl:
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
        provider = conn.get('cloud_provider', '')
        if provider.startswith('aws-'):
            from aws_discovery import get_iam_token as _aws_iam
            # Use the effective host/port (may be secondary endpoint for Aurora reader)
            effective_host = (
                (conn.get('secondary_endpoint') or conn.get('host', ''))
                if conn.get('active_endpoint') == 'secondary'
                else conn.get('host', '')
            )
            effective_port = (
                (conn.get('secondary_port') or conn.get('port', 5432))
                if conn.get('active_endpoint') == 'secondary'
                else conn.get('port', 5432)
            )
            password = _aws_iam(
                effective_host,
                effective_port,
                conn.get('database', 'postgres'),
                conn.get('username', ''),
                conn.get('cloud_region', ''),
            )
        elif provider.startswith('azure-'):
            from azure_discovery import get_azure_ad_token
            password = get_azure_ad_token()
        else:
            from gcp_discovery import get_iam_token
            password = get_iam_token()

    with open_tunnel(conn) as (host, port), psycopg.connect(
        **_psycopg_kwargs(conn, host, port, password=password,
                          skip_ssl=conn.get('cloud_proxy_enabled', False))
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
        raise ProxyNotFoundError(binary)

    local_port = _free_port()
    cmd = [binary, instance_id, '--port', str(local_port)]
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
    except OSError as e:
        raise RuntimeError(f'Could not start {binary}: {e}')

    # Capture stderr in a background thread so we don't miss lines emitted
    # after startup (e.g. when proxy crashes mid-connection).
    stderr_lines = []

    def _capture_stderr():
        try:
            for raw in proc.stderr:
                stderr_lines.append(raw.decode('utf-8', errors='replace').rstrip())
        except Exception:
            pass

    stderr_thread = threading.Thread(target=_capture_stderr, daemon=True)
    stderr_thread.start()

    def _get_stderr():
        stderr_thread.join(timeout=1)
        return '\n'.join(stderr_lines).strip()

    def _friendly_proxy_error(detail):
        if 'default credentials' in detail or 'could not find default credentials' in detail:
            return (
                f'{binary} could not authenticate.\n\n'
                'Run the following in a terminal, then retry:\n\n'
                'gcloud auth application-default login\n\n'
                'Note: this is separate from `gcloud auth login` — the proxy '
                'uses Application Default Credentials (ADC).'
            )
        if 'NOT_AUTHORIZED' in detail or 'cloudsql.instances.connect' in detail or (
            '403' in detail and 'forbidden' in detail.lower()
        ):
            return (
                'Permission denied: your account does not have access to this Cloud SQL instance.\n\n'
                'Ask a project owner to grant you the Cloud SQL Client role:\n\n'
                'gcloud projects add-iam-policy-binding PROJECT \\\n'
                '  --member="user:YOUR_EMAIL" \\\n'
                '  --role="roles/cloudsql.client"'
            )
        return f'{binary} crashed.\n\n{detail}' if detail else f'{binary} crashed.'

    try:
        # Wait for the proxy to begin listening (up to 10 s)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(('127.0.0.1', local_port), timeout=0.5):
                    break
            except OSError:
                if proc.poll() is not None:
                    raise RuntimeError(_friendly_proxy_error(_get_stderr()))
                time.sleep(0.2)
        else:
            proc.terminate()
            msg = f'{binary} did not start listening within 10 seconds.'
            detail = _get_stderr()
            if detail:
                msg += f'\n\n{detail}'
            raise RuntimeError(msg)
        try:
            yield '127.0.0.1', local_port
        except Exception as exc:
            # Terminate proxy now so the stderr pipe closes and we can read it.
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=1)
            detail = _get_stderr()
            if detail:
                raise RuntimeError(_friendly_proxy_error(detail)) from exc
            raise
    finally:
        # No-op if already terminated above; cleans up on the success path.
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
    - Secondary endpoint when conn['active_endpoint'] == 'secondary'
    - Direct otherwise
    """
    if conn.get('cloud_proxy_enabled'):
        with _cloud_proxy_tunnel(conn) as (host, port):
            yield host, port
        return

    # Resolve effective DB endpoint (respects Aurora reader toggle)
    if conn.get('active_endpoint') == 'secondary':
        db_host = conn.get('secondary_endpoint') or conn['host']
        db_port = conn.get('secondary_port') or conn['port']
    else:
        db_host = conn['host']
        db_port = conn['port']

    if not conn.get('ssh_enabled'):
        yield db_host, db_port
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
                args=(local_sock, transport, db_host, db_port),
                daemon=True,
            ).start()

    threading.Thread(target=accept_loop, daemon=True).start()

    try:
        yield '127.0.0.1', local_port
    finally:
        stop.set()
        server_sock.close()
        client.close()
