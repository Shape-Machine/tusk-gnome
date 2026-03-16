import threading

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Gtk, GLib, GObject

# TreeStore column indices
COL_ICON = 0
COL_LABEL = 1
COL_TYPE = 2    # 'schema' | 'table' | 'view' | 'loading' | 'error'
COL_CONN = 3    # connection dict (TYPE_PYOBJECT)
COL_SCHEMA = 4
COL_TABLE = 5


class DbBrowser(Gtk.Box):
    __gsignals__ = {
        'table-selected': (
            GObject.SignalFlags.RUN_FIRST, None,
            (GObject.TYPE_PYOBJECT, str, str),  # conn, schema, table
        )
    }

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.set_vexpand(True)
        self._build_ui()

    def _build_ui(self):
        self._store = Gtk.TreeStore(str, str, str, GObject.TYPE_PYOBJECT, str, str)

        self._tree = Gtk.TreeView(model=self._store)
        self._tree.set_headers_visible(False)
        self._tree.set_activate_on_single_click(False)
        self._tree.connect('row-activated', self._on_row_activated)
        self._tree.get_selection().set_mode(Gtk.SelectionMode.SINGLE)

        icon_renderer = Gtk.CellRendererPixbuf()
        text_renderer = Gtk.CellRendererText()
        text_renderer.set_property('ellipsize', 3)  # Pango.EllipsizeMode.END

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

    def clear(self):
        self._store.clear()

    def load(self, conn):
        self._store.clear()
        self._store.append(None, [
            'content-loading-symbolic', 'Connecting…', 'loading', conn, '', ''
        ])
        threading.Thread(target=self._fetch_schema, args=(conn,), daemon=True).start()

    def _fetch_schema(self, conn):
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
                schema_items.setdefault(schema, []).append((table, ttype))

            GLib.idle_add(self._populate, conn, schema_items)

        except Exception as e:
            GLib.idle_add(self._show_error, str(e))

    def _populate(self, conn, schema_items):
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
            for table, ttype in items:
                is_table = ttype == 'BASE TABLE'
                icon = 'x-office-spreadsheet-symbolic' if is_table else 'view-grid-symbolic'
                item_type = 'table' if is_table else 'view'
                self._store.append(schema_it, [
                    icon, table, item_type, conn, schema, table
                ])

        self._tree.expand_all()

    def _show_error(self, error_msg):
        self._store.clear()
        self._store.append(None, [
            'dialog-error-symbolic', f'Error: {error_msg}', 'error', None, '', ''
        ])

    def _on_row_activated(self, _tree, path, _col):
        it = self._store.get_iter(path)
        item_type = self._store.get_value(it, COL_TYPE)
        if item_type in ('table', 'view'):
            conn = self._store.get_value(it, COL_CONN)
            schema = self._store.get_value(it, COL_SCHEMA)
            table = self._store.get_value(it, COL_TABLE)
            self.emit('table-selected', conn, schema, table)
