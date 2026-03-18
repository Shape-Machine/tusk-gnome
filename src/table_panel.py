import csv
import io
import json
import threading

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Gtk, Adw, GLib, Gio

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
        self._page_schema = self._view_stack.add_titled_with_icon(
            self._schema_scroll, 'schema', 'Schema', 'view-list-symbolic'
        )

        self._keys_scroll = self._make_tab_scroll()
        self._page_keys = self._view_stack.add_titled_with_icon(
            self._keys_scroll, 'keys', 'Keys', 'changes-prevent-symbolic'
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
        self._page_indexes = self._view_stack.add_titled_with_icon(
            self._indexes_scroll, 'indexes', 'Indexes', 'edit-find-symbolic'
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

        data_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        data_box.append(self._filter_entry)
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

        switcher_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self._switcher.set_hexpand(True)
        switcher_bar.append(self._switcher)
        switcher_bar.append(self._refresh_btn)

        tabs_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        tabs_box.append(switcher_bar)
        tabs_box.append(Gtk.Separator())
        tabs_box.append(self._view_stack)
        self._outer.add_named(tabs_box, 'tabs')
        self.append(self._outer)

    def _make_tab_scroll(self):
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        return scroll

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

            data = text.encode()
            GLib.idle_add(
                lambda: gfile.replace_contents(
                    data, None, False, Gio.FileCreateFlags.REPLACE_DESTINATION, None
                )
            )
        except Exception:
            pass

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
                self._data_scroll.set_child(
                    make_column_view(self._all_data_cols, rows, table_name=table_name)
                )
        else:
            text = self._filter_entry.get_text().strip()
            empty = Adw.StatusPage(title='No matching rows' if text else 'No data')
            empty.set_vexpand(True)
            self._data_scroll.set_child(empty)

    def _on_refresh(self):
        if self._refresh_btn.get_sensitive():
            self.load(self._conn, self._current_schema, self._current_table, self._item_type)

    def load(self, conn, schema, table, item_type='table'):
        self._conn = conn
        self._current_schema = schema
        self._current_table = table
        self._item_type = item_type
        self._data_page = 0
        self._filter_entry.set_text('')
        self._load_gen += 1
        self._refresh_btn.set_sensitive(False)
        self._export_btn.set_sensitive(False)
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
                    else:
                        keys_rows = relations_rows = indexes_rows = []
                        ddl = ''

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
                indexes_rows, ddl, definition, data_cols, data_rows, gen,
            )
        except Exception as e:
            GLib.idle_add(self._show_error, str(e), gen)

    def _populate(self, schema_rows, keys_rows, relations_rows, triggers_rows,
                  indexes_rows, ddl, definition, data_cols, data_rows, gen):
        if gen != self._load_gen:
            return
        self._spinner.stop()
        self._refresh_btn.set_sensitive(True)
        self._export_btn.set_sensitive(self._item_type == 'table')

        self._fill_scroll(self._schema_scroll,    _SCHEMA_COLS,    schema_rows,    'No columns')
        self._fill_scroll(self._keys_scroll,      _KEYS_COLS,      keys_rows,      'No keys')
        self._fill_scroll(self._relations_scroll, _RELATIONS_COLS, relations_rows, 'No relations')
        self._fill_scroll(self._triggers_scroll,  _TRIGGERS_COLS,  triggers_rows,  'No triggers')
        self._fill_scroll(self._indexes_scroll,   _INDEXES_COLS,   indexes_rows,   'No indexes')

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

    def _show_error(self, error_msg, gen):
        if gen != self._load_gen:
            return
        self._spinner.stop()
        self._refresh_btn.set_sensitive(True)
        self._error_page.set_title('Failed to Load Table')
        self._error_page.set_description(error_msg)
        self._error_page.set_icon_name('dialog-error-symbolic')
        self._outer.set_visible_child_name('error')
