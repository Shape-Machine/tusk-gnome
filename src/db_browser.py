import threading

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Gtk, GLib, GObject, Gdk, Gio

COL_ICON = 0
COL_LABEL = 1
COL_TYPE = 2    # 'schema' | 'group' | 'table' | 'view' | 'sequence' | 'enum' | 'function' | 'users' | 'role' | 'loading' | 'error'
COL_CONN = 3
COL_SCHEMA = 4
COL_TABLE = 5


class DbBrowser(Gtk.Box):
    __gsignals__ = {
        'database-switched': (
            GObject.SignalFlags.RUN_FIRST, None,
            (GObject.TYPE_PYOBJECT, str),  # conn, new_dbname
        ),
        'drop-database-requested': (
            GObject.SignalFlags.RUN_FIRST, None,
            (GObject.TYPE_PYOBJECT, str),  # conn, dbname
        ),
        'table-selected': (
            GObject.SignalFlags.RUN_FIRST, None,
            (GObject.TYPE_PYOBJECT, str, str, str),  # conn, schema, table, item_type
        ),
        'create-table-requested': (
            GObject.SignalFlags.RUN_FIRST, None,
            (GObject.TYPE_PYOBJECT, str),  # conn, schema
        ),
        'drop-table-requested': (
            GObject.SignalFlags.RUN_FIRST, None,
            (GObject.TYPE_PYOBJECT, str, str, str),  # conn, schema, table, item_type
        ),
        'truncate-table-requested': (
            GObject.SignalFlags.RUN_FIRST, None,
            (GObject.TYPE_PYOBJECT, str, str),  # conn, schema, table
        ),
        'rename-table-requested': (
            GObject.SignalFlags.RUN_FIRST, None,
            (GObject.TYPE_PYOBJECT, str, str),  # conn, schema, table
        ),
        'clone-table-requested': (
            GObject.SignalFlags.RUN_FIRST, None,
            (GObject.TYPE_PYOBJECT, str, str),  # conn, schema, table
        ),
        'create-schema-requested': (
            GObject.SignalFlags.RUN_FIRST, None,
            (GObject.TYPE_PYOBJECT,),  # conn
        ),
        'rename-schema-requested': (
            GObject.SignalFlags.RUN_FIRST, None,
            (GObject.TYPE_PYOBJECT, str),  # conn, schema
        ),
        'drop-schema-requested': (
            GObject.SignalFlags.RUN_FIRST, None,
            (GObject.TYPE_PYOBJECT, str),  # conn, schema
        ),
        'create-view-requested': (
            GObject.SignalFlags.RUN_FIRST, None,
            (GObject.TYPE_PYOBJECT, str),  # conn, schema
        ),
        'role-attrs-loaded': (
            GObject.SignalFlags.RUN_FIRST, None,
            (GObject.TYPE_PYOBJECT, GObject.TYPE_PYOBJECT),  # conn, attrs dict
        ),
    }

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.set_vexpand(True)
        self._load_gen = 0
        self._build_ui()

    def _build_ui(self):
        # Loading bar
        self._loading_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._loading_bar.set_margin_start(8)
        self._loading_bar.set_margin_top(4)
        self._loading_bar.set_margin_bottom(4)
        self._loading_spinner = Gtk.Spinner()
        self._loading_spinner.set_size_request(16, 16)
        loading_label = Gtk.Label(label='Connecting…')
        loading_label.add_css_class('caption')
        loading_label.add_css_class('dim-label')
        self._loading_bar.append(self._loading_spinner)
        self._loading_bar.append(loading_label)
        self._loading_bar.set_visible(False)
        self.append(self._loading_bar)

        # Database switcher bar
        db_switcher_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        db_switcher_bar.set_margin_start(6)
        db_switcher_bar.set_margin_end(6)
        db_switcher_bar.set_margin_top(4)
        db_switcher_bar.set_margin_bottom(2)
        db_switcher_bar.set_visible(False)
        self._db_switcher_bar = db_switcher_bar

        db_label = Gtk.Label(label='Database:')
        db_label.add_css_class('caption')
        db_label.add_css_class('dim-label')
        db_switcher_bar.append(db_label)

        self._db_string_list = Gtk.StringList.new([])
        self._db_dropdown = Gtk.DropDown.new(self._db_string_list, None)
        self._db_dropdown.set_hexpand(True)
        self._db_dropdown.add_css_class('flat')
        self._db_dropdown_handler = self._db_dropdown.connect(
            'notify::selected', self._on_db_selected
        )
        db_switcher_bar.append(self._db_dropdown)

        db_menu = Gio.Menu()
        db_menu.append('Drop Database…', 'dbmenu.drop-database')
        self._db_menu_btn = Gtk.MenuButton()
        self._db_menu_btn.set_icon_name('view-more-symbolic')
        self._db_menu_btn.set_menu_model(db_menu)
        self._db_menu_btn.add_css_class('flat')
        self._db_menu_btn.set_valign(Gtk.Align.CENTER)
        self._db_menu_btn.set_tooltip_text('Database options')
        db_switcher_bar.append(self._db_menu_btn)

        # Insert the action group once at build time so the MenuButton
        # can always resolve 'dbmenu.drop-database'.
        self._setup_db_menu_actions()

        self.append(db_switcher_bar)

        # Schema warning bar
        self._schema_warning_bar = Gtk.Label()
        self._schema_warning_bar.add_css_class('caption')
        self._schema_warning_bar.add_css_class('warning')
        self._schema_warning_bar.set_xalign(0)
        self._schema_warning_bar.set_margin_start(8)
        self._schema_warning_bar.set_margin_end(6)
        self._schema_warning_bar.set_margin_bottom(2)
        self._schema_warning_bar.set_wrap(True)
        self._schema_warning_bar.set_visible(False)
        self.append(self._schema_warning_bar)

        # Search + New Schema toolbar
        search_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        search_bar.set_margin_start(6)
        search_bar.set_margin_end(6)
        search_bar.set_margin_top(4)
        search_bar.set_margin_bottom(4)
        search_bar.set_visible(False)
        self._search_bar = search_bar

        self._search_entry = Gtk.SearchEntry()
        self._search_entry.set_placeholder_text('Filter…')
        self._search_entry.set_hexpand(True)
        self._search_entry.connect('search-changed', self._on_search_changed)
        search_bar.append(self._search_entry)

        self._new_schema_btn = Gtk.Button(icon_name='folder-new-symbolic')
        self._new_schema_btn.add_css_class('flat')
        self._new_schema_btn.set_tooltip_text('New Schema…')
        self._new_schema_btn.connect('clicked', self._on_new_schema_clicked)
        search_bar.append(self._new_schema_btn)

        self.append(search_bar)

        self._store = Gtk.TreeStore(str, str, str, GObject.TYPE_PYOBJECT, str, str)

        self._filter = self._store.filter_new()
        self._filter.set_visible_func(self._is_visible)

        self._tree = Gtk.TreeView(model=self._filter)
        self._tree.set_headers_visible(False)
        self._tree.set_activate_on_single_click(False)
        self._tree.connect('row-activated', self._on_row_activated)
        self._tree.get_selection().set_mode(Gtk.SelectionMode.SINGLE)

        key_ctrl = Gtk.EventControllerKey()
        key_ctrl.connect('key-pressed', self._on_key_pressed)
        self._tree.add_controller(key_ctrl)

        right_click = Gtk.GestureClick(button=3)
        right_click.connect('pressed', self._on_right_click)
        self._tree.add_controller(right_click)

        self._ctx_popover = None
        self._ctx_conn = None
        self._ctx_schema = None
        self._ctx_table = None
        self._ctx_item_type = None
        self._expansion_snapshot = None
        self._last_conn = None
        self._read_only = False
        self._db_switch_inhibit = False

        icon_renderer = Gtk.CellRendererPixbuf()
        text_renderer = Gtk.CellRendererText()
        text_renderer.set_property('ellipsize', 3)

        col = Gtk.TreeViewColumn()
        col.pack_start(icon_renderer, False)
        col.pack_start(text_renderer, True)
        col.add_attribute(icon_renderer, 'icon-name', COL_ICON)
        col.add_attribute(text_renderer, 'text', COL_LABEL)
        self._tree.append_column(col)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        scroll.set_child(self._tree)

        self.append(scroll)

    def _is_visible(self, model, it, _data):
        query = self._search_entry.get_text().lower().strip()
        if not query:
            return True
        item_type = model.get_value(it, COL_TYPE)
        if item_type == 'schema':
            child = model.iter_children(it)
            while child:
                if self._group_has_match(model, child, query):
                    return True
                child = model.iter_next(child)
            return False
        if item_type == 'group':
            return self._group_has_match(model, it, query)
        if item_type in ('table', 'view', 'sequence', 'enum', 'function'):
            return query in model.get_value(it, COL_LABEL).lower()
        if item_type == 'users':
            child = model.iter_children(it)
            while child:
                if query in model.get_value(child, COL_LABEL).lower():
                    return True
                child = model.iter_next(child)
            return False
        if item_type == 'role':
            return query in model.get_value(it, COL_LABEL).lower()
        return True  # info, error

    def _group_has_match(self, model, group_it, query):
        child = model.iter_children(group_it)
        while child:
            child_type = model.get_value(child, COL_TYPE)
            if child_type in ('table', 'view', 'sequence', 'enum', 'function'):
                if query in model.get_value(child, COL_LABEL).lower():
                    return True
            elif child_type == 'group':
                # overloaded function parent node — check its children
                if self._group_has_match(model, child, query):
                    return True
            child = model.iter_next(child)
        return False

    def _on_search_changed(self, _entry):
        query = self._search_entry.get_text().strip()
        if query:
            if self._saved_expansion is None:
                self._saved_expansion = self._get_expanded_paths()
            self._filter.refilter()
            self._tree.expand_all()
        else:
            self._filter.refilter()
            if self._saved_expansion is not None:
                self._restore_expanded_paths(self._saved_expansion)
                self._saved_expansion = None

    def _get_expanded_paths(self):
        expanded = []
        self._tree.map_expanded_rows(lambda _tree, path: expanded.append(path.copy()))
        return expanded

    def _restore_expanded_paths(self, paths):
        self._tree.collapse_all()
        for path in paths:
            self._tree.expand_row(path, False)

    def _snapshot_expansion(self):
        """Return a set of (schema, label) keys for currently expanded nodes."""
        keys = set()
        def visit(_tree, path):
            it = self._filter.get_iter(path)
            if it is None:
                return
            schema = self._filter.get_value(it, COL_SCHEMA)
            label  = self._filter.get_value(it, COL_LABEL)
            keys.add((schema, label))
        self._tree.map_expanded_rows(visit)
        return keys

    def _restore_expansion(self, keys):
        """Re-expand nodes whose (schema, label) key is in *keys*."""
        if not keys:
            return
        it = self._store.get_iter_first()
        while it:
            self._restore_expansion_node(it, keys)
            it = self._store.iter_next(it)

    def _restore_expansion_node(self, it, keys):
        schema = self._store.get_value(it, COL_SCHEMA)
        label  = self._store.get_value(it, COL_LABEL)
        if (schema, label) in keys:
            path = self._store.get_path(it)
            # Convert store path to filter path before expanding
            fpath = self._filter.convert_child_path_to_path(path)
            if fpath:
                self._tree.expand_row(fpath, False)
        child = self._store.iter_children(it)
        while child:
            self._restore_expansion_node(child, keys)
            child = self._store.iter_next(child)

    def _expand_schema(self, schema_name):
        """Expand the row for *schema_name* in the tree."""
        it = self._store.get_iter_first()
        while it:
            if (self._store.get_value(it, COL_TYPE) == 'schema' and
                    self._store.get_value(it, COL_SCHEMA) == schema_name):
                path = self._store.get_path(it)
                fpath = self._filter.convert_child_path_to_path(path)
                if fpath:
                    self._tree.expand_row(fpath, False)
                return
            it = self._store.iter_next(it)

    def _on_db_selected(self, dropdown, _param):
        if self._db_switch_inhibit:
            return
        idx = dropdown.get_selected()
        if idx == Gtk.INVALID_LIST_POSITION:
            return
        new_db = self._db_string_list.get_string(idx)
        if self._last_conn and new_db != self._last_conn.get('database', ''):
            self.emit('database-switched', self._last_conn, new_db)

    def _setup_db_menu_actions(self):
        """Insert the database menu action group once at build time."""
        ag = Gio.SimpleActionGroup()
        action = Gio.SimpleAction.new('drop-database', None)
        action.connect('activate', self._on_drop_database_activated)
        ag.add_action(action)
        self.insert_action_group('dbmenu', ag)

    def _on_drop_database_activated(self, _action, _param):
        if self._last_conn is None:
            return
        dbname = self._last_conn.get('database', '')
        if dbname:
            self.emit('drop-database-requested', self._last_conn, dbname)

    def clear(self):
        self._load_gen += 1
        self._loading_spinner.stop()
        self._loading_bar.set_visible(False)
        self._db_switcher_bar.set_visible(False)
        self._schema_warning_bar.set_visible(False)
        self._search_entry.set_text('')
        self._search_bar.set_visible(False)
        self._store.clear()
        self._ctx_conn = None
        self._ctx_schema = None
        self._ctx_table = None
        self._ctx_item_type = None

    def set_rename_hint(self, old_schema, new_schema):
        """Call before load() after a schema rename so expansion state is preserved."""
        self._rename_hint = (old_schema, new_schema)

    def load(self, conn):
        self._load_gen += 1
        gen = self._load_gen
        self._last_conn = conn
        self._read_only = conn.get('read_only', False)
        self._new_schema_btn.set_visible(not self._read_only)
        self._db_menu_btn.set_visible(not self._read_only)
        self._saved_expansion = None
        self._expansion_snapshot = self._snapshot_expansion()
        hint = getattr(self, '_rename_hint', None)
        if hint:
            old, new = hint
            self._expansion_snapshot = {
                (new if s == old else s, lbl)
                for s, lbl in self._expansion_snapshot
            }
            self._rename_hint = None
        self._store.clear()
        self._search_entry.set_text('')
        self._loading_bar.set_visible(True)
        self._loading_spinner.start()
        threading.Thread(target=self._fetch_schema, args=(conn, gen), daemon=True).start()

    def _fetch_schema(self, conn, gen):
        try:
            import psycopg
            from tunnel import open_db

            with open_db(conn) as db:
                with db.cursor() as cur:
                    cur.execute("""
                        SELECT table_schema, table_name, table_type
                        FROM information_schema.tables
                        WHERE table_schema NOT IN (
                            'pg_catalog', 'information_schema',
                            'pg_toast', 'pg_temp_1', 'pg_toast_temp_1'
                        )
                        ORDER BY table_schema, table_type DESC, table_name
                    """)
                    table_rows = cur.fetchall()

                    cur.execute("""
                        SELECT sequence_schema, sequence_name
                        FROM information_schema.sequences
                        WHERE sequence_schema NOT IN (
                            'pg_catalog', 'information_schema',
                            'pg_toast', 'pg_temp_1', 'pg_toast_temp_1'
                        )
                        ORDER BY sequence_schema, sequence_name
                    """)
                    sequence_rows = cur.fetchall()

                    cur.execute("""
                        SELECT n.nspname, t.typname
                        FROM pg_type t JOIN pg_namespace n ON t.typnamespace = n.oid
                        WHERE t.typtype = 'e'
                          AND n.nspname NOT IN (
                              'pg_catalog', 'information_schema',
                              'pg_toast', 'pg_temp_1', 'pg_toast_temp_1'
                          )
                        ORDER BY n.nspname, t.typname
                    """)
                    enum_rows = cur.fetchall()

                    cur.execute("""
                        SELECT n.nspname, p.proname, pg_get_function_arguments(p.oid) AS args
                        FROM pg_proc p JOIN pg_namespace n ON p.pronamespace = n.oid
                        WHERE n.nspname NOT IN (
                            'pg_catalog', 'information_schema',
                            'pg_toast', 'pg_temp_1', 'pg_toast_temp_1'
                        )
                          AND p.prokind IN ('f', 'p')
                        ORDER BY n.nspname, p.proname, args
                    """)
                    function_rows = cur.fetchall()

                    cur.execute("""
                        SELECT nspname FROM pg_namespace
                        WHERE nspname NOT IN (
                            'pg_catalog', 'information_schema',
                            'pg_toast', 'pg_temp_1', 'pg_toast_temp_1'
                        )
                          AND nspname NOT LIKE 'pg_%'
                        ORDER BY nspname
                    """)
                    all_schemas = [r[0] for r in cur.fetchall()]

                    cur.execute("""
                        SELECT datname FROM pg_database
                        WHERE datistemplate = false
                          AND datname NOT IN ('template0', 'template1')
                          AND has_database_privilege(current_user, datname, 'CONNECT')
                        ORDER BY datname
                    """)
                    all_databases = [r[0] for r in cur.fetchall()]

                    # Current user's own role attributes (for connection badge)
                    current_role_attrs = {}
                    try:
                        cur.execute("""
                            SELECT rolsuper, rolcreatedb, rolcreaterole, rolcanlogin, rolreplication
                            FROM pg_roles WHERE rolname = current_user
                        """)
                        row = cur.fetchone()
                        if row:
                            current_role_attrs = {
                                'superuser': bool(row[0]),
                                'createdb': bool(row[1]),
                                'createrole': bool(row[2]),
                                'login': bool(row[3]),
                                'replication': bool(row[4]),
                            }
                    except psycopg.errors.InsufficientPrivilege:
                        pass  # degrade gracefully — badge stays hidden

                    # All roles + membership (for Users & Roles tree section)
                    roles_list = None
                    try:
                        cur.execute("""
                            SELECT r.rolname,
                                   r.rolsuper,
                                   r.rolcanlogin,
                                   r.rolcreatedb,
                                   r.rolcreaterole,
                                   r.rolinherit,
                                   r.rolreplication,
                                   array_agg(g.rolname ORDER BY g.rolname)
                                       FILTER (WHERE g.rolname IS NOT NULL) AS member_of
                            FROM pg_roles r
                            LEFT JOIN pg_auth_members m ON m.member = r.oid
                            LEFT JOIN pg_roles g ON g.oid = m.roleid
                            GROUP BY r.rolname, r.rolsuper, r.rolcanlogin,
                                     r.rolcreatedb, r.rolcreaterole,
                                     r.rolinherit, r.rolreplication
                            ORDER BY r.rolname
                        """)
                        roles_list = [
                            {
                                'name': rr[0],
                                'superuser': bool(rr[1]),
                                'login': bool(rr[2]),
                                'createdb': bool(rr[3]),
                                'createrole': bool(rr[4]),
                                'inherit': bool(rr[5]),
                                'replication': bool(rr[6]),
                                'member_of': rr[7] or [],
                            }
                            for rr in cur.fetchall()
                        ]
                    except psycopg.errors.InsufficientPrivilege:
                        pass  # insufficient privileges — roles_list stays None

            schema_items = {}

            def _schema(s):
                return schema_items.setdefault(s, {
                    'tables': [], 'views': [], 'sequences': [], 'enums': [], 'functions': []
                })

            # Ensure all schemas appear even if empty
            for s in all_schemas:
                _schema(s)

            for schema, table, ttype in table_rows:
                bucket = _schema(schema)
                if ttype == 'BASE TABLE':
                    bucket['tables'].append(table)
                else:
                    bucket['views'].append(table)

            for schema, seq in sequence_rows:
                _schema(schema)['sequences'].append(seq)

            for schema, enum in enum_rows:
                _schema(schema)['enums'].append(enum)

            for schema, name, args in function_rows:
                _schema(schema)['functions'].append((name, args))

            default_schema = conn.get('default_schema', '').strip()
            schema_warning = None
            if default_schema and default_schema not in all_schemas:
                schema_warning = (
                    f'Default schema "{default_schema}" not found on this server.'
                )

            GLib.idle_add(self._populate, conn, schema_items, all_databases,
                          schema_warning, current_role_attrs, roles_list, gen)

        except Exception as e:
            GLib.idle_add(self._show_error, str(e), gen)

    def _populate(self, conn, schema_items, all_databases,
                  schema_warning, current_role_attrs, roles_list, gen):
        if gen != self._load_gen:
            return
        self._loading_spinner.stop()
        self._loading_bar.set_visible(False)
        self._store.clear()

        # Emit badge signal so window.py can update the connection row indicator
        self.emit('role-attrs-loaded', conn, current_role_attrs)

        # Update database switcher
        self._db_switch_inhibit = True
        current_db = conn.get('database', '')
        self._db_string_list.splice(0, self._db_string_list.get_n_items(), all_databases)
        try:
            selected_idx = all_databases.index(current_db)
        except ValueError:
            selected_idx = 0
        self._db_dropdown.set_selected(selected_idx)
        self._db_switch_inhibit = False
        self._db_switcher_bar.set_visible(bool(all_databases))

        # Show schema warning if default schema not found
        if schema_warning:
            self._schema_warning_bar.set_label(schema_warning)
            self._schema_warning_bar.set_visible(True)
        else:
            self._schema_warning_bar.set_visible(False)

        if not schema_items:
            self._store.append(None, [
                'dialog-information-symbolic', 'No tables found', 'info', conn, '', ''
            ])
            return

        default_schema = conn.get('default_schema', '').strip()

        for schema, items in sorted(schema_items.items()):
            schema_it = self._store.append(None, [
                'folder-symbolic', schema, 'schema', conn, schema, ''
            ])

            tables_it = self._store.append(schema_it, [
                'x-office-spreadsheet-symbolic', 'Tables', 'group', conn, schema, ''
            ])
            if items['tables']:
                for table in items['tables']:
                    self._store.append(tables_it, [
                        'x-office-spreadsheet-symbolic', table, 'table', conn, schema, table
                    ])
            else:
                self._store.append(tables_it, [
                    'dialog-information-symbolic', 'No tables in this schema', 'info', conn, schema, ''
                ])

            views_it = self._store.append(schema_it, [
                'view-grid-symbolic', 'Views', 'group', conn, schema, ''
            ])
            if items['views']:
                for view in items['views']:
                    self._store.append(views_it, [
                        'view-grid-symbolic', view, 'view', conn, schema, view
                    ])
            else:
                self._store.append(views_it, [
                    'dialog-information-symbolic', 'No views in this schema', 'info', conn, schema, ''
                ])

            if items['sequences']:
                seq_it = self._store.append(schema_it, [
                    'view-list-ordered-symbolic', 'Sequences', 'group', conn, schema, ''
                ])
                for seq in items['sequences']:
                    self._store.append(seq_it, [
                        'view-list-ordered-symbolic', seq, 'sequence', conn, schema, seq
                    ])

            if items['enums']:
                enum_it = self._store.append(schema_it, [
                    'emblem-important-symbolic', 'Enums', 'group', conn, schema, ''
                ])
                for enum in items['enums']:
                    self._store.append(enum_it, [
                        'emblem-important-symbolic', enum, 'enum', conn, schema, enum
                    ])

            if items['functions']:
                from itertools import groupby
                func_it = self._store.append(schema_it, [
                    'system-run-symbolic', 'Functions', 'group', conn, schema, ''
                ])
                for name, overloads in groupby(items['functions'], key=lambda x: x[0]):
                    overloads = list(overloads)
                    if len(overloads) == 1:
                        label = f'{name}({overloads[0][1]})'
                        self._store.append(func_it, [
                            'system-run-symbolic', label, 'function', conn, schema, name
                        ])
                    else:
                        parent_it = self._store.append(func_it, [
                            'system-run-symbolic', name, 'group', conn, schema, ''
                        ])
                        for _, args in overloads:
                            label = f'{name}({args})'
                            self._store.append(parent_it, [
                                'system-run-symbolic', label, 'function', conn, schema, name
                            ])

        # Users & Roles section
        users_it = self._store.append(None, [
            'system-users-symbolic', 'Users & Roles', 'users', conn, '', ''
        ])
        if roles_list is None:
            self._store.append(users_it, [
                'dialog-error-symbolic', 'Insufficient privileges', 'info', conn, '', ''
            ])
        elif not roles_list:
            self._store.append(users_it, [
                'dialog-information-symbolic', 'No roles found', 'info', conn, '', ''
            ])
        else:
            def _role_label(role):
                attrs = []
                if role['superuser']:
                    attrs.append('superuser')
                if role['createdb']:
                    attrs.append('createdb')
                if role['createrole']:
                    attrs.append('createrole')
                if role['inherit']:
                    attrs.append('inherit')
                if role['replication']:
                    attrs.append('replication')
                attr_str = f' ({", ".join(attrs)})' if attrs else ''
                member_str = (
                    f' — member of: {", ".join(role["member_of"])}'
                    if role['member_of'] else ''
                )
                return f'{role["name"]}{attr_str}{member_str}'

            login_roles = [r for r in roles_list if r['login']]
            group_roles  = [r for r in roles_list if not r['login']]

            users_sub = self._store.append(users_it, [
                'system-users-symbolic', 'Users', 'users', conn, '', ''
            ])
            for role in login_roles:
                self._store.append(users_sub, [
                    'person-symbolic', _role_label(role), 'role', conn, '', role['name']
                ])

            roles_sub = self._store.append(users_it, [
                'key-symbolic', 'Roles', 'users', conn, '', ''
            ])
            for role in group_roles:
                self._store.append(roles_sub, [
                    'key-symbolic', _role_label(role), 'role', conn, '', role['name']
                ])

        self._saved_expansion = None
        self._search_bar.set_visible(True)
        snapshot = getattr(self, '_expansion_snapshot', None)
        if snapshot:
            self._restore_expansion(snapshot)
            self._expansion_snapshot = None
        elif default_schema:
            self._expand_schema(default_schema)

    def _show_error(self, error_msg, gen):
        if gen != self._load_gen:
            return
        self._loading_spinner.stop()
        self._loading_bar.set_visible(False)
        self._store.clear()
        self._store.append(None, [
            'dialog-error-symbolic', f'Error: {error_msg}', 'error', None, '', ''
        ])

    def get_loaded_schemas(self):
        """Return list of schema names currently loaded in the tree."""
        schemas = []
        it = self._store.get_iter_first()
        while it:
            if self._store.get_value(it, COL_TYPE) == 'schema':
                schemas.append(self._store.get_value(it, COL_SCHEMA))
            it = self._store.iter_next(it)
        return schemas

    def _on_right_click(self, _gesture, _n_press, x, y):
        result = self._tree.get_path_at_pos(int(x), int(y))
        if result is None:
            return
        path, _col, _cx, _cy = result
        if path is None:
            return
        it = self._filter.get_iter(path)
        if it is None:
            return
        item_type = self._filter.get_value(it, COL_TYPE)
        label = self._filter.get_value(it, COL_LABEL)
        conn = self._filter.get_value(it, COL_CONN)
        schema = self._filter.get_value(it, COL_SCHEMA)
        table = self._filter.get_value(it, COL_TABLE)

        self._ctx_conn = conn
        self._ctx_schema = schema
        self._ctx_table = table
        self._ctx_item_type = item_type

        if item_type in ('table', 'view'):
            self._show_table_context_menu(x, y, item_type)
        elif item_type == 'schema':
            self._show_schema_node_context_menu(x, y)
        elif item_type == 'group' and label == 'Tables':
            self._show_schema_context_menu(x, y)
        elif item_type == 'group' and label == 'Views':
            self._show_views_group_context_menu(x, y)

    def _on_new_schema_clicked(self, _btn):
        if self._last_conn is None:
            return
        self.emit('create-schema-requested', self._last_conn)

    def _show_schema_context_menu(self, x, y):
        """Context menu for the Tables group node — just Create Table."""
        if self._read_only:
            return
        ag = Gio.SimpleActionGroup()
        action = Gio.SimpleAction.new('create-table', None)
        action.connect('activate', lambda *_: self.emit(
            'create-table-requested', self._ctx_conn, self._ctx_schema
        ))
        ag.add_action(action)
        self.insert_action_group('browser', ag)

        menu = Gio.Menu()
        menu.append('Create Table…', 'browser.create-table')
        self._popup_menu(menu, x, y)

    def _show_schema_node_context_menu(self, x, y):
        """Context menu for a schema node — Create Table, Rename Schema, Drop Schema."""
        if self._read_only:
            return
        ag = Gio.SimpleActionGroup()

        def add_action(name, cb):
            action = Gio.SimpleAction.new(name, None)
            action.connect('activate', lambda *_: cb())
            ag.add_action(action)

        add_action('create-table', lambda: self.emit(
            'create-table-requested', self._ctx_conn, self._ctx_schema
        ))
        add_action('rename-schema', lambda: self.emit(
            'rename-schema-requested', self._ctx_conn, self._ctx_schema
        ))
        add_action('drop-schema', lambda: self.emit(
            'drop-schema-requested', self._ctx_conn, self._ctx_schema
        ))
        self.insert_action_group('schm', ag)

        section1 = Gio.Menu()
        section1.append('Create Table…', 'schm.create-table')
        section2 = Gio.Menu()
        section2.append('Rename Schema…', 'schm.rename-schema')
        section2.append('Drop Schema…', 'schm.drop-schema')
        menu = Gio.Menu()
        menu.append_section(None, section1)
        menu.append_section(None, section2)
        self._popup_menu(menu, x, y)

    def _show_views_group_context_menu(self, x, y):
        """Context menu for the Views group node — New View."""
        if self._read_only:
            return
        ag = Gio.SimpleActionGroup()
        action = Gio.SimpleAction.new('create-view', None)
        action.connect('activate', lambda *_: self.emit(
            'create-view-requested', self._ctx_conn, self._ctx_schema
        ))
        ag.add_action(action)
        self.insert_action_group('views', ag)

        menu = Gio.Menu()
        menu.append('New View…', 'views.create-view')
        self._popup_menu(menu, x, y)

    def _popup_menu(self, menu, x, y):
        popover = Gtk.PopoverMenu(menu_model=menu)
        popover.set_has_arrow(False)
        # Parent to the DbBrowser box (outside the ScrolledWindow) so GTK
        # does not constrain the popover height to the tree's scroll area.
        popover.set_parent(self)
        # Translate click coordinates from tree-widget space to self space.
        coords = self._tree.translate_coordinates(self, int(x), int(y))
        tx, ty = coords if coords else (int(x), int(y))
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = tx, ty, 1, 1
        popover.set_pointing_to(rect)
        popover.popup()

    def _show_table_context_menu(self, x, y, item_type):
        if self._read_only:
            return
        ag = Gio.SimpleActionGroup()

        def add_action(name, cb):
            action = Gio.SimpleAction.new(name, None)
            action.connect('activate', lambda *_: cb())
            ag.add_action(action)

        add_action('create-table', lambda: self.emit(
            'create-table-requested', self._ctx_conn, self._ctx_schema
        ))
        add_action('rename-table', lambda: self.emit(
            'rename-table-requested', self._ctx_conn, self._ctx_schema, self._ctx_table
        ))
        add_action('clone-table', lambda: self.emit(
            'clone-table-requested', self._ctx_conn, self._ctx_schema, self._ctx_table
        ))
        add_action('truncate-table', lambda: self.emit(
            'truncate-table-requested', self._ctx_conn, self._ctx_schema, self._ctx_table
        ))
        add_action('drop-object', lambda: self.emit(
            'drop-table-requested', self._ctx_conn, self._ctx_schema,
            self._ctx_table, self._ctx_item_type
        ))

        self.insert_action_group('tbl', ag)

        section1 = Gio.Menu()
        section1.append('Create Table…', 'tbl.create-table')
        if item_type == 'table':
            section1.append('Rename Table…', 'tbl.rename-table')
            section1.append('Clone Structure…', 'tbl.clone-table')
        section2 = Gio.Menu()
        if item_type == 'table':
            section2.append('Truncate…', 'tbl.truncate-table')
        drop_label = 'Drop Table…' if item_type == 'table' else 'Drop View…'
        section2.append(drop_label, 'tbl.drop-object')

        menu = Gio.Menu()
        menu.append_section(None, section1)
        menu.append_section(None, section2)
        self._popup_menu(menu, x, y)

    def _on_row_activated(self, tree, path, _col):
        it = self._filter.get_iter(path)
        item_type = self._filter.get_value(it, COL_TYPE)
        if item_type in ('table', 'view'):
            conn = self._filter.get_value(it, COL_CONN)
            schema = self._filter.get_value(it, COL_SCHEMA)
            table = self._filter.get_value(it, COL_TABLE)
            self.emit('table-selected', conn, schema, table, item_type)
        elif item_type in ('schema', 'group', 'users'):
            if tree.row_expanded(path):
                tree.collapse_row(path)
            else:
                tree.expand_row(path, False)

    def _on_key_pressed(self, _ctrl, keyval, _code, _state):
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            _model, it = self._tree.get_selection().get_selected()
            if it:
                item_type = self._filter.get_value(it, COL_TYPE)
                if item_type in ('table', 'view'):
                    conn = self._filter.get_value(it, COL_CONN)
                    schema = self._filter.get_value(it, COL_SCHEMA)
                    table = self._filter.get_value(it, COL_TABLE)
                    self.emit('table-selected', conn, schema, table, item_type)
                    return True
                if item_type in ('schema', 'group', 'users'):
                    path, _ = self._tree.get_cursor()
                    if path:
                        if self._tree.row_expanded(path):
                            self._tree.collapse_row(path)
                        else:
                            self._tree.expand_row(path, False)
                    return True
        return False
