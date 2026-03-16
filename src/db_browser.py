import threading

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Gtk, GLib, GObject, Gdk

COL_ICON = 0
COL_LABEL = 1
COL_TYPE = 2    # 'schema' | 'group' | 'table' | 'view' | 'loading' | 'error'
COL_CONN = 3
COL_SCHEMA = 4
COL_TABLE = 5


class DbBrowser(Gtk.Box):
    __gsignals__ = {
        'table-selected': (
            GObject.SignalFlags.RUN_FIRST, None,
            (GObject.TYPE_PYOBJECT, str, str, str),  # conn, schema, table, item_type
        )
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

        # Search entry
        self._search_entry = Gtk.SearchEntry()
        self._search_entry.set_placeholder_text('Filter…')
        self._search_entry.set_margin_start(6)
        self._search_entry.set_margin_end(6)
        self._search_entry.set_margin_top(4)
        self._search_entry.set_margin_bottom(4)
        self._search_entry.set_visible(False)
        self._search_entry.connect('search-changed', self._on_search_changed)
        self.append(self._search_entry)

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
        if item_type in ('table', 'view'):
            return query in model.get_value(it, COL_LABEL).lower()
        return True  # info, error

    def _group_has_match(self, model, group_it, query):
        child = model.iter_children(group_it)
        while child:
            if model.get_value(child, COL_TYPE) in ('table', 'view'):
                if query in model.get_value(child, COL_LABEL).lower():
                    return True
            child = model.iter_next(child)
        return False

    def _on_search_changed(self, _entry):
        query = self._search_entry.get_text().strip()
        if query:
            if not self._saved_expansion:
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

    def clear(self):
        self._load_gen += 1
        self._loading_spinner.stop()
        self._loading_bar.set_visible(False)
        self._search_entry.set_text('')
        self._search_entry.set_visible(False)
        self._store.clear()

    def load(self, conn):
        self._load_gen += 1
        gen = self._load_gen
        self._saved_expansion = None
        self._store.clear()
        self._search_entry.set_text('')
        self._loading_bar.set_visible(True)
        self._loading_spinner.start()
        threading.Thread(target=self._fetch_schema, args=(conn, gen), daemon=True).start()

    def _fetch_schema(self, conn, gen):
        try:
            import psycopg
            from tunnel import open_tunnel

            with open_tunnel(conn) as (host, port), psycopg.connect(
                host=host,
                port=port,
                dbname=conn['database'],
                user=conn['username'],
                password=conn['password'],
                connect_timeout=10,
            ) as db:
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
                    rows = cur.fetchall()

            schema_items = {}
            for schema, table, ttype in rows:
                schema_items.setdefault(schema, {'tables': [], 'views': []})
                if ttype == 'BASE TABLE':
                    schema_items[schema]['tables'].append(table)
                else:
                    schema_items[schema]['views'].append(table)

            GLib.idle_add(self._populate, conn, schema_items, gen)

        except Exception as e:
            GLib.idle_add(self._show_error, str(e), gen)

    def _populate(self, conn, schema_items, gen):
        if gen != self._load_gen:
            return
        self._loading_spinner.stop()
        self._loading_bar.set_visible(False)
        self._store.clear()

        if not schema_items:
            self._store.append(None, [
                'dialog-information-symbolic', 'No tables found', 'info', conn, '', ''
            ])
            return

        for schema, items in sorted(schema_items.items()):
            schema_it = self._store.append(None, [
                'folder-symbolic', schema, 'schema', conn, schema, ''
            ])

            if items['tables']:
                tables_it = self._store.append(schema_it, [
                    'x-office-spreadsheet-symbolic', 'Tables', 'group', conn, schema, ''
                ])
                for table in items['tables']:
                    self._store.append(tables_it, [
                        'x-office-spreadsheet-symbolic', table, 'table', conn, schema, table
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

        self._saved_expansion = None
        self._search_entry.set_visible(True)
        self._tree.expand_all()

    def _show_error(self, error_msg, gen):
        if gen != self._load_gen:
            return
        self._loading_spinner.stop()
        self._loading_bar.set_visible(False)
        self._store.clear()
        self._store.append(None, [
            'dialog-error-symbolic', f'Error: {error_msg}', 'error', None, '', ''
        ])

    def _on_row_activated(self, _tree, path, _col):
        it = self._filter.get_iter(path)
        item_type = self._filter.get_value(it, COL_TYPE)
        if item_type in ('table', 'view'):
            conn = self._filter.get_value(it, COL_CONN)
            schema = self._filter.get_value(it, COL_SCHEMA)
            table = self._filter.get_value(it, COL_TABLE)
            self.emit('table-selected', conn, schema, table, item_type)

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
        return False
