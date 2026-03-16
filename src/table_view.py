import threading

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Gtk, Adw, GLib

ROW_LIMIT = 500


class TableView(Gtk.Box):
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.set_vexpand(True)
        self._build_ui()

    def _build_ui(self):
        self._stack = Gtk.Stack()
        self._stack.set_vexpand(True)

        # Loading
        spinner_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        spinner_box.set_halign(Gtk.Align.CENTER)
        spinner_box.set_valign(Gtk.Align.CENTER)
        self._spinner = Gtk.Spinner()
        self._spinner.set_size_request(32, 32)
        spinner_box.append(self._spinner)
        self._stack.add_named(spinner_box, 'loading')

        # Error / info
        self._status_page = Adw.StatusPage()
        self._stack.add_named(self._status_page, 'status')

        # Data
        self._scroll = Gtk.ScrolledWindow()
        self._scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self._scroll.set_vexpand(True)
        self._stack.add_named(self._scroll, 'data')

        self.append(self._stack)

    def load(self, conn, schema, table):
        self._spinner.start()
        self._stack.set_visible_child_name('loading')
        threading.Thread(
            target=self._fetch,
            args=(conn, schema, table),
            daemon=True,
        ).start()

    def _fetch(self, conn, schema, table):
        try:
            import psycopg
            from psycopg import sql
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
                    cur.execute(
                        sql.SQL('SELECT * FROM {}.{} LIMIT %s').format(
                            sql.Identifier(schema),
                            sql.Identifier(table),
                        ),
                        [ROW_LIMIT],
                    )
                    columns = [desc.name for desc in cur.description]
                    rows = cur.fetchall()

            GLib.idle_add(self._show_data, columns, rows)

        except Exception as e:
            GLib.idle_add(self._show_error, str(e))

    def _show_data(self, columns, rows):
        self._spinner.stop()

        list_store = Gtk.ListStore(*([str] * len(columns)))
        for row in rows:
            list_store.append(['' if v is None else str(v) for v in row])

        tree = Gtk.TreeView(model=list_store)
        tree.set_grid_lines(Gtk.TreeViewGridLines.BOTH)
        tree.set_enable_search(False)

        for i, col_name in enumerate(columns):
            renderer = Gtk.CellRendererText()
            renderer.set_property('ellipsize', 3)  # END
            col = Gtk.TreeViewColumn(col_name, renderer, text=i)
            col.set_resizable(True)
            col.set_min_width(80)
            col.set_max_width(400)
            tree.append_column(col)

        self._scroll.set_child(tree)
        self._stack.set_visible_child_name('data')

    def _show_error(self, error_msg):
        self._spinner.stop()
        self._status_page.set_title('Failed to Load Table')
        self._status_page.set_description(error_msg)
        self._status_page.set_icon_name('dialog-error-symbolic')
        self._stack.set_visible_child_name('status')
