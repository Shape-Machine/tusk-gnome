import datetime
import os
import re
import socket
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


_COLOR_RE = re.compile(r'^#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?$')


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
        self._conn_search = ''
        self._conn_sort = prefs.get('conn_sort', 'manual')
        self._active_tag_filters = set()
        self._warned_conn_ids = set()  # conn_ids warned this session (warn_on_connect)
        self._conn_health = {}         # conn_id → {status, msg, ts}
        self._conn_mgr_rows = {}       # conn_id → manager list row
        self._conn_popover_rows = {}   # conn_id → popover list row
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
            .conn-active-icon {
                color: @accent_color;
            }
            .conn-active-pill {
                background-color: @accent_bg_color;
                color: @accent_fg_color;
                border-radius: 999px;
                padding: 1px 8px;
                font-size: 11px;
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
        add('new-sql-file',   lambda *_: self._file_explorer._prompt_create('file'))
        add('new-folder',     lambda *_: self._file_explorer._prompt_create('folder'))
        add('close-tab',      lambda *_: self._close_current_tab())
        add('next-tab',       lambda *_: self._tab_view.select_next_page())
        add('prev-tab',       lambda *_: self._tab_view.select_previous_page())
        add('import-pgpass',              lambda *_: self._on_import_pgpass())
        add('show-connection-manager',    lambda *_: self._show_connection_manager())
        add('check-all-health',           lambda *_: self._on_check_all_health())
        add('export-pgpass-bulk',         lambda *_: self._on_export_pgpass_bulk())
        add('export-connections-json',    lambda *_: self._on_export_connections_json())
        add('import-connections-json',    lambda *_: self._on_import_connections_json())
        add('cleanup-stale',              lambda *_: self._on_cleanup_stale())
        for _key in ('name', 'last-connected', 'manual'):
            _a = Gio.SimpleAction.new(f'conn-sort-{_key}', None)
            _a.connect('activate', lambda _act, _par, k=_key: self._on_sort_changed(k))
            self.add_action(_a)
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
                <property name="title">Connection Manager</property>
                <property name="accelerator">&lt;ctrl&gt;Home</property>
              </object>
            </child>
            <child>
              <object class="GtkShortcutsShortcut">
                <property name="title">Quick Open</property>
                <property name="accelerator">&lt;ctrl&gt;p</property>
              </object>
            </child>
            <child>
              <object class="GtkShortcutsShortcut">
                <property name="title">New SQL File</property>
                <property name="accelerator">&lt;ctrl&gt;n</property>
              </object>
            </child>
            <child>
              <object class="GtkShortcutsShortcut">
                <property name="title">New Folder</property>
                <property name="accelerator">&lt;ctrl&gt;&lt;shift&gt;n</property>
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
        self._sidebar_paned_pos_saved = prefs.get('sidebar_pane_pos', 320)
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
        if prefs.get('file_explorer_collapsed', False):
            self._sidebar_paned_adjusting = True
            self._sidebar_paned.set_position(99999)
            self._sidebar_paned_adjusting = False

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

        self._main_stack = Gtk.Stack()
        self._main_stack.set_vexpand(True)

        # ── Connection manager (replaces old 'welcome' and 'empty' pages) ───────
        mgr_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # Toolbar: sort button (left) + add button (right)
        mgr_toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        mgr_toolbar.set_margin_top(MARGIN_SM)
        mgr_toolbar.set_margin_bottom(MARGIN_XS)
        mgr_toolbar.set_margin_start(MARGIN_MD)
        mgr_toolbar.set_margin_end(MARGIN_MD)

        sort_menu = Gio.Menu()
        sort_menu.append('Name (A–Z)', 'win.conn-sort-name')
        sort_menu.append('Last Connected', 'win.conn-sort-last-connected')
        sort_menu.append('Manual', 'win.conn-sort-manual')
        self._sort_btn = Gtk.MenuButton()
        self._sort_btn.set_icon_name('view-sort-ascending-symbolic')
        self._sort_btn.set_tooltip_text('Sort connections')
        self._sort_btn.set_menu_model(sort_menu)
        self._sort_btn.add_css_class('flat')
        mgr_toolbar.append(self._sort_btn)

        tags_btn = Gtk.Button(label='Manage Tags')
        tags_btn.add_css_class('flat')
        tags_btn.connect('clicked', self._on_manage_tags)
        mgr_toolbar.append(tags_btn)

        self._mgr_search = Gtk.SearchEntry()
        self._mgr_search.set_placeholder_text('Search…')
        self._mgr_search.set_hexpand(True)
        self._mgr_search.connect('search-changed', self._on_mgr_search_changed)
        mgr_toolbar.append(self._mgr_search)

        overflow_menu = Gio.Menu()
        overflow_menu.append('Check all connections', 'win.check-all-health')
        overflow_menu.append('Export all to .pgpass…', 'win.export-pgpass-bulk')
        overflow_menu.append('Export connections…', 'win.export-connections-json')
        overflow_menu.append('Import connections…', 'win.import-connections-json')
        overflow_menu.append('Clean up stale…', 'win.cleanup-stale')
        overflow_btn = Gtk.MenuButton()
        overflow_btn.set_icon_name('view-more-symbolic')
        overflow_btn.set_tooltip_text('More actions')
        overflow_btn.set_menu_model(overflow_menu)
        overflow_btn.add_css_class('flat')
        mgr_toolbar.append(overflow_btn)

        mgr_add_btn = Gtk.Button(label='Add Connection')
        mgr_add_btn.add_css_class('suggested-action')
        mgr_add_btn.add_css_class('pill')
        mgr_add_btn.connect('clicked', self._on_add_connection)
        mgr_toolbar.append(mgr_add_btn)

        mgr_box.append(mgr_toolbar)

        # Tag filter column (vertical, hidden until tags exist)
        self._mgr_tag_strip = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self._mgr_tag_strip.set_margin_top(MARGIN_XS)
        self._mgr_tag_strip.set_margin_start(MARGIN_XS)
        self._mgr_tag_strip.set_margin_end(MARGIN_XS)

        tag_scroll = Gtk.ScrolledWindow()
        tag_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        tag_scroll.set_size_request(140, -1)
        tag_scroll.set_child(self._mgr_tag_strip)
        self._mgr_tag_scroll = tag_scroll
        tag_scroll.set_visible(False)

        # Connection list
        self._mgr_list = Gtk.ListBox()
        self._mgr_list.add_css_class('navigation-sidebar')
        self._mgr_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self._mgr_list.connect('row-activated', self._on_connection_activated)
        self._mgr_list.set_filter_func(self._mgr_filter_row)
        self._mgr_list.set_sort_func(self._mgr_sort_rows)

        mgr_scroll = Gtk.ScrolledWindow()
        mgr_scroll.set_vexpand(True)
        mgr_scroll.set_hexpand(True)
        mgr_scroll.set_child(self._mgr_list)

        content_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        content_box.set_vexpand(True)
        content_box.append(tag_scroll)
        content_box.append(mgr_scroll)
        mgr_box.append(content_box)

        self._main_stack.add_named(mgr_box, 'manager')

        # ── Tab view ─────────────────────────────────────────────────────────────
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

        self._main_stack.set_visible_child_name('manager')
        main_box.append(self._main_stack)
        return main_box

    # ── Connections ───────────────────────────────────────────────────────────

    def _load_connections(self):
        tags_registry = self._store.get_tags_registry()
        for conn in self._store.list():
            self._add_connection_row(conn, tags_registry=tags_registry)
        self._refresh_tag_strip()

    @staticmethod
    def _conn_subtitle(conn):
        return f"{conn['host']}:{conn['port']}/{conn['database']}"

    @staticmethod
    def _format_last_connected(ts):
        if not ts:
            return 'Never connected'
        try:
            dt = datetime.datetime.fromisoformat(ts.rstrip('Z')).replace(tzinfo=datetime.timezone.utc)
            delta = datetime.datetime.now(datetime.timezone.utc) - dt
            secs = int(delta.total_seconds())
            if secs < 60:
                return 'Just now'
            if secs < 3600:
                m = secs // 60
                return f'{m} minute{"s" if m != 1 else ""} ago'
            if secs < 86400:
                h = secs // 3600
                return f'{h} hour{"s" if h != 1 else ""} ago'
            d = secs // 86400
            return f'{d} day{"s" if d != 1 else ""} ago'
        except (ValueError, TypeError):
            return 'Never connected'

    def _add_connection_row(self, conn, position=-1, tags_registry=None):
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
        self._conn_popover_rows[conn['id']] = row
        self._add_mgr_row(conn, position, tags_registry=tags_registry)
        return row

    def _add_mgr_row(self, conn, position=-1, tags_registry=None):
        row = Adw.ActionRow()
        row.set_title(conn['name'])
        row.set_subtitle(self._conn_subtitle(conn))
        row.set_activatable(True)
        row._conn = conn

        # "Connected" pill — hidden until active
        pill = Gtk.Label(label='Connected')
        pill.add_css_class('conn-active-pill')
        pill.set_valign(Gtk.Align.CENTER)
        pill.set_visible(False)
        row.add_suffix(pill)
        row._active_pill = pill

        # Last connected label
        ts_text = self._format_last_connected(conn.get('last_connected'))
        ts_lbl = Gtk.Label(label=ts_text)
        ts_lbl.add_css_class('dim-label')
        ts_lbl.set_valign(Gtk.Align.CENTER)
        if conn.get('last_connected'):
            try:
                dt = datetime.datetime.fromisoformat(conn['last_connected'].rstrip('Z'))
                ts_lbl.set_tooltip_text(dt.strftime('%Y-%m-%d %H:%M UTC'))
            except (ValueError, TypeError):
                pass
        row.add_suffix(ts_lbl)
        row._ts_label = ts_lbl

        # Tag chips (colored text)
        if tags_registry is None:
            tags_registry = self._store.get_tags_registry()
        for tag_name in conn.get('tags', []):
            raw_color = tags_registry.get(tag_name, {}).get('color', '#888888')
            color = raw_color if _COLOR_RE.match(raw_color) else '#888888'
            chip = Gtk.Label()
            chip.set_markup(
                f'<span foreground="{color}" size="small">'
                f'{GLib.markup_escape_text(tag_name)}</span>'
            )
            chip.set_valign(Gtk.Align.CENTER)
            row.add_suffix(chip)

        if conn.get('read_only'):
            lock = Gtk.Image.new_from_icon_name('changes-prevent-symbolic')
            lock.set_tooltip_text('Read-only connection')
            lock.set_valign(Gtk.Align.CENTER)
            lock.add_css_class('dim-label')
            row.add_suffix(lock)

        # Icon — accent-coloured when active
        icon = Gtk.Image.new_from_icon_name('network-server-symbolic')
        icon.set_valign(Gtk.Align.CENTER)
        row.add_prefix(icon)
        row._active_icon = icon

        # Health dot — grey until a check has run
        health_dot = Gtk.Label()
        health_dot.set_valign(Gtk.Align.CENTER)
        self._apply_health_dot(health_dot, self._conn_health.get(conn['id'], {}))
        row.add_prefix(health_dot)
        row._health_dot = health_dot

        # Context menu
        menu = Gio.Menu()
        menu.append('Disconnect', 'mgr.disconnect')
        menu.append('Check connection', 'mgr.check-health')
        menu.append('Edit', 'mgr.edit')
        menu.append('Duplicate', 'mgr.duplicate')
        menu.append('Copy as URI', 'mgr.copy-uri')
        menu.append('Export to .pgpass…', 'mgr.export-pgpass')
        menu.append('Delete', 'mgr.delete')
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
        disconnect_action.connect('activate', lambda _a, _p, r=row: self._on_disconnect(r))
        ag.add_action(disconnect_action)
        row._disconnect_action = disconnect_action
        for name, cb in [
            ('check-health',  lambda _a, _p, r=row: self._check_health(r._conn)),
            ('edit',          lambda _a, _p, r=row: self._on_edit_connection(r)),
            ('duplicate',     lambda _a, _p, r=row: self._on_duplicate_connection(r)),
            ('copy-uri',      lambda _a, _p, r=row: self._on_copy_as_uri(r)),
            ('export-pgpass', lambda _a, _p, r=row: self._on_export_pgpass(r)),
            ('delete',        lambda _a, _p, r=row: self._on_delete_connection(r)),
        ]:
            a = Gio.SimpleAction.new(name, None)
            a.connect('activate', cb)
            ag.add_action(a)
        row.insert_action_group('mgr', ag)

        if position == -1:
            self._mgr_list.append(row)
        else:
            self._mgr_list.insert(row, position)
        self._conn_mgr_rows[conn['id']] = row
        return row

    def _on_add_connection(self, _btn):
        self._conn_popover.popdown()
        dlg = ConnectionDialog(parent=self, store=self._store)
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
        self._add_connection_row(conn)

    def _on_edit_connection(self, row):
        self._conn_popover.popdown()
        dlg = ConnectionDialog(parent=self, connection=row._conn, store=self._store)
        dlg.connect('connection-saved', self._on_connection_updated, row)
        dlg.present(self)

    def _on_duplicate_connection(self, row):
        self._conn_popover.popdown()
        try:
            conn = self._conn_with_password(row._conn)
        except KeyringUnavailableError as e:
            self._show_keyring_error(str(e))
            return
        dlg = ConnectionDialog(parent=self, connection=conn, duplicate=True, store=self._store)
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
            conn = self._store.update(conn)
        except KeyringUnavailableError as e:
            self._show_keyring_error(str(e))
            return
        conn_id = conn['id']

        # Rebuild both rows so suffix widgets (lock icon, tag chips) stay in sync.
        p_row = self._conn_popover_rows.pop(conn_id, None)
        pos = p_row.get_index() if p_row else -1
        if p_row:
            self._conn_list.remove(p_row)

        m_row = self._conn_mgr_rows.pop(conn_id, None)
        if m_row:
            self._mgr_list.remove(m_row)

        self._add_connection_row(conn, position=pos)

        if self._active_conn_id == conn_id:
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
        conn_id = conn['id']
        try:
            self._store.remove(conn_id)
        except KeyringUnavailableError as e:
            self._show_keyring_error(str(e))
            return

        p_row = self._conn_popover_rows.pop(conn_id, None)
        if p_row:
            self._conn_list.remove(p_row)

        m_row = self._conn_mgr_rows.pop(conn_id, None)
        if m_row:
            self._mgr_list.remove(m_row)

        if self._active_conn_id == conn_id:
            self._set_active_conn(None)
            self._browser.clear()

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

        # warn_on_connect: once per session per connection
        conn_id = row._conn['id']
        if conn_id not in self._warned_conn_ids:
            registry = self._store.get_tags_registry()
            warn_tags = [
                t for t in conn.get('tags', [])
                if registry.get(t, {}).get('warn_on_connect')
            ]
            if warn_tags:
                tag_list = ', '.join(f'"{t}"' for t in warn_tags)
                dialog = Adw.AlertDialog(
                    heading='Production environment',
                    body=f'This connection is tagged {tag_list}. Proceed with care.',
                )
                dialog.add_response('cancel', 'Cancel')
                dialog.add_response('readonly', 'Connect as Read-Only')
                dialog.add_response('connect', 'Connect')
                dialog.set_response_appearance('readonly', Adw.ResponseAppearance.SUGGESTED)
                dialog.set_default_response('readonly')
                dialog.set_close_response('cancel')
                dialog.connect('response', self._on_warn_response, conn, row, conn_id)
                dialog.present(self)
                return

        self._do_connect(conn, row)

    def _on_warn_response(self, _dialog, response, conn, row, conn_id):
        if response == 'cancel':
            return
        self._warned_conn_ids.add(conn_id)
        if response == 'readonly':
            conn = {**conn, 'read_only': True}
        self._do_connect(conn, row)

    def _do_connect(self, conn, row):
        # Record last connected timestamp on both row copies
        now_ts = datetime.datetime.now(datetime.timezone.utc).isoformat().replace('+00:00', 'Z')
        conn_id = row._conn['id']
        row._conn['last_connected'] = now_ts
        mgr_row = self._conn_mgr_rows.get(conn_id)
        if mgr_row:
            mgr_row._conn['last_connected'] = now_ts
            mgr_row._ts_label.set_label(self._format_last_connected(now_ts))
            mgr_row._ts_label.set_tooltip_text(
                datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
            )
        try:
            self._store.update(row._conn)
        except KeyringUnavailableError:
            pass  # non-fatal — don't block the connection
        if self._conn_sort == 'last-connected':
            self._mgr_list.invalidate_sort()

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

        # Update per-row disconnect action enabled state (popover + manager)
        row = self._conn_list.get_first_child()
        while row:
            if hasattr(row, '_conn'):
                row._disconnect_action.set_enabled(
                    bool(conn and row._conn['id'] == conn['id'])
                )
            row = row.get_next_sibling()

        for conn_id, m_row in self._conn_mgr_rows.items():
            is_active = bool(conn and conn_id == conn['id'])
            m_row._disconnect_action.set_enabled(is_active)
            m_row._active_pill.set_visible(is_active)
            if is_active:
                m_row._active_icon.add_css_class('conn-active-icon')
            else:
                m_row._active_icon.remove_css_class('conn-active-icon')

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

    # ── Connection manager helpers ────────────────────────────────────────────

    def _on_manage_tags(self, _btn):
        from tags_dialog import TagsDialog
        dlg = TagsDialog(self._store)
        dlg.connect('tags-changed', self._on_tags_changed)
        dlg.present(self)

    def _on_tags_changed(self, _dlg):
        # Rebuild every manager row using fresh store data so tag changes
        # (including deletions) aren't overwritten when _do_connect() later
        # calls store.update(row._conn).
        tags_registry = self._store.get_tags_registry()
        fresh = {c['id']: c for c in self._store.list()}
        # Refresh popover row _conn references (no visual rebuild needed)
        for conn_id, p_row in self._conn_popover_rows.items():
            if conn_id in fresh:
                p_row._conn = fresh[conn_id]
        for conn_id, m_row in list(self._conn_mgr_rows.items()):
            conn = fresh.get(conn_id, m_row._conn)
            pos = m_row.get_index()
            self._conn_mgr_rows.pop(conn_id)
            self._mgr_list.remove(m_row)
            self._add_mgr_row(conn, position=pos, tags_registry=tags_registry)
        self._refresh_tag_strip()
        self._set_active_conn(self._active_conn)

    def _show_connection_manager(self):
        self._main_stack.set_visible_child_name('manager')
        self._mgr_search.grab_focus()

    def _on_mgr_search_changed(self, entry):
        self._conn_search = entry.get_text()
        self._mgr_list.invalidate_filter()

    def _on_sort_changed(self, key):
        self._conn_sort = key
        prefs.put('conn_sort', key)
        self._mgr_list.invalidate_sort()

    def _mgr_filter_row(self, row):
        if not hasattr(row, '_conn'):
            return True
        conn = row._conn
        text = self._conn_search.lower().strip()
        # Tag filter (AND with text search)
        if self._active_tag_filters:
            conn_tags = set(conn.get('tags', []))
            if not (conn_tags & self._active_tag_filters):
                return False
        if not text:
            return True
        # tag: prefix syntax
        if text.startswith('tag:'):
            tag_name = text[4:].strip()
            return tag_name in [t.lower() for t in conn.get('tags', [])]
        haystack = ' '.join([
            conn.get('name', ''),
            conn.get('host', ''),
            conn.get('database', ''),
        ]).lower()
        return text in haystack

    def _mgr_sort_rows(self, a, b):
        if not hasattr(a, '_conn') or not hasattr(b, '_conn'):
            return 0
        ca, cb = a._conn, b._conn
        if self._conn_sort == 'name':
            na, nb = ca.get('name', '').lower(), cb.get('name', '').lower()
            return -1 if na < nb else (1 if na > nb else 0)
        if self._conn_sort == 'last-connected':
            ta = ca.get('last_connected') or ''
            tb = cb.get('last_connected') or ''
            return -1 if ta > tb else (1 if ta < tb else 0)
        return 0  # manual: preserve insertion order

    def _refresh_tag_strip(self):
        while (child := self._mgr_tag_strip.get_first_child()):
            self._mgr_tag_strip.remove(child)
        tags = self._store.get_tags_registry()
        if not tags:
            self._mgr_tag_scroll.set_visible(False)
            return
        self._mgr_tag_scroll.set_visible(True)
        for tag_name in sorted(tags):
            color = tags[tag_name].get('color', '#888888')
            safe_color = color if _COLOR_RE.match(color) else '#888888'
            lbl = Gtk.Label()
            lbl.set_markup(
                f'<span foreground="{safe_color}">⬤</span>'
                f'  {GLib.markup_escape_text(tag_name)}'
            )
            lbl.set_xalign(0)
            btn = Gtk.ToggleButton()
            btn.set_child(lbl)
            btn.add_css_class('flat')
            btn.set_active(tag_name in self._active_tag_filters)
            btn.connect('toggled', self._on_tag_filter_toggled, tag_name)
            self._mgr_tag_strip.append(btn)

    def _on_tag_filter_toggled(self, btn, tag_name):
        if btn.get_active():
            self._active_tag_filters.add(tag_name)
        else:
            self._active_tag_filters.discard(tag_name)
        self._mgr_list.invalidate_filter()

    # ── Health checks ─────────────────────────────────────────────────────────

    @staticmethod
    def _apply_health_dot(dot, health):
        status = health.get('status', 'unknown')
        color = {'ok': '#33d17a', 'error': '#e01b24', 'tunnel': '#e5a50a'}.get(status, '#888888')
        dot.set_markup(f'<span foreground="{color}">⬤</span>')
        msg = health.get('msg', 'Not checked')
        ts = health.get('ts')
        tip = msg
        if ts:
            try:
                dt = datetime.datetime.fromisoformat(ts.rstrip('Z')).replace(tzinfo=datetime.timezone.utc)
                tip += f'\nChecked {dt.strftime("%H:%M:%S UTC")}'
            except (ValueError, TypeError):
                pass
        dot.set_tooltip_text(tip)

    def _check_health(self, conn):
        conn_id = conn['id']
        # Tunnel/proxy connections can't be TCP-checked to the raw host
        if conn.get('ssh_host') or conn.get('cloud_proxy_enabled'):
            self._conn_health[conn_id] = {'status': 'tunnel', 'msg': 'Requires tunnel/proxy', 'ts': None}
            self._update_health_dot(conn_id)
            return

        def _run():
            ts = datetime.datetime.now(datetime.timezone.utc).isoformat().replace('+00:00', 'Z')
            try:
                with socket.create_connection((conn['host'], int(conn['port'])), timeout=3):
                    pass
                result = {'status': 'ok', 'msg': 'Reachable', 'ts': ts}
            except socket.timeout:
                result = {'status': 'error', 'msg': 'Timed out', 'ts': ts}
            except (OSError, ValueError) as exc:
                result = {'status': 'error', 'msg': str(exc), 'ts': ts}
            self._conn_health[conn_id] = result
            GLib.idle_add(self._update_health_dot, conn_id)

        threading.Thread(target=_run, daemon=True).start()

    def _update_health_dot(self, conn_id):
        row = self._conn_mgr_rows.get(conn_id)
        if row and hasattr(row, '_health_dot'):
            self._apply_health_dot(row._health_dot, self._conn_health.get(conn_id, {}))

    def _on_check_all_health(self):
        for conn in self._store.list():
            self._check_health(conn)

    # ── Connection import / export ────────────────────────────────────────────

    def _on_export_pgpass_bulk(self):
        import os
        import stat
        import tempfile
        conns = self._store.list()
        written = skipped_dup = skipped_no_pwd = skipped_tunnel = 0

        pgpass_path = os.path.expanduser('~/.pgpass')
        existing_lines = []
        if os.path.exists(pgpass_path):
            try:
                with open(pgpass_path, encoding='utf-8') as f:
                    existing_lines = f.read().splitlines()
            except OSError as e:
                self._show_toast(f'Could not read ~/.pgpass: {e}')
                return

        def _escape(s):
            return str(s).replace('\\', '\\\\').replace(':', '\\:')

        new_lines = list(existing_lines)
        for conn in conns:
            if conn.get('ssh_host') or conn.get('cloud_proxy_enabled'):
                skipped_tunnel += 1
                continue
            try:
                password = self._store.get_password(conn['id'])
            except Exception:
                skipped_no_pwd += 1
                continue
            if not password:
                skipped_no_pwd += 1
                continue
            fields = [str(conn.get('host', '')), str(conn.get('port', '')),
                      '*', str(conn.get('username', '')), password]
            if any('\n' in v or '\r' in v for v in fields):
                skipped_no_pwd += 1
                continue
            line = ':'.join(_escape(v) for v in fields)
            if line in new_lines:
                skipped_dup += 1
                continue
            new_lines.append(line)
            written += 1

        if written == 0:
            parts = [f'Nothing written to ~/.pgpass.']
            if skipped_dup:
                parts.append(f'{skipped_dup} already present.')
            if skipped_no_pwd:
                parts.append(f'{skipped_no_pwd} skipped (no password).')
            if skipped_tunnel:
                parts.append(f'{skipped_tunnel} skipped (SSH/proxy).')
            self._show_toast(' '.join(parts))
            return

        tmp_path = None
        try:
            content = '\n'.join(new_lines) + '\n'
            pgpass_dir = os.path.dirname(pgpass_path) or os.path.expanduser('~')
            with tempfile.NamedTemporaryFile(
                mode='w', encoding='utf-8', dir=pgpass_dir, delete=False,
            ) as tmp:
                tmp.write(content)
                tmp_path = tmp.name
            os.chmod(tmp_path, 0o600)
            os.replace(tmp_path, pgpass_path)
            tmp_path = None
        except OSError as e:
            self._show_toast(f'Export failed: {e}')
            return
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

        parts = [f'{written} {"entry" if written == 1 else "entries"} written to ~/.pgpass.']
        if skipped_dup:
            parts.append(f'{skipped_dup} skipped (duplicate).')
        if skipped_no_pwd:
            parts.append(f'{skipped_no_pwd} skipped (no password).')
        if skipped_tunnel:
            parts.append(f'{skipped_tunnel} skipped (SSH/proxy).')
        self._show_toast(' '.join(parts))

    def _on_export_connections_json(self):
        def _do_export(include_passwords):
            try:
                data = self._store.export_json(include_passwords=include_passwords)
            except KeyringUnavailableError as e:
                self._show_toast(f'Export failed: keyring unavailable — {e}')
                return
            file_dialog = Gtk.FileDialog()
            file_dialog.set_title('Export Connections')
            file_dialog.set_initial_name('connections.tusk-connections.json')
            filter_json = Gtk.FileFilter()
            filter_json.set_name('Tusk connection files (*.tusk-connections.json)')
            filter_json.add_pattern('*.tusk-connections.json')
            filters = Gio.ListStore.new(Gtk.FileFilter)
            filters.append(filter_json)
            file_dialog.set_filters(filters)
            file_dialog.save(self, None, self._on_export_json_file_chosen, data)

        dialog = Adw.AlertDialog(
            heading='Export Connections',
            body='Passwords are excluded by default. Including them in the export file is a security risk — do not share the file with others.',
        )
        dialog.add_response('cancel', 'Cancel')
        dialog.add_response('without', 'Export Without Passwords')
        dialog.add_response('with', 'Include Passwords')
        dialog.set_response_appearance('with', Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response('without')
        dialog.set_close_response('cancel')
        dialog.connect('response', lambda d, r: _do_export(r == 'with') if r != 'cancel' else None)
        dialog.present(self)

    def _on_export_json_file_chosen(self, file_dialog, result, data):
        try:
            gfile = file_dialog.save_finish(result)
        except Exception:
            return
        import json as _json
        try:
            path = gfile.get_path()
            if path is None:
                self._show_toast('Export failed: only local files are supported.')
                return
            tmp = path + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                _json.dump(data, f, indent=2)
            os.replace(tmp, path)
            n = len(data.get('connections', []))
            self._show_toast(f'{n} connection{"s" if n != 1 else ""} exported.')
        except OSError as e:
            self._show_toast(f'Export failed: {e}')

    def _on_import_connections_json(self):
        file_dialog = Gtk.FileDialog()
        file_dialog.set_title('Import Connections')
        filter_json = Gtk.FileFilter()
        filter_json.set_name('Tusk connection files (*.tusk-connections.json)')
        filter_json.add_pattern('*.tusk-connections.json')
        filter_all = Gtk.FileFilter()
        filter_all.set_name('All files')
        filter_all.add_pattern('*')
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(filter_json)
        filters.append(filter_all)
        file_dialog.set_filters(filters)
        file_dialog.open(self, None, self._on_import_json_file_chosen)

    def _on_import_json_file_chosen(self, file_dialog, result):
        import json as _json
        try:
            gfile = file_dialog.open_finish(result)
        except Exception:
            return
        try:
            path = gfile.get_path()
            if path is None:
                self._show_toast('Import failed: only local files are supported.')
                return
            with open(path, encoding='utf-8') as f:
                data = _json.load(f)
        except (OSError, ValueError) as e:
            self._show_toast(f'Could not read file: {e}')
            return
        conns = data.get('connections', [])
        tags = data.get('tags_registry', {})
        if not conns:
            self._show_toast('No connections found in file.')
            return
        from connections_import_dialog import ConnectionsImportDialog
        existing_names = {c['name'] for c in self._store.list()}
        dlg = ConnectionsImportDialog(conns, tags, existing_names)
        dlg.connect('import-confirmed', self._on_import_json_confirmed)
        dlg.present(self)

    def _on_import_json_confirmed(self, _dlg, resolved_conns, tags_registry):
        added, skipped = self._store.bulk_import(resolved_conns, tags_registry)
        # Add new rows to UI for freshly imported connections
        existing_ids = set(self._conn_mgr_rows.keys())
        tags_reg = self._store.get_tags_registry()
        for conn in self._store.list():
            if conn['id'] not in existing_ids:
                self._add_connection_row(conn, tags_registry=tags_reg)
        self._refresh_tag_strip()
        msg = f'{added} connection{"s" if added != 1 else ""} imported.'
        if skipped:
            msg += f' {skipped} skipped (already present).'
        self._show_toast(msg)

    def _on_cleanup_stale(self):
        from stale_dialog import StaleConnectionsDialog
        dlg = StaleConnectionsDialog(self._store, self._conn_health)
        dlg.connect('connections-deleted', self._on_stale_connections_deleted)
        dlg.present(self)

    def _on_stale_connections_deleted(self, _dlg, conn_ids):
        deleted = 0
        for conn_id in conn_ids:
            try:
                self._store.remove(conn_id)
            except Exception as e:
                self._show_toast(f'Could not delete connection: {e}')
                continue
            mgr_row = self._conn_mgr_rows.pop(conn_id, None)
            if mgr_row:
                self._mgr_list.remove(mgr_row)
            pop_row = self._conn_popover_rows.pop(conn_id, None)
            if pop_row:
                self._conn_list.remove(pop_row)
            self._conn_health.pop(conn_id, None)
            if conn_id == self._active_conn_id:
                self._set_active_conn(None)
            deleted += 1
        if deleted:
            self._show_toast(f'{deleted} connection{"s" if deleted != 1 else ""} deleted.')

    def _show_toast(self, msg, timeout=4):
        toast = Adw.Toast(title=msg)
        toast.set_timeout(timeout)
        self._toast_overlay.add_toast(toast)

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
            self._sidebar_paned.set_position(self._sidebar_paned_pos_saved)
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
            self._main_stack.set_visible_child_name('manager')

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

