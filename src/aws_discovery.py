"""AWS database discovery — RDS PostgreSQL and Aurora PostgreSQL via aws CLI.

All public functions return plain dicts / strings and never touch GTK.
The dialog (aws_discovery_dialog.py) calls these from a background thread.
"""

import json
import os
import re
import shutil
import subprocess
import urllib.request
import uuid

CERT_DIR = os.path.join(os.path.expanduser('~'), '.config', 'tusk', 'certs')

_RDS_CA_URL = 'https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem'
_RDS_CA_PATH = os.path.join(CERT_DIR, 'rds-global-bundle.pem')

# Aurora writer: <cluster>.cluster-<id>.<region>.rds.amazonaws.com
_AURORA_WRITER_RE = re.compile(
    r'^([\w-]+)\.cluster-([\w]+)\.([\w-]+)\.rds\.amazonaws\.com$', re.IGNORECASE
)


# ── aws CLI helpers ────────────────────────────────────────────────────────────

def _friendly_aws_error(stderr, fallback):
    """Return a user-readable error message for a failed aws command."""
    detail = stderr or fallback
    low = detail.lower()
    if 'accessdenied' in low or 'unauthorized' in low or 'not authorized' in low:
        return (
            'Permission denied. Your AWS credentials do not have the required permissions.\n\n'
            'Required IAM permissions:\n'
            '  rds:DescribeDBInstances\n'
            '  rds:DescribeDBClusters\n\n'
            'Ask your AWS administrator to attach these permissions to your IAM user or role.'
        )
    if 'unabletolocatecredentials' in low or 'no credentials' in low or 'expired' in low:
        return (
            'AWS credentials not found or expired.\n\n'
            'Run `aws configure` or set up a credential profile, then try again.'
        )
    return detail


def awscli_available():
    """Return True if `aws` is on $PATH."""
    return shutil.which('aws') is not None


def _aws(*args, region=None):
    """Run an aws CLI command and return parsed JSON output.

    Raises RuntimeError with a user-readable message on failure.
    """
    cmd = ['aws'] + list(args) + ['--output', 'json']
    if region:
        cmd += ['--region', region]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f'aws CLI timed out running: {" ".join(cmd)}')
    except FileNotFoundError:
        raise RuntimeError('aws CLI not found on $PATH.')

    if result.returncode != 0:
        raise RuntimeError(_friendly_aws_error(
            result.stderr.strip(), f'aws exited with code {result.returncode}'
        ))
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f'Could not parse aws output: {e}')


def _aws_value(*args):
    """Run an aws CLI command and return stripped text output."""
    cmd = ['aws'] + list(args) + ['--output', 'text']
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    except subprocess.TimeoutExpired:
        raise RuntimeError('aws CLI timed out.')
    except FileNotFoundError:
        raise RuntimeError('aws CLI not found on $PATH.')
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f'aws exited with code {result.returncode}')
    return result.stdout.strip()


def get_default_region():
    """Return the configured default AWS region, or None."""
    try:
        val = _aws_value('configure', 'get', 'region')
        return val if val and val not in ('None', '') else None
    except RuntimeError:
        return None


def get_caller_identity():
    """Return the active AWS identity dict, or raise RuntimeError if not authenticated."""
    return _aws('sts', 'get-caller-identity')


def list_regions():
    """Return a sorted list of available EC2/RDS region name strings.

    Raises RuntimeError on failure.
    """
    data = _aws('ec2', 'describe-regions', '--query', 'Regions[].RegionName')
    return sorted(data if isinstance(data, list) else [])


# ── SSL cert ───────────────────────────────────────────────────────────────────

def get_rds_ca_bundle():
    """Return the RDS global CA bundle path — downloads it if not cached.

    Returns None if the download fails; callers should still import with
    ssl_mode='require' and surface a non-fatal warning to the user.
    """
    if os.path.exists(_RDS_CA_PATH):
        return _RDS_CA_PATH
    try:
        os.makedirs(CERT_DIR, exist_ok=True)
        urllib.request.urlretrieve(_RDS_CA_URL, _RDS_CA_PATH)
        return _RDS_CA_PATH
    except Exception:
        return None


# ── Aurora endpoint helpers ────────────────────────────────────────────────────

def is_aurora_writer_endpoint(host):
    """Return True if host matches the Aurora writer endpoint pattern."""
    return bool(_AURORA_WRITER_RE.match(host or ''))


def aurora_reader_from_writer(writer_host):
    """Derive the Aurora reader endpoint hostname from a writer endpoint.

    Returns None if the hostname does not match the Aurora writer pattern.
    """
    m = _AURORA_WRITER_RE.match(writer_host or '')
    if not m:
        return None
    cluster, cid, region = m.group(1), m.group(2), m.group(3)
    return f'{cluster}.cluster-ro-{cid}.{region}.rds.amazonaws.com'


# ── Discovery ──────────────────────────────────────────────────────────────────

def discover_rds(region):
    """Return a list of RDS PostgreSQL instance dicts for the region."""
    data = _aws(
        'rds', 'describe-db-instances',
        '--query', "DBInstances[?Engine=='postgres']",
        region=region,
    )
    return data if isinstance(data, list) else []


def discover_aurora(region):
    """Return a list of Aurora PostgreSQL cluster dicts for the region."""
    data = _aws(
        'rds', 'describe-db-clusters',
        '--query', "DBClusters[?Engine=='aurora-postgresql']",
        region=region,
    )
    return data if isinstance(data, list) else []


# ── Connection builders ────────────────────────────────────────────────────────

def build_rds_conn(instance, region, cert_path=None):
    """Convert an RDS instance dict into a Tusk connection dict."""
    identifier = instance.get('DBInstanceIdentifier', '')
    engine_version = instance.get('EngineVersion', '')
    iam_enabled = instance.get('IAMDatabaseAuthenticationEnabled', False)

    endpoint = instance.get('Endpoint', {})
    host = endpoint.get('Address', '')
    port = endpoint.get('Port', 5432)

    tags = ['aws']
    if region:
        tags.append(region)

    return {
        'id': str(uuid.uuid4()),
        'name': f'{identifier} (RDS)',
        'host': host,
        'port': port,
        'database': 'postgres',
        'username': 'postgres',
        'cloud_provider': 'aws-rds',
        'cloud_instance_id': identifier,
        'cloud_region': region,
        'cloud_auth_mode': 'iam' if iam_enabled else 'password',
        'cloud_proxy_enabled': False,
        'cloud_proxy_port': None,
        'ssl_mode': 'require',
        'ssl_root_cert': cert_path,
        'tags': tags,
        '_aws_service': 'RDS',
        '_aws_version': engine_version,
        '_aws_region': region,
    }


def build_aurora_conn(cluster, region, cert_path=None):
    """Convert an Aurora cluster dict into a Tusk connection dict.

    Pre-fills secondary_endpoint / secondary_port from the cluster reader
    endpoint, implementing the multi-endpoint profile behaviour from #272.
    """
    identifier = cluster.get('DBClusterIdentifier', '')
    engine_version = cluster.get('EngineVersion', '')
    iam_enabled = cluster.get('IAMDatabaseAuthenticationEnabled', False)

    writer_host = cluster.get('Endpoint', '')
    # Prefer the ReaderEndpoint from the API response; fall back to deriving it.
    reader_host = cluster.get('ReaderEndpoint', '') or aurora_reader_from_writer(writer_host)
    port = cluster.get('Port', 5432)

    tags = ['aws']
    if region:
        tags.append(region)

    return {
        'id': str(uuid.uuid4()),
        'name': f'{identifier} (Aurora)',
        'host': writer_host,
        'port': port,
        'database': 'postgres',
        'username': 'postgres',
        'cloud_provider': 'aws-aurora',
        'cloud_instance_id': identifier,
        'cloud_region': region,
        'cloud_auth_mode': 'iam' if iam_enabled else 'password',
        'cloud_proxy_enabled': False,
        'cloud_proxy_port': None,
        'ssl_mode': 'require',
        'ssl_root_cert': cert_path,
        'secondary_endpoint': reader_host or None,
        'secondary_port': port if reader_host else None,
        'tags': tags,
        '_aws_service': 'Aurora',
        '_aws_version': engine_version,
        '_aws_region': region,
    }


# ── IAM token helper ───────────────────────────────────────────────────────────

def get_iam_token(host, port, dbname, username, region):
    """Generate a short-lived IAM auth token for RDS/Aurora database authentication.

    Raises RuntimeError if the aws CLI is unavailable or the call fails.
    """
    try:
        result = subprocess.run(
            [
                'aws', 'rds', 'generate-db-auth-token',
                '--hostname', host,
                '--port', str(port),
                '--region', region,
                '--username', username,
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except FileNotFoundError:
        raise RuntimeError('aws CLI not found on $PATH.')
    except subprocess.TimeoutExpired:
        raise RuntimeError('aws rds generate-db-auth-token timed out.')
    if result.returncode != 0:
        raise RuntimeError(
            _friendly_aws_error(result.stderr.strip(), 'aws rds generate-db-auth-token failed.')
        )
    token = result.stdout.strip()
    if not token:
        raise RuntimeError('aws returned an empty IAM auth token.')
    return token
