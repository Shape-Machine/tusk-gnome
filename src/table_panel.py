import csv
import io
import json
import threading

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Gtk, Adw, GLib, Gio, GObject, Pango, Gdk

import prefs
from data_grid import make_column_view, update_column_view

try:
    gi.require_version('GtkSource', '5')
    from gi.repository import GtkSource
    _HAS_SOURCE = True
except (ValueError, ImportError):
    _HAS_SOURCE = False


_SCHEMA_COLS    = ['Column', 'Type', 'Length', 'Nullable', 'Default']
_KEYS_COLS      = ['Constraint', 'Type', 'Columns']
_RELATIONS_COLS = ['Constraint', 'Column', 'References', 'Ref Column', 'On Update', 'On Delete']
_TRIGGERS_COLS  = ['Name', 'Event', 'Timing', 'Orientation', 'Statement']
_INDEXES_COLS   = ['Name', 'Definition']


def _make_source_view():
    if _HAS_SOURCE:
        buf = GtkSource.Buffer()
        lang = GtkSource.LanguageManager.get_default().get_language('sql')
        if lang:
            buf.set_language(lang)
        view = GtkSource.View.new_with_buffer(buf)
        view.set_show_line_numbers(True)
        view.set_tab_width(4)
        return buf, view
    buf = Gtk.TextBuffer()
    view = Gtk.TextView(buffer=buf)
    return buf, view


def _apply_scheme(buf, dark):
    if not _HAS_SOURCE:
        return
    mgr = GtkSource.StyleSchemeManager.get_default()
    name = 'Adwaita-dark' if dark else 'Adwaita'
    scheme = mgr.get_scheme(name) or mgr.get_scheme('classic')
    if scheme:
        buf.set_style_scheme(scheme)

_PAGE_SIZES = [100, 500, 1000]

_SCHEMA_SQL = """
    SELECT column_name, data_type,
           COALESCE(character_maximum_length::text,
                    numeric_precision::text, '') AS length,
           is_nullable,
           COALESCE(column_default, '') AS default_val
    FROM information_schema.columns
    WHERE table_schema = %s AND table_name = %s
    ORDER BY ordinal_position
"""

_KEYS_SQL = """
    SELECT tc.constraint_name, tc.constraint_type,
           COALESCE(
               string_agg(kcu.column_name, ', '
                          ORDER BY kcu.ordinal_position),
               cc.check_clause
           ) AS columns
    FROM information_schema.table_constraints tc
    LEFT JOIN information_schema.key_column_usage kcu
      ON tc.constraint_name = kcu.constraint_name
     AND tc.table_schema    = kcu.table_schema
     AND tc.table_name      = kcu.table_name
    LEFT JOIN information_schema.check_constraints cc
      ON tc.constraint_name = cc.constraint_name
     AND tc.constraint_schema = cc.constraint_schema
    WHERE tc.table_schema = %s AND tc.table_name = %s
      AND tc.constraint_type IN ('PRIMARY KEY', 'UNIQUE', 'CHECK', 'FOREIGN KEY')
      AND NOT (tc.constraint_type = 'CHECK' AND cc.check_clause LIKE '%%IS NOT NULL')
    GROUP BY tc.constraint_name, tc.constraint_type, cc.check_clause
    ORDER BY tc.constraint_type, tc.constraint_name
"""

_RELATIONS_SQL = """
    SELECT tc.constraint_name,
           kcu.column_name,
           ccu.table_schema || '.' || ccu.table_name AS ref_table,
           ccu.column_name AS ref_column,
           rc.update_rule,
           rc.delete_rule
    FROM information_schema.table_constraints tc
    JOIN information_schema.key_column_usage kcu
      ON tc.constraint_name = kcu.constraint_name
     AND tc.table_schema    = kcu.table_schema
    JOIN information_schema.constraint_column_usage ccu
      ON tc.constraint_name = ccu.constraint_name
     AND tc.table_schema    = ccu.table_schema
    JOIN information_schema.referential_constraints rc
      ON tc.constraint_name  = rc.constraint_name
     AND rc.constraint_schema = tc.table_schema
    WHERE tc.table_schema = %s AND tc.table_name = %s
      AND tc.constraint_type = 'FOREIGN KEY'
    ORDER BY tc.constraint_name
"""

_TRIGGERS_SQL = """
    SELECT trigger_name, event_manipulation, action_timing,
           action_orientation, action_statement
    FROM information_schema.triggers
    WHERE event_object_schema = %s AND event_object_table = %s
    ORDER BY trigger_name, event_manipulation
"""

_INDEXES_SQL = """
    SELECT indexname, indexdef
    FROM pg_indexes
    WHERE schemaname = %s AND tablename = %s
    ORDER BY indexname
"""

_DEFINITION_SQL = """
    SELECT view_definition
    FROM information_schema.views
    WHERE table_schema = %s AND table_name = %s
"""

_STATS_SQL = """
    SELECT s.n_live_tup, pg_total_relation_size(c.oid)
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    LEFT JOIN pg_stat_user_tables s
           ON s.schemaname = n.nspname AND s.relname = c.relname
    WHERE n.nspname = %s AND c.relname = %s
"""


def _fmt_size(n_bytes):
    if n_bytes is None:
        return None
    for unit, threshold in (('GB', 1 << 30), ('MB', 1 << 20), ('KB', 1 << 10)):
        if n_bytes >= threshold:
            return f'{n_bytes / threshold:.1f} {unit}'
    return f'{n_bytes} B'


def _fmt_rows(n):
    if n is None:
        return '~? rows'
    if n >= 1_000_000:
        return f'~{n / 1_000_000:.1f}M rows'
    if n >= 1_000:
        return f'~{n / 1_000:.1f}K rows'
    return f'~{n:,} rows'

_DDL_SQL = """
    SELECT
      'CREATE TABLE ' || quote_ident(n.nspname) || '.' || quote_ident(c.relname)
      || E' (\n' ||
      string_agg(
        '    ' || quote_ident(a.attname) || ' ' ||
        pg_catalog.format_type(a.atttypid, a.atttypmod) ||
        CASE WHEN a.attnotnull THEN ' NOT NULL' ELSE '' END ||
        COALESCE(' DEFAULT ' || pg_catalog.pg_get_expr(ad.adbin, ad.adrelid), ''),
        E',\n' ORDER BY a.attnum
      ) || E'\n);'
    FROM pg_catalog.pg_class c
    JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
    JOIN pg_catalog.pg_attribute a ON a.attrelid = c.oid
      AND a.attnum > 0 AND NOT a.attisdropped
    LEFT JOIN pg_catalog.pg_attrdef ad ON ad.adrelid = c.oid AND ad.adnum = a.attnum
    WHERE n.nspname = %s AND c.relname = %s
    GROUP BY n.nspname, c.relname
"""


def _validate_sql_fragment(text):
    """Reject SQL fragments that contain statement-terminating or comment characters.

    Returns an error string if invalid, or None if the fragment is safe to embed.
    User-supplied type names, default expressions, and USING clauses are passed
    through pgsql.SQL() as literal SQL text, so we guard against multi-statement
    injection at the application level.
    """
    forbidden = (';', '--', '/*', '*/', '\x00')
    for token in forbidden:
        if token in text:
            return f'Invalid SQL fragment: "{token}" is not allowed in this field.'
    return None


class _NamedRow(GObject.Object):
    """Generic GObject wrapper for rows whose first column is a name used in DDL actions."""
    __gtype_name__ = 'TuskNamedRow'

    def __init__(self, row_tuple):
        super().__init__()
        self._data = row_tuple

    def get(self, i):
        v = self._data[i]
        return '' if v is None else str(v)

    @property
    def name(self):
        return self._data[0]


class _SchemaRow(GObject.Object):
    """GObject wrapper for a schema column row, used in the schema ColumnView."""
    __gtype_name__ = 'TuskSchemaRow'

    def __init__(self, row_tuple):
        super().__init__()
        self._data = row_tuple  # (col_name, data_type, length, is_nullable, default_val)

    def get(self, i):
        v = self._data[i]
        return '' if v is None else str(v)

    @property
    def col_name(self):    return self._data[0]
    @property
    def data_type(self):   return self._data[1]
    @property
    def is_nullable(self): return self._data[3]
    @property
    def default_val(self): return self._data[4] or ''


class TablePanel(Gtk.Box):
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._load_gen = 0
        self._build_ui()

    def _build_ui(self):
        # ViewSwitcher lives inside the panel (tabs visible once content loads)
        self._view_stack = Adw.ViewStack()

        self._switcher = Adw.ViewSwitcher()
        self._switcher.set_stack(self._view_stack)
        self._switcher.set_policy(Adw.ViewSwitcherPolicy.WIDE)

        self._outer = Gtk.Stack()
        self._outer.set_vexpand(True)

        # Loading
        spinner_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        spinner_box.set_halign(Gtk.Align.CENTER)
        spinner_box.set_valign(Gtk.Align.CENTER)
        self._spinner = Gtk.Spinner()
        self._spinner.set_size_request(32, 32)
        spinner_box.append(self._spinner)
        self._outer.add_named(spinner_box, 'loading')

        # Error
        self._error_page = Adw.StatusPage()
        self._outer.add_named(self._error_page, 'error')

        # Tabs (view_stack already created above; wrap it with switcher in outer)
        self._view_stack.set_vexpand(True)

        self._schema_scroll = self._make_tab_scroll()

        # Schema toolbar (tables only — hidden for views)
        self._schema_toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self._schema_toolbar.set_margin_start(6)
        self._schema_toolbar.set_margin_end(6)
        self._schema_toolbar.set_margin_top(2)
        self._schema_toolbar.set_margin_bottom(2)
        self._schema_toolbar.set_visible(False)

        _schema_spacer = Gtk.Box()
        _schema_spacer.set_hexpand(True)
        self._schema_toolbar.append(_schema_spacer)

        _reorder_btn = Gtk.Button(icon_name='view-sort-descending-symbolic')
        _reorder_btn.add_css_class('flat')
        _reorder_btn.set_tooltip_text('Reorder columns…')
        _reorder_btn.connect('clicked', self._on_reorder_clicked)
        self._schema_toolbar.append(_reorder_btn)

        _add_col_btn = Gtk.Button(icon_name='list-add-symbolic')
        _add_col_btn.add_css_class('flat')
        _add_col_btn.set_tooltip_text('Add column')
        _add_col_btn.connect('clicked', self._on_add_column_clicked)
        self._schema_toolbar.append(_add_col_btn)

        _schema_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        _schema_box.append(self._schema_toolbar)
        _schema_box.append(Gtk.Separator())
        _schema_box.append(self._schema_scroll)

        self._page_schema = self._view_stack.add_titled_with_icon(
            _schema_box, 'schema', 'Schema', 'view-list-symbolic'
        )

        self._keys_scroll = self._make_tab_scroll()
        self._keys_toolbar = self._make_action_toolbar(
            'Add Constraint', self._on_add_constraint_clicked
        )
        _keys_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        _keys_box.append(self._keys_toolbar)
        _keys_box.append(Gtk.Separator())
        _keys_box.append(self._keys_scroll)
        self._page_keys = self._view_stack.add_titled_with_icon(
            _keys_box, 'keys', 'Keys', 'changes-prevent-symbolic'
        )

        self._relations_scroll = self._make_tab_scroll()
        self._page_relations = self._view_stack.add_titled_with_icon(
            self._relations_scroll, 'relations', 'Relations', 'insert-link-symbolic'
        )

        self._triggers_scroll = self._make_tab_scroll()
        self._page_triggers = self._view_stack.add_titled_with_icon(
            self._triggers_scroll, 'triggers', 'Triggers', 'media-playback-start-symbolic'
        )

        self._indexes_scroll = self._make_tab_scroll()
        self._indexes_toolbar = self._make_action_toolbar(
            'Add Index', self._on_add_index_clicked
        )
        _indexes_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        _indexes_box.append(self._indexes_toolbar)
        _indexes_box.append(Gtk.Separator())
        _indexes_box.append(self._indexes_scroll)
        self._page_indexes = self._view_stack.add_titled_with_icon(
            _indexes_box, 'indexes', 'Indexes', 'edit-find-symbolic'
        )

        # DDL tab (tables only)
        self._ddl_buffer, ddl_view = _make_source_view()
        ddl_view.set_editable(False)
        ddl_view.set_monospace(True)
        ddl_view.set_wrap_mode(Gtk.WrapMode.NONE)
        ddl_view.set_top_margin(12)
        ddl_view.set_left_margin(12)
        ddl_scroll = Gtk.ScrolledWindow()
        ddl_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        ddl_scroll.set_vexpand(True)
        ddl_scroll.set_child(ddl_view)
        self._page_ddl = self._view_stack.add_titled_with_icon(
            ddl_scroll, 'ddl', 'DDL', 'accessories-text-editor-symbolic'
        )
        if _HAS_SOURCE:
            style_mgr = Adw.StyleManager.get_default()
            _apply_scheme(self._ddl_buffer, style_mgr.get_dark())
            style_mgr.connect('notify::dark',
                              lambda m, _: _apply_scheme(self._ddl_buffer, m.get_dark()))

        # Definition tab (views only)
        self._definition_buffer = Gtk.TextBuffer()
        definition_view = Gtk.TextView(buffer=self._definition_buffer)
        definition_view.set_editable(False)
        definition_view.set_monospace(True)
        definition_view.set_wrap_mode(Gtk.WrapMode.NONE)
        definition_view.set_top_margin(12)
        definition_view.set_left_margin(12)
        definition_scroll = Gtk.ScrolledWindow()
        definition_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        definition_scroll.set_vexpand(True)
        definition_scroll.set_child(definition_view)
        self._page_definition = self._view_stack.add_titled_with_icon(
            definition_scroll, 'definition', 'Definition', 'accessories-text-editor-symbolic'
        )

        # Data tab
        self._data_scroll = Gtk.ScrolledWindow()
        self._data_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self._data_scroll.set_vexpand(True)

        self._data_nav_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self._data_nav_bar.set_margin_start(6)
        self._data_nav_bar.set_margin_end(6)
        self._data_nav_bar.set_margin_top(2)
        self._data_nav_bar.set_margin_bottom(2)
        self._data_nav_bar.set_visible(False)

        self._data_prev_btn = Gtk.Button(icon_name='go-previous-symbolic')
        self._data_prev_btn.add_css_class('flat')
        self._data_prev_btn.set_tooltip_text('Previous page')
        self._data_prev_btn.connect('clicked', lambda _: self._change_data_page(-1))
        self._data_prev_btn.set_sensitive(False)

        self._data_page_label = Gtk.Label()
        self._data_page_label.add_css_class('caption')
        self._data_page_label.add_css_class('dim-label')
        self._data_page_label.set_hexpand(True)

        self._data_next_btn = Gtk.Button(icon_name='go-next-symbolic')
        self._data_next_btn.add_css_class('flat')
        self._data_next_btn.set_tooltip_text('Next page')
        self._data_next_btn.connect('clicked', lambda _: self._change_data_page(1))
        self._data_next_btn.set_sensitive(False)

        saved_size = prefs.get('table_page_size', 500)
        saved_idx = _PAGE_SIZES.index(saved_size) if saved_size in _PAGE_SIZES else 1
        self._page_size_drop = Gtk.DropDown(
            model=Gtk.StringList.new([str(s) for s in _PAGE_SIZES])
        )
        self._page_size_drop.set_selected(saved_idx)
        self._page_size_drop.set_tooltip_text('Rows per page')
        self._page_size_drop.connect('notify::selected', self._on_page_size_changed)

        export_menu = Gio.Menu()
        export_menu.append('Export all as CSV…',        'tbl.export-csv')
        export_menu.append('Export all as JSON…',       'tbl.export-json')
        export_menu.append('Export all as INSERT SQL…', 'tbl.export-sql')

        self._export_btn = Gtk.MenuButton()
        self._export_btn.set_icon_name('document-save-symbolic')
        self._export_btn.set_tooltip_text('Export table')
        self._export_btn.add_css_class('flat')
        self._export_btn.set_menu_model(export_menu)
        self._export_btn.set_sensitive(False)

        export_ag = Gio.SimpleActionGroup()
        for fmt in ('csv', 'json', 'sql'):
            action = Gio.SimpleAction.new(f'export-{fmt}', None)
            action.connect('activate', lambda _a, _p, f=fmt: self._export_table(f))
            export_ag.add_action(action)
        self._data_nav_bar.insert_action_group('tbl', export_ag)

        self._data_nav_bar.append(self._data_prev_btn)
        self._data_nav_bar.append(self._data_page_label)
        self._data_nav_bar.append(self._data_next_btn)
        self._data_nav_bar.append(self._page_size_drop)
        self._data_nav_bar.append(self._export_btn)

        self._filter_entry = Gtk.SearchEntry()
        self._filter_entry.set_placeholder_text('Filter rows…')
        self._filter_entry.set_margin_start(6)
        self._filter_entry.set_margin_end(6)
        self._filter_entry.set_margin_top(4)
        self._filter_entry.set_margin_bottom(4)
        self._filter_entry.connect('search-changed', self._apply_local_filter)

        self._insert_btn = Gtk.Button(icon_name='list-add-symbolic')
        self._insert_btn.add_css_class('flat')
        self._insert_btn.set_tooltip_text('Insert row')
        self._insert_btn.connect('clicked', self._on_insert_clicked)

        self._edit_btn = Gtk.Button(icon_name='document-edit-symbolic')
        self._edit_btn.add_css_class('flat')
        self._edit_btn.set_tooltip_text('Edit selected row')
        self._edit_btn.set_sensitive(False)
        self._edit_btn.connect('clicked', self._on_edit_clicked)

        self._delete_btn = Gtk.Button(icon_name='edit-delete-symbolic')
        self._delete_btn.add_css_class('flat')
        self._delete_btn.set_tooltip_text('Delete selected row(s)')
        self._delete_btn.set_sensitive(False)
        self._delete_btn.connect('clicked', self._on_delete_clicked)

        self._edit_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        self._edit_bar.set_margin_end(4)
        self._edit_bar.set_halign(Gtk.Align.END)
        self._edit_bar.set_visible(False)
        self._edit_bar.append(self._insert_btn)
        self._edit_bar.append(self._edit_btn)
        self._edit_bar.append(self._delete_btn)

        data_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        data_box.append(self._filter_entry)
        data_box.append(self._edit_bar)
        data_box.append(Gtk.Separator())
        data_box.append(self._data_scroll)
        data_box.append(self._data_nav_bar)
        self._page_data = self._view_stack.add_titled_with_icon(
            data_box, 'data', 'Data', 'x-office-spreadsheet-symbolic'
        )

        self._refresh_btn = Gtk.Button(icon_name='view-refresh-symbolic')
        self._refresh_btn.add_css_class('flat')
        self._refresh_btn.set_tooltip_text('Refresh  Ctrl+R')
        self._refresh_btn.set_sensitive(False)
        self._refresh_btn.connect('clicked', lambda _: self._on_refresh())

        self._stats_label = Gtk.Label()
        self._stats_label.add_css_class('caption')
        self._stats_label.add_css_class('dim-label')
        self._stats_label.set_margin_start(12)
        self._stats_label.set_margin_top(4)
        self._stats_label.set_margin_bottom(4)
        self._stats_label.set_xalign(0)
        self._stats_label.set_visible(False)

        switcher_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self._switcher.set_hexpand(True)
        switcher_bar.append(self._switcher)
        switcher_bar.append(self._refresh_btn)

        self._stats_separator = Gtk.Separator()
        self._stats_separator.set_visible(False)

        tabs_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        tabs_box.append(switcher_bar)
        tabs_box.append(Gtk.Separator())
        tabs_box.append(self._view_stack)
        tabs_box.append(self._stats_separator)
        tabs_box.append(self._stats_label)
        self._outer.add_named(tabs_box, 'tabs')
        self.append(self._outer)

    def _make_tab_scroll(self):
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        return scroll

    def _make_action_toolbar(self, tooltip, handler):
        """Create a right-aligned single-button toolbar for tab action buttons."""
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        toolbar.set_margin_start(6)
        toolbar.set_margin_end(6)
        toolbar.set_margin_top(2)
        toolbar.set_margin_bottom(2)
        toolbar.set_visible(False)
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        toolbar.append(spacer)
        btn = Gtk.Button(icon_name='list-add-symbolic')
        btn.add_css_class('flat')
        btn.set_tooltip_text(tooltip)
        btn.connect('clicked', handler)
        toolbar.append(btn)
        return toolbar

    def _fill_scroll(self, scroll, cols, rows, empty_text):
        if rows:
            existing = scroll.get_child()
            if isinstance(existing, Gtk.ColumnView):
                update_column_view(existing, rows)
            else:
                scroll.set_child(make_column_view(cols, rows))
        else:
            empty = Adw.StatusPage(title=empty_text)
            empty.set_vexpand(True)
            scroll.set_child(empty)

    def _fill_schema_scroll(self, schema_rows):
        """Build/refresh the schema ColumnView with a per-row right-click action menu."""
        if not schema_rows:
            empty = Adw.StatusPage(title='No columns')
            empty.set_vexpand(True)
            self._schema_scroll.set_child(empty)
            return

        self._schema_raw_rows = schema_rows  # (col_name, data_type, length, is_nullable, default_val)

        store = Gio.ListStore(item_type=_SchemaRow)
        for row in schema_rows:
            store.append(_SchemaRow(row))

        col_view = Gtk.ColumnView()
        col_view.set_show_row_separators(True)
        col_view.set_show_column_separators(True)
        col_view.set_hexpand(True)

        _right_clicked_row = [None]
        _cell_hit = [False]

        for i, col_name in enumerate(_SCHEMA_COLS):
            factory = Gtk.SignalListItemFactory()

            def on_setup(_factory, list_item):
                label = Gtk.Label()
                label.set_xalign(0)
                label.set_ellipsize(Pango.EllipsizeMode.END)
                label.set_max_width_chars(40)
                cell_gesture = Gtk.GestureClick(button=3)
                def _on_cell_rclick(_g, _n, _x, _y, lbl=label):
                    _right_clicked_row[0] = getattr(lbl, '_item', None)
                    _cell_hit[0] = True
                cell_gesture.connect('pressed', _on_cell_rclick)
                label.add_controller(cell_gesture)
                list_item.set_child(label)

            def on_bind(_factory, list_item, idx=i):
                label = list_item.get_child()
                item = list_item.get_item()
                label._item = item
                label.set_text(item.get(idx))

            factory.connect('setup', on_setup)
            factory.connect('bind', on_bind)

            col = Gtk.ColumnViewColumn(title=col_name, factory=factory)
            col.set_resizable(True)
            col.set_expand(True)
            col_view.append_column(col)

        col_view.set_model(Gtk.NoSelection(model=store))

        # ── Schema action context menu ──────────────────────────────────────
        ag = Gio.SimpleActionGroup()

        def make_action(name, handler):
            action = Gio.SimpleAction.new(name, None)
            action.connect('activate', handler)
            ag.add_action(action)

        make_action('change-type', lambda *_: self._on_change_type(_right_clicked_row[0]))
        make_action('set-default', lambda *_: self._on_set_default(_right_clicked_row[0]))
        make_action('toggle-null', lambda *_: self._on_toggle_nullable(_right_clicked_row[0]))
        make_action('set-pk',      lambda *_: self._on_set_primary_key(_right_clicked_row[0]))
        make_action('drop-column', lambda *_: self._on_drop_column(_right_clicked_row[0]))

        col_view.insert_action_group('schema', ag)

        section1 = Gio.Menu()
        section1.append('Change Type…',       'schema.change-type')
        section1.append('Set Default…',       'schema.set-default')
        section1.append('Toggle NOT NULL',    'schema.toggle-null')
        section1.append('Set as Primary Key', 'schema.set-pk')
        section2 = Gio.Menu()
        section2.append('Drop Column…', 'schema.drop-column')
        menu = Gio.Menu()
        menu.append_section(None, section1)
        menu.append_section(None, section2)

        popover = Gtk.PopoverMenu(menu_model=menu)
        popover.set_has_arrow(False)
        popover.set_parent(col_view)

        def on_right_click(_gesture, _n, x, y):
            if not _cell_hit[0]:
                return
            _cell_hit[0] = False
            rect = Gdk.Rectangle()
            rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
            popover.set_pointing_to(rect)
            popover.popup()

        gesture = Gtk.GestureClick(button=3)
        gesture.connect('pressed', on_right_click)
        col_view.add_controller(gesture)

        self._schema_scroll.set_child(col_view)

    # ── Schema toolbar actions ──────────────────────────────────────────────

    def _on_add_column_clicked(self, _btn):
        from column_dialogs import AddColumnDialog
        col_names = [r[0] for r in getattr(self, '_schema_raw_rows', [])]
        conn, schema, table = self._conn, self._current_schema, self._current_table

        def on_save(name, pg_type, nullable, default, after_col):
            import psycopg
            from psycopg import sql as pgsql

            for fragment, label in [(pg_type, 'Type'), (default, 'Default')]:
                if fragment is not None:
                    err = _validate_sql_fragment(fragment)
                    if err:
                        self._show_edit_error(f'{label}: {err}')
                        return

            parts = [
                pgsql.SQL('ALTER TABLE {}.{} ADD COLUMN {} {}').format(
                    pgsql.Identifier(schema),
                    pgsql.Identifier(table),
                    pgsql.Identifier(name),
                    pgsql.SQL(pg_type),
                )
            ]
            if not nullable:
                parts.append(pgsql.SQL('NOT NULL'))
            if default is not None:
                parts.append(pgsql.SQL('DEFAULT ') + pgsql.SQL(default))
            ddl = pgsql.SQL(' ').join(parts)

            def run():
                try:
                    from tunnel import open_tunnel
                    with open_tunnel(conn) as (host, port), psycopg.connect(
                        host=host, port=port,
                        dbname=conn['database'], user=conn['username'],
                        password=conn['password'], connect_timeout=10,
                    ) as db:
                        with db.cursor() as cur:
                            cur.execute(ddl)
                            if after_col:
                                comment_sql = pgsql.SQL(
                                    'COMMENT ON COLUMN {}.{}.{} IS {}'
                                ).format(
                                    pgsql.Identifier(schema),
                                    pgsql.Identifier(table),
                                    pgsql.Identifier(name),
                                    pgsql.Literal(f'position:after:{after_col}'),
                                )
                                cur.execute(comment_sql)
                        db.commit()
                    GLib.idle_add(self._reload_schema_tab)
                except Exception as e:
                    GLib.idle_add(self._show_edit_error, str(e))

            threading.Thread(target=run, daemon=True).start()

        AddColumnDialog(col_names, on_save).present(self.get_root())

    def _on_reorder_clicked(self, _btn):
        from column_dialogs import ReorderColumnsDialog
        col_names = [r[0] for r in getattr(self, '_schema_raw_rows', [])]
        schema, table = self._current_schema, self._current_table
        ReorderColumnsDialog(schema, table, col_names).present(self.get_root())

    # ── Per-column context menu actions ────────────────────────────────────

    def _on_change_type(self, item):
        if item is None:
            return
        from column_dialogs import ChangeTypeDialog
        conn, schema, table = self._conn, self._current_schema, self._current_table

        def on_save(new_type, using_expr):
            import psycopg
            from psycopg import sql as pgsql

            for fragment, label in [(new_type, 'Type'), (using_expr, 'USING expression')]:
                if fragment is not None:
                    err = _validate_sql_fragment(fragment)
                    if err:
                        self._show_edit_error(f'{label}: {err}')
                        return

            if using_expr:
                ddl = pgsql.SQL(
                    'ALTER TABLE {}.{} ALTER COLUMN {} TYPE {} USING {}'
                ).format(
                    pgsql.Identifier(schema), pgsql.Identifier(table),
                    pgsql.Identifier(item.col_name),
                    pgsql.SQL(new_type),
                    pgsql.SQL(using_expr),
                )
            else:
                ddl = pgsql.SQL(
                    'ALTER TABLE {}.{} ALTER COLUMN {} TYPE {}'
                ).format(
                    pgsql.Identifier(schema), pgsql.Identifier(table),
                    pgsql.Identifier(item.col_name),
                    pgsql.SQL(new_type),
                )
            self._exec_ddl_and_reload_schema(conn, ddl)

        ChangeTypeDialog(item.col_name, item.data_type, on_save).present(self.get_root())

    def _on_set_default(self, item):
        if item is None:
            return
        from column_dialogs import SetDefaultDialog
        conn, schema, table = self._conn, self._current_schema, self._current_table

        def on_save(expr):
            import psycopg
            from psycopg import sql as pgsql

            if expr:
                err = _validate_sql_fragment(expr)
                if err:
                    self._show_edit_error(f'Default expression: {err}')
                    return
                ddl = pgsql.SQL(
                    'ALTER TABLE {}.{} ALTER COLUMN {} SET DEFAULT {}'
                ).format(
                    pgsql.Identifier(schema), pgsql.Identifier(table),
                    pgsql.Identifier(item.col_name),
                    pgsql.SQL(expr),
                )
            else:
                ddl = pgsql.SQL(
                    'ALTER TABLE {}.{} ALTER COLUMN {} DROP DEFAULT'
                ).format(
                    pgsql.Identifier(schema), pgsql.Identifier(table),
                    pgsql.Identifier(item.col_name),
                )
            self._exec_ddl_and_reload_schema(conn, ddl)

        SetDefaultDialog(item.col_name, item.default_val, on_save).present(self.get_root())

    def _on_toggle_nullable(self, item):
        if item is None:
            return
        conn, schema, table = self._conn, self._current_schema, self._current_table
        is_nullable = item.is_nullable == 'YES'

        if is_nullable:
            # Setting NOT NULL — pre-check for existing NULLs
            def check():
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
                            cur.execute(
                                pgsql.SQL('SELECT COUNT(*) FROM {}.{} WHERE {} IS NULL').format(
                                    pgsql.Identifier(schema), pgsql.Identifier(table),
                                    pgsql.Identifier(item.col_name),
                                )
                            )
                            null_count = cur.fetchone()[0]
                    GLib.idle_add(_show_confirm, null_count)
                except Exception as e:
                    GLib.idle_add(self._show_edit_error, str(e))

            def _show_confirm(null_count):
                body = f'Set column "{item.col_name}" to NOT NULL?'
                if null_count > 0:
                    body += f'\n\nWarning: {null_count} existing NULL value{"s" if null_count != 1 else ""} will prevent this change.'
                dialog = Adw.AlertDialog(heading='Toggle NOT NULL', body=body)
                dialog.add_response('cancel', 'Cancel')
                dialog.add_response('apply', 'Set NOT NULL')
                dialog.set_response_appearance('apply', Adw.ResponseAppearance.SUGGESTED)
                dialog.set_default_response('cancel')
                dialog.set_close_response('cancel')

                def on_response(_d, response):
                    if response == 'apply':
                        import psycopg
                        from psycopg import sql as pgsql
                        ddl = pgsql.SQL(
                            'ALTER TABLE {}.{} ALTER COLUMN {} SET NOT NULL'
                        ).format(
                            pgsql.Identifier(schema), pgsql.Identifier(table),
                            pgsql.Identifier(item.col_name),
                        )
                        self._exec_ddl_and_reload_schema(conn, ddl)

                dialog.connect('response', on_response)
                dialog.present(self.get_root())

            threading.Thread(target=check, daemon=True).start()
        else:
            # Dropping NOT NULL — just confirm
            dialog = Adw.AlertDialog(
                heading='Toggle NOT NULL',
                body=f'Allow NULL values in column "{item.col_name}"?',
            )
            dialog.add_response('cancel', 'Cancel')
            dialog.add_response('apply', 'Drop NOT NULL')
            dialog.set_response_appearance('apply', Adw.ResponseAppearance.SUGGESTED)
            dialog.set_default_response('cancel')
            dialog.set_close_response('cancel')

            def on_response(_d, response):
                if response == 'apply':
                    import psycopg
                    from psycopg import sql as pgsql
                    ddl = pgsql.SQL(
                        'ALTER TABLE {}.{} ALTER COLUMN {} DROP NOT NULL'
                    ).format(
                        pgsql.Identifier(schema), pgsql.Identifier(table),
                        pgsql.Identifier(item.col_name),
                    )
                    self._exec_ddl_and_reload_schema(conn, ddl)

            dialog.connect('response', on_response)
            dialog.present(self.get_root())

    def _on_drop_column(self, item):
        if item is None:
            return
        conn, schema, table = self._conn, self._current_schema, self._current_table

        dialog = Adw.AlertDialog(
            heading=f'Drop column "{item.col_name}"?',
            body='This action cannot be undone. Any indexes, constraints, or '
                 'foreign keys that reference this column will also be dropped.',
        )
        dialog.add_response('cancel', 'Cancel')
        dialog.add_response('drop', 'Drop Column')
        dialog.set_response_appearance('drop', Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response('cancel')
        dialog.set_close_response('cancel')

        def on_response(_d, response):
            if response == 'drop':
                import psycopg
                from psycopg import sql as pgsql
                ddl = pgsql.SQL('ALTER TABLE {}.{} DROP COLUMN {}').format(
                    pgsql.Identifier(schema), pgsql.Identifier(table),
                    pgsql.Identifier(item.col_name),
                )
                self._exec_ddl_and_reload_schema(conn, ddl)

        dialog.connect('response', on_response)
        dialog.present(self.get_root())

    def _on_set_primary_key(self, item):
        if item is None:
            return
        conn, schema, table = self._conn, self._current_schema, self._current_table
        existing_pk = self._pk_cols

        if existing_pk:
            body = (
                f'Set "{item.col_name}" as primary key?\n\n'
                f'The existing primary key ({", ".join(existing_pk)}) will be '
                f'dropped first.'
            )
        else:
            body = f'Set "{item.col_name}" as the primary key for this table?'

        dialog = Adw.AlertDialog(heading='Set as Primary Key', body=body)
        dialog.add_response('cancel', 'Cancel')
        dialog.add_response('apply', 'Set Primary Key')
        dialog.set_response_appearance('apply', Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response('cancel')
        dialog.set_close_response('cancel')

        def on_response(_d, response):
            if response != 'apply':
                return
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
                            if existing_pk:
                                # Find the PK constraint name
                                cur.execute(
                                    """SELECT constraint_name
                                       FROM information_schema.table_constraints
                                       WHERE table_schema = %s AND table_name = %s
                                         AND constraint_type = 'PRIMARY KEY'""",
                                    [schema, table],
                                )
                                row = cur.fetchone()
                                if row:
                                    cur.execute(
                                        pgsql.SQL('ALTER TABLE {}.{} DROP CONSTRAINT {}').format(
                                            pgsql.Identifier(schema),
                                            pgsql.Identifier(table),
                                            pgsql.Identifier(row[0]),
                                        )
                                    )
                            cur.execute(
                                pgsql.SQL('ALTER TABLE {}.{} ADD PRIMARY KEY ({})').format(
                                    pgsql.Identifier(schema),
                                    pgsql.Identifier(table),
                                    pgsql.Identifier(item.col_name),
                                )
                            )
                        db.commit()
                    GLib.idle_add(self._reload_schema_tab)
                except Exception as e:
                    GLib.idle_add(self._show_edit_error, str(e))

            threading.Thread(target=run, daemon=True).start()

        dialog.connect('response', on_response)
        dialog.present(self.get_root())

    def _exec_ddl_and_reload_schema(self, conn, ddl):
        """Execute a DDL statement in a background thread and reload the schema tab."""
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
                GLib.idle_add(self._reload_schema_tab)
            except Exception as e:
                GLib.idle_add(self._show_edit_error, str(e))

        threading.Thread(target=run, daemon=True).start()

    def _reload_schema_tab(self):
        """Re-fetch schema and keys data and refresh those tab views."""
        conn, schema, table = self._conn, self._current_schema, self._current_table

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
                        cur.execute(_SCHEMA_SQL, [schema, table])
                        schema_rows = cur.fetchall()
                        cur.execute(_KEYS_SQL, [schema, table])
                        keys_rows = cur.fetchall()
                GLib.idle_add(self._update_schema_view, schema_rows, keys_rows)
            except Exception as e:
                GLib.idle_add(self._show_edit_error, str(e))

        threading.Thread(target=run, daemon=True).start()

    def _update_schema_view(self, schema_rows, keys_rows):
        self._schema_info = [(r[0], r[1], r[3], r[4]) for r in schema_rows]
        pk_entry = next((r for r in keys_rows if r[1] == 'PRIMARY KEY'), None)
        self._pk_cols = pk_entry[2].split(', ') if pk_entry else []
        self._fill_schema_scroll(schema_rows)
        self._fill_keys_scroll(keys_rows)

    # ── Indexes tab ─────────────────────────────────────────────────────────

    def _fill_indexes_scroll(self, indexes_rows):
        """Build/refresh the Indexes ColumnView with a per-row Drop Index context menu."""
        if not indexes_rows:
            empty = Adw.StatusPage(title='No indexes')
            empty.set_vexpand(True)
            self._indexes_scroll.set_child(empty)
            return

        store = Gio.ListStore(item_type=_NamedRow)
        for row in indexes_rows:
            store.append(_NamedRow(row))

        col_view = self._make_named_col_view(
            _INDEXES_COLS, store,
            context_items=[('Drop Index…', self._on_drop_index)],
        )
        self._indexes_scroll.set_child(col_view)

    def _on_add_index_clicked(self, _btn):
        from column_dialogs import AddIndexDialog
        col_names = [r[0] for r in getattr(self, '_schema_raw_rows', [])]
        conn, schema, table = self._conn, self._current_schema, self._current_table

        def on_save(name, cols, idx_type, unique, concurrently):
            import psycopg
            from psycopg import sql as pgsql

            err = _validate_sql_fragment(name)
            if err:
                self._show_edit_error(f'Index name: {err}')
                return

            unique_kw = pgsql.SQL('UNIQUE ') if unique else pgsql.SQL('')
            conc_kw = pgsql.SQL('CONCURRENTLY ') if concurrently else pgsql.SQL('')
            col_sql = pgsql.SQL(', ').join(pgsql.Identifier(c) for c in cols)
            ddl = pgsql.SQL(
                'CREATE {unique}INDEX {conc}{name} ON {schema}.{table} USING {itype} ({cols})'
            ).format(
                unique=unique_kw,
                conc=conc_kw,
                name=pgsql.Identifier(name),
                schema=pgsql.Identifier(schema),
                table=pgsql.Identifier(table),
                itype=pgsql.SQL(idx_type),
                cols=col_sql,
            )
            self._exec_ddl_and_reload_indexes(conn, ddl, autocommit=concurrently)

        AddIndexDialog(table, col_names, on_save).present(self.get_root())

    def _on_drop_index(self, item):
        if item is None:
            return
        conn, schema = self._conn, self._current_schema

        dialog = Adw.AlertDialog(
            heading=f'Drop index "{item.name}"?',
            body='This action cannot be undone.',
        )
        dialog.add_response('cancel', 'Cancel')
        dialog.add_response('drop', 'Drop Index')
        dialog.set_response_appearance('drop', Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response('cancel')
        dialog.set_close_response('cancel')

        def on_response(_d, response):
            if response == 'drop':
                import psycopg
                from psycopg import sql as pgsql
                # DROP INDEX is schema-qualified, not table-qualified
                ddl = pgsql.SQL('DROP INDEX CONCURRENTLY {}.{}').format(
                    pgsql.Identifier(schema),
                    pgsql.Identifier(item.name),
                )
                # DROP INDEX CONCURRENTLY cannot run inside a transaction
                self._exec_ddl_and_reload_indexes(conn, ddl, autocommit=True)

        dialog.connect('response', on_response)
        dialog.present(self.get_root())

    def _exec_ddl_and_reload_indexes(self, conn, ddl, autocommit=False):
        def run():
            try:
                import psycopg
                from tunnel import open_tunnel
                with open_tunnel(conn) as (host, port), psycopg.connect(
                    host=host, port=port,
                    dbname=conn['database'], user=conn['username'],
                    password=conn['password'], connect_timeout=10,
                    autocommit=autocommit,
                ) as db:
                    with db.cursor() as cur:
                        cur.execute(ddl)
                    if not autocommit:
                        db.commit()
                GLib.idle_add(self._reload_indexes_tab)
            except Exception as e:
                GLib.idle_add(self._show_edit_error, str(e))

        threading.Thread(target=run, daemon=True).start()

    def _reload_indexes_tab(self):
        conn, schema, table = self._conn, self._current_schema, self._current_table

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
                        cur.execute(_INDEXES_SQL, [schema, table])
                        rows = cur.fetchall()
                GLib.idle_add(self._fill_indexes_scroll, rows)
            except Exception as e:
                GLib.idle_add(self._show_edit_error, str(e))

        threading.Thread(target=run, daemon=True).start()

    # ── Keys tab ────────────────────────────────────────────────────────────

    def _fill_keys_scroll(self, keys_rows):
        """Build/refresh the Keys ColumnView with a per-row Drop Constraint context menu."""
        if not keys_rows:
            empty = Adw.StatusPage(title='No keys')
            empty.set_vexpand(True)
            self._keys_scroll.set_child(empty)
            return

        store = Gio.ListStore(item_type=_NamedRow)
        for row in keys_rows:
            store.append(_NamedRow(row))

        col_view = self._make_named_col_view(
            _KEYS_COLS, store,
            context_items=[('Drop Constraint…', self._on_drop_constraint)],
        )
        self._keys_scroll.set_child(col_view)

    def _on_add_constraint_clicked(self, _btn):
        from column_dialogs import AddConstraintDialog
        col_names = [r[0] for r in getattr(self, '_schema_raw_rows', [])]
        conn, schema, table = self._conn, self._current_schema, self._current_table

        def on_save(name, constraint_sql):
            import psycopg
            from psycopg import sql as pgsql

            err = _validate_sql_fragment(name)
            if err:
                self._show_edit_error(f'Constraint name: {err}')
                return

            err = _validate_sql_fragment(constraint_sql)
            if err:
                self._show_edit_error(f'Constraint definition: {err}')
                return

            ddl = pgsql.SQL(
                'ALTER TABLE {}.{} ADD CONSTRAINT {} {}'
            ).format(
                pgsql.Identifier(schema),
                pgsql.Identifier(table),
                pgsql.Identifier(name),
                pgsql.SQL(constraint_sql),
            )
            self._exec_ddl_and_reload_keys(conn, ddl)

        AddConstraintDialog(table, col_names, on_save).present(self.get_root())

    def _on_drop_constraint(self, item):
        if item is None:
            return
        conn, schema, table = self._conn, self._current_schema, self._current_table

        dialog = Adw.AlertDialog(
            heading=f'Drop constraint "{item.name}"?',
            body='This action cannot be undone. Dropping a primary key referenced '
                 'by foreign keys in other tables will also fail with an error.',
        )
        dialog.add_response('cancel', 'Cancel')
        dialog.add_response('drop', 'Drop Constraint')
        dialog.set_response_appearance('drop', Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response('cancel')
        dialog.set_close_response('cancel')

        def on_response(_d, response):
            if response == 'drop':
                import psycopg
                from psycopg import sql as pgsql
                ddl = pgsql.SQL('ALTER TABLE {}.{} DROP CONSTRAINT {}').format(
                    pgsql.Identifier(schema),
                    pgsql.Identifier(table),
                    pgsql.Identifier(item.name),
                )
                self._exec_ddl_and_reload_keys(conn, ddl)

        dialog.connect('response', on_response)
        dialog.present(self.get_root())

    def _exec_ddl_and_reload_keys(self, conn, ddl):
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
                GLib.idle_add(self._reload_keys_tab)
            except Exception as e:
                GLib.idle_add(self._show_edit_error, str(e))

        threading.Thread(target=run, daemon=True).start()

    def _reload_keys_tab(self):
        conn, schema, table = self._conn, self._current_schema, self._current_table

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
                        cur.execute(_KEYS_SQL, [schema, table])
                        keys_rows = cur.fetchall()
                GLib.idle_add(self._fill_keys_scroll, keys_rows)
            except Exception as e:
                GLib.idle_add(self._show_edit_error, str(e))

        threading.Thread(target=run, daemon=True).start()

    # ── Shared helper for named-row ColumnViews with context menu ────────────

    def _make_named_col_view(self, col_names, store, context_items):
        """Build a ColumnView from a _NamedRow ListStore with a right-click context menu.

        context_items – list of (label, handler(item)) tuples for the context menu.
        """
        col_view = Gtk.ColumnView()
        col_view.set_show_row_separators(True)
        col_view.set_show_column_separators(True)
        col_view.set_hexpand(True)

        _right_clicked_item = [None]
        _cell_hit = [False]

        for i, name in enumerate(col_names):
            factory = Gtk.SignalListItemFactory()

            def on_setup(_factory, list_item):
                label = Gtk.Label()
                label.set_xalign(0)
                label.set_ellipsize(Pango.EllipsizeMode.END)
                label.set_max_width_chars(60)
                cell_gesture = Gtk.GestureClick(button=3)
                def _on_cell_rclick(_g, _n, _x, _y, lbl=label):
                    _right_clicked_item[0] = getattr(lbl, '_item', None)
                    _cell_hit[0] = True
                cell_gesture.connect('pressed', _on_cell_rclick)
                label.add_controller(cell_gesture)
                list_item.set_child(label)

            def on_bind(_factory, list_item, idx=i):
                label = list_item.get_child()
                item = list_item.get_item()
                label._item = item
                label.set_text(item.get(idx))

            factory.connect('setup', on_setup)
            factory.connect('bind', on_bind)

            col = Gtk.ColumnViewColumn(title=name, factory=factory)
            col.set_resizable(True)
            col.set_expand(True)
            col_view.append_column(col)

        col_view.set_model(Gtk.NoSelection(model=store))

        ag = Gio.SimpleActionGroup()
        menu = Gio.Menu()
        section = Gio.Menu()

        for label, handler in context_items:
            action_name = label.lower().replace(' ', '-').replace('…', '')
            action = Gio.SimpleAction.new(action_name, None)
            action.connect('activate', lambda _a, _p, h=handler: h(_right_clicked_item[0]))
            ag.add_action(action)
            section.append(label, f'ctx.{action_name}')

        menu.append_section(None, section)
        col_view.insert_action_group('ctx', ag)

        popover = Gtk.PopoverMenu(menu_model=menu)
        popover.set_has_arrow(False)
        popover.set_parent(col_view)

        def on_right_click(_gesture, _n, x, y):
            if not _cell_hit[0]:
                return
            _cell_hit[0] = False
            rect = Gdk.Rectangle()
            rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
            popover.set_pointing_to(rect)
            popover.popup()

        gesture = Gtk.GestureClick(button=3)
        gesture.connect('pressed', on_right_click)
        col_view.add_controller(gesture)

        return col_view

    def _set_tabs_for_type(self, item_type):
        is_table = item_type == 'table'
        self._schema_toolbar.set_visible(is_table)
        self._keys_toolbar.set_visible(is_table)
        self._indexes_toolbar.set_visible(is_table)
        self._page_keys.set_visible(is_table)
        self._page_relations.set_visible(is_table)
        self._page_indexes.set_visible(is_table)
        self._page_ddl.set_visible(is_table)
        self._page_definition.set_visible(not is_table)
        # Switch away from a now-hidden tab if needed
        if self._view_stack.get_visible_child_name() in ('keys', 'relations', 'indexes', 'ddl') and not is_table:
            self._view_stack.set_visible_child_name('schema')
        if self._view_stack.get_visible_child_name() == 'definition' and is_table:
            self._view_stack.set_visible_child_name('schema')

    @property
    def _page_size(self):
        return _PAGE_SIZES[self._page_size_drop.get_selected()]

    def _on_page_size_changed(self, _drop, _param):
        prefs.put('table_page_size', self._page_size)
        if hasattr(self, '_conn'):
            self._data_prev_btn.set_sensitive(False)
            self._data_next_btn.set_sensitive(False)
            threading.Thread(
                target=self._fetch_data_page,
                args=(self._conn, self._current_schema, self._current_table, 0),
                daemon=True,
            ).start()

    def _export_table(self, fmt):
        ext = 'sql' if fmt == 'sql' else fmt
        dialog = Gtk.FileDialog()
        dialog.set_initial_name(f'{self._current_table}.{ext}')
        def _on_save(d, result):
            try:
                gfile = d.save_finish(result)
            except Exception:
                return
            threading.Thread(
                target=self._fetch_and_write,
                args=(gfile, fmt, self._conn, self._current_schema, self._current_table),
                daemon=True,
            ).start()
        dialog.save(self.get_root(), None, _on_save)

    def _fetch_and_write(self, gfile, fmt, conn, schema, table):
        try:
            import psycopg
            from psycopg import sql as pgsql
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
                        pgsql.SQL('SELECT * FROM {}.{}').format(
                            pgsql.Identifier(schema), pgsql.Identifier(table)
                        )
                    )
                    cols = [d.name for d in cur.description]
                    rows = cur.fetchall()

            if fmt == 'csv':
                buf = io.StringIO()
                w = csv.writer(buf)
                w.writerow(cols)
                w.writerows(rows)
                text = buf.getvalue()
            elif fmt == 'json':
                text = json.dumps(
                    [{col: v for col, v in zip(cols, row)} for row in rows],
                    indent=2, default=str,
                )
            else:
                def _quote(name):
                    return '"' + name.replace('"', '""') + '"'
                def _val(v):
                    if v is None: return 'NULL'
                    if isinstance(v, bool): return 'TRUE' if v else 'FALSE'
                    if isinstance(v, (int, float)): return str(v)
                    return "'" + str(v).replace("'", "''") + "'"
                qtable = f'{_quote(schema)}.{_quote(table)}'
                col_str = ', '.join(_quote(c) for c in cols)
                lines = [
                    f'INSERT INTO {qtable} ({col_str}) VALUES ({", ".join(_val(v) for v in row)});'
                    for row in rows
                ]
                text = '\n'.join(lines)

            gfile.replace_contents(
                text.encode(), None, False,
                Gio.FileCreateFlags.REPLACE_DESTINATION, None,
            )
        except Exception as e:
            GLib.idle_add(self._show_export_error, str(e))

    def _show_export_error(self, msg):
        self._data_page_label.set_label(f'Export failed: {msg}')

    def _apply_local_filter(self, *_):
        if not hasattr(self, '_all_data_rows'):
            return
        text = self._filter_entry.get_text().strip().lower()
        if text:
            filtered = [r for r in self._all_data_rows if any(text in str(v).lower() for v in r)]
        else:
            filtered = self._all_data_rows
        self._render_data_rows(filtered)

    def _render_data_rows(self, rows):
        table_name = (
            f'{self._current_schema}.{self._current_table}'
            if self._item_type == 'table' else None
        )
        if rows:
            existing = self._data_scroll.get_child()
            if isinstance(existing, Gtk.ColumnView):
                update_column_view(existing, rows)
            else:
                col_view = make_column_view(self._all_data_cols, rows, table_name=table_name)
                self._data_scroll.set_child(col_view)
                self._column_view = col_view
                if self._item_type == 'table':
                    self._edit_btn.set_sensitive(False)
                    self._delete_btn.set_sensitive(False)
                    col_view.connect('activate', self._on_data_row_activate)
                    col_view.get_model().connect('selection-changed', self._on_data_selection_changed)
        else:
            text = self._filter_entry.get_text().strip()
            empty = Adw.StatusPage(title='No matching rows' if text else 'No data')
            empty.set_vexpand(True)
            self._data_scroll.set_child(empty)
            self._column_view = None

    def _on_refresh(self):
        if self._refresh_btn.get_sensitive():
            self.load(self._conn, self._current_schema, self._current_table, self._item_type)

    def load(self, conn, schema, table, item_type='table'):
        self._conn = conn
        self._current_schema = schema
        self._current_table = table
        self._item_type = item_type
        self._data_page = 0
        self._schema_info = []
        self._pk_cols = []
        self._column_view = None
        self._filter_entry.set_text('')
        self._load_gen += 1
        self._refresh_btn.set_sensitive(False)
        self._export_btn.set_sensitive(False)
        self._edit_bar.set_visible(item_type == 'table')
        self._edit_btn.set_sensitive(False)
        self._delete_btn.set_sensitive(False)
        self._set_tabs_for_type(item_type)
        self._spinner.start()
        self._outer.set_visible_child_name('loading')
        threading.Thread(
            target=self._fetch_all,
            args=(conn, schema, table, item_type, self._load_gen),
            daemon=True,
        ).start()

    def _fetch_all(self, conn, schema, table, item_type, gen):
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
                    cur.execute(_SCHEMA_SQL, [schema, table])
                    schema_rows = cur.fetchall()

                    if item_type == 'table':
                        cur.execute(_KEYS_SQL, [schema, table])
                        keys_rows = cur.fetchall()

                        cur.execute(_RELATIONS_SQL, [schema, table])
                        relations_rows = cur.fetchall()

                        cur.execute(_INDEXES_SQL, [schema, table])
                        indexes_rows = cur.fetchall()

                        cur.execute(_DDL_SQL, [schema, table])
                        row = cur.fetchone()
                        ddl = row[0] if row else ''
                        definition = None

                        cur.execute(_STATS_SQL, [schema, table])
                        stats_row = cur.fetchone()  # (n_live_tup, total_bytes)
                    else:
                        keys_rows = relations_rows = indexes_rows = []
                        ddl = ''
                        stats_row = None

                        cur.execute(_DEFINITION_SQL, [schema, table])
                        row = cur.fetchone()
                        definition = row[0] if row else ''

                    cur.execute(_TRIGGERS_SQL, [schema, table])
                    triggers_rows = cur.fetchall()

                    cur.execute(
                        sql.SQL('SELECT * FROM {}.{} LIMIT %s OFFSET %s').format(
                            sql.Identifier(schema),
                            sql.Identifier(table),
                        ),
                        [self._page_size + 1, 0],
                    )
                    data_cols = [d.name for d in cur.description]
                    data_rows = cur.fetchall()

            GLib.idle_add(
                self._populate,
                schema_rows, keys_rows, relations_rows, triggers_rows,
                indexes_rows, ddl, definition, data_cols, data_rows, stats_row, gen,
            )
        except Exception as e:
            GLib.idle_add(self._show_error, str(e), gen)

    def _populate(self, schema_rows, keys_rows, relations_rows, triggers_rows,
                  indexes_rows, ddl, definition, data_cols, data_rows, stats_row, gen):
        if gen != self._load_gen:
            return
        self._spinner.stop()
        self._refresh_btn.set_sensitive(True)
        self._export_btn.set_sensitive(self._item_type == 'table')

        self._schema_info = [(r[0], r[1], r[3], r[4]) for r in schema_rows]
        pk_entry = next((r for r in keys_rows if r[1] == 'PRIMARY KEY'), None)
        self._pk_cols = pk_entry[2].split(', ') if pk_entry else []
        if self._item_type == 'table':
            if self._pk_cols:
                self._edit_btn.set_tooltip_text('Edit selected row')
                self._delete_btn.set_tooltip_text('Delete selected row(s)')
            else:
                self._edit_btn.set_tooltip_text('Table has no primary key')
                self._delete_btn.set_tooltip_text('Table has no primary key')

        if stats_row:
            n_live_tup, total_bytes = stats_row
            parts = [_fmt_rows(n_live_tup)]
            size = _fmt_size(total_bytes)
            if size:
                parts.append(size)
            self._stats_label.set_label(' · '.join(parts))
            self._stats_label.set_visible(True)
            self._stats_separator.set_visible(True)
        else:
            self._stats_label.set_visible(False)
            self._stats_separator.set_visible(False)

        self._fill_schema_scroll(schema_rows)
        self._fill_keys_scroll(keys_rows)
        self._fill_scroll(self._relations_scroll, _RELATIONS_COLS, relations_rows, 'No relations')
        self._fill_scroll(self._triggers_scroll,  _TRIGGERS_COLS,  triggers_rows,  'No triggers')
        self._fill_indexes_scroll(indexes_rows)

        self._ddl_buffer.set_text(ddl)

        if definition is not None:
            self._definition_buffer.set_text(definition)

        self._populate_data(data_cols, data_rows, 0)
        self._outer.set_visible_child_name('tabs')

    def _populate_data(self, cols, rows, page):
        self._data_page = page
        # The query fetched page_size + 1 rows; the extra row is a sentinel
        # indicating there is another page — it is never displayed.
        has_more = len(rows) > self._page_size
        rows = rows[:self._page_size]

        self._all_data_cols = cols
        self._all_data_rows = rows
        self._filter_entry.set_text('')
        self._data_scroll.set_child(None)  # force fresh ColumnView; reuse is unsafe across tables
        self._render_data_rows(rows)

        offset = page * self._page_size
        if rows:
            row_start = offset + 1
            row_end = offset + len(rows)
            label = f'Rows {row_start}–{row_end}'
            if has_more:
                label += f' (page {page + 1})'
            self._data_page_label.set_label(label)
        else:
            self._data_page_label.set_label('')

        self._data_prev_btn.set_sensitive(page > 0)
        self._data_next_btn.set_sensitive(has_more)
        self._data_nav_bar.set_visible(bool(rows) or page > 0)

    def _change_data_page(self, delta):
        page = self._data_page + delta
        self._data_prev_btn.set_sensitive(False)
        self._data_next_btn.set_sensitive(False)
        threading.Thread(
            target=self._fetch_data_page,
            args=(self._conn, self._current_schema, self._current_table, page),
            daemon=True,
        ).start()

    def _fetch_data_page(self, conn, schema, table, page):
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
                        sql.SQL('SELECT * FROM {}.{} LIMIT %s OFFSET %s').format(
                            sql.Identifier(schema),
                            sql.Identifier(table),
                        ),
                        [self._page_size + 1, page * self._page_size],
                    )
                    cols = [d.name for d in cur.description]
                    rows = cur.fetchall()

            GLib.idle_add(self._populate_data, cols, rows, page)
        except Exception as e:
            GLib.idle_add(self._show_data_page_error, str(e))

    def _show_data_page_error(self, error_msg):
        self._data_page_label.set_label(f'Error: {error_msg}')
        self._data_prev_btn.set_sensitive(self._data_page > 0)
        self._data_next_btn.set_sensitive(False)

    def _on_data_selection_changed(self, _sel, _pos, _n):
        if not self._column_view:
            return
        bitset = self._column_view.get_model().get_selection()
        n = bitset.get_size()
        has_pk = bool(self._pk_cols)
        self._edit_btn.set_sensitive(n == 1 and has_pk)
        self._delete_btn.set_sensitive(n >= 1 and has_pk)

    def _on_data_row_activate(self, col_view, position):
        if not self._pk_cols:
            return
        row = col_view.get_model().get_item(position)
        if row is None:
            return
        initial = {col: row.raw(i) for i, col in enumerate(self._all_data_cols)}
        self._show_edit_dialog(initial)

    def _show_edit_dialog(self, initial_values):
        from row_edit_dialog import RowEditDialog
        conn, schema, table = self._conn, self._current_schema, self._current_table
        pk_cols, page = list(self._pk_cols), self._data_page

        def on_save(values):
            threading.Thread(
                target=self._exec_update,
                args=(conn, schema, table, values, initial_values, pk_cols, page),
                daemon=True,
            ).start()

        RowEditDialog(
            mode='edit',
            columns=self._all_data_cols,
            schema_info=self._schema_info,
            pk_cols=pk_cols,
            initial_values=initial_values,
            on_save=on_save,
        ).present(self.get_root())

    def _on_insert_clicked(self, _btn):
        from row_edit_dialog import RowEditDialog
        conn, schema, table = self._conn, self._current_schema, self._current_table
        schema_info, page = list(self._schema_info), self._data_page

        def on_save(values):
            info_by_col = {r[0]: r for r in schema_info}
            cols, vals = [], []
            for col, val in values.items():
                if val is None:
                    info = info_by_col.get(col)
                    if info and info[3]:  # has default_val → let DB use it
                        continue
                cols.append(col)
                vals.append(val)
            threading.Thread(
                target=self._exec_insert,
                args=(conn, schema, table, cols, vals, page),
                daemon=True,
            ).start()

        RowEditDialog(
            mode='insert',
            columns=self._all_data_cols,
            schema_info=schema_info,
            pk_cols=self._pk_cols,
            initial_values=None,
            on_save=on_save,
        ).present(self.get_root())

    def _on_edit_clicked(self, _btn):
        if not self._column_view:
            return
        bitset = self._column_view.get_model().get_selection()
        if bitset.get_size() != 1:
            return
        valid, pos, _ = Gtk.BitsetIter.init_first(bitset)
        if not valid:
            return
        row = self._column_view.get_model().get_item(pos)
        initial = {col: row.raw(i) for i, col in enumerate(self._all_data_cols)}
        self._show_edit_dialog(initial)

    def _on_delete_clicked(self, _btn):
        if not self._column_view:
            return
        selection = self._column_view.get_model()
        bitset = selection.get_selection()
        n = bitset.get_size()
        if n == 0:
            return
        rows_to_delete = []
        valid, pos, it = Gtk.BitsetIter.init_first(bitset)
        while valid:
            row = selection.get_item(pos)
            rows_to_delete.append({col: row.raw(i) for i, col in enumerate(self._all_data_cols)})
            valid, pos = Gtk.BitsetIter.next(it)

        conn, schema, table = self._conn, self._current_schema, self._current_table
        pk_cols, page = list(self._pk_cols), self._data_page
        page_size = self._page_size

        msg = f'Delete {n} row{"s" if n > 1 else ""}?'
        dialog = Adw.AlertDialog(heading=msg, body='This action cannot be undone.')
        dialog.add_response('cancel', 'Cancel')
        dialog.add_response('delete', 'Delete')
        dialog.set_response_appearance('delete', Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response('cancel')
        dialog.set_close_response('cancel')

        def on_response(_d, response):
            if response == 'delete':
                threading.Thread(
                    target=self._exec_delete,
                    args=(conn, schema, table, rows_to_delete, pk_cols, page, page_size),
                    daemon=True,
                ).start()

        dialog.connect('response', on_response)
        dialog.present(self.get_root())

    def _exec_insert(self, conn, schema, table, cols, vals, page):
        try:
            import psycopg
            from psycopg import sql as pgsql
            from tunnel import open_tunnel

            with open_tunnel(conn) as (host, port), psycopg.connect(
                host=host, port=port,
                dbname=conn['database'], user=conn['username'], password=conn['password'],
                connect_timeout=10,
            ) as db:
                with db.cursor() as cur:
                    if cols:
                        query = pgsql.SQL('INSERT INTO {}.{} ({}) VALUES ({})').format(
                            pgsql.Identifier(schema),
                            pgsql.Identifier(table),
                            pgsql.SQL(', ').join(pgsql.Identifier(c) for c in cols),
                            pgsql.SQL(', ').join(pgsql.Placeholder() for _ in cols),
                        )
                        cur.execute(query, vals)
                    else:
                        cur.execute(
                            pgsql.SQL('INSERT INTO {}.{} DEFAULT VALUES').format(
                                pgsql.Identifier(schema), pgsql.Identifier(table)
                            )
                        )
                db.commit()
            GLib.idle_add(self._reload_data_page, conn, schema, table, page)
        except Exception as e:
            GLib.idle_add(self._show_edit_error, str(e))

    def _exec_update(self, conn, schema, table, new_values, original_values, pk_cols, page):
        try:
            import psycopg
            from psycopg import sql as pgsql
            from tunnel import open_tunnel

            set_cols = list(new_values.keys())
            set_vals = list(new_values.values())
            where_vals = [original_values[c] for c in pk_cols]

            with open_tunnel(conn) as (host, port), psycopg.connect(
                host=host, port=port,
                dbname=conn['database'], user=conn['username'], password=conn['password'],
                connect_timeout=10,
            ) as db:
                with db.cursor() as cur:
                    query = pgsql.SQL('UPDATE {}.{} SET {} WHERE {}').format(
                        pgsql.Identifier(schema),
                        pgsql.Identifier(table),
                        pgsql.SQL(', ').join(
                            pgsql.SQL('{} = {}').format(pgsql.Identifier(c), pgsql.Placeholder())
                            for c in set_cols
                        ),
                        pgsql.SQL(' AND ').join(
                            pgsql.SQL('{} = {}').format(pgsql.Identifier(c), pgsql.Placeholder())
                            for c in pk_cols
                        ),
                    )
                    cur.execute(query, set_vals + where_vals)
                db.commit()
            GLib.idle_add(self._reload_data_page, conn, schema, table, page)
        except Exception as e:
            GLib.idle_add(self._show_edit_error, str(e))

    def _exec_delete(self, conn, schema, table, rows_to_delete, pk_cols, page, page_size):
        try:
            import psycopg
            from psycopg import sql as pgsql
            from tunnel import open_tunnel

            where_clause = pgsql.SQL(' AND ').join(
                pgsql.SQL('{} = {}').format(pgsql.Identifier(c), pgsql.Placeholder())
                for c in pk_cols
            )
            del_query = pgsql.SQL('DELETE FROM {}.{} WHERE {}').format(
                pgsql.Identifier(schema),
                pgsql.Identifier(table),
                where_clause,
            )
            with open_tunnel(conn) as (host, port), psycopg.connect(
                host=host, port=port,
                dbname=conn['database'], user=conn['username'], password=conn['password'],
                connect_timeout=10,
            ) as db:
                with db.cursor() as cur:
                    for row_vals in rows_to_delete:
                        cur.execute(del_query, [row_vals[c] for c in pk_cols])
                    # If on a non-first page, check whether it still has rows after
                    # the delete; if not, navigate back to the previous page.
                    reload_page = page
                    if page > 0:
                        cur.execute(
                            pgsql.SQL(
                                'SELECT EXISTS(SELECT 1 FROM {}.{} OFFSET %s)'
                            ).format(
                                pgsql.Identifier(schema), pgsql.Identifier(table)
                            ),
                            [page * page_size],
                        )
                        if not cur.fetchone()[0]:
                            reload_page = page - 1
                db.commit()
            GLib.idle_add(self._reload_data_page, conn, schema, table, reload_page)
        except Exception as e:
            GLib.idle_add(self._show_edit_error, str(e))

    def _reload_data_page(self, conn, schema, table, page):
        threading.Thread(
            target=self._fetch_data_page,
            args=(conn, schema, table, page),
            daemon=True,
        ).start()

    def _show_edit_error(self, msg):
        dialog = Adw.AlertDialog(heading='Error', body=msg)
        dialog.add_response('ok', 'OK')
        dialog.present(self.get_root())

    def _show_error(self, error_msg, gen):
        if gen != self._load_gen:
            return
        self._spinner.stop()
        self._refresh_btn.set_sensitive(True)
        self._error_page.set_title('Failed to Load Table')
        self._error_page.set_description(error_msg)
        self._error_page.set_icon_name('dialog-error-symbolic')
        self._outer.set_visible_child_name('error')
