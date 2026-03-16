import threading

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Gtk, Adw, GLib, Pango

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

        self._schema_scroll, self._schema_tree = self._make_tree(
            ['Column', 'Type', 'Length', 'Nullable', 'Default']
        )
        self._page_schema = self._view_stack.add_titled_with_icon(
            self._schema_scroll, 'schema', 'Schema', 'view-list-symbolic'
        )

        self._keys_scroll, self._keys_tree = self._make_tree(
            ['Constraint', 'Type', 'Columns']
        )
        self._page_keys = self._view_stack.add_titled_with_icon(
            self._keys_scroll, 'keys', 'Keys', 'changes-prevent-symbolic'
        )

        self._relations_scroll, self._relations_tree = self._make_tree(
            ['Constraint', 'Column', 'References', 'Ref Column', 'On Update', 'On Delete']
        )
        self._page_relations = self._view_stack.add_titled_with_icon(
            self._relations_scroll, 'relations', 'Relations', 'insert-link-symbolic'
        )

        self._triggers_scroll, self._triggers_tree = self._make_tree(
            ['Name', 'Event', 'Timing', 'Orientation', 'Statement']
        )
        self._page_triggers = self._view_stack.add_titled_with_icon(
            self._triggers_scroll, 'triggers', 'Triggers', 'media-playback-start-symbolic'
        )

        self._indexes_scroll, self._indexes_tree = self._make_tree(
            ['Name', 'Definition']
        )
        self._page_indexes = self._view_stack.add_titled_with_icon(
            self._indexes_scroll, 'indexes', 'Indexes', 'edit-find-symbolic'
        )

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
        self._page_data = self._view_stack.add_titled_with_icon(
            self._data_scroll, 'data', 'Data', 'x-office-spreadsheet-symbolic'
        )

        tabs_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        tabs_box.append(self._switcher)
        tabs_box.append(Gtk.Separator())
        tabs_box.append(self._view_stack)
        self._outer.add_named(tabs_box, 'tabs')
        self.append(self._outer)

    def _make_tree(self, columns):
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
        return scroll, tree

    def _set_tabs_for_type(self, item_type):
        is_table = item_type == 'table'
        self._page_keys.set_visible(is_table)
        self._page_relations.set_visible(is_table)
        self._page_indexes.set_visible(is_table)
        self._page_definition.set_visible(not is_table)
        # Switch away from a now-hidden tab if needed
        if self._view_stack.get_visible_child_name() in ('keys', 'relations', 'indexes') and not is_table:
            self._view_stack.set_visible_child_name('schema')
        if self._view_stack.get_visible_child_name() == 'definition' and is_table:
            self._view_stack.set_visible_child_name('schema')

    def load(self, conn, schema, table, item_type='table'):
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

                        definition = None
                    else:
                        keys_rows = relations_rows = indexes_rows = []

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
                indexes_rows, definition, data_cols, data_rows,
            )
        except Exception as e:
            GLib.idle_add(self._show_error, str(e))

    def _fill_tree(self, tree, rows):
        store = tree.get_model()
        store.clear()
        for row in rows:
            store.append(['' if v is None else str(v) for v in row])

    def _populate(self, schema_rows, keys_rows, relations_rows, triggers_rows,
                  indexes_rows, definition, data_cols, data_rows):
        self._spinner.stop()

        self._fill_tree(self._schema_tree, schema_rows)
        self._fill_tree(self._keys_tree, keys_rows)
        self._fill_tree(self._relations_tree, relations_rows)
        self._fill_tree(self._triggers_tree, triggers_rows)
        self._fill_tree(self._indexes_tree, indexes_rows)

        if definition is not None:
            self._definition_buffer.set_text(definition)

        # Data tab — rebuild with dynamic columns
        store = Gtk.ListStore(*([str] * len(data_cols)))
        for row in data_rows:
            store.append(['' if v is None else str(v) for v in row])

        tree = Gtk.TreeView(model=store)
        tree.set_grid_lines(Gtk.TreeViewGridLines.BOTH)
        tree.set_enable_search(False)

        for i, name in enumerate(data_cols):
            renderer = Gtk.CellRendererText()
            renderer.set_property('ellipsize', 3)
            col = Gtk.TreeViewColumn(name, renderer, text=i)
            col.set_resizable(True)
            col.set_min_width(80)
            col.set_max_width(400)
            tree.append_column(col)

        self._data_scroll.set_child(tree)
        self._outer.set_visible_child_name('tabs')

    def _show_error(self, error_msg):
        self._spinner.stop()
        self._error_page.set_title('Failed to Load Table')
        self._error_page.set_description(error_msg)
        self._error_page.set_icon_name('dialog-error-symbolic')
        self._outer.set_visible_child_name('error')
