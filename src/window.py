import os
import threading

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Gtk, Adw, Gio, GLib, GObject, Gdk, Pango

import prefs
from connections import ConnectionStore, KeyringUnavailableError
from connection_dialog import ConnectionDialog
from style import MARGIN_XS, MARGIN_SM, MARGIN_MD
from db_browser import DbBrowser
from file_explorer import FileExplorer
from sql_editor import SqlEditor
from table_panel import TablePanel
from role_panel import RolePanel
from function_editor import FunctionEditor
from command_palette import CommandPalette
from activity_panel import ActivityPanel


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
        self._static_css = Gtk.CssProvider()
        self._static_css.load_from_string("""
            .connection-active-bar {
                background-color: @accent_bg_color;
                border-radius: 2px;
            }
            .connection-role-badge {
                font-size: 13px;
            }
        """)
        display = Gdk.Display.get_default()
        Gtk.StyleContext.add_provider_for_display(
            display, self._sidebar_css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        Gtk.StyleContext.add_provider_for_display(
            display, self._main_css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        Gtk.StyleContext.add_provider_for_display(
            display, self._static_css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
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

        add('quick-open',     lambda *_: self._on_quick_open())
        add('close-tab',      lambda *_: self._close_current_tab())
        add('next-tab',       lambda *_: self._tab_view.select_next_page())
        add('prev-tab',       lambda *_: self._tab_view.select_previous_page())
        add('import-pgpass',  lambda *_: self._on_import_pgpass())
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
            <property name="title">Sidebar</property>
            <child>
              <object class="GtkShortcutsShortcut">
                <property name="title">Expand</property>
                <property name="accelerator">Right</property>
              </object>
            </child>
            <child>
              <object class="GtkShortcutsShortcut">
                <property name="title">Collapse</property>
                <property name="accelerator">Left</property>
              </object>
            </child>
            <child>
              <object class="GtkShortcutsShortcut">
                <property name="title">Open / Toggle</property>
                <property name="accelerator">Return</property>
              </object>
            </child>
            <child>
              <object class="GtkShortcutsShortcut">
                <property name="title">Go Up (File Explorer)</property>
                <property name="accelerator">BackSpace</property>
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
            <property name="title">Navigation</property>
            <child>
              <object class="GtkShortcutsShortcut">
                <property name="title">Quick Open</property>
                <property name="accelerator">&lt;ctrl&gt;p</property>
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

    def show_toast(self, title, timeout=2, button_label=None, on_button=None):
        """Show a brief toast notification in the main window overlay."""
        toast = Adw.Toast(title=title)
        toast.set_timeout(timeout)
        if button_label:
            toast.set_button_label(button_label)
        if on_button:
            toast.connect('button-clicked', lambda _t: on_button())
        self._toast_overlay.add_toast(toast)

    def _on_copy_to_clipboard(self, _browser, text):
        Gdk.Display.get_default().get_clipboard().set(text)
        self.show_toast(f'Copied: {text}')

    def _build_ui(self):
        root = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        root.set_position(300)
        root.set_shrink_start_child(False)
        root.set_shrink_end_child(False)

        root.set_start_child(self._build_sidebar())
        root.set_end_child(self._build_main())
        self._toast_overlay = Adw.ToastOverlay()
        self._toast_overlay.set_child(root)
        self.set_content(self._toast_overlay)

    def _build_sidebar(self):
        sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        sidebar.add_css_class('tusk-sidebar')

        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(False)
        header.set_title_widget(Gtk.Label(label='Tusk'))

        add_btn = Adw.SplitButton()
        add_btn.set_icon_name('list-add-symbolic')
        add_btn.set_tooltip_text('New Connection')
        add_btn.connect('clicked', self._on_add_connection)
        add_menu = Gio.Menu()
        add_menu.append('Import from .pgpass…', 'win.import-pgpass')
        add_btn.set_menu_model(add_menu)
        header.pack_end(add_btn)

        sidebar.append(header)

        # Connection dropdown
        dropdown_child = Gtk.Box(spacing=6)
        dropdown_child.set_margin_start(2)
        conn_icon = Gtk.Image.new_from_icon_name('network-server-symbolic')
        self._conn_dropdown_label = Gtk.Label(label='Select connection…')
        self._conn_dropdown_label.set_hexpand(True)
        self._conn_dropdown_label.set_xalign(0)
        self._conn_dropdown_label.set_ellipsize(Pango.EllipsizeMode.END)
        self._conn_dropdown_badge = Gtk.Label(label='🛡')
        self._conn_dropdown_badge.add_css_class('dim-label')
        self._conn_dropdown_badge.add_css_class('connection-role-badge')
        self._conn_dropdown_badge.set_visible(False)
        dropdown_child.append(conn_icon)
        dropdown_child.append(self._conn_dropdown_label)
        dropdown_child.append(self._conn_dropdown_badge)

        self._conn_dropdown = Gtk.MenuButton()
        self._conn_dropdown.set_child(dropdown_child)
        self._conn_dropdown.set_hexpand(True)
        self._conn_dropdown.add_css_class('flat')

        self._conn_list = Gtk.ListBox()
        self._conn_list.add_css_class('navigation-sidebar')
        self._conn_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self._conn_list.connect('row-activated', self._on_connection_activated)

        self._conn_popover_scroll = Gtk.ScrolledWindow()
        self._conn_popover_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._conn_popover_scroll.set_propagate_natural_height(True)
        self._conn_popover_scroll.set_max_content_height(320)
        self._conn_popover_scroll.set_child(self._conn_list)

        self._conn_popover = Gtk.Popover()
        self._conn_popover.set_child(self._conn_popover_scroll)
        self._conn_popover.set_has_arrow(False)
        self._conn_dropdown.set_popover(self._conn_popover)
        self._conn_popover.connect(
            'map',
            lambda _: self._conn_popover_scroll.set_size_request(
                self._conn_dropdown.get_width() - 20, -1
            ),
        )

        conn_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        conn_bar.set_margin_top(MARGIN_XS)
        conn_bar.set_margin_bottom(MARGIN_XS)
        conn_bar.set_margin_start(MARGIN_SM)
        conn_bar.set_margin_end(MARGIN_SM)
        conn_bar.append(self._conn_dropdown)
        sidebar.append(conn_bar)

        sidebar.append(Gtk.Separator())

        # Vertical pane: DB browser (top) + file explorer (bottom)
        sidebar_paned = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL)
        sidebar_paned.set_position(prefs.get('sidebar_pane_pos', 320))
        sidebar_paned.set_vexpand(True)
        sidebar_paned.set_shrink_start_child(False)
        sidebar_paned.set_shrink_end_child(False)
        self._sidebar_paned = sidebar_paned
        self._sidebar_paned_adjusting = False
        sidebar_paned.connect('notify::position', self._on_sidebar_pane_moved)

        self._browser = DbBrowser()
        self._browser.connect('table-selected', self._on_table_selected)
        self._browser.connect('function-selected', self._on_function_selected)
        self._browser.connect('create-table-requested', self._on_create_table_requested)
        self._browser.connect('drop-table-requested', self._on_drop_table_requested)
        self._browser.connect('truncate-table-requested', self._on_truncate_table_requested)
        self._browser.connect('rename-table-requested', self._on_rename_table_requested)
        self._browser.connect('clone-table-requested', self._on_clone_table_requested)
        self._browser.connect('create-schema-requested', self._on_create_schema_requested)
        self._browser.connect('rename-schema-requested', self._on_rename_schema_requested)
        self._browser.connect('drop-schema-requested', self._on_drop_schema_requested)
        self._browser.connect('create-view-requested', self._on_create_view_requested)
        self._browser.connect('database-switched', self._on_database_switched)
        self._browser.connect('drop-database-requested', self._on_drop_database_requested)
        self._browser.connect('role-attrs-loaded', self._on_role_attrs_loaded)
        self._browser.connect('role-selected', self._on_role_selected)
        self._browser.connect('create-role-requested', self._on_create_role_requested)
        self._browser.connect('drop-role-requested', self._on_drop_role_requested)
        self._browser.connect('change-password-requested', self._on_change_password_requested)
        self._browser.connect('edit-connection-requested', self._on_edit_connection_from_browser)
        self._browser.connect('copy-to-clipboard', self._on_copy_to_clipboard)
        self._browser.connect('server-activity-requested', lambda _b, conn: self._on_server_activity(conn))
        sidebar_paned.set_start_child(self._browser)

        self._file_explorer = FileExplorer()
        self._file_explorer.connect('file-activated', self._on_file_activated)
        self._file_explorer.connect('file-deleted', self._on_file_deleted)
        self._file_explorer.connect('file-renamed', self._on_file_renamed)
        self._file_explorer.connect('collapsed-changed', self._on_file_explorer_collapsed)
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

        # Welcome state: shown when no connections exist yet
        welcome = Adw.StatusPage()
        welcome.set_title('No connections yet')
        welcome.set_description('Connect to a PostgreSQL database to get started.')
        welcome.set_icon_name('xyz.shapemachine.tusk-gnome')

        welcome_btn = Gtk.Button(label='Add Connection')
        welcome_btn.add_css_class('suggested-action')
        welcome_btn.add_css_class('pill')
        welcome_btn.set_halign(Gtk.Align.CENTER)
        welcome_btn.connect('clicked', self._on_add_connection)

        welcome_hint = Gtk.Label(
            label="You'll need: hostname, port (default 5432), database name, and credentials."
        )
        welcome_hint.add_css_class('dim-label')
        welcome_hint.set_wrap(True)
        welcome_hint.set_halign(Gtk.Align.CENTER)
        welcome_hint.set_margin_top(8)

        welcome_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        welcome_box.set_halign(Gtk.Align.CENTER)
        welcome_box.append(welcome_btn)
        welcome_box.append(welcome_hint)
        welcome.set_child(welcome_box)

        self._main_stack.add_named(welcome, 'welcome')

        self._main_stack.set_visible_child_name('empty')
        main_box.append(self._main_stack)
        return main_box

    # ── Connections ───────────────────────────────────────────────────────────

    def _load_connections(self):
        connections = list(self._store.list())
        for conn in connections:
            self._add_connection_row(conn)
        if not connections:
            self._main_stack.set_visible_child_name('welcome')

    @staticmethod
    def _conn_subtitle(conn):
        return f"{conn['host']}:{conn['port']}/{conn['database']}"

    def _add_connection_row(self, conn, position=-1):
        row = Adw.ActionRow()
        row.set_title(conn['name'])
        row.set_subtitle(self._conn_subtitle(conn))
        row.set_icon_name('network-server-symbolic')
        row.set_activatable(True)
        row._conn = conn

        if conn.get('read_only'):
            lock = Gtk.Image.new_from_icon_name('changes-prevent-symbolic')
            lock.set_tooltip_text('Read-only connection')
            lock.set_valign(Gtk.Align.CENTER)
            lock.add_css_class('dim-label')
            row.add_suffix(lock)

        role_badge = Gtk.Label(label='🛡')
        role_badge.add_css_class('dim-label')
        role_badge.add_css_class('connection-role-badge')
        role_badge.set_visible(False)
        role_badge.set_valign(Gtk.Align.CENTER)
        row.add_suffix(role_badge)
        row._role_badge = role_badge

        menu = Gio.Menu()
        menu.append('Disconnect', 'row.disconnect')
        menu.append('Edit', 'row.edit')
        menu.append('Duplicate', 'row.duplicate')
        menu.append('Copy as URI', 'row.copy-uri')
        menu.append('Export to .pgpass…', 'row.export-pgpass')
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
        duplicate_action = Gio.SimpleAction.new('duplicate', None)
        duplicate_action.connect('activate', lambda a, p, r=row: self._on_duplicate_connection(r))
        ag.add_action(duplicate_action)
        copy_uri_action = Gio.SimpleAction.new('copy-uri', None)
        copy_uri_action.connect('activate', lambda a, p, r=row: self._on_copy_as_uri(r))
        ag.add_action(copy_uri_action)
        export_pgpass_action = Gio.SimpleAction.new('export-pgpass', None)
        export_pgpass_action.connect('activate', lambda a, p, r=row: self._on_export_pgpass(r))
        ag.add_action(export_pgpass_action)
        delete_action = Gio.SimpleAction.new('delete', None)
        delete_action.connect('activate', lambda a, p, r=row: self._on_delete_connection(r))
        ag.add_action(delete_action)
        row.insert_action_group('row', ag)

        if position == -1:
            self._conn_list.append(row)
        else:
            self._conn_list.insert(row, position)
        return row

    def _on_add_connection(self, _btn):
        self._conn_popover.popdown()
        dlg = ConnectionDialog(parent=self)
        dlg.connect('connection-saved', self._on_connection_added)
        dlg.present(self)

    def _on_copy_as_uri(self, row):
        self._conn_popover.popdown()
        from urllib.parse import quote
        conn = row._conn
        user = quote(conn['username'], safe='')
        host = conn['host']
        port = conn['port']
        database = quote(conn['database'], safe='')
        uri = f'postgresql://{user}@{host}:{port}/{database}'
        Gdk.Display.get_default().get_clipboard().set(uri)
        toast = Adw.Toast(title='URI copied to clipboard')
        toast.set_timeout(2)
        self._toast_overlay.add_toast(toast)

    def _on_export_pgpass(self, row):
        import os
        import stat
        self._conn_popover.popdown()
        conn = row._conn
        try:
            password = self._store.get_password(conn['id'])
        except Exception as e:
            self._show_keyring_error(str(e))
            return
        if not password:
            alert = Adw.AlertDialog(
                heading='No Password Stored',
                body=f'"{conn["name"]}" has no stored password and cannot be exported to .pgpass.',
            )
            alert.add_response('ok', 'OK')
            alert.present(self)
            return

        pgpass_path = os.path.expanduser('~/.pgpass')

        # pgpass is line-based — newlines in any field would corrupt the file
        for field, value in [('host', conn['host']), ('username', conn['username']),
                              ('password', password)]:
            if '\n' in str(value) or '\r' in str(value):
                alert = Adw.AlertDialog(
                    heading='Cannot Export',
                    body=f'The {field} contains a newline character, which cannot be represented in .pgpass.',
                )
                alert.add_response('ok', 'OK')
                alert.present(self)
                return

        def _escape(s):
            return str(s).replace('\\', '\\\\').replace(':', '\\:')

        new_line = ':'.join([
            _escape(conn['host']),
            _escape(conn['port']),
            '*',
            _escape(conn['username']),
            _escape(password),
        ])

        # Read existing entries
        existing_lines = []
        warnings = []
        if os.path.exists(pgpass_path):
            try:
                st = os.stat(pgpass_path)
                if st.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
                    mode_str = oct(stat.S_IMODE(st.st_mode))
                    warnings.append(
                        f'.pgpass permissions are {mode_str} — should be 0600. '
                        'Credentials may be visible to other users on this system.'
                    )
                with open(pgpass_path, encoding='utf-8') as f:
                    existing_lines = f.read().splitlines()
            except OSError as e:
                alert = Adw.AlertDialog(
                    heading='Could Not Read .pgpass',
                    body=str(e),
                )
                alert.add_response('ok', 'OK')
                alert.present(self)
                return

        # Deduplicate
        if new_line in existing_lines:
            msg = 'Entry already exists in .pgpass — nothing written'
            if warnings:
                msg += f'. ⚠ {warnings[0]}'
            toast = Adw.Toast(title=msg)
            toast.set_timeout(4)
            self._toast_overlay.add_toast(toast)
            return

        # Write atomically: write to a temp file then os.replace() so the
        # original is never left truncated if the write is interrupted.
        tmp_path = None
        try:
            import tempfile
            lines = existing_lines + [new_line, '']
            content = '\n'.join(lines)
            pgpass_dir = os.path.dirname(pgpass_path) or os.path.expanduser('~')
            with tempfile.NamedTemporaryFile(
                mode='w', encoding='utf-8',
                dir=pgpass_dir, delete=False,
            ) as tmp:
                tmp.write(content)
                tmp_path = tmp.name
            os.chmod(tmp_path, 0o600)
            os.replace(tmp_path, pgpass_path)
            tmp_path = None  # replaced successfully — no cleanup needed
        except OSError as e:
            alert = Adw.AlertDialog(heading='Export Failed', body=str(e))
            alert.add_response('ok', 'OK')
            alert.present(self)
            return
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

        msg = '1 entry written to ~/.pgpass'
        if warnings:
            msg += f' — ⚠ {warnings[0]}'
        toast = Adw.Toast(title=msg)
        toast.set_timeout(4)
        self._toast_overlay.add_toast(toast)

    def _on_import_pgpass(self):
        import os
        from pgpass_dialog import PgpassImportDialog, parse_pgpass

        pgpass_path = os.path.expanduser('~/.pgpass')
        if not os.path.exists(pgpass_path):
            dialog = Adw.MessageDialog(
                transient_for=self,
                heading='No .pgpass File',
                body='~/.pgpass was not found on this system.',
            )
            dialog.add_response('ok', 'OK')
            dialog.present()
            return

        try:
            entries, warnings = parse_pgpass(pgpass_path)
        except Exception as e:
            dialog = Adw.MessageDialog(
                transient_for=self,
                heading='Could Not Read .pgpass',
                body=str(e),
            )
            dialog.add_response('ok', 'OK')
            dialog.present()
            return

        if not entries:
            dialog = Adw.MessageDialog(
                transient_for=self,
                heading='No Importable Entries',
                body=(
                    '~/.pgpass contains no importable entries. '
                    'Entries with wildcards (*) in any field are skipped.'
                ),
            )
            dialog.add_response('ok', 'OK')
            dialog.present()
            return

        existing_names = {c['name'] for c in self._store.list()}
        dlg = PgpassImportDialog(parent=self, entries=entries, warnings=warnings,
                                 existing_names=existing_names)
        dlg.connect('entries-selected', self._on_pgpass_entries_selected)
        dlg.present(self)

    def _on_pgpass_entries_selected(self, _dlg, entries):
        if self._main_stack.get_visible_child_name() == 'welcome':
            self._main_stack.set_visible_child_name('empty')
        for entry in entries:
            conn = {
                'name': (
                    f'{entry["username"]}@{entry["hostname"]}/{entry["database"]}'
                ),
                'host': entry['hostname'],
                'port': entry['port'],
                'database': entry['database'],
                'username': entry['username'],
                'password': entry['password'],
            }
            try:
                self._store.add(conn)
            except KeyringUnavailableError as e:
                self._show_keyring_error(str(e))
                return
            self._add_connection_row(conn)

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
        if self._main_stack.get_visible_child_name() == 'welcome':
            self._main_stack.set_visible_child_name('empty')
        self._add_connection_row(conn)

    def _on_edit_connection(self, row):
        self._conn_popover.popdown()
        dlg = ConnectionDialog(parent=self, connection=row._conn)
        dlg.connect('connection-saved', self._on_connection_updated, row)
        dlg.present(self)

    def _on_duplicate_connection(self, row):
        self._conn_popover.popdown()
        try:
            conn = self._conn_with_password(row._conn)
        except KeyringUnavailableError as e:
            self._show_keyring_error(str(e))
            return
        dlg = ConnectionDialog(parent=self, connection=conn, duplicate=True)
        dlg.connect('connection-saved', self._on_connection_duplicated, row)
        dlg.present(self)

    def _on_connection_duplicated(self, _dlg, conn, source_row):
        try:
            self._store.add_after(source_row._conn['id'], conn)
        except KeyringUnavailableError as e:
            self._show_keyring_error(str(e))
            return
        # Find position of source_row in the listbox and insert after it
        position = 0
        child = self._conn_list.get_first_child()
        while child:
            position += 1
            if child is source_row:
                break
            child = child.get_next_sibling()
        self._add_connection_row(conn, position=position)

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
        self._conn_popover.popdown()
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
        if self._conn_list.get_first_child() is None:
            self._main_stack.set_visible_child_name('welcome')

    def _conn_with_password(self, conn):
        return {
            **conn,
            'password': self._store.get_password(conn['id']),
            'ssh_passphrase': self._store.get_ssh_passphrase(conn['id']),
        }

    def _on_connection_activated(self, _listbox, row):
        self._conn_popover.popdown()
        try:
            conn = self._conn_with_password(row._conn)
        except KeyringUnavailableError as e:
            self._show_keyring_error(str(e))
            return
        self._set_active_conn(conn)
        self._browser.load(conn)

    def _on_database_switched(self, _browser, conn, new_dbname):
        # Close all table and function tabs from the current database before switching
        if self._active_conn_id:
            pages = self._tab_view.get_pages()
            to_close = [
                pages.get_item(i)
                for i in range(pages.get_n_items())
                if getattr(pages.get_item(i), '_tab_id', '').startswith(
                    (f'table:{self._active_conn_id}:', f'fn:{self._active_conn_id}:')
                )
            ]
            for page in to_close:
                self._tab_view.close_page(page)

        new_conn = {**conn, 'database': new_dbname}
        self._set_active_conn(new_conn)
        self._browser.load(new_conn)

    def _on_role_attrs_loaded(self, _browser, conn, attrs):
        """Update the role badge on the matching connection row and dropdown."""
        is_superuser = attrs.get('superuser', False)
        tooltip_parts = [f'{k}: {"yes" if v else "no"}' for k, v in attrs.items()]
        tooltip = '\n'.join(tooltip_parts) if tooltip_parts else ''

        # Update popover row badge
        row = self._conn_list.get_first_child()
        while row:
            if hasattr(row, '_conn') and row._conn['id'] == conn['id']:
                row._role_badge.set_visible(is_superuser)
                if is_superuser:
                    row._role_badge.set_tooltip_text(tooltip)
                break
            row = row.get_next_sibling()

        # Update dropdown badge if this is the active connection
        if self._active_conn_id == conn['id']:
            self._conn_dropdown_badge.set_visible(is_superuser)
            if is_superuser:
                self._conn_dropdown_badge.set_tooltip_text(tooltip)

    def _on_drop_database_requested(self, _browser, conn, dbname):
        entry = Gtk.Entry(placeholder_text=dbname, hexpand=True)
        entry_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        entry_label = Gtk.Label(
            label=f'Type <b>{GLib.markup_escape_text(dbname)}</b> to confirm',
            use_markup=True,
            xalign=0,
        )
        entry_row.append(entry_label)
        entry_row.append(entry)

        dialog = Adw.AlertDialog(
            heading=f'Drop database "{dbname}"?',
            body='All data in this database will be permanently deleted. This cannot be undone.',
        )
        dialog.set_extra_child(entry_row)
        dialog.add_response('cancel', 'Cancel')
        dialog.add_response('drop', 'Drop Database')
        dialog.set_response_appearance('drop', Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_response_enabled('drop', False)
        dialog.set_default_response('cancel')
        dialog.set_close_response('cancel')

        entry.connect('changed', lambda e: dialog.set_response_enabled(
            'drop', e.get_text() == dbname
        ))

        dialog.connect('response', self._on_drop_database_response, conn, dbname)
        dialog.present(self)

    def _on_drop_database_response(self, _dialog, response, conn, dbname):
        if response != 'drop':
            return

        def run():
            try:
                import psycopg
                from psycopg import sql as pgsql
                from tunnel import open_db

                # Must not be connected to the database we're dropping.
                # Connect to 'postgres' as a safe fallback.
                fallback_conn = {**conn, 'database': 'postgres'}
                with open_db(fallback_conn, autocommit=True) as db:
                    with db.cursor() as cur:
                        cur.execute(
                            pgsql.SQL('DROP DATABASE {}').format(pgsql.Identifier(dbname))
                        )

                GLib.idle_add(self._after_drop_database, conn, dbname)
            except Exception as e:
                GLib.idle_add(self._show_browser_error, 'Drop Database Failed', str(e))

        threading.Thread(target=run, daemon=True).start()

    def _after_drop_database(self, conn, dropped_dbname):
        # Close all table tabs from the dropped database
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

        # Switch active connection to postgres fallback and reload browser
        new_conn = {**conn, 'database': 'postgres'}
        self._set_active_conn(new_conn)
        self._browser.load(new_conn)

    def _on_disconnect(self, row):
        if self._active_conn_id == row._conn['id']:
            self._set_active_conn(None)
            self._browser.clear()

    def _set_active_conn(self, conn):
        # Close all table and function tabs belonging to the previous connection
        if self._active_conn_id:
            pages = self._tab_view.get_pages()
            to_close = [
                pages.get_item(i)
                for i in range(pages.get_n_items())
                if getattr(pages.get_item(i), '_tab_id', '').startswith(
                    (f'table:{self._active_conn_id}:', f'fn:{self._active_conn_id}:')
                )
            ]
            for page in to_close:
                self._tab_view.close_page(page)

        self._active_conn_id = conn['id'] if conn else None
        self._active_conn = conn

        # Update per-row disconnect action enabled state
        row = self._conn_list.get_first_child()
        while row:
            if hasattr(row, '_conn'):
                row._disconnect_action.set_enabled(
                    bool(conn and row._conn['id'] == conn['id'])
                )
            row = row.get_next_sibling()

        # Update file explorer New Query button visibility
        self._file_explorer.set_connected(bool(conn))

        # Update dropdown label
        if conn:
            label = conn['name']
            if conn.get('read_only'):
                label += '  🔒'
            self._conn_dropdown_label.set_label(label)
        else:
            self._conn_dropdown_label.set_label('Select connection…')
            self._conn_dropdown_badge.set_visible(False)

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
        panel.load(conn, schema, table, item_type, read_only=conn.get('read_only', False))

        icon_name = 'x-office-spreadsheet-symbolic' if item_type == 'table' else 'view-grid-symbolic'
        page = self._tab_view.append(panel)
        page.set_title(f'{schema}.{table}')
        page.set_icon(Gio.ThemedIcon.new(icon_name))
        page._tab_id = tab_id

        self._show_tabs()
        self._tab_view.set_selected_page(page)

    def _on_role_selected(self, _browser, conn, role_name):
        tab_id = f'role:{conn["id"]}:{role_name}'
        existing = self._find_tab(tab_id)
        if existing:
            self._tab_view.set_selected_page(existing)
            return

        panel = RolePanel()
        panel.load(conn, role_name)

        page = self._tab_view.append(panel)
        page.set_title(role_name)
        page.set_icon(Gio.ThemedIcon.new('person-symbolic'))
        page._tab_id = tab_id

        self._show_tabs()
        self._tab_view.set_selected_page(page)

    def _on_function_selected(self, _browser, conn, schema, fn_name, fn_args):
        signature = f'{fn_name}({fn_args})'
        tab_id = f'fn:{conn["id"]}:{schema}.{signature}'
        existing = self._find_tab(tab_id)
        if existing:
            self._tab_view.set_selected_page(existing)
            return

        editor = FunctionEditor(conn, schema, fn_name, fn_args)

        page = self._tab_view.append(editor)
        page.set_title(f'fn: {signature}')
        page.set_icon(Gio.ThemedIcon.new('system-run-symbolic'))
        page._tab_id = tab_id

        editor.connect('modified-changed', lambda _ed, dirty: page.set_title(
            f'● fn: {signature}' if dirty else f'fn: {signature}'
        ))

        self._show_tabs()
        self._tab_view.set_selected_page(page)

    def _on_quick_open(self):
        items = self._browser.get_palette_items()
        items += self._get_sql_file_items()
        if not items:
            self.show_toast('No tables or SQL files found')
            return
        palette = CommandPalette(items)
        palette.connect('item-activated', self._on_palette_item_activated)
        palette.present(self)

    def _get_sql_file_items(self):
        folder = self._file_explorer.current_dir
        try:
            result = []
            with os.scandir(folder) as scan:
                for entry in scan:
                    try:
                        if entry.is_file() and entry.name.lower().endswith('.sql'):
                            result.append((None, '', entry.path, 'file', entry.name))
                    except OSError:
                        pass
            result.sort(key=lambda t: t[4].lower())
            return result
        except OSError:
            return []

    def _on_palette_item_activated(self, _palette, conn, schema, name, item_type):
        if item_type == 'function':
            # name is 'proname(args)' — split to recover fn_name and fn_args
            paren = name.index('(')
            fn_name = name[:paren]
            fn_args = name[paren + 1:-1]
            self._on_function_selected(None, conn, schema, fn_name, fn_args)
        elif item_type == 'file':
            self._on_file_activated(None, name)
        else:
            self._on_table_selected(None, conn, schema, name, item_type)

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
        editor.connect('query-finished', self._on_query_finished)

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

    def _on_sidebar_pane_moved(self, paned, _):
        if not self._sidebar_paned_adjusting:
            prefs.put('sidebar_pane_pos', paned.get_position())

    def _on_file_explorer_collapsed(self, _explorer, is_collapsed):
        self._sidebar_paned_adjusting = True
        if is_collapsed:
            self._sidebar_paned_pos_saved = self._sidebar_paned.get_position()
            self._sidebar_paned.set_position(99999)
        else:
            self._sidebar_paned.set_position(
                getattr(self, '_sidebar_paned_pos_saved',
                        prefs.get('sidebar_pane_pos', 320))
            )
        self._sidebar_paned_adjusting = False

    def _on_query_finished(self, editor, elapsed_ms, is_error):
        threshold_s = prefs.get('notify_threshold_s', 10)
        if threshold_s <= 0:
            return
        elapsed_s = elapsed_ms // 1000
        if elapsed_s < threshold_s:
            return
        if self.is_active():
            return
        elapsed_label = f'{elapsed_s}s'
        title = f'Query failed ({elapsed_label})' if is_error else f'Query finished ({elapsed_label})'
        if is_error:
            body = (editor._last_error_msg or '').split('\n')[0][:80]
        else:
            body = (editor._last_sql or '').split('\n')[0][:80]
        notification = Gio.Notification.new(title)
        notification.set_body(body)
        notification.set_default_action_and_target_value(
            'app.focus-editor',
            GLib.Variant('s', editor.file_path or ''),
        )
        self.get_application().send_notification('query-done', notification)

    def focus_editor_tab(self, file_path):
        if not file_path:
            return
        page = self._find_tab(f'file:{file_path}')
        if page:
            self._tab_view.set_selected_page(page)

    def _on_server_activity(self, conn=None):
        conn = conn or self._active_conn
        if not conn:
            self.show_toast('Connect to a database first')
            return
        tab_id = f'activity:{conn["id"]}'
        existing = self._find_tab(tab_id)
        if existing:
            self._tab_view.set_selected_page(existing)
            return
        panel = ActivityPanel(conn)
        page = self._tab_view.append(panel)
        page.set_title('Server Activity')
        page.set_icon(Gio.ThemedIcon.new('utilities-system-monitor-symbolic'))
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
                    from tunnel import open_db

                    with open_db(conn) as db:
                        with db.cursor() as cur:
                            cur.execute(ddl_sql)
                        db.commit()
                    GLib.idle_add(self._browser.load, conn)
                    GLib.idle_add(self.show_toast, 'Table created')
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

        qi = lambda n: '"' + n.replace('"', '""') + '"'
        obj_type = 'VIEW' if is_view else 'TABLE'

        def _drop_table_ddl(cascade=False):
            ddl = f'DROP {obj_type} {qi(schema)}.{qi(table)}'
            if cascade:
                ddl += ' CASCADE'
            return ddl + ';'

        sql_label = Gtk.Label(label=_drop_table_ddl())
        sql_label.add_css_class('monospace')
        sql_label.set_xalign(0)
        sql_label.set_selectable(True)
        sql_label.set_wrap(True)
        sql_label.set_margin_top(4)

        cascade_check = Gtk.CheckButton(label='Also drop dependent objects — views, foreign keys and more (CASCADE)')
        cascade_check.set_margin_top(8)
        cascade_check.set_margin_start(4)
        cascade_check.connect('toggled', lambda cb: sql_label.set_label(_drop_table_ddl(cb.get_active())))

        if is_view:
            extra = sql_label
        else:
            extra = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            extra.append(sql_label)
            extra.append(cascade_check)

        dialog = Adw.AlertDialog(heading=heading, body=body)
        dialog.set_extra_child(extra)
        dialog.add_response('cancel', 'Cancel')
        drop_label = 'Drop View' if is_view else 'Drop Table'
        dialog.add_response('drop', drop_label)
        dialog.set_response_appearance('drop', Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response('cancel')
        dialog.set_close_response('cancel')

        def execute_drop(cascade):
            ddl = _drop_table_ddl(cascade)

            def run():
                try:
                    import psycopg
                    from tunnel import open_db

                    with open_db(conn) as db:
                        with db.cursor() as cur:
                            cur.execute(ddl)
                        db.commit()
                    tab_id = f'table:{conn["id"]}:{schema}.{table}'
                    GLib.idle_add(self._close_tab_by_id, tab_id)
                    GLib.idle_add(self._browser.load, conn)
                    GLib.idle_add(self.show_toast, f'{"View" if is_view else "Table"} dropped')
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
        qi = lambda n: '"' + n.replace('"', '""') + '"'

        def _truncate_ddl(restart=False):
            ddl = f'TRUNCATE TABLE {qi(schema)}.{qi(table)}'
            if restart:
                ddl += ' RESTART IDENTITY'
            return ddl + ';'

        sql_label = Gtk.Label(label=_truncate_ddl())
        sql_label.add_css_class('monospace')
        sql_label.set_xalign(0)
        sql_label.set_selectable(True)
        sql_label.set_wrap(True)
        sql_label.set_margin_top(4)

        restart_check = Gtk.CheckButton(label='Restart identity sequences (RESTART IDENTITY)')
        restart_check.set_margin_top(8)
        restart_check.set_margin_start(4)
        restart_check.connect('toggled', lambda cb: sql_label.set_label(_truncate_ddl(cb.get_active())))

        extra = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        extra.append(sql_label)
        extra.append(restart_check)

        dialog = Adw.AlertDialog(
            heading=f'Truncate "{schema}.{table}"?',
            body='Truncate empties the table but keeps its structure and indexes. This cannot be undone.',
        )
        dialog.set_extra_child(extra)
        dialog.add_response('cancel', 'Cancel')
        dialog.add_response('truncate', 'Truncate')
        dialog.set_response_appearance('truncate', Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response('cancel')
        dialog.set_close_response('cancel')

        def on_response(_d, response):
            if response != 'truncate':
                return
            ddl = _truncate_ddl(restart_check.get_active())

            def run():
                try:
                    import psycopg
                    from tunnel import open_db

                    with open_db(conn) as db:
                        with db.cursor() as cur:
                            cur.execute(ddl)
                        db.commit()
                    # Refresh data tab if open
                    tab_id = f'table:{conn["id"]}:{schema}.{table}'
                    GLib.idle_add(self._refresh_tab_by_id, tab_id)
                    GLib.idle_add(self.show_toast, f'"{table}" truncated')
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
                    from tunnel import open_db

                    with open_db(conn) as db:
                        with db.cursor() as cur:
                            cur.execute(ddl)
                        db.commit()
                    old_tab_id = f'table:{conn["id"]}:{schema}.{table}'
                    new_tab_id = f'table:{conn["id"]}:{schema}.{new_name}'
                    GLib.idle_add(self._rename_tab, old_tab_id, new_tab_id,
                                  f'{schema}.{new_name}', conn, schema, new_name)
                    GLib.idle_add(self._browser.load, conn)
                    GLib.idle_add(self.show_toast, 'Table renamed')
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
                from tunnel import open_db

                with open_db(conn) as db:
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
                    from tunnel import open_db

                    with open_db(conn) as db:
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
                body=f'{err_str}\n\nChoose "Also Delete Dependent Objects" to also drop all dependent views and constraints.',
            )
            dialog.add_response('cancel', 'Cancel')
            dialog.add_response('cascade', 'Also Delete Dependent Objects')
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
                from tunnel import open_db

                with open_db(conn) as db:
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
                    from tunnel import open_db

                    with open_db(conn) as db:
                        with db.cursor() as cur:
                            cur.execute(pgsql.SQL('CREATE SCHEMA {}').format(
                                pgsql.Identifier(schema_name)
                            ))
                        db.commit()
                    GLib.idle_add(self._browser.load, conn)
                    GLib.idle_add(self.show_toast, 'Schema created')
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
                    from tunnel import open_db

                    with open_db(conn) as db:
                        with db.cursor() as cur:
                            cur.execute(pgsql.SQL('ALTER SCHEMA {} RENAME TO {}').format(
                                pgsql.Identifier(schema), pgsql.Identifier(new_name)
                            ))
                        db.commit()
                    GLib.idle_add(self._browser.set_rename_hint, schema, new_name)
                    GLib.idle_add(self._browser.load, conn)
                    GLib.idle_add(self.show_toast, 'Schema renamed')
                except Exception as e:
                    GLib.idle_add(self._show_browser_error, 'Rename Schema Failed', str(e))
            threading.Thread(target=run, daemon=True).start()

        dlg = RenameDialog(schema, on_rename, title='Rename Schema')
        dlg.present(self)

    def _on_drop_schema_requested(self, _browser, conn, schema):
        qi = lambda n: '"' + n.replace('"', '""') + '"'

        def _drop_schema_ddl(cascade=False):
            ddl = f'DROP SCHEMA {qi(schema)}'
            if cascade:
                ddl += ' CASCADE'
            return ddl + ';'

        sql_label = Gtk.Label(label=_drop_schema_ddl())
        sql_label.add_css_class('monospace')
        sql_label.set_xalign(0)
        sql_label.set_selectable(True)
        sql_label.set_wrap(True)
        sql_label.set_margin_top(4)

        cascade_check = Gtk.CheckButton(label='Also drop all tables, views and other objects in this schema (CASCADE)')
        cascade_check.set_margin_top(8)
        cascade_check.set_margin_start(4)
        cascade_check.connect('toggled', lambda cb: sql_label.set_label(_drop_schema_ddl(cb.get_active())))

        extra = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        extra.append(sql_label)
        extra.append(cascade_check)

        dialog = Adw.AlertDialog(
            heading=f'Drop schema "{schema}"?',
            body='This will permanently remove the schema. Non-empty schemas require CASCADE.',
        )
        dialog.set_extra_child(extra)
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
                    from tunnel import open_db

                    with open_db(conn) as db:
                        with db.cursor() as cur:
                            ddl = pgsql.SQL('DROP SCHEMA {}').format(pgsql.Identifier(schema))
                            if cascade:
                                ddl = pgsql.SQL('DROP SCHEMA {} CASCADE').format(
                                    pgsql.Identifier(schema)
                                )
                            cur.execute(ddl)
                        db.commit()
                    GLib.idle_add(self._browser.load, conn)
                    GLib.idle_add(self.show_toast, 'Schema dropped')
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
            body=f'{err_str}\n\nChoose "Also Delete All Objects" to also drop all tables, views and other objects in this schema.',
        )
        dialog.add_response('cancel', 'Cancel')
        dialog.add_response('cascade', 'Also Delete All Objects')
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
                from tunnel import open_db

                with open_db(conn) as db:
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
                from table_panel import _validate_sql_fragment
                err = _validate_sql_fragment(sql_def)
                if err:
                    GLib.idle_add(on_done, err)
                    return
                try:
                    import psycopg
                    from psycopg import sql as pgsql
                    from tunnel import open_db

                    with open_db(conn) as db:
                        with db.cursor() as cur:
                            ddl = pgsql.SQL('CREATE OR REPLACE VIEW {}.{} AS {}').format(
                                pgsql.Identifier(schema),
                                pgsql.Identifier(name),
                                pgsql.SQL(sql_def),
                            )
                            cur.execute(ddl)
                        db.commit()
                    GLib.idle_add(self._browser.load, conn)
                    GLib.idle_add(self.show_toast, 'View created')
                    GLib.idle_add(on_done)
                except Exception as e:
                    GLib.idle_add(on_done, str(e))
            threading.Thread(target=run, daemon=True).start()

        dlg = CreateViewDialog(schema, on_save)
        dlg.present(self)

    # ── Role management (#156, #160) ──────────────────────────────────────────

    def _on_create_role_requested(self, _browser, conn):
        from role_panel import _NewRoleDialog
        dlg = _NewRoleDialog(conn)
        dlg.connect('role-created', lambda _d, _name: self._browser.load(conn))
        dlg.present(self)

    def _on_drop_role_requested(self, _browser, conn, role_name):
        dialog = Adw.AlertDialog(
            heading=f'Drop role "{role_name}"?',
            body='This permanently removes the role. The role must not own any objects or have any granted privileges.',
        )
        dialog.add_response('cancel', 'Cancel')
        dialog.add_response('drop', 'Drop Role')
        dialog.set_response_appearance('drop', Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response('cancel')
        dialog.set_close_response('cancel')

        def on_response(_d, response):
            if response != 'drop':
                return
            def run():
                try:
                    from psycopg import sql as pgsql
                    from tunnel import open_db
                    with open_db(conn) as db:
                        with db.cursor() as cur:
                            cur.execute(pgsql.SQL('DROP ROLE {}').format(
                                pgsql.Identifier(role_name)
                            ))
                        db.commit()
                    tab_id = f'role:{conn["id"]}:{role_name}'
                    GLib.idle_add(self._close_tab_by_id, tab_id)
                    GLib.idle_add(self._browser.load, conn)
                except Exception as e:
                    GLib.idle_add(self._show_browser_error, 'Drop Role Failed', str(e))
            threading.Thread(target=run, daemon=True).start()

        dialog.connect('response', on_response)
        dialog.present(self)

    def _on_change_password_requested(self, _browser, conn, role_name):
        from role_panel import _ChangePasswordDialog
        dlg = _ChangePasswordDialog(conn, role_name)
        def _on_changed(*_):
            toast = Adw.Toast(title=f'Password changed for "{role_name}"')
            toast.set_timeout(2)
            self._toast_overlay.add_toast(toast)
        dlg.connect('password-changed', _on_changed)
        dlg.present(self)

    def _on_edit_connection_from_browser(self, _browser, conn):
        row = self._conn_list.get_first_child()
        while row:
            if hasattr(row, '_conn') and row._conn.get('id') == conn.get('id'):
                self._on_edit_connection(row)
                return
            row = row.get_next_sibling()

