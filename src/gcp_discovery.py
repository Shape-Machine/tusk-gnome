"""GCP database discovery — Cloud SQL and AlloyDB via gcloud CLI.

All public functions return plain dicts / strings and never touch GTK.
The dialog (gcp_discovery_dialog.py) calls these from a background thread.
"""

import json
import os
import shutil
import subprocess
import uuid

CERT_DIR = os.path.join(os.path.expanduser('~'), '.config', 'tusk', 'certs')


# ── gcloud helpers ─────────────────────────────────────────────────────────────

def _friendly_gcloud_error(stderr, fallback):
    """Return a user-readable error message for a failed gcloud command.

    Detects common failure patterns (API not enabled, permission denied) and
    replaces the raw gcloud stderr with actionable guidance.
    """
    detail = stderr or fallback
    low = detail.lower()
    if 'has not been used' in low or 'is disabled' in low or 'enable it by visiting' in low:
        # Extract the API name if present (e.g. sqladmin.googleapis.com)
        api = ''
        for word in detail.split():
            if 'googleapis.com' in word:
                api = word.strip('[].,')
                break
        msg = 'A required GCP API is not enabled on this project.'
        if api:
            msg += f'\n\nEnable it in the GCP console:\nhttps://console.cloud.google.com/apis/library/{api}'
        else:
            msg += '\n\nOpen the GCP console → APIs & Services → Enable APIs and Services.'
        return msg
    return detail


def gcloud_available():
    """Return True if `gcloud` is on $PATH."""
    return shutil.which('gcloud') is not None


def _gcloud(*args, project=None):
    """Run a gcloud command and return parsed JSON output.

    Raises RuntimeError with a user-readable message on failure.
    """
    cmd = ['gcloud'] + list(args) + ['--format=json']
    if project:
        cmd += ['--project', project]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f'gcloud timed out running: {" ".join(cmd)}')
    except FileNotFoundError:
        raise RuntimeError('gcloud not found on $PATH.')

    if result.returncode != 0:
        raise RuntimeError(_friendly_gcloud_error(result.stderr.strip(),
                                                   f'gcloud exited with code {result.returncode}'))

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f'Could not parse gcloud output: {e}')


def _gcloud_value(*args, project=None):
    """Run a gcloud command with --format=value(...) and return stripped output."""
    cmd = ['gcloud'] + list(args)
    if project:
        cmd += ['--project', project]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError('gcloud timed out.')
    except FileNotFoundError:
        raise RuntimeError('gcloud not found on $PATH.')
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f'gcloud exited with code {result.returncode}')
    return result.stdout.strip()


def get_active_project():
    """Return the currently configured gcloud project, or None."""
    try:
        val = _gcloud_value('config', 'get-value', 'project')
    except RuntimeError:
        return None
    return val if val and val != '(unset)' else None


def get_active_account():
    """Return the active gcloud account email, or None if not authenticated."""
    try:
        accounts = _gcloud('auth', 'list', '--filter=status:ACTIVE')
        if accounts:
            return accounts[0].get('account')
    except RuntimeError:
        pass
    return None


# ── Cloud SQL discovery ────────────────────────────────────────────────────────

def discover_cloud_sql(project):
    """Return a list of Cloud SQL PostgreSQL instance dicts for the project."""
    instances = _gcloud(
        'sql', 'instances', 'list',
        '--filter=databaseVersion:POSTGRES*',
        project=project,
    )
    return instances if isinstance(instances, list) else []


def save_cloud_sql_server_ca(instance, project):
    """Extract the server CA cert from the instance dict and write it to disk.

    The cert is already present in the instances list response under
    instance['serverCaCert']['cert'], so no extra gcloud call is needed.
    Returns the file path, or None if the cert is missing/write fails.
    """
    try:
        pem = instance.get('serverCaCert', {}).get('cert', '')
        if not pem:
            return None
        instance_name = instance.get('name', 'unknown')
        os.makedirs(CERT_DIR, exist_ok=True)
        cert_path = os.path.join(CERT_DIR, f'cloudsql-{project}-{instance_name}.pem')
        with open(cert_path, 'w') as f:
            f.write(pem)
        return cert_path
    except Exception:
        return None


def _has_public_ip(instance):
    """Return True if the Cloud SQL instance has a public IP address."""
    for ip in instance.get('ipAddresses', []):
        if ip.get('type') == 'PRIMARY':
            return True
    return False


def _iam_auth_enabled(instance):
    """Return True if the Cloud SQL instance has IAM database authentication on."""
    for flag in instance.get('settings', {}).get('databaseFlags', []):
        if flag.get('name') == 'cloudsql.iam_authentication' and flag.get('value') == 'on':
            return True
    return False


def build_cloud_sql_conn(instance, project, fetch_cert=True):
    """Convert a Cloud SQL instance dict into a Tusk connection dict."""
    name = instance.get('name', '')
    region = instance.get('region', '')
    db_version = instance.get('databaseVersion', '')  # e.g. POSTGRES_15
    connection_name = instance.get('connectionName', f'{project}:{region}:{name}')

    has_pub_ip = _has_public_ip(instance)
    proxy_enabled = not has_pub_ip
    iam_enabled = _iam_auth_enabled(instance)

    # Pick a reachable host: public IP if available, else localhost (proxy)
    host = 'localhost'
    for ip in instance.get('ipAddresses', []):
        if ip.get('type') == 'PRIMARY':
            host = ip['ipAddress']
            break

    cert_path = save_cloud_sql_server_ca(instance, project) if fetch_cert else None

    tags = ['gcp']
    if region:
        tags.append(region)

    conn = {
        'id': str(uuid.uuid4()),
        'name': f'{name} (Cloud SQL)',
        'host': host,
        'port': 5432,
        'database': 'postgres',
        'username': 'postgres',
        'cloud_provider': 'gcp-cloudsql',
        'cloud_instance_id': connection_name,
        'cloud_region': region,
        'cloud_auth_mode': 'iam' if iam_enabled else 'password',
        'cloud_proxy_enabled': proxy_enabled,
        'cloud_proxy_port': None,
        'ssl_mode': 'require',
        'ssl_root_cert': cert_path,
        'tags': tags,
        '_gcp_service': 'Cloud SQL',
        '_gcp_version': db_version,
        '_gcp_region': region,
    }
    return conn


# ── AlloyDB discovery ──────────────────────────────────────────────────────────

def discover_alloydb(project):
    """Return a list of (cluster, instance) tuples for AlloyDB primary instances.

    Raises RuntimeError if the initial cluster list fails (auth error, API disabled, etc.).
    Per-cluster instance listing errors are silently skipped so a partial list is returned.
    """
    clusters = _gcloud('alloydb', 'clusters', 'list', '--region=-', project=project)
    if not isinstance(clusters, list):
        return []

    results = []
    for cluster in clusters:
        cluster_id = cluster.get('name', '').split('/')[-1]
        region = cluster.get('name', '').split('/')[-3] if '/' in cluster.get('name', '') else ''
        try:
            instances = _gcloud(
                'alloydb', 'instances', 'list',
                f'--cluster={cluster_id}',
                f'--region={region}',
                project=project,
            )
        except RuntimeError:
            continue
        if not isinstance(instances, list):
            continue
        for inst in instances:
            # Primary instances only (exclude READ_POOL per issue spec)
            if inst.get('instanceType') == 'PRIMARY':
                results.append((cluster, inst))
    return results


def fetch_alloydb_server_ca(cluster_name, project):
    """Return the AlloyDB server CA cert path, or None.

    AlloyDB uses a Google-managed CA that is trusted by the system root store,
    so no custom ssl_root_cert is needed. psycopg will validate against system
    CAs when ssl_mode='require'. Returns None to leave ssl_root_cert unset.
    """
    return None


def _alloydb_has_public_ip(instance):
    """Return True if the AlloyDB instance has a public IP address."""
    return bool(instance.get('publicIpAddress'))


def build_alloydb_conn(cluster, instance, project, fetch_cert=True):
    """Convert an AlloyDB (cluster, instance) pair into a Tusk connection dict."""
    cluster_id = cluster.get('name', '').split('/')[-1]
    region = cluster.get('name', '').split('/')[-3] if '/' in cluster.get('name', '') else ''
    instance_id = instance.get('name', '').split('/')[-1]

    has_pub_ip = _alloydb_has_public_ip(instance)
    proxy_enabled = not has_pub_ip

    # Public IP if available, else localhost (proxy)
    host = instance.get('publicIpAddress') or 'localhost'

    # AlloyDB instance URI for Auth Proxy: projects/PROJECT/locations/REGION/clusters/CLUSTER/instances/INSTANCE
    instance_uri = instance.get('name', '')

    cert_path = None
    if fetch_cert:
        cert_path = fetch_alloydb_server_ca(cluster_id, project)

    tags = ['gcp']
    if region:
        tags.append(region)

    conn = {
        'id': str(uuid.uuid4()),
        'name': f'{cluster_id}/{instance_id} (AlloyDB)',
        'host': host,
        'port': 5432,
        'database': 'postgres',
        'username': 'postgres',
        'cloud_provider': 'gcp-alloydb',
        'cloud_instance_id': instance_uri,
        'cloud_region': region,
        'cloud_auth_mode': 'iam',   # AlloyDB defaults to IAM auth
        'cloud_proxy_enabled': proxy_enabled,
        'cloud_proxy_port': None,
        'ssl_mode': 'require',
        'ssl_root_cert': cert_path,
        'tags': tags,
        '_gcp_service': 'AlloyDB',
        '_gcp_version': 'AlloyDB',
        '_gcp_region': region,
    }
    return conn


# ── IAM token helper ───────────────────────────────────────────────────────────

def get_iam_token():
    """Return a fresh access token from gcloud for IAM database authentication.

    Raises RuntimeError if gcloud is unavailable or authentication fails.
    """
    try:
        result = subprocess.run(
            ['gcloud', 'auth', 'print-access-token'],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except FileNotFoundError:
        raise RuntimeError('gcloud not found on $PATH.')
    except subprocess.TimeoutExpired:
        raise RuntimeError('gcloud auth print-access-token timed out.')
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or 'gcloud auth print-access-token failed.')
    token = result.stdout.strip()
    if not token:
        raise RuntimeError('gcloud returned an empty access token.')
    return token
