import threading

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Gtk, Adw, GLib, Pango

from data_grid import make_column_view

try:
    gi.require_version('GtkSource', '5')
    from gi.repository import GtkSource
    _HAS_SOURCE = True
except (ValueError, ImportError):
    _HAS_SOURCE = False


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

ROW_LIMIT = 500

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
           string_agg(kcu.column_name, ', '
                      ORDER BY kcu.ordinal_position) AS columns
    FROM information_schema.table_constraints tc
    JOIN information_schema.key_column_usage kcu
      ON tc.constraint_name = kcu.constraint_name
     AND tc.table_schema    = kcu.table_schema
     AND tc.table_name      = kcu.table_name
    WHERE tc.table_schema = %s AND tc.table_name = %s
      AND tc.constraint_type IN ('PRIMARY KEY', 'UNIQUE')
    GROUP BY tc.constraint_name, tc.constraint_type
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


class TablePanel(Gtk.Box):
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
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

        self._schema_stack, self._schema_tree = self._make_tree(
            ['Column', 'Type', 'Length', 'Nullable', 'Default'], 'No columns'
        )
        self._page_schema = self._view_stack.add_titled_with_icon(
            self._schema_stack, 'schema', 'Schema', 'view-list-symbolic'
        )

        self._keys_stack, self._keys_tree = self._make_tree(
            ['Constraint', 'Type', 'Columns'], 'No keys'
        )
        self._page_keys = self._view_stack.add_titled_with_icon(
            self._keys_stack, 'keys', 'Keys', 'changes-prevent-symbolic'
        )

        self._relations_stack, self._relations_tree = self._make_tree(
            ['Constraint', 'Column', 'References', 'Ref Column', 'On Update', 'On Delete'], 'No relations'
        )
        self._page_relations = self._view_stack.add_titled_with_icon(
            self._relations_stack, 'relations', 'Relations', 'insert-link-symbolic'
        )

        self._triggers_stack, self._triggers_tree = self._make_tree(
            ['Name', 'Event', 'Timing', 'Orientation', 'Statement'], 'No triggers'
        )
        self._page_triggers = self._view_stack.add_titled_with_icon(
            self._triggers_stack, 'triggers', 'Triggers', 'media-playback-start-symbolic'
        )

        self._indexes_stack, self._indexes_tree = self._make_tree(
            ['Name', 'Definition'], 'No indexes'
        )
        self._page_indexes = self._view_stack.add_titled_with_icon(
            self._indexes_stack, 'indexes', 'Indexes', 'edit-find-symbolic'
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

        self._data_limit_bar = Gtk.Label()
        self._data_limit_bar.add_css_class('caption')
        self._data_limit_bar.add_css_class('dim-label')
        self._data_limit_bar.set_xalign(0)
        self._data_limit_bar.set_margin_start(10)
        self._data_limit_bar.set_margin_top(4)
        self._data_limit_bar.set_margin_bottom(4)
        self._data_limit_bar.set_visible(False)

        data_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        data_box.append(self._data_scroll)
        data_box.append(self._data_limit_bar)
        self._page_data = self._view_stack.add_titled_with_icon(
            data_box, 'data', 'Data', 'x-office-spreadsheet-symbolic'
        )

        tabs_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        tabs_box.append(self._switcher)
        tabs_box.append(Gtk.Separator())
        tabs_box.append(self._view_stack)
        self._outer.add_named(tabs_box, 'tabs')
        self.append(self._outer)

    def _make_tree(self, columns, empty_text='No data'):
        store = Gtk.ListStore(*([str] * len(columns)))
        tree = Gtk.TreeView(model=store)
        tree.set_grid_lines(Gtk.TreeViewGridLines.HORIZONTAL)
        tree.set_enable_search(False)

        for i, name in enumerate(columns):
            renderer = Gtk.CellRendererText()
            renderer.set_property('ellipsize', 3)
            col = Gtk.TreeViewColumn(name, renderer, text=i)
            col.set_resizable(True)
            col.set_min_width(80)
            tree.append_column(col)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        scroll.set_child(tree)

        empty = Adw.StatusPage(title=empty_text)
        empty.set_vexpand(True)

        stack = Gtk.Stack()
        stack.set_vexpand(True)
        stack.add_named(scroll, 'tree')
        stack.add_named(empty, 'empty')

        return stack, tree

    def _set_tabs_for_type(self, item_type):
        is_table = item_type == 'table'
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

    def load(self, conn, schema, table, item_type='table'):
        self._current_schema = schema
        self._current_table = table
        self._set_tabs_for_type(item_type)
        self._spinner.start()
        self._outer.set_visible_child_name('loading')
        threading.Thread(
            target=self._fetch_all,
            args=(conn, schema, table, item_type),
            daemon=True,
        ).start()

    def _fetch_all(self, conn, schema, table, item_type):
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
                    else:
                        keys_rows = relations_rows = indexes_rows = []
                        ddl = ''

                        cur.execute(_DEFINITION_SQL, [schema, table])
                        row = cur.fetchone()
                        definition = row[0] if row else ''

                    cur.execute(_TRIGGERS_SQL, [schema, table])
                    triggers_rows = cur.fetchall()

                    cur.execute(
                        sql.SQL('SELECT * FROM {}.{} LIMIT %s').format(
                            sql.Identifier(schema),
                            sql.Identifier(table),
                        ),
                        [ROW_LIMIT],
                    )
                    data_cols = [d.name for d in cur.description]
                    data_rows = cur.fetchall()

            GLib.idle_add(
                self._populate,
                schema_rows, keys_rows, relations_rows, triggers_rows,
                indexes_rows, ddl, definition, data_cols, data_rows,
            )
        except Exception as e:
            GLib.idle_add(self._show_error, str(e))

    def _fill_tree(self, stack, tree, rows):
        store = tree.get_model()
        store.clear()
        for row in rows:
            store.append(['' if v is None else str(v) for v in row])
        stack.set_visible_child_name('tree' if rows else 'empty')

    def _populate(self, schema_rows, keys_rows, relations_rows, triggers_rows,
                  indexes_rows, ddl, definition, data_cols, data_rows):
        self._spinner.stop()

        self._fill_tree(self._schema_stack, self._schema_tree, schema_rows)
        self._fill_tree(self._keys_stack, self._keys_tree, keys_rows)
        self._fill_tree(self._relations_stack, self._relations_tree, relations_rows)
        self._fill_tree(self._triggers_stack, self._triggers_tree, triggers_rows)
        self._fill_tree(self._indexes_stack, self._indexes_tree, indexes_rows)

        self._ddl_buffer.set_text(ddl)

        if definition is not None:
            self._definition_buffer.set_text(definition)

        # Data tab — rebuild with dynamic columns
        table_name = (
            f'{self._current_schema}.{self._current_table}'
            if definition is None else None
        )
        if data_rows:
            self._data_scroll.set_child(make_column_view(data_cols, data_rows, table_name=table_name))
        else:
            empty = Adw.StatusPage(title='No data')
            empty.set_vexpand(True)
            self._data_scroll.set_child(empty)
        if len(data_rows) >= ROW_LIMIT:
            self._data_limit_bar.set_label(f'Showing first {ROW_LIMIT} rows — results may be truncated')
            self._data_limit_bar.set_visible(True)
        else:
            self._data_limit_bar.set_visible(False)
        self._outer.set_visible_child_name('tabs')

    def _show_error(self, error_msg):
        self._spinner.stop()
        self._error_page.set_title('Failed to Load Table')
        self._error_page.set_description(error_msg)
        self._error_page.set_icon_name('dialog-error-symbolic')
        self._outer.set_visible_child_name('error')
