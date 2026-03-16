import os

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Gtk, Adw, Gio, GLib, GObject

import prefs
from connections import ConnectionStore
from connection_dialog import ConnectionDialog
from db_browser import DbBrowser
from file_explorer import FileExplorer
from sql_editor import SqlEditor
from table_panel import TablePanel


class TuskWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs):
        super().__init__(
            title='Tusk',
            default_width=1280,
            default_height=800,
            **kwargs,
        )
        self._store = ConnectionStore()
        self._active_conn_id = None
        self._active_conn = None       # full conn dict with password
        self._build_ui()
        self._add_actions()
        self._load_connections()

    # ── Actions / shortcuts ───────────────────────────────────────────────────

    def _add_actions(self):
        def add(name, cb):
            a = Gio.SimpleAction.new(name, None)
            a.connect('activate', cb)
            self.add_action(a)

        add('close-tab',  lambda *_: self._close_current_tab())
        add('next-tab',   lambda *_: self._tab_view.select_next_page())
        add('prev-tab',   lambda *_: self._tab_view.select_previous_page())
        for i in range(1, 10):
            idx = i - 1
            a = Gio.SimpleAction.new(f'goto-tab-{i}', None)
            a.connect('activate', lambda _a, _p, n=idx: self._goto_tab(n))
            self.add_action(a)

    def _close_current_tab(self):
        page = self._tab_view.get_selected_page()
        if page:
            self._tab_view.close_page(page)

    def _goto_tab(self, index):
        pages = self._tab_view.get_pages()
        if index < pages.get_n_items():
            self._tab_view.set_selected_page(pages.get_item(index))

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        root = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        root.set_position(300)
        root.set_shrink_start_child(False)
        root.set_shrink_end_child(False)

        root.set_start_child(self._build_sidebar())
        root.set_end_child(self._build_main())
        self.set_content(root)

    def _build_sidebar(self):
        sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(False)
        header.set_title_widget(Gtk.Label(label='Tusk'))

        add_btn = Gtk.Button(icon_name='list-add-symbolic')
        add_btn.set_tooltip_text('New Connection')
        add_btn.connect('clicked', self._on_add_connection)
        header.pack_end(add_btn)

        sidebar.append(header)

        # Connection list
        self._conn_list = Gtk.ListBox()
        self._conn_list.add_css_class('navigation-sidebar')
        self._conn_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._conn_list.connect('row-activated', self._on_connection_activated)

        conn_scroll = Gtk.ScrolledWindow()
        conn_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        conn_scroll.set_propagate_natural_height(True)
        conn_scroll.set_max_content_height(200)
        conn_scroll.set_child(self._conn_list)
        sidebar.append(conn_scroll)

        sidebar.append(Gtk.Separator())

        # Vertical pane: DB browser (top) + file explorer (bottom)
        sidebar_paned = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL)
        sidebar_paned.set_position(prefs.get('sidebar_pane_pos', 320))
        sidebar_paned.set_vexpand(True)
        sidebar_paned.set_shrink_start_child(False)
        sidebar_paned.set_shrink_end_child(False)
        sidebar_paned.connect('notify::position',
                              lambda p, _: prefs.put('sidebar_pane_pos', p.get_position()))

        self._browser = DbBrowser()
        self._browser.connect('table-selected', self._on_table_selected)
        sidebar_paned.set_start_child(self._browser)

        self._file_explorer = FileExplorer()
        self._file_explorer.connect('file-activated', self._on_file_activated)
        sidebar_paned.set_end_child(self._file_explorer)

        sidebar.append(sidebar_paned)
        return sidebar

    def _build_main(self):
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        self._main_header = Adw.HeaderBar()
        self._header_label = Gtk.Label(label='Tusk')
        self._main_header.set_title_widget(self._header_label)

        menu = Gio.Menu()
        menu.append('About Tusk', 'app.about')

        menu_btn = Gtk.MenuButton()
        menu_btn.set_icon_name('open-menu-symbolic')
        menu_btn.set_menu_model(menu)
        self._main_header.pack_end(menu_btn)

        main_box.append(self._main_header)

        # Content: empty state or tab view
        self._main_stack = Gtk.Stack()
        self._main_stack.set_vexpand(True)

        empty = Adw.StatusPage()
        empty.set_title('Nothing Open')
        empty.set_description('Select a table from the browser or open a .sql file')
        empty.set_icon_name('io.tusk.Tusk')
        self._main_stack.add_named(empty, 'empty')

        tabs_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._tab_bar = Adw.TabBar()
        self._tab_view = Adw.TabView()
        self._tab_bar.set_view(self._tab_view)
        self._tab_view.set_vexpand(True)
        self._tab_view.connect('notify::selected-page', self._on_tab_changed)
        self._tab_view.connect('close-page', self._on_close_page)
        tabs_box.append(self._tab_bar)
        tabs_box.append(self._tab_view)
        self._main_stack.add_named(tabs_box, 'tabs')

        self._main_stack.set_visible_child_name('empty')
        main_box.append(self._main_stack)
        return main_box

    # ── Connections ───────────────────────────────────────────────────────────

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

        menu = Gio.Menu()
        menu.append('Edit', 'row.edit')
        menu.append('Delete', 'row.delete')

        menu_btn = Gtk.MenuButton()
        menu_btn.set_icon_name('view-more-symbolic')
        menu_btn.set_menu_model(menu)
        menu_btn.add_css_class('flat')
        menu_btn.set_valign(Gtk.Align.CENTER)
        row.add_suffix(menu_btn)

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
        index = old_row.get_index()
        self._conn_list.remove(old_row)
        new_row = self._add_connection_row(conn)
        self._conn_list.remove(new_row)
        self._conn_list.insert(new_row, index)
        if self._active_conn_id == conn['id']:
            self._set_active_conn(conn)
            self._browser.clear()

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
            self._set_active_conn(None)
            self._browser.clear()

    def _conn_with_password(self, conn):
        return {
            **conn,
            'password': self._store.get_password(conn['id']),
            'ssh_passphrase': self._store.get_ssh_passphrase(conn['id']),
        }

    def _on_connection_activated(self, _listbox, row):
        conn = self._conn_with_password(row._conn)
        self._set_active_conn(conn)
        self._browser.load(conn)

    def _set_active_conn(self, conn):
        self._active_conn_id = conn['id'] if conn else None
        self._active_conn = conn
        # Update all open SQL editors
        pages = self._tab_view.get_pages()
        for i in range(pages.get_n_items()):
            widget = pages.get_item(i).get_child()
            if isinstance(widget, SqlEditor):
                widget.set_connection(conn)

    # ── Table / file tabs ─────────────────────────────────────────────────────

    def _on_table_selected(self, _browser, conn, schema, table, item_type):
        tab_id = f'table:{conn["id"]}:{schema}.{table}'
        existing = self._find_tab(tab_id)
        if existing:
            self._tab_view.set_selected_page(existing)
            return

        panel = TablePanel()
        panel.load(conn, schema, table, item_type)

        icon_name = 'x-office-spreadsheet-symbolic' if item_type == 'table' else 'view-grid-symbolic'
        page = self._tab_view.append(panel)
        page.set_title(f'{schema}.{table}')
        page.set_icon(Gio.ThemedIcon.new(icon_name))
        page._tab_id = tab_id

        self._show_tabs()
        self._tab_view.set_selected_page(page)

    def _on_file_activated(self, _explorer, file_path):
        tab_id = f'file:{file_path}'
        existing = self._find_tab(tab_id)
        if existing:
            self._tab_view.set_selected_page(existing)
            return

        editor = SqlEditor(file_path)
        editor.set_connection(self._active_conn)
        editor.connect('run-sql', lambda e: e.run())

        page = self._tab_view.append(editor)
        page.set_title(os.path.basename(file_path))
        page.set_icon(Gio.ThemedIcon.new('x-office-document-symbolic'))
        page._tab_id = tab_id

        self._show_tabs()
        self._tab_view.set_selected_page(page)

    def _find_tab(self, tab_id):
        pages = self._tab_view.get_pages()
        for i in range(pages.get_n_items()):
            page = pages.get_item(i)
            if getattr(page, '_tab_id', None) == tab_id:
                return page
        return None

    def _show_tabs(self):
        self._main_stack.set_visible_child_name('tabs')

    def _on_tab_changed(self, tab_view, _param):
        page = tab_view.get_selected_page()
        if page:
            self._header_label.set_label(page.get_title())
        else:
            self._header_label.set_label('Tusk')

    def _on_close_page(self, tab_view, page):
        tab_view.close_page_finish(page, True)
        # Switch back to empty if no tabs remain
        GLib.idle_add(self._check_tabs_empty)
        return True

    def _check_tabs_empty(self):
        if self._tab_view.get_n_pages() == 0:
            self._header_label.set_label('Tusk')
            self._main_stack.set_visible_child_name('empty')
