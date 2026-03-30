import os
import threading

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Gtk, Adw, Gio, GLib, GObject, Gdk

import prefs
from connections import ConnectionStore, KeyringUnavailableError
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
        self._sidebar_css = Gtk.CssProvider()
        self._main_css = Gtk.CssProvider()
        display = Gdk.Display.get_default()
        Gtk.StyleContext.add_provider_for_display(
            display, self._sidebar_css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        Gtk.StyleContext.add_provider_for_display(
            display, self._main_css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self._build_ui()
        self._apply_fonts()
        self._add_actions()
        self.set_help_overlay(self._build_shortcuts_window())
        self._load_connections()

    # ── Actions / shortcuts ───────────────────────────────────────────────────

    def _add_actions(self):
        def add(name, cb):
            a = Gio.SimpleAction.new(name, None)
            a.connect('activate', cb)
            self.add_action(a)

        add('close-tab',      lambda *_: self._close_current_tab())
        add('next-tab',       lambda *_: self._tab_view.select_next_page())
        add('prev-tab',       lambda *_: self._tab_view.select_previous_page())
        self._refresh_action = Gio.SimpleAction.new('refresh-tab', None)
        self._refresh_action.connect('activate', lambda *_: self._refresh_current_tab())
        self._refresh_action.set_enabled(False)
        self.add_action(self._refresh_action)
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

    # ── Fonts ─────────────────────────────────────────────────────────────────

    _FONT_FAMILIES = ['', 'Sans', 'Serif', 'Monospace']

    def _apply_fonts(self):
        for scope, provider in [('sidebar', self._sidebar_css), ('main', self._main_css)]:
            family = self._FONT_FAMILIES[prefs.get(f'{scope}_font', 0)]
            size   = prefs.get(f'{scope}_size', 10)
            decls  = (f'font-family: {family}; ' if family else '') + f'font-size: {size}pt;'
            css = (
                f'.tusk-{scope}, '
                f'.tusk-{scope} label, '
                f'.tusk-{scope} textview text, '
                f'.tusk-{scope} treeview '
                f'{{ {decls} }}'
            )
            provider.load_from_string(css)

    # ── Shortcuts window ──────────────────────────────────────────────────────

    def _build_shortcuts_window(self):
        xml = """<?xml version="1.0" encoding="UTF-8"?>
<interface>
  <object class="GtkShortcutsWindow" id="win">
    <property name="modal">1</property>
    <child>
      <object class="GtkShortcutsSection">
        <child>
          <object class="GtkShortcutsGroup">
            <property name="title">Tabs</property>
            <child>
              <object class="GtkShortcutsShortcut">
                <property name="title">Close Tab</property>
                <property name="accelerator">&lt;ctrl&gt;w</property>
              </object>
            </child>
            <child>
              <object class="GtkShortcutsShortcut">
                <property name="title">Next Tab</property>
                <property name="accelerator">&lt;ctrl&gt;Tab</property>
              </object>
            </child>
            <child>
              <object class="GtkShortcutsShortcut">
                <property name="title">Previous Tab</property>
                <property name="accelerator">&lt;ctrl&gt;&lt;shift&gt;Tab</property>
              </object>
            </child>
            <child>
              <object class="GtkShortcutsShortcut">
                <property name="title">Go to Tab 1–9</property>
                <property name="accelerator">&lt;alt&gt;1</property>
              </object>
            </child>
          </object>
        </child>
        <child>
          <object class="GtkShortcutsGroup">
            <property name="title">Table Inspector</property>
            <child>
              <object class="GtkShortcutsShortcut">
                <property name="title">Refresh</property>
                <property name="accelerator">&lt;ctrl&gt;r</property>
              </object>
            </child>
          </object>
        </child>
        <child>
          <object class="GtkShortcutsGroup">
            <property name="title">SQL Editor</property>
            <child>
              <object class="GtkShortcutsShortcut">
                <property name="title">Run All</property>
                <property name="accelerator">F5</property>
              </object>
            </child>
            <child>
              <object class="GtkShortcutsShortcut">
                <property name="title">Run Selected</property>
                <property name="accelerator">&lt;ctrl&gt;Return</property>
              </object>
            </child>
            <child>
              <object class="GtkShortcutsShortcut">
                <property name="title">Save File</property>
                <property name="accelerator">&lt;ctrl&gt;s</property>
              </object>
            </child>
          </object>
        </child>
        <child>
          <object class="GtkShortcutsGroup">
            <property name="title">Application</property>
            <child>
              <object class="GtkShortcutsShortcut">
                <property name="title">Preferences</property>
                <property name="accelerator">&lt;ctrl&gt;comma</property>
              </object>
            </child>
            <child>
              <object class="GtkShortcutsShortcut">
                <property name="title">Keyboard Shortcuts</property>
                <property name="accelerator">&lt;ctrl&gt;question</property>
              </object>
            </child>
            <child>
              <object class="GtkShortcutsShortcut">
                <property name="title">Quit</property>
                <property name="accelerator">&lt;ctrl&gt;q</property>
              </object>
            </child>
          </object>
        </child>
      </object>
    </child>
  </object>
</interface>"""
        builder = Gtk.Builder.new_from_string(xml, -1)
        return builder.get_object('win')

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
        sidebar.add_css_class('tusk-sidebar')

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

        placeholder = Adw.StatusPage()
        placeholder.set_title('No connections')
        placeholder.set_description('Click + to add your first connection')
        placeholder.set_icon_name('network-server-symbolic')
        self._conn_list.set_placeholder(placeholder)

        conn_scroll = Gtk.ScrolledWindow()
        conn_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        conn_scroll.set_propagate_natural_height(True)
        conn_scroll.set_max_content_height(200)
        conn_scroll.set_child(self._conn_list)
        sidebar.append(conn_scroll)

        sidebar.append(Gtk.Separator())

        self._active_conn_label = Gtk.Label()
        self._active_conn_label.add_css_class('caption')
        self._active_conn_label.add_css_class('dim-label')
        self._active_conn_label.set_xalign(0)
        self._active_conn_label.set_margin_start(10)
        self._active_conn_label.set_margin_top(4)
        self._active_conn_label.set_margin_bottom(4)
        self._active_conn_label.set_visible(False)
        sidebar.append(self._active_conn_label)

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
        self._browser.connect('create-table-requested', self._on_create_table_requested)
        self._browser.connect('drop-table-requested', self._on_drop_table_requested)
        self._browser.connect('truncate-table-requested', self._on_truncate_table_requested)
        self._browser.connect('rename-table-requested', self._on_rename_table_requested)
        self._browser.connect('clone-table-requested', self._on_clone_table_requested)
        self._browser.connect('create-schema-requested', self._on_create_schema_requested)
        self._browser.connect('rename-schema-requested', self._on_rename_schema_requested)
        self._browser.connect('drop-schema-requested', self._on_drop_schema_requested)
        self._browser.connect('create-view-requested', self._on_create_view_requested)
        sidebar_paned.set_start_child(self._browser)

        self._file_explorer = FileExplorer()
        self._file_explorer.connect('file-activated', self._on_file_activated)
        self._file_explorer.connect('file-deleted', self._on_file_deleted)
        self._file_explorer.connect('file-renamed', self._on_file_renamed)
        sidebar_paned.set_end_child(self._file_explorer)

        sidebar.append(sidebar_paned)
        return sidebar

    def _build_main(self):
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        main_box.add_css_class('tusk-main')

        self._main_header = Adw.HeaderBar()
        self._header_label = Gtk.Label(label='Tusk')
        self._main_header.set_title_widget(self._header_label)

        menu = Gio.Menu()

        util_section = Gio.Menu()
        util_section.append('Preferences', 'app.preferences')
        util_section.append('Keyboard Shortcuts', 'win.show-help-overlay')
        menu.append_section(None, util_section)

        app_section = Gio.Menu()
        app_section.append('Sponsor Tusk', 'app.sponsor')
        app_section.append('About Tusk', 'app.about')
        menu.append_section(None, app_section)

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
        empty.set_icon_name('xyz.shapemachine.tusk-gnome')

        sponsor_btn = Gtk.Button(label='Sponsor Tusk')
        sponsor_btn.set_action_name('app.sponsor')
        sponsor_btn.add_css_class('flat')
        sponsor_btn.set_halign(Gtk.Align.CENTER)
        empty.set_child(sponsor_btn)

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

    @staticmethod
    def _conn_subtitle(conn):
        return f"{conn['host']}:{conn['port']}/{conn['database']}"

    def _add_connection_row(self, conn):
        row = Adw.ActionRow()
        row.set_title(conn['name'])
        row.set_subtitle(self._conn_subtitle(conn))
        row.set_icon_name('network-server-symbolic')
        row.set_activatable(True)
        row._conn = conn

        dot = Gtk.Label(label='●')
        dot.add_css_class('accent')
        dot.set_visible(False)
        dot.set_tooltip_text('Active connection')
        dot.set_valign(Gtk.Align.CENTER)
        row.add_suffix(dot)
        row._active_dot = dot

        menu = Gio.Menu()
        menu.append('Disconnect', 'row.disconnect')
        menu.append('Edit', 'row.edit')
        menu.append('Delete', 'row.delete')

        menu_btn = Gtk.MenuButton()
        menu_btn.set_icon_name('view-more-symbolic')
        menu_btn.set_menu_model(menu)
        menu_btn.add_css_class('flat')
        menu_btn.set_valign(Gtk.Align.CENTER)
        menu_btn.set_tooltip_text('Connection options')
        row.add_suffix(menu_btn)

        ag = Gio.SimpleActionGroup()
        disconnect_action = Gio.SimpleAction.new('disconnect', None)
        disconnect_action.set_enabled(False)
        disconnect_action.connect('activate', lambda a, p, r=row: self._on_disconnect(r))
        ag.add_action(disconnect_action)
        row._disconnect_action = disconnect_action
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

    def _show_keyring_error(self, msg):
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading='Secrets Service Unavailable',
            body=f'Passwords could not be saved or retrieved. '
                 f'Make sure a secrets service (e.g. GNOME Keyring) is running.\n\nDetail: {msg}',
        )
        dialog.add_response('ok', 'OK')
        dialog.set_default_response('ok')
        dialog.present()

    def _on_connection_added(self, _dlg, conn):
        try:
            self._store.add(conn)
        except KeyringUnavailableError as e:
            self._show_keyring_error(str(e))
            return
        self._add_connection_row(conn)

    def _on_edit_connection(self, row):
        dlg = ConnectionDialog(parent=self, connection=row._conn)
        dlg.connect('connection-saved', self._on_connection_updated, row)
        dlg.present()

    def _on_connection_updated(self, _dlg, conn, old_row):
        try:
            self._store.update(conn)
        except KeyringUnavailableError as e:
            self._show_keyring_error(str(e))
            return
        old_row._conn = conn
        old_row.set_title(conn['name'])
        old_row.set_subtitle(self._conn_subtitle(conn))
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
        try:
            self._store.remove(conn['id'])
        except KeyringUnavailableError as e:
            self._show_keyring_error(str(e))
            return
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
        try:
            conn = self._conn_with_password(row._conn)
        except KeyringUnavailableError as e:
            self._show_keyring_error(str(e))
            return
        self._set_active_conn(conn)
        self._browser.load(conn)

    def _on_disconnect(self, row):
        if self._active_conn_id == row._conn['id']:
            self._set_active_conn(None)
            self._browser.clear()

    def _set_active_conn(self, conn):
        # Close all table tabs belonging to the previous connection
        if self._active_conn_id:
            prefix = f'table:{self._active_conn_id}:'
            pages = self._tab_view.get_pages()
            to_close = [
                pages.get_item(i)
                for i in range(pages.get_n_items())
                if getattr(pages.get_item(i), '_tab_id', '').startswith(prefix)
            ]
            for page in to_close:
                self._tab_view.close_page(page)

        self._active_conn_id = conn['id'] if conn else None
        self._active_conn = conn

        # Update row indicators
        row = self._conn_list.get_first_child()
        while row:
            if hasattr(row, '_conn'):
                is_active = bool(conn and row._conn['id'] == conn['id'])
                row._active_dot.set_visible(is_active)
                row._disconnect_action.set_enabled(is_active)
            row = row.get_next_sibling()

        # Update active connection label above DB browser
        if conn:
            self._active_conn_label.set_label(f'Connected: {conn["name"]}')
            self._active_conn_label.set_visible(True)
        else:
            self._active_conn_label.set_label('')
            self._active_conn_label.set_visible(False)

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
        editor.connect('run-selected-sql', lambda e: e.run_selected())
        editor.connect('ddl-executed', lambda _: self._on_ddl_executed())

        page = self._tab_view.append(editor)
        page.set_title(os.path.basename(file_path))
        page.set_icon(Gio.ThemedIcon.new('x-office-document-symbolic'))
        page._tab_id = tab_id

        self._show_tabs()
        self._tab_view.set_selected_page(page)

    def _on_file_deleted(self, _explorer, file_path):
        tab_id = f'file:{file_path}'
        page = self._find_tab(tab_id)
        if page:
            self._tab_view.close_page(page)

    def _on_file_renamed(self, _explorer, old_path, new_path):
        tab_id = f'file:{old_path}'
        page = self._find_tab(tab_id)
        if page:
            page._tab_id = f'file:{new_path}'
            page.set_title(os.path.basename(new_path))
            page.get_child().file_path = new_path

    def _find_tab(self, tab_id):
        pages = self._tab_view.get_pages()
        for i in range(pages.get_n_items()):
            page = pages.get_item(i)
            if getattr(page, '_tab_id', None) == tab_id:
                return page
        return None

    def _show_tabs(self):
        self._main_stack.set_visible_child_name('tabs')

    def _refresh_current_tab(self):
        page = self._tab_view.get_selected_page()
        if page and isinstance(page.get_child(), TablePanel):
            page.get_child()._on_refresh()

    def _on_tab_changed(self, tab_view, _param):
        page = tab_view.get_selected_page()
        if page:
            self._header_label.set_label(page.get_title())
            self._refresh_action.set_enabled(isinstance(page.get_child(), TablePanel))
        else:
            self._header_label.set_label('Tusk')
            self._refresh_action.set_enabled(False)

    def _on_close_page(self, tab_view, page):
        tab_view.close_page_finish(page, True)
        # Switch back to empty if no tabs remain
        GLib.idle_add(self._check_tabs_empty)
        return True

    def _check_tabs_empty(self):
        if self._tab_view.get_n_pages() == 0:
            self._header_label.set_label('Tusk')
            self._main_stack.set_visible_child_name('empty')

    def _on_ddl_executed(self):
        if self._active_conn:
            self._browser.load(self._active_conn)

    # ── Create Table (from browser right-click) ───────────────────────────────

    def _on_create_table_requested(self, _browser, conn, schema):
        from column_dialogs import CreateTableDialog
        schemas = self._browser.get_loaded_schemas()
        if not schemas:
            schemas = [schema]

        def on_save(ddl_sql, on_done):
            def run():
                try:
                    import psycopg
                    from tunnel import open_tunnel
                    with open_tunnel(conn) as (host, port), psycopg.connect(
                        host=host, port=port,
                        dbname=conn['database'], user=conn['username'],
                        password=conn['password'], connect_timeout=10,
                    ) as db:
                        with db.cursor() as cur:
                            cur.execute(ddl_sql)
                        db.commit()
                    GLib.idle_add(self._browser.load, conn)
                    GLib.idle_add(on_done)
                except Exception as e:
                    GLib.idle_add(on_done, str(e))
            threading.Thread(target=run, daemon=True).start()

        dlg = CreateTableDialog(schemas, schema, on_save)
        dlg.present(self)

    # ── Drop Table / Drop View (from browser right-click) ────────────────────

    def _on_drop_table_requested(self, _browser, conn, schema, table, item_type):
        is_view = item_type == 'view'
        heading = f'Drop {"view" if is_view else "table"} "{schema}.{table}"?'
        body = ('This removes the view definition.' if is_view else
                'All data will be permanently deleted. This action cannot be undone.')

        cascade_check = Gtk.CheckButton(label='Drop with CASCADE (removes dependent objects)')
        cascade_check.set_margin_top(8)
        cascade_check.set_margin_start(4)

        dialog = Adw.AlertDialog(heading=heading, body=body)
        if not is_view:
            dialog.set_extra_child(cascade_check)
        dialog.add_response('cancel', 'Cancel')
        drop_label = 'Drop View' if is_view else 'Drop Table'
        dialog.add_response('drop', drop_label)
        dialog.set_response_appearance('drop', Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response('cancel')
        dialog.set_close_response('cancel')

        def execute_drop(cascade):
            obj_type = 'VIEW' if is_view else 'TABLE'
            qi = lambda n: '"' + n.replace('"', '""') + '"'
            ddl = f'DROP {obj_type} {qi(schema)}.{qi(table)}'
            if cascade:
                ddl += ' CASCADE'
            ddl += ';'

            def run():
                try:
                    import psycopg
                    from tunnel import open_tunnel
                    with open_tunnel(conn) as (host, port), psycopg.connect(
                        host=host, port=port,
                        dbname=conn['database'], user=conn['username'],
                        password=conn['password'], connect_timeout=10,
                    ) as db:
                        with db.cursor() as cur:
                            cur.execute(ddl)
                        db.commit()
                    tab_id = f'table:{conn["id"]}:{schema}.{table}'
                    GLib.idle_add(self._close_tab_by_id, tab_id)
                    GLib.idle_add(self._browser.load, conn)
                except Exception as e:
                    GLib.idle_add(self._show_drop_error, e, conn, schema, table, item_type)

            threading.Thread(target=run, daemon=True).start()

        def on_response(_d, response):
            if response != 'drop':
                return
            execute_drop(cascade=(not is_view) and cascade_check.get_active())

        dialog.connect('response', on_response)
        dialog.present(self)

    # ── Truncate Table (from browser right-click) ─────────────────────────────

    def _on_truncate_table_requested(self, _browser, conn, schema, table):
        restart_check = Gtk.CheckButton(label='Restart identity sequences (RESTART IDENTITY)')
        restart_check.set_margin_top(8)
        restart_check.set_margin_start(4)

        dialog = Adw.AlertDialog(
            heading=f'Truncate "{schema}.{table}"?',
            body='All rows will be deleted. The table structure is preserved.',
        )
        dialog.set_extra_child(restart_check)
        dialog.add_response('cancel', 'Cancel')
        dialog.add_response('truncate', 'Truncate')
        dialog.set_response_appearance('truncate', Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response('cancel')
        dialog.set_close_response('cancel')

        def on_response(_d, response):
            if response != 'truncate':
                return
            qi = lambda n: '"' + n.replace('"', '""') + '"'
            ddl = f'TRUNCATE TABLE {qi(schema)}.{qi(table)}'
            if restart_check.get_active():
                ddl += ' RESTART IDENTITY'
            ddl += ';'

            def run():
                try:
                    import psycopg
                    from tunnel import open_tunnel
                    with open_tunnel(conn) as (host, port), psycopg.connect(
                        host=host, port=port,
                        dbname=conn['database'], user=conn['username'],
                        password=conn['password'], connect_timeout=10,
                    ) as db:
                        with db.cursor() as cur:
                            cur.execute(ddl)
                        db.commit()
                    # Refresh data tab if open
                    tab_id = f'table:{conn["id"]}:{schema}.{table}'
                    GLib.idle_add(self._refresh_tab_by_id, tab_id)
                except Exception as e:
                    GLib.idle_add(self._show_browser_error, 'Truncate Failed', str(e))

            threading.Thread(target=run, daemon=True).start()

        dialog.connect('response', on_response)
        dialog.present(self)

    # ── Rename Table (from browser right-click) ───────────────────────────────

    def _on_rename_table_requested(self, _browser, conn, schema, table):
        from column_dialogs import RenameDialog

        def on_rename(new_name):
            qi = lambda n: '"' + n.replace('"', '""') + '"'
            ddl = f'ALTER TABLE {qi(schema)}.{qi(table)} RENAME TO {qi(new_name)};'

            def run():
                try:
                    import psycopg
                    from tunnel import open_tunnel
                    with open_tunnel(conn) as (host, port), psycopg.connect(
                        host=host, port=port,
                        dbname=conn['database'], user=conn['username'],
                        password=conn['password'], connect_timeout=10,
                    ) as db:
                        with db.cursor() as cur:
                            cur.execute(ddl)
                        db.commit()
                    old_tab_id = f'table:{conn["id"]}:{schema}.{table}'
                    new_tab_id = f'table:{conn["id"]}:{schema}.{new_name}'
                    GLib.idle_add(self._rename_tab, old_tab_id, new_tab_id,
                                  f'{schema}.{new_name}', conn, schema, new_name)
                    GLib.idle_add(self._browser.load, conn)
                except Exception as e:
                    GLib.idle_add(self._show_browser_error, 'Rename Failed', str(e))

            threading.Thread(target=run, daemon=True).start()

        dlg = RenameDialog(table, on_rename, title='Rename Table')
        dlg.present(self)

    # ── Clone Table Structure (from browser right-click) ─────────────────────

    def _on_clone_table_requested(self, _browser, conn, schema, table):
        from column_dialogs import CreateTableDialog

        def run():
            try:
                import psycopg
                from tunnel import open_tunnel
                with open_tunnel(conn) as (host, port), psycopg.connect(
                    host=host, port=port,
                    dbname=conn['database'], user=conn['username'],
                    password=conn['password'], connect_timeout=10,
                ) as db:
                    with db.cursor() as cur:
                        cur.execute('''
                            SELECT
                                a.attname,
                                pg_catalog.format_type(a.atttypid, a.atttypmod),
                                NOT a.attnotnull,
                                pg_get_expr(d.adbin, d.adrelid),
                                EXISTS (
                                    SELECT 1 FROM pg_index i
                                    JOIN pg_attribute ia ON ia.attrelid = i.indrelid
                                        AND ia.attnum = ANY(i.indkey)
                                    WHERE i.indrelid = a.attrelid
                                        AND ia.attnum = a.attnum
                                        AND i.indisprimary
                                )
                            FROM pg_attribute a
                            LEFT JOIN pg_attrdef d
                                ON d.adrelid = a.attrelid AND d.adnum = a.attnum
                            JOIN pg_class c ON c.oid = a.attrelid
                            JOIN pg_namespace n ON n.oid = c.relnamespace
                            WHERE n.nspname = %s AND c.relname = %s
                              AND a.attnum > 0 AND NOT a.attisdropped
                            ORDER BY a.attnum
                        ''', (schema, table))
                        cols = [
                            {
                                'name': r[0],
                                'type': r[1],
                                'nullable': r[2],
                                'default': r[3] or '',
                                'is_pk': r[4],
                            }
                            for r in cur.fetchall()
                        ]
                GLib.idle_add(self._open_clone_dialog, conn, schema, cols, table)
            except Exception as e:
                GLib.idle_add(self._show_browser_error, 'Clone Failed', str(e))

        threading.Thread(target=run, daemon=True).start()

    def _open_clone_dialog(self, conn, schema, cols, source_table):
        from column_dialogs import CreateTableDialog
        schemas = self._browser.get_loaded_schemas() or [schema]

        def on_save(ddl_sql, on_done):
            def run():
                try:
                    import psycopg
                    from tunnel import open_tunnel
                    with open_tunnel(conn) as (host, port), psycopg.connect(
                        host=host, port=port,
                        dbname=conn['database'], user=conn['username'],
                        password=conn['password'], connect_timeout=10,
                    ) as db:
                        with db.cursor() as cur:
                            cur.execute(ddl_sql)
                        db.commit()
                    GLib.idle_add(self._browser.load, conn)
                    GLib.idle_add(on_done)
                except Exception as e:
                    GLib.idle_add(on_done, str(e))
            threading.Thread(target=run, daemon=True).start()

        dlg = CreateTableDialog(
            schemas, schema, on_save,
            prefill_name=f'{source_table}_copy',
            prefill_columns=cols,
        )
        dlg.present(self)

    # ── Tab helpers ───────────────────────────────────────────────────────────

    def _close_tab_by_id(self, tab_id):
        page = self._find_tab(tab_id)
        if page:
            self._tab_view.close_page(page)

    def _refresh_tab_by_id(self, tab_id):
        page = self._find_tab(tab_id)
        if page and isinstance(page.get_child(), TablePanel):
            page.get_child()._on_refresh()

    def _rename_tab(self, old_tab_id, new_tab_id, new_title, conn, schema, new_name):
        page = self._find_tab(old_tab_id)
        if page:
            page._tab_id = new_tab_id
            page.set_title(new_title)
            if isinstance(page.get_child(), TablePanel):
                page.get_child().load(conn, schema, new_name, 'table')

    def _show_drop_error(self, exc, conn, schema, table, item_type):
        """Show a drop error dialog. For dependency errors, offer a Try with CASCADE button."""
        err_str = str(exc)
        is_dependency = 'depends on' in err_str or (
            hasattr(exc, 'sqlstate') and exc.sqlstate == '2BP01'
        )
        is_view = item_type == 'view'

        if is_dependency and not is_view:
            dialog = Adw.AlertDialog(
                heading='Drop Failed — Dependent Objects Exist',
                body=f'{err_str}\n\nUse "Drop with CASCADE" to also drop all dependent objects.',
            )
            dialog.add_response('cancel', 'Cancel')
            dialog.add_response('cascade', 'Drop with CASCADE')
            dialog.set_response_appearance('cascade', Adw.ResponseAppearance.DESTRUCTIVE)
            dialog.set_default_response('cancel')
            dialog.set_close_response('cancel')

            def on_cascade(_d, response):
                if response == 'cascade':
                    self._on_drop_table_requested_cascade(conn, schema, table, item_type)

            dialog.connect('response', on_cascade)
        else:
            dialog = Adw.AlertDialog(heading='Drop Failed', body=err_str)
            dialog.add_response('ok', 'OK')

        dialog.present(self)

    def _on_drop_table_requested_cascade(self, conn, schema, table, item_type):
        """Re-run drop with CASCADE after user confirms from the error dialog."""
        obj_type = 'VIEW' if item_type == 'view' else 'TABLE'
        qi = lambda n: '"' + n.replace('"', '""') + '"'
        ddl = f'DROP {obj_type} {qi(schema)}.{qi(table)} CASCADE;'

        def run():
            try:
                import psycopg
                from tunnel import open_tunnel
                with open_tunnel(conn) as (host, port), psycopg.connect(
                    host=host, port=port,
                    dbname=conn['database'], user=conn['username'],
                    password=conn['password'], connect_timeout=10,
                ) as db:
                    with db.cursor() as cur:
                        cur.execute(ddl)
                    db.commit()
                tab_id = f'table:{conn["id"]}:{schema}.{table}'
                GLib.idle_add(self._close_tab_by_id, tab_id)
                GLib.idle_add(self._browser.load, conn)
            except Exception as e:
                GLib.idle_add(self._show_browser_error, 'Drop Failed', str(e))

        threading.Thread(target=run, daemon=True).start()

    def _show_browser_error(self, heading, body):
        dialog = Adw.AlertDialog(heading=heading, body=body)
        dialog.add_response('ok', 'OK')
        dialog.present(self)

    # ── Schema management (#97, #100) ─────────────────────────────────────────

    def _on_create_schema_requested(self, _browser, conn):
        from column_dialogs import CreateSchemaDialog

        def on_save(schema_name, on_done):
            def run():
                try:
                    import psycopg
                    from psycopg import sql as pgsql
                    from tunnel import open_tunnel
                    with open_tunnel(conn) as (host, port), psycopg.connect(
                        host=host, port=port,
                        dbname=conn['database'], user=conn['username'],
                        password=conn['password'], connect_timeout=10,
                    ) as db:
                        with db.cursor() as cur:
                            cur.execute(pgsql.SQL('CREATE SCHEMA {}').format(
                                pgsql.Identifier(schema_name)
                            ))
                        db.commit()
                    GLib.idle_add(self._browser.load, conn)
                    GLib.idle_add(on_done)
                except Exception as e:
                    GLib.idle_add(on_done, str(e))
            threading.Thread(target=run, daemon=True).start()

        dlg = CreateSchemaDialog(on_save)
        dlg.present(self)

    def _on_rename_schema_requested(self, _browser, conn, schema):
        from column_dialogs import RenameDialog

        def on_rename(new_name):
            def run():
                try:
                    import psycopg
                    from psycopg import sql as pgsql
                    from tunnel import open_tunnel
                    with open_tunnel(conn) as (host, port), psycopg.connect(
                        host=host, port=port,
                        dbname=conn['database'], user=conn['username'],
                        password=conn['password'], connect_timeout=10,
                    ) as db:
                        with db.cursor() as cur:
                            cur.execute(pgsql.SQL('ALTER SCHEMA {} RENAME TO {}').format(
                                pgsql.Identifier(schema), pgsql.Identifier(new_name)
                            ))
                        db.commit()
                    GLib.idle_add(self._browser.set_rename_hint, schema, new_name)
                    GLib.idle_add(self._browser.load, conn)
                except Exception as e:
                    GLib.idle_add(self._show_browser_error, 'Rename Schema Failed', str(e))
            threading.Thread(target=run, daemon=True).start()

        dlg = RenameDialog(schema, on_rename, title='Rename Schema')
        dlg.present(self)

    def _on_drop_schema_requested(self, _browser, conn, schema):
        cascade_check = Gtk.CheckButton(label='Drop with CASCADE (removes all objects in schema)')
        cascade_check.set_margin_top(8)
        cascade_check.set_margin_start(4)

        dialog = Adw.AlertDialog(
            heading=f'Drop schema "{schema}"?',
            body='This will permanently remove the schema. Non-empty schemas require CASCADE.',
        )
        dialog.set_extra_child(cascade_check)
        dialog.add_response('cancel', 'Cancel')
        dialog.add_response('drop', 'Drop Schema')
        dialog.set_response_appearance('drop', Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response('cancel')
        dialog.set_close_response('cancel')

        def execute_drop(cascade):
            def run():
                try:
                    import psycopg
                    from psycopg import sql as pgsql
                    from tunnel import open_tunnel
                    with open_tunnel(conn) as (host, port), psycopg.connect(
                        host=host, port=port,
                        dbname=conn['database'], user=conn['username'],
                        password=conn['password'], connect_timeout=10,
                    ) as db:
                        with db.cursor() as cur:
                            ddl = pgsql.SQL('DROP SCHEMA {}').format(pgsql.Identifier(schema))
                            if cascade:
                                ddl = pgsql.SQL('DROP SCHEMA {} CASCADE').format(
                                    pgsql.Identifier(schema)
                                )
                            cur.execute(ddl)
                        db.commit()
                    GLib.idle_add(self._browser.load, conn)
                except Exception as e:
                    err = str(e)
                    if 'depends on' in err or (hasattr(e, 'sqlstate') and e.sqlstate == '2BP01'):
                        GLib.idle_add(self._show_drop_schema_cascade_error, err, conn, schema)
                    else:
                        GLib.idle_add(self._show_browser_error, 'Drop Schema Failed', err)
            threading.Thread(target=run, daemon=True).start()

        def on_response(_d, response):
            if response == 'drop':
                execute_drop(cascade_check.get_active())

        dialog.connect('response', on_response)
        dialog.present(self)

    def _show_drop_schema_cascade_error(self, err_str, conn, schema):
        dialog = Adw.AlertDialog(
            heading='Drop Schema Failed — Objects Exist',
            body=f'{err_str}\n\nEnable CASCADE to drop the schema and all its objects.',
        )
        dialog.add_response('cancel', 'Cancel')
        dialog.add_response('cascade', 'Drop with CASCADE')
        dialog.set_response_appearance('cascade', Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response('cancel')
        dialog.set_close_response('cancel')

        def on_cascade(_d, response):
            if response == 'cascade':
                self._drop_schema_cascade(conn, schema)

        dialog.connect('response', on_cascade)
        dialog.present(self)

    def _drop_schema_cascade(self, conn, schema):
        def run():
            try:
                import psycopg
                from psycopg import sql as pgsql
                from tunnel import open_tunnel
                with open_tunnel(conn) as (host, port), psycopg.connect(
                    host=host, port=port,
                    dbname=conn['database'], user=conn['username'],
                    password=conn['password'], connect_timeout=10,
                ) as db:
                    with db.cursor() as cur:
                        cur.execute(pgsql.SQL('DROP SCHEMA {} CASCADE').format(
                            pgsql.Identifier(schema)
                        ))
                    db.commit()
                GLib.idle_add(self._browser.load, conn)
            except Exception as e:
                GLib.idle_add(self._show_browser_error, 'Drop Schema Failed', str(e))
        threading.Thread(target=run, daemon=True).start()

    # ── View management (#95) ─────────────────────────────────────────────────

    def _on_create_view_requested(self, _browser, conn, schema):
        from column_dialogs import CreateViewDialog

        def on_save(schema, name, sql_def, on_done):
            def run():
                try:
                    import psycopg
                    from psycopg import sql as pgsql
                    from tunnel import open_tunnel
                    with open_tunnel(conn) as (host, port), psycopg.connect(
                        host=host, port=port,
                        dbname=conn['database'], user=conn['username'],
                        password=conn['password'], connect_timeout=10,
                    ) as db:
                        with db.cursor() as cur:
                            ddl = pgsql.SQL('CREATE OR REPLACE VIEW {}.{} AS {}').format(
                                pgsql.Identifier(schema),
                                pgsql.Identifier(name),
                                pgsql.SQL(sql_def),
                            )
                            cur.execute(ddl)
                        db.commit()
                    GLib.idle_add(self._browser.load, conn)
                    GLib.idle_add(on_done)
                except Exception as e:
                    GLib.idle_add(on_done, str(e))
            threading.Thread(target=run, daemon=True).start()

        dlg = CreateViewDialog(schema, on_save)
        dlg.present(self)

