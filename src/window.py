import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Gtk, Adw, Gio

from connections import ConnectionStore
from connection_dialog import ConnectionDialog
from db_browser import DbBrowser
from table_view import TableView


class TuskWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs):
        super().__init__(
            title='Tusk',
            default_width=1100,
            default_height=700,
            **kwargs,
        )
        self._store = ConnectionStore()
        self._active_conn_id = None
        self._build_ui()
        self._load_connections()

    def _build_ui(self):
        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        paned.set_position(280)
        paned.set_shrink_start_child(False)
        paned.set_shrink_end_child(False)

        # ── Sidebar ──────────────────────────────────────────────────────────
        sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        sidebar_header = Adw.HeaderBar()
        sidebar_header.set_show_end_title_buttons(False)
        sidebar_header.set_title_widget(Gtk.Label(label='Tusk'))

        add_btn = Gtk.Button(icon_name='list-add-symbolic')
        add_btn.set_tooltip_text('New Connection')
        add_btn.connect('clicked', self._on_add_connection)
        sidebar_header.pack_end(add_btn)

        sidebar.append(sidebar_header)

        # Connection list
        self._conn_list = Gtk.ListBox()
        self._conn_list.add_css_class('navigation-sidebar')
        self._conn_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._conn_list.connect('row-activated', self._on_connection_activated)

        conn_scroll = Gtk.ScrolledWindow()
        conn_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        conn_scroll.set_propagate_natural_height(True)
        conn_scroll.set_max_content_height(220)
        conn_scroll.set_child(self._conn_list)
        sidebar.append(conn_scroll)

        sidebar.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # Database tree browser
        self._browser = DbBrowser()
        self._browser.connect('table-selected', self._on_table_selected)
        sidebar.append(self._browser)

        paned.set_start_child(sidebar)
        paned.set_resize_start_child(False)

        # ── Main content ─────────────────────────────────────────────────────
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        main_header = Adw.HeaderBar()
        self._title_label = Gtk.Label(label='Tusk')
        main_header.set_title_widget(self._title_label)
        main_box.append(main_header)

        self._content_stack = Gtk.Stack()
        self._content_stack.set_vexpand(True)

        empty = Adw.StatusPage()
        empty.set_title('No Table Selected')
        empty.set_description('Connect to a database and select a table to view its data')
        empty.set_icon_name('network-server-symbolic')
        self._content_stack.add_named(empty, 'empty')

        self._table_view = TableView()
        self._content_stack.add_named(self._table_view, 'table')

        self._content_stack.set_visible_child_name('empty')
        main_box.append(self._content_stack)

        paned.set_end_child(main_box)

        self.set_content(paned)

    def _load_connections(self):
        for conn in self._store.list():
            self._add_connection_row(conn)

    def _add_connection_row(self, conn):
        row = Adw.ActionRow()
        row.set_title(conn['name'])
        row.set_subtitle(f"{conn['host']}:{conn['port']}/{conn['database']}")
        row.set_icon_name('network-server-symbolic')
        row.set_activatable(True)
        row._conn = conn

        # Overflow menu
        menu = Gio.Menu()
        menu.append('Edit', 'row.edit')
        menu.append('Delete', 'row.delete')

        menu_btn = Gtk.MenuButton()
        menu_btn.set_icon_name('view-more-symbolic')
        menu_btn.set_menu_model(menu)
        menu_btn.add_css_class('flat')
        menu_btn.set_valign(Gtk.Align.CENTER)
        row.add_suffix(menu_btn)

        # Scope actions to this row so each row has independent edit/delete
        ag = Gio.SimpleActionGroup()

        edit_action = Gio.SimpleAction.new('edit', None)
        edit_action.connect('activate', lambda a, p, r=row: self._on_edit_connection(r))
        ag.add_action(edit_action)

        delete_action = Gio.SimpleAction.new('delete', None)
        delete_action.connect('activate', lambda a, p, r=row: self._on_delete_connection(r))
        ag.add_action(delete_action)

        row.insert_action_group('row', ag)

        self._conn_list.append(row)
        return row

    def _on_add_connection(self, _btn):
        dlg = ConnectionDialog(parent=self)
        dlg.connect('connection-saved', self._on_connection_added)
        dlg.present()

    def _on_connection_added(self, _dlg, conn):
        self._store.add(conn)
        self._add_connection_row(conn)

    def _on_edit_connection(self, row):
        dlg = ConnectionDialog(parent=self, connection=row._conn)
        dlg.connect('connection-saved', self._on_connection_updated, row)
        dlg.present()

    def _on_connection_updated(self, _dlg, conn, old_row):
        self._store.update(conn)
        # Replace the old row with a fresh one
        index = old_row.get_index()
        self._conn_list.remove(old_row)
        new_row = self._add_connection_row(conn)
        # Re-insert at the same position
        self._conn_list.remove(new_row)
        self._conn_list.insert(new_row, index)
        # If this was the active connection, clear the browser
        if self._active_conn_id == conn['id']:
            self._browser.clear()
            self._reset_content()

    def _on_delete_connection(self, row):
        conn = row._conn
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading='Delete Connection?',
            body=f'"{conn["name"]}" will be permanently removed.',
        )
        dialog.add_response('cancel', 'Cancel')
        dialog.add_response('delete', 'Delete')
        dialog.set_response_appearance('delete', Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response('cancel')
        dialog.set_close_response('cancel')
        dialog.connect('response', self._on_delete_response, row)
        dialog.present()

    def _on_delete_response(self, _dialog, response, row):
        if response != 'delete':
            return
        conn = row._conn
        self._store.remove(conn['id'])
        self._conn_list.remove(row)
        if self._active_conn_id == conn['id']:
            self._browser.clear()
            self._reset_content()

    def _reset_content(self):
        self._active_conn_id = None
        self._title_label.set_label('Tusk')
        self._content_stack.set_visible_child_name('empty')

    def _conn_with_password(self, conn):
        return {
            **conn,
            'password': self._store.get_password(conn['id']),
            'ssh_passphrase': self._store.get_ssh_passphrase(conn['id']),
        }

    def _on_connection_activated(self, _listbox, row):
        self._active_conn_id = row._conn['id']
        self._browser.load(self._conn_with_password(row._conn))

    def _on_table_selected(self, _browser, conn, schema, table):
        self._title_label.set_label(f'{schema}.{table}')
        self._table_view.load(conn, schema, table)
        self._content_stack.set_visible_child_name('table')
