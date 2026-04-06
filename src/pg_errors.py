_PG_ERROR_MAP = {
    '42P01': 'This table no longer exists — it may have been dropped.',
    '42501': "You don't have permission to access this.",
    '08006': 'Lost connection to the database.',
    '28P01': 'Authentication failed — check your username and password.',
    '23505': 'This value already exists — a unique constraint was violated.',
    '23503': 'Cannot complete this operation — a related row in another table depends on it.',
    '23502': 'A required field is missing — a NOT NULL constraint was violated.',
    '40001': 'Transaction conflict — another operation interfered. Please try again.',
    '57014': 'Query was cancelled.',
    '42703': 'Column does not exist.',
    '42P07': 'An object with that name already exists.',
}


def friendly_pg_error(e):
    code = getattr(e, 'pgcode', None) or getattr(e, 'sqlstate', None)
    if code and code in _PG_ERROR_MAP:
        return _PG_ERROR_MAP[code]
    return str(e)
