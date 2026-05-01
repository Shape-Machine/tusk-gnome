"""Azure Database for PostgreSQL discovery — Flexible Server via az CLI.

All public functions return plain dicts / strings and never touch GTK.
The dialog (azure_discovery_dialog.py) calls these from a background thread.
"""

import json
import os
import shutil
import subprocess
import urllib.request
import uuid

CERT_DIR = os.path.join(os.path.expanduser('~'), '.config', 'tusk', 'certs')

_AZURE_CA_URL = 'https://dl.cacerts.digicert.com/DigiCertGlobalRootCA.crt.pem'
_AZURE_CA_PATH = os.path.join(CERT_DIR, 'azure-postgres-ca.pem')


# ── az CLI helpers ─────────────────────────────────────────────────────────────

def _friendly_azure_error(stderr, fallback):
    """Return a user-readable error message for a failed az command."""
    detail = stderr or fallback
    low = detail.lower()
    if 'authorizationerror' in low or 'authorization_failed' in low or 'does not have authorization' in low:
        return (
            'Permission denied. Your Azure account does not have the required permissions.\n\n'
            'Required role: Reader on the subscription, or\n'
            '  Microsoft.DBforPostgreSQL/flexibleServers/read\n\n'
            'Ask your Azure administrator to grant you the Reader role on the subscription.'
        )
    if 'please run' in low and 'az login' in low:
        return (
            'Not signed in to Azure.\n\n'
            'Run `az login` in a terminal, then try again.'
        )
    if 'no subscriptions found' in low:
        return (
            'No Azure subscriptions found for your account.\n\n'
            'Make sure your account has access to at least one subscription.'
        )
    if 'cloud not be found' in low or 'resourcenotfound' in low:
        return (
            'The requested Azure resource could not be found.\n\n'
            'Check that your subscription ID is correct and that you have access.'
        )
    return detail


def azcli_available():
    """Return True if `az` is on $PATH."""
    return shutil.which('az') is not None


def _az(*args):
    """Run an az CLI command and return parsed JSON output.

    Raises RuntimeError with a user-readable message on failure.
    """
    cmd = ['az'] + list(args) + ['--output', 'json']
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f'az CLI timed out running: {" ".join(cmd)}')
    except FileNotFoundError:
        raise RuntimeError('az CLI not found on $PATH.')

    if result.returncode != 0:
        raise RuntimeError(_friendly_azure_error(
            result.stderr.strip(), f'az exited with code {result.returncode}'
        ))
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f'Could not parse az output: {e}')


# ── Authentication & account ───────────────────────────────────────────────────

def get_active_account():
    """Return the active az account dict, or raise RuntimeError if not authenticated.

    The returned dict has at least: id, name, isDefault, tenantId, user.name
    """
    return _az('account', 'show')


def list_subscriptions():
    """Return a list of accessible subscription dicts.

    Each dict has: id, name, isDefault, state, tenantId
    Raises RuntimeError on failure.
    """
    data = _az('account', 'list', '--all')
    subs = data if isinstance(data, list) else []
    # Filter to enabled subscriptions only
    return [s for s in subs if s.get('state', '').lower() == 'enabled']


# ── SSL cert ───────────────────────────────────────────────────────────────────

def get_azure_ca_cert():
    """Return the Azure PostgreSQL CA cert path — downloads it if not cached.

    Returns None if the download fails; callers should still import with
    ssl_mode='require' and surface a non-fatal warning to the user.
    """
    if os.path.exists(_AZURE_CA_PATH):
        return _AZURE_CA_PATH
    tmp = _AZURE_CA_PATH + '.tmp'
    try:
        os.makedirs(CERT_DIR, exist_ok=True)
        with urllib.request.urlopen(_AZURE_CA_URL, timeout=15) as resp:
            with open(tmp, 'wb') as f:
                f.write(resp.read())
        os.replace(tmp, _AZURE_CA_PATH)
        return _AZURE_CA_PATH
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        return None


# ── Discovery ──────────────────────────────────────────────────────────────────

def discover_azure_postgres(subscription_id):
    """Return a list of Flexible Server instance dicts for the subscription."""
    data = _az(
        'postgres', 'flexible-server', 'list',
        '--subscription', subscription_id,
    )
    return data if isinstance(data, list) else []


def detect_single_server(subscription_id):
    """Return a list of (deprecated) Single Server instance dicts.

    Returns an empty list on error — Single Server detection is best-effort.
    """
    try:
        data = _az(
            'postgres', 'server', 'list',
            '--subscription', subscription_id,
        )
        return data if isinstance(data, list) else []
    except RuntimeError:
        return []


# ── Connection builder ─────────────────────────────────────────────────────────

def build_azure_conn(server, subscription_id, cert_path=None):
    """Convert an Azure Flexible Server dict into a Tusk connection dict."""
    name = server.get('name', '')
    resource_group = server.get('resourceGroup', '')
    location = server.get('location', '')
    version = server.get('version', '')
    fqdn = server.get('fullyQualifiedDomainName', f'{name}.postgres.database.azure.com')
    admin_login = server.get('administratorLogin', 'postgres')
    resource_id = server.get('id', '')

    tags = ['azure']
    if location:
        tags.append(location)

    return {
        'id': str(uuid.uuid4()),
        'name': f'{name} (Azure)',
        'host': fqdn,
        'port': 5432,
        'database': 'postgres',
        'username': admin_login,
        'cloud_provider': 'azure-database',
        'cloud_instance_id': resource_id or name,
        'cloud_region': location,
        'cloud_auth_mode': 'password',
        'cloud_proxy_enabled': False,
        'cloud_proxy_port': None,
        'ssl_mode': 'require',
        'ssl_root_cert': cert_path,
        'tags': tags,
        '_azure_service': 'Flexible Server',
        '_azure_version': version,
        '_azure_resource_group': resource_group,
        '_azure_location': location,
        '_azure_subscription_id': subscription_id,
    }


# ── Azure AD token ─────────────────────────────────────────────────────────────

def get_azure_ad_token():
    """Return a short-lived Azure AD access token for PostgreSQL authentication.

    Uses the oss-rdbms resource type as required by Azure Database for PostgreSQL.
    Raises RuntimeError if the az CLI is unavailable or the call fails.
    """
    try:
        result = subprocess.run(
            [
                'az', 'account', 'get-access-token',
                '--resource-type', 'oss-rdbms',
                '--output', 'json',
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except FileNotFoundError:
        raise RuntimeError('az CLI not found on $PATH.')
    except subprocess.TimeoutExpired:
        raise RuntimeError('az account get-access-token timed out.')
    if result.returncode != 0:
        raise RuntimeError(
            _friendly_azure_error(result.stderr.strip(), 'az account get-access-token failed.')
        )
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f'Could not parse az token output: {e}')
    token = data.get('accessToken', '')
    if not token:
        raise RuntimeError('az returned an empty access token.')
    return token
