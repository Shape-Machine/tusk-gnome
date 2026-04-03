_PG_ERROR_MAP = {
    '42P01': 'This table no longer exists — it may have been dropped.',
    '42501': "You don't have permission to access this.",
    '08006': 'Lost connection to the database.',
    '28P01': 'Authentication failed — check your username and password.',
}


def friendly_pg_error(e):
    code = getattr(e, 'pgcode', None) or getattr(e, 'sqlstate', None)
    if code and code in _PG_ERROR_MAP:
        return _PG_ERROR_MAP[code]
    return str(e)
