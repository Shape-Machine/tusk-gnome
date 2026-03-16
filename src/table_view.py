import threading

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Gtk, Adw, GLib, Gio, GObject, Pango

ROW_LIMIT = 500


class _Row(GObject.Object):
    __gtype_name__ = 'TuskTableRow'

    def __init__(self, values):
        super().__init__()
        self._values = values

    def get(self, i):
        return self._values[i]


def _make_column_view(columns, rows):
    store = Gio.ListStore(item_type=_Row)
    for row in rows:
        store.append(_Row(['' if v is None else str(v) for v in row]))

    col_view = Gtk.ColumnView(model=Gtk.NoSelection(model=store))
    col_view.set_show_row_separators(True)
    col_view.set_show_column_separators(True)
    col_view.set_hexpand(True)

    for i, name in enumerate(columns):
        factory = Gtk.SignalListItemFactory()

        def on_setup(_factory, list_item):
            label = Gtk.Label()
            label.set_xalign(0)
            label.set_ellipsize(Pango.EllipsizeMode.END)
            label.set_max_width_chars(40)
            list_item.set_child(label)

        def on_bind(_factory, list_item, col_idx=i):
            list_item.get_child().set_text(list_item.get_item().get(col_idx))

        factory.connect('setup', on_setup)
        factory.connect('bind', on_bind)

        col = Gtk.ColumnViewColumn(title=name, factory=factory)
        col.set_resizable(True)
        col_view.append_column(col)

    return col_view


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
        self._scroll.set_child(_make_column_view(columns, rows))
        self._stack.set_visible_child_name('data')

    def _show_error(self, error_msg):
        self._spinner.stop()
        self._status_page.set_title('Failed to Load Table')
        self._status_page.set_description(error_msg)
        self._status_page.set_icon_name('dialog-error-symbolic')
        self._stack.set_visible_child_name('status')
