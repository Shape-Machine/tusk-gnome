import threading

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Gtk, Adw, GLib, Gio, GObject, Pango

from pg_errors import friendly_pg_error as _friendly_pg_error
from style import MARGIN_XS, MARGIN_SM, MARGIN_MD


# ── SQL ───────────────────────────────────────────────────────────────────────

_MEMBERSHIPS_SQL = """
    SELECT g.rolname AS group_role,
           m.admin_option
    FROM pg_auth_members m
    JOIN pg_roles g ON g.oid = m.roleid
    JOIN pg_roles r ON r.oid = m.member
    WHERE r.rolname = %s
    ORDER BY g.rolname
"""

_ALL_GROUP_ROLES_SQL = """
    SELECT rolname FROM pg_roles
    WHERE NOT rolcanlogin
    ORDER BY rolname
"""

_ALL_MEMBERSHIPS_SQL = """
    SELECT r.rolname AS member, g.rolname AS group_role, m.admin_option
    FROM pg_auth_members m
    JOIN pg_roles r ON r.oid = m.member
    JOIN pg_roles g ON g.oid = m.roleid
    ORDER BY r.rolname, g.rolname
"""

_EFFECTIVE_PERMS_SQL = """
    WITH RECURSIVE role_tree(member, roleid) AS (
        SELECT m.member, m.roleid
        FROM pg_auth_members m
        JOIN pg_roles r ON r.oid = m.member
        WHERE r.rolname = %s
        UNION
        SELECT m.member, m.roleid
        FROM pg_auth_members m
        JOIN role_tree rt ON rt.roleid = m.member
    ),
    all_roles AS (
        SELECT rolname FROM pg_roles WHERE rolname = %s
        UNION
        SELECT g.rolname
        FROM role_tree rt
        JOIN pg_roles g ON g.oid = rt.roleid
    )
    SELECT DISTINCT
        g.table_schema || '.' || g.table_name AS object,
        g.privilege_type,
        CASE WHEN g.grantee = %s THEN 'direct'
             ELSE 'via ' || g.grantee
        END AS source
    FROM information_schema.role_table_grants g
    WHERE g.grantee IN (SELECT rolname FROM all_roles)
      AND g.table_schema NOT IN ('pg_catalog','information_schema')
    ORDER BY object, g.privilege_type
"""

_TABLE_PRIVS_SQL = """
    SELECT privilege_type
    FROM information_schema.role_table_grants
    WHERE grantee = %s
      AND table_schema = %s
      AND table_name = %s
"""

_SCHEMAS_SQL = """
    SELECT schema_name FROM information_schema.schemata
    WHERE schema_name NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
    ORDER BY schema_name
"""

_TABLES_IN_SCHEMA_SQL = """
    SELECT table_name FROM information_schema.tables
    WHERE table_schema = %s
      AND table_type IN ('BASE TABLE', 'VIEW')
    ORDER BY table_name
"""

_TABLE_PRIVS_ALL = ['SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE', 'REFERENCES', 'TRIGGER']


def _make_scrolled(child):
    sw = Gtk.ScrolledWindow()
    sw.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
    sw.set_vexpand(True)
    sw.set_hexpand(True)
    sw.set_child(child)
    return sw


def _make_column_view(cols):
    store = Gio.ListStore(item_type=_Row.__gtype__)
    cv = Gtk.ColumnView(model=Gtk.NoSelection(model=store))
    cv.set_hexpand(True)
    cv.set_vexpand(True)
    cv.add_css_class('data-table')
    for col_title in cols:
        factory = Gtk.SignalListItemFactory()
        factory.connect('setup', lambda f, item: item.set_child(
            Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, margin_start=6, margin_end=6)
        ))
        col_obj = Gtk.ColumnViewColumn(title=col_title, factory=factory)
        col_obj.set_expand(True)
        cv.append_column(col_obj)
    return store, cv


class _Row(GObject.Object):
    def __init__(self, values):
        super().__init__()
        self.values = values


def _bind_row(col_index):
    def _bind(factory, item):
        label = item.get_child()
        row = item.get_item()
        if row and col_index < len(row.values):
            label.set_label(str(row.values[col_index]) if row.values[col_index] is not None else '')
    return _bind


def _wire_column_view(cv, store, cols):
    """Wire up bind callbacks for a column view built with _make_column_view."""
    columns = cv.get_columns()
    for i in range(len(cols)):
        col = columns.get_item(i)
        col.get_factory().connect('bind', _bind_row(i))
    return store


# ── MembershipsTab ────────────────────────────────────────────────────────────

class MembershipsTab(Gtk.Box):
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._conn = None
        self._role_name = None
        self._memberships = []
        # Cache: conn id → {role_name: [(group_role, admin_opt), ...]}
        # Prefetched on first load per connection; invalidated on grant/revoke.
        self._cache = {}
        # In-flight set: conn ids for which a prefetch thread is already running.
        self._loading = set()

        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        toolbar.set_margin_top(MARGIN_SM)
        toolbar.set_margin_bottom(MARGIN_SM)
        toolbar.set_margin_start(MARGIN_MD)
        toolbar.set_margin_end(MARGIN_MD)

        self._grant_btn = Gtk.Button(label='Grant Membership…')
        self._grant_btn.add_css_class('suggested-action')
        self._grant_btn.connect('clicked', self._on_grant_clicked)
        toolbar.append(self._grant_btn)

        self._status_label = Gtk.Label()
        self._status_label.add_css_class('dim-label')
        self._status_label.add_css_class('caption')
        toolbar.append(self._status_label)

        refresh_btn = Gtk.Button(icon_name='view-refresh-symbolic')
        refresh_btn.add_css_class('flat')
        refresh_btn.set_tooltip_text('Refresh')
        refresh_btn.connect('clicked', self._on_refresh_clicked)
        toolbar.append(refresh_btn)

        self.append(toolbar)
        self.append(Gtk.Separator())

        self._store, cv = _make_column_view(['Group Role', 'Admin Option'])
        _wire_column_view(cv, self._store, ['Group Role', 'Admin Option'])
        self.append(_make_scrolled(cv))

    def load(self, conn, role_name):
        self._conn = conn
        self._role_name = role_name
        conn_key = id(conn)
        if conn_key in self._cache:
            # Serve from cache instantly — no round-trip needed
            rows = self._cache[conn_key].get(role_name, [])
            self._populate(rows, None)
            return
        self._status_label.set_label('Loading…')
        if conn_key in self._loading:
            # A prefetch is already in-flight; result will populate via _store_cache_and_populate
            return
        self._loading.add(conn_key)
        threading.Thread(
            target=self._fetch_all_memberships, args=(conn, role_name), daemon=True
        ).start()

    def _fetch_all_memberships(self, conn, role_name):
        """Fetch all role memberships in one query and populate the cache."""
        try:
            from tunnel import open_db
            with open_db(conn) as db:
                with db.cursor() as cur:
                    cur.execute(_ALL_MEMBERSHIPS_SQL)
                    rows = cur.fetchall()
            cache = {}
            for member, group_role, admin_opt in rows:
                cache.setdefault(member, []).append((group_role, admin_opt))
            GLib.idle_add(self._store_cache_and_populate, conn, cache, role_name)
        except Exception as e:
            GLib.idle_add(self._on_fetch_error, conn, _friendly_pg_error(e))

    def _on_fetch_error(self, conn, msg):
        self._loading.discard(id(conn))
        self._populate(None, msg)

    def _store_cache_and_populate(self, conn, cache, role_name):
        self._loading.discard(id(conn))
        self._cache[id(conn)] = cache
        if self._conn is conn and self._role_name == role_name:
            self._populate(cache.get(role_name, []), None)

    def _on_refresh_clicked(self, _btn):
        if self._conn:
            conn_key = id(self._conn)
            self._cache.pop(conn_key, None)
            self._loading.discard(conn_key)
            self._status_label.set_label('Loading…')
            self._loading.add(conn_key)
            threading.Thread(
                target=self._fetch_all_memberships, args=(self._conn, self._role_name), daemon=True
            ).start()

    def _populate(self, rows, error):
        self._store.remove_all()
        if error:
            self._status_label.set_label(f'Error: {error}')
            return
        self._memberships = rows or []
        for group_role, admin_opt in self._memberships:
            self._store.append(_Row([group_role, 'yes' if admin_opt else 'no']))
        count = len(self._memberships)
        self._status_label.set_label(
            f'{count} group membership{"s" if count != 1 else ""}'
        )

    def _on_grant_clicked(self, _btn):
        if not self._conn:
            return
        dialog = _GrantMembershipDialog(
            transient_for=self.get_root(),
            conn=self._conn,
            role_name=self._role_name,
        )
        dialog.connect('membership-granted', lambda *_: self._invalidate_and_reload())
        dialog.present()

    def _invalidate_and_reload(self):
        self._cache.pop(id(self._conn), None)
        self.load(self._conn, self._role_name)

    def revoke_selected(self, group_role):
        if not self._conn:
            return
        dialog = Adw.MessageDialog(
            transient_for=self.get_root(),
            heading='Revoke Membership?',
            body=f'Revoke membership of "{self._role_name}" from group "{group_role}"?',
        )
        dialog.add_response('cancel', 'Cancel')
        dialog.add_response('revoke', 'Revoke')
        dialog.set_response_appearance('revoke', Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response('cancel')
        dialog.connect('response', self._on_revoke_response, group_role)
        dialog.present()

    def _on_revoke_response(self, _dialog, response, group_role):
        if response != 'revoke':
            return
        threading.Thread(
            target=self._do_revoke, args=(group_role,), daemon=True
        ).start()

    def _do_revoke(self, group_role):
        try:
            from tunnel import open_db
            from psycopg import sql
            with open_db(self._conn) as db:
                with db.cursor() as cur:
                    cur.execute(sql.SQL('REVOKE {} FROM {}').format(
                        sql.Identifier(group_role),
                        sql.Identifier(self._role_name),
                    ))
                db.commit()
            GLib.idle_add(self._invalidate_and_reload)
        except Exception as e:
            GLib.idle_add(self._show_error, _friendly_pg_error(e))

    def _show_error(self, msg):
        self._status_label.set_label(f'Error: {msg}')


class _GrantMembershipDialog(Adw.Dialog):
    __gsignals__ = {
        'membership-granted': (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, transient_for, conn, role_name):
        super().__init__(title='Grant Membership', content_width=360)
        self.add_css_class('tusk-main')
        self._conn = conn
        self._role_name = role_name

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(False)
        cancel_btn = Gtk.Button(label='Cancel')
        cancel_btn.connect('clicked', lambda _: self.close())
        header.pack_start(cancel_btn)
        self._grant_btn = Gtk.Button(label='Grant')
        self._grant_btn.add_css_class('suggested-action')
        self._grant_btn.set_sensitive(False)
        self._grant_btn.connect('clicked', self._on_grant)
        header.pack_end(self._grant_btn)
        box.append(header)

        prefs_group = Adw.PreferencesGroup()
        prefs_group.set_margin_top(12)
        prefs_group.set_margin_bottom(12)
        prefs_group.set_margin_start(12)
        prefs_group.set_margin_end(12)

        self._group_row = Adw.ComboRow(title='Group Role')
        self._group_model = Gtk.StringList()
        self._group_row.set_model(self._group_model)
        prefs_group.add(self._group_row)

        self._admin_row = Adw.SwitchRow(title='WITH ADMIN OPTION',
                                         subtitle='Allows this role to grant the group to others')
        prefs_group.add(self._admin_row)

        self._error_status = Adw.StatusPage(
            icon_name='dialog-error-symbolic',
            title='Could Not Load Roles',
        )
        self._error_status.set_visible(False)
        box.append(prefs_group)
        box.append(self._error_status)

        self.set_child(box)

        threading.Thread(target=self._load_groups, daemon=True).start()

    def _load_groups(self):
        try:
            from tunnel import open_db
            with open_db(self._conn) as db:
                with db.cursor() as cur:
                    cur.execute(_ALL_GROUP_ROLES_SQL)
                    groups = [r[0] for r in cur.fetchall()]
            GLib.idle_add(self._populate_groups, groups)
        except Exception as e:
            GLib.idle_add(self._show_load_error, _friendly_pg_error(e))

    def _show_load_error(self, msg):
        self._error_status.set_description(msg)
        self._error_status.set_visible(True)

    def _populate_groups(self, groups):
        for g in groups:
            self._group_model.append(g)
        self._grant_btn.set_sensitive(len(groups) > 0)

    def _on_grant(self, _btn):
        idx = self._group_row.get_selected()
        item = self._group_model.get_item(idx)
        if not item:
            return
        group_role = item.get_string()
        admin_opt = self._admin_row.get_active()
        threading.Thread(
            target=self._do_grant, args=(group_role, admin_opt), daemon=True
        ).start()

    def _do_grant(self, group_role, admin_opt):
        try:
            from tunnel import open_db
            from psycopg import sql
            with open_db(self._conn) as db:
                with db.cursor() as cur:
                    stmt = sql.SQL('GRANT {} TO {}').format(
                        sql.Identifier(group_role),
                        sql.Identifier(self._role_name),
                    )
                    if admin_opt:
                        stmt = sql.SQL('{} WITH ADMIN OPTION').format(stmt)
                    cur.execute(stmt)
                db.commit()
            GLib.idle_add(self._on_done)
        except Exception as e:
            GLib.idle_add(self._on_error, _friendly_pg_error(e))

    def _on_done(self):
        self.emit('membership-granted')
        self.close()

    def _on_error(self, msg):
        toast = Adw.Toast(title=f'Error: {msg}')
        toast.set_timeout(4)
        # Best effort — show in root overlay if available
        root = self.get_root()
        if hasattr(root, 'add_toast'):
            root.add_toast(toast)


# ── EffectivePermissionsTab ───────────────────────────────────────────────────

class EffectivePermissionsTab(Gtk.Box):
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._conn = None
        self._role_name = None

        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        toolbar.set_margin_top(MARGIN_SM)
        toolbar.set_margin_bottom(MARGIN_SM)
        toolbar.set_margin_start(MARGIN_MD)
        toolbar.set_margin_end(MARGIN_MD)

        self._status_label = Gtk.Label()
        self._status_label.add_css_class('dim-label')
        self._status_label.add_css_class('caption')
        toolbar.append(self._status_label)

        refresh_btn = Gtk.Button(icon_name='view-refresh-symbolic')
        refresh_btn.add_css_class('flat')
        refresh_btn.set_tooltip_text('Refresh')
        refresh_btn.connect('clicked', lambda _: self.load(self._conn, self._role_name))
        toolbar.append(refresh_btn)

        self.append(toolbar)
        self.append(Gtk.Separator())

        self._store, cv = _make_column_view(['Object', 'Privilege', 'Source'])
        _wire_column_view(cv, self._store, ['Object', 'Privilege', 'Source'])
        self.append(_make_scrolled(cv))

    def load(self, conn, role_name):
        self._conn = conn
        self._role_name = role_name
        self._status_label.set_label('Loading…')
        threading.Thread(target=self._fetch, daemon=True).start()

    def _fetch(self):
        try:
            from tunnel import open_db
            with open_db(self._conn) as db:
                with db.cursor() as cur:
                    cur.execute(
                        _EFFECTIVE_PERMS_SQL,
                        (self._role_name, self._role_name, self._role_name)
                    )
                    rows = cur.fetchall()
            GLib.idle_add(self._populate, rows, None)
        except Exception as e:
            GLib.idle_add(self._populate, None, _friendly_pg_error(e))

    def _populate(self, rows, error):
        self._store.remove_all()
        if error:
            self._status_label.set_label(f'Error: {error}')
            return
        for row in (rows or []):
            self._store.append(_Row(list(row)))
        count = len(rows or [])
        self._status_label.set_label(
            f'{count} privilege{"s" if count != 1 else ""}'
        )


# ── ObjectPrivilegesTab ───────────────────────────────────────────────────────

class ObjectPrivilegesTab(Gtk.Box):
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._conn = None
        self._role_name = None
        self._current_privs = set()
        self._updating = False

        # Top bar: schema + table selectors
        selectors = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        selectors.set_margin_top(8)
        selectors.set_margin_bottom(8)
        selectors.set_margin_start(10)
        selectors.set_margin_end(10)

        schema_label = Gtk.Label(label='Schema:')
        schema_label.add_css_class('dim-label')
        selectors.append(schema_label)

        self._schema_model = Gtk.StringList()
        self._schema_drop = Gtk.DropDown(model=self._schema_model)
        self._schema_drop.set_enable_search(True)
        self._schema_drop.connect('notify::selected', self._on_schema_changed)
        selectors.append(self._schema_drop)

        table_label = Gtk.Label(label='Table:')
        table_label.add_css_class('dim-label')
        selectors.append(table_label)

        self._table_model = Gtk.StringList()
        self._table_drop = Gtk.DropDown(model=self._table_model)
        self._table_drop.set_enable_search(True)
        self._table_drop.set_hexpand(True)
        self._table_drop.connect('notify::selected', self._on_table_changed)
        selectors.append(self._table_drop)

        self._status_label = Gtk.Label()
        self._status_label.add_css_class('dim-label')
        self._status_label.add_css_class('caption')
        selectors.append(self._status_label)

        self.append(selectors)
        self.append(Gtk.Separator())

        # Privilege checkboxes
        self._checks_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._checks_box.set_margin_top(8)
        self._checks_box.set_margin_start(16)
        self._checks_box.set_margin_end(16)
        self._checks_box.set_margin_bottom(8)

        self._check_widgets = {}
        for priv in _TABLE_PRIVS_ALL:
            cb = Gtk.CheckButton(label=priv)
            cb.connect('toggled', self._on_priv_toggled, priv)
            self._check_widgets[priv] = cb
            self._checks_box.append(cb)

        sw = _make_scrolled(self._checks_box)
        self.append(sw)

        self._set_checks_sensitive(False)

    def _set_checks_sensitive(self, sensitive):
        for cb in self._check_widgets.values():
            cb.set_sensitive(sensitive)

    def load(self, conn, role_name):
        self._conn = conn
        self._role_name = role_name
        self._set_checks_sensitive(False)
        threading.Thread(target=self._fetch_schemas, daemon=True).start()

    def _fetch_schemas(self):
        try:
            from tunnel import open_db
            with open_db(self._conn) as db:
                with db.cursor() as cur:
                    cur.execute(_SCHEMAS_SQL)
                    schemas = [r[0] for r in cur.fetchall()]
            GLib.idle_add(self._populate_schemas, schemas)
        except Exception as e:
            GLib.idle_add(lambda: self._status_label.set_label(f'Error: {e}'))

    def _populate_schemas(self, schemas):
        self._updating = True
        while self._schema_model.get_n_items():
            self._schema_model.remove(0)
        for s in schemas:
            self._schema_model.append(s)
        if schemas:
            self._schema_drop.set_selected(0)
        self._updating = False
        if schemas:
            self._load_tables(schemas[0])

    def _on_schema_changed(self, drop, _pspec):
        if self._updating:
            return
        idx = drop.get_selected()
        item = self._schema_model.get_item(idx)
        if item:
            self._load_tables(item.get_string())

    def _load_tables(self, schema):
        self._set_checks_sensitive(False)
        threading.Thread(
            target=self._fetch_tables, args=(schema,), daemon=True
        ).start()

    def _fetch_tables(self, schema):
        try:
            from tunnel import open_db
            with open_db(self._conn) as db:
                with db.cursor() as cur:
                    cur.execute(_TABLES_IN_SCHEMA_SQL, (schema,))
                    tables = [r[0] for r in cur.fetchall()]
            GLib.idle_add(self._populate_tables, tables)
        except Exception as e:
            GLib.idle_add(lambda: self._status_label.set_label(f'Error: {e}'))

    def _populate_tables(self, tables):
        self._updating = True
        while self._table_model.get_n_items():
            self._table_model.remove(0)
        for t in tables:
            self._table_model.append(t)
        if tables:
            self._table_drop.set_selected(0)
        self._updating = False
        if tables:
            self._load_privs()

    def _on_table_changed(self, _drop, _pspec):
        if self._updating:
            return
        self._load_privs()

    def _get_selected_schema_table(self):
        s_idx = self._schema_drop.get_selected()
        t_idx = self._table_drop.get_selected()
        s_item = self._schema_model.get_item(s_idx)
        t_item = self._table_model.get_item(t_idx)
        if s_item and t_item:
            return s_item.get_string(), t_item.get_string()
        return None, None

    def _load_privs(self):
        schema, table = self._get_selected_schema_table()
        if not schema or not table:
            return
        self._set_checks_sensitive(False)
        self._status_label.set_label('Loading…')
        threading.Thread(
            target=self._fetch_privs, args=(schema, table), daemon=True
        ).start()

    def _fetch_privs(self, schema, table):
        try:
            from tunnel import open_db
            with open_db(self._conn) as db:
                with db.cursor() as cur:
                    cur.execute(_TABLE_PRIVS_SQL, (self._role_name, schema, table))
                    privs = {r[0] for r in cur.fetchall()}
            GLib.idle_add(self._populate_privs, privs)
        except Exception as e:
            GLib.idle_add(lambda: self._status_label.set_label(f'Error: {e}'))

    def _populate_privs(self, privs):
        self._current_privs = privs
        self._updating = True
        for priv, cb in self._check_widgets.items():
            cb.set_active(priv in privs)
        self._updating = False
        self._set_checks_sensitive(True)
        self._status_label.set_label('')

    def _on_priv_toggled(self, cb, priv):
        if self._updating:
            return
        schema, table = self._get_selected_schema_table()
        if not schema or not table:
            return
        grant = cb.get_active()
        self._set_checks_sensitive(False)
        threading.Thread(
            target=self._do_grant_revoke,
            args=(schema, table, priv, grant),
            daemon=True,
        ).start()

    def _do_grant_revoke(self, schema, table, priv, grant):
        try:
            from tunnel import open_db
            from psycopg import sql
            with open_db(self._conn) as db:
                with db.cursor() as cur:
                    # priv is one of a fixed whitelist (_TABLE_PRIVS_ALL) — safe to inline
                    if grant:
                        stmt = sql.SQL('GRANT {} ON TABLE {}.{} TO {}').format(
                            sql.SQL(priv),
                            sql.Identifier(schema),
                            sql.Identifier(table),
                            sql.Identifier(self._role_name),
                        )
                    else:
                        stmt = sql.SQL('REVOKE {} ON TABLE {}.{} FROM {}').format(
                            sql.SQL(priv),
                            sql.Identifier(schema),
                            sql.Identifier(table),
                            sql.Identifier(self._role_name),
                        )
                    cur.execute(stmt)
                db.commit()
            GLib.idle_add(self._load_privs)
        except Exception as e:
            GLib.idle_add(self._on_grant_error, _friendly_pg_error(e))

    def _on_grant_error(self, msg):
        self._status_label.set_label(f'Error: {msg}')
        self._load_privs()  # reload to show actual state


# ── RolePanel ─────────────────────────────────────────────────────────────────

class RolePanel(Gtk.Box):
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._conn = None
        self._role_name = None

        # View stack
        self._stack = Adw.ViewStack()
        self._stack.set_vexpand(True)

        self._memberships_tab = MembershipsTab()
        self._effective_tab = EffectivePermissionsTab()
        self._object_tab = ObjectPrivilegesTab()

        self._stack.add_titled_with_icon(
            self._memberships_tab, 'memberships', 'Memberships', 'system-users-symbolic'
        )
        self._stack.add_titled_with_icon(
            self._effective_tab, 'effective', 'Effective Permissions', 'security-high-symbolic'
        )
        self._stack.add_titled_with_icon(
            self._object_tab, 'objects', 'Object Privileges', 'emblem-system-symbolic'
        )

        # Switcher at top (matches TablePanel pattern)
        switcher = Adw.ViewSwitcher()
        switcher.set_stack(self._stack)
        switcher.set_policy(Adw.ViewSwitcherPolicy.WIDE)
        switcher.set_hexpand(True)

        switcher_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        switcher_bar.append(switcher)

        self.append(switcher_bar)
        self.append(Gtk.Separator())
        self.append(self._stack)

    def load(self, conn, role_name):
        self._conn = conn
        self._role_name = role_name
        self._memberships_tab.load(conn, role_name)
        self._effective_tab.load(conn, role_name)
        self._object_tab.load(conn, role_name)


# ── _NewRoleDialog ────────────────────────────────────────────────────────────

class _NewRoleDialog(Adw.Dialog):
    __gsignals__ = {
        'role-created': (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self, conn):
        super().__init__(title='New Role', content_width=400, content_height=560)
        self.add_css_class('tusk-main')
        self._conn = conn

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(False)
        cancel_btn = Gtk.Button(label='Cancel')
        cancel_btn.connect('clicked', lambda _: self.close())
        header.pack_start(cancel_btn)
        self._create_btn = Gtk.Button(label='Create')
        self._create_btn.add_css_class('suggested-action')
        self._create_btn.set_sensitive(False)
        self._create_btn.connect('clicked', self._on_create)
        header.pack_end(self._create_btn)
        box.append(header)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.set_margin_top(12)
        content.set_margin_bottom(12)
        content.set_margin_start(12)
        content.set_margin_end(12)

        identity_group = Adw.PreferencesGroup(title='Identity')

        self._name_row = Adw.EntryRow(title='Role Name')
        self._name_row.connect('changed', self._on_name_changed)
        identity_group.add(self._name_row)

        self._password_row = Adw.PasswordEntryRow(title='Password (optional)')
        identity_group.add(self._password_row)

        content.append(identity_group)

        caps_group = Adw.PreferencesGroup(title='Capabilities')

        self._login_row = Adw.SwitchRow(title='Can Login',
                                         subtitle='Role can be used to connect to a database')
        caps_group.add(self._login_row)

        self._superuser_row = Adw.SwitchRow(title='Superuser',
                                             subtitle='Bypasses all permission checks')
        caps_group.add(self._superuser_row)

        self._createdb_row = Adw.SwitchRow(title='Create Databases')
        caps_group.add(self._createdb_row)

        self._createrole_row = Adw.SwitchRow(title='Create Roles')
        caps_group.add(self._createrole_row)

        self._inherit_row = Adw.SwitchRow(title='Inherit Privileges',
                                           subtitle='Automatically inherits privileges from member roles')
        self._inherit_row.set_active(True)
        caps_group.add(self._inherit_row)

        content.append(caps_group)

        limit_group = Adw.PreferencesGroup(title='Limits')

        self._conn_limit_row = Adw.SpinRow.new_with_range(-1, 9999, 1)
        self._conn_limit_row.set_title('Connection Limit')
        self._conn_limit_row.set_subtitle('-1 means no limit')
        self._conn_limit_row.set_value(-1)
        limit_group.add(self._conn_limit_row)

        content.append(limit_group)

        self._error_label = Gtk.Label()
        self._error_label.add_css_class('error')
        self._error_label.add_css_class('caption')
        self._error_label.set_wrap(True)
        self._error_label.set_visible(False)
        content.append(self._error_label)

        scroll.set_child(content)
        box.append(scroll)
        self.set_child(box)

    def _on_name_changed(self, _row):
        self._create_btn.set_sensitive(bool(self._name_row.get_text().strip()))
        self._error_label.set_visible(False)

    def _on_create(self, _btn):
        self._create_btn.set_sensitive(False)
        name = self._name_row.get_text().strip()
        password = self._password_row.get_text()
        login = self._login_row.get_active()
        superuser = self._superuser_row.get_active()
        createdb = self._createdb_row.get_active()
        createrole = self._createrole_row.get_active()
        inherit = self._inherit_row.get_active()
        conn_limit = int(self._conn_limit_row.get_value())
        threading.Thread(
            target=self._do_create,
            args=(name, password, login, superuser, createdb, createrole, inherit, conn_limit),
            daemon=True,
        ).start()

    def _do_create(self, name, password, login, superuser, createdb, createrole, inherit, conn_limit):
        try:
            from tunnel import open_db
            from psycopg import sql
            with open_db(self._conn) as db:
                with db.cursor() as cur:
                    options = sql.SQL(' ').join([
                        sql.SQL('LOGIN' if login else 'NOLOGIN'),
                        sql.SQL('SUPERUSER' if superuser else 'NOSUPERUSER'),
                        sql.SQL('CREATEDB' if createdb else 'NOCREATEDB'),
                        sql.SQL('CREATEROLE' if createrole else 'NOCREATEROLE'),
                        sql.SQL('INHERIT' if inherit else 'NOINHERIT'),
                        sql.SQL('CONNECTION LIMIT {}').format(sql.Literal(conn_limit)),
                    ])
                    if password:
                        options = sql.SQL('{} PASSWORD {}').format(options, sql.Literal(password))
                    stmt = sql.SQL('CREATE ROLE {} WITH {}').format(
                        sql.Identifier(name), options
                    )
                    cur.execute(stmt)
                db.commit()
            GLib.idle_add(self._on_done, name)
        except Exception as e:
            GLib.idle_add(self._on_error, _friendly_pg_error(e))

    def _on_done(self, name):
        self.emit('role-created', name)
        self.close()

    def _on_error(self, msg):
        self._error_label.set_label(msg)
        self._error_label.set_visible(True)
        self._create_btn.set_sensitive(True)


# ── _ChangePasswordDialog ─────────────────────────────────────────────────────

class _ChangePasswordDialog(Adw.Dialog):
    __gsignals__ = {
        'password-changed': (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, conn, role_name):
        super().__init__(title=f'Change Password — {role_name}', content_width=380)
        self.add_css_class('tusk-main')
        self._conn = conn
        self._role_name = role_name

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(False)
        cancel_btn = Gtk.Button(label='Cancel')
        cancel_btn.connect('clicked', lambda _: self.close())
        header.pack_start(cancel_btn)
        self._set_btn = Gtk.Button(label='Set Password')
        self._set_btn.add_css_class('suggested-action')
        self._set_btn.connect('clicked', self._on_set)
        header.pack_end(self._set_btn)
        box.append(header)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.set_margin_top(12)
        content.set_margin_bottom(12)
        content.set_margin_start(12)
        content.set_margin_end(12)

        pwd_group = Adw.PreferencesGroup()

        self._new_pwd_row = Adw.PasswordEntryRow(title='New Password')
        self._new_pwd_row.connect('changed', self._on_fields_changed)
        pwd_group.add(self._new_pwd_row)

        self._confirm_row = Adw.PasswordEntryRow(title='Confirm Password')
        self._confirm_row.connect('changed', self._on_fields_changed)
        pwd_group.add(self._confirm_row)

        self._empty_pwd_row = Adw.SwitchRow(title='Set empty password',
                                             subtitle='Clears the password (sets PASSWORD NULL)')
        self._empty_pwd_row.connect('notify::active', self._on_empty_toggled)
        pwd_group.add(self._empty_pwd_row)

        content.append(pwd_group)

        self._error_label = Gtk.Label()
        self._error_label.add_css_class('error')
        self._error_label.add_css_class('caption')
        self._error_label.set_wrap(True)
        self._error_label.set_visible(False)
        content.append(self._error_label)

        box.append(content)
        self.set_child(box)

    def _on_fields_changed(self, _row):
        self._error_label.set_visible(False)

    def _on_empty_toggled(self, _row, _pspec):
        use_empty = self._empty_pwd_row.get_active()
        self._new_pwd_row.set_sensitive(not use_empty)
        self._confirm_row.set_sensitive(not use_empty)
        self._error_label.set_visible(False)

    def _on_set(self, _btn):
        use_empty = self._empty_pwd_row.get_active()
        if use_empty:
            password = None
        else:
            password = self._new_pwd_row.get_text()
            confirm = self._confirm_row.get_text()
            if not password:
                self._error_label.set_label('Password cannot be empty. Use "Set empty password" to remove it.')
                self._error_label.set_visible(True)
                return
            if password != confirm:
                self._error_label.set_label('Passwords do not match.')
                self._error_label.set_visible(True)
                return
        self._set_btn.set_sensitive(False)
        threading.Thread(
            target=self._do_set_password, args=(password,), daemon=True
        ).start()

    def _do_set_password(self, password):
        try:
            from tunnel import open_db
            from psycopg import sql
            with open_db(self._conn) as db:
                with db.cursor() as cur:
                    if password is None:
                        stmt = sql.SQL('ALTER ROLE {} PASSWORD NULL').format(
                            sql.Identifier(self._role_name)
                        )
                    else:
                        stmt = sql.SQL('ALTER ROLE {} PASSWORD {}').format(
                            sql.Identifier(self._role_name),
                            sql.Literal(password),
                        )
                    cur.execute(stmt)
                db.commit()
            GLib.idle_add(self._on_done)
        except Exception as e:
            GLib.idle_add(self._on_error, _friendly_pg_error(e))

    def _on_done(self):
        self.emit('password-changed')
        self.close()

    def _on_error(self, msg):
        self._error_label.set_label(msg)
        self._error_label.set_visible(True)
        self._set_btn.set_sensitive(True)
