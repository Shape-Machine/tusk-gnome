import threading

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Gtk, Adw, GLib, Gdk
from psycopg import sql as pgsql


def _is_numeric(pg_type):
    """Return True if the postgres type name is numeric."""
    return pg_type.lower() in (
        'smallint', 'integer', 'int', 'int2', 'int4', 'int8', 'bigint',
        'real', 'float4', 'float8', 'double precision', 'numeric', 'decimal',
        'money',
    )


class ColStatsDialog(Adw.Dialog):
    """Fetches and displays statistics for a single table column."""

    def __init__(self, conn, schema, table, col_name, schema_info):
        super().__init__(
            title=f'Statistics: {col_name}',
            content_width=400,
        )
        self._conn = conn
        self._schema = schema
        self._table = table
        self._col_name = col_name
        self._pg_type = next(
            (r[1] for r in (schema_info or []) if r[0] == col_name), None
        )
        self._cancel = threading.Event()
        self._build_ui()
        self.connect('closed', lambda _: self._cancel.set())

    def _build_ui(self):
        self._header = Adw.HeaderBar()
        self._cancel_btn = Gtk.Button(label='Cancel')
        self._cancel_btn.connect('clicked', lambda _: self._cancel.set())
        self._header.pack_end(self._cancel_btn)

        self._toolbar_view = Adw.ToolbarView()
        self._toolbar_view.add_top_bar(self._header)
        self.set_child(self._toolbar_view)

        # Start with spinner; results widget is built and swapped in later
        spinner_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        spinner_box.set_valign(Gtk.Align.CENTER)
        spinner_box.set_halign(Gtk.Align.CENTER)
        spinner_box.set_margin_top(32)
        spinner_box.set_margin_bottom(32)
        spinner = Gtk.Spinner(spinning=True)
        spinner.set_size_request(32, 32)
        spinner_box.append(spinner)
        spinner_box.append(Gtk.Label(label='Fetching statistics…'))
        self._toolbar_view.set_content(spinner_box)

        threading.Thread(target=self._fetch, daemon=True).start()

    def _make_results_widget(self, results_box):
        """Wrap results_box in a scroll that caps at 75% of screen height."""
        monitor = Gdk.Display.get_default().get_monitors().get_item(0)
        max_h = int(monitor.get_geometry().height * 0.75) if monitor else 800

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_propagate_natural_height(True)
        scroll.set_max_content_height(max_h)
        clamp = Adw.Clamp(maximum_size=380)
        clamp.set_child(results_box)
        scroll.set_child(clamp)
        return scroll

    def _fetch(self):
        try:
            from tunnel import open_db
            with open_db(self._conn) as db:
                schema_id = pgsql.Identifier(self._schema)
                table_id  = pgsql.Identifier(self._table)
                col_id    = pgsql.Identifier(self._col_name)

                if self._cancel.is_set():
                    return

                with db.cursor() as cur:
                    cur.execute(
                        pgsql.SQL('''
                            SELECT
                                COUNT(*)                    AS total,
                                COUNT({col})                AS not_null,
                                COUNT(*) - COUNT({col})     AS null_count,
                                COUNT(DISTINCT {col})       AS distinct_count,
                                MIN({col}::text)            AS min_val,
                                MAX({col}::text)            AS max_val
                            FROM {schema}.{table}
                        ''').format(schema=schema_id, table=table_id, col=col_id)
                    )
                    basic = cur.fetchone()

                if self._cancel.is_set():
                    return

                numeric = None
                if self._pg_type and _is_numeric(self._pg_type):
                    with db.cursor() as cur:
                        try:
                            cur.execute(
                                pgsql.SQL('''
                                    SELECT AVG({col}), SUM({col})
                                    FROM {schema}.{table}
                                ''').format(schema=schema_id, table=table_id, col=col_id)
                            )
                            numeric = cur.fetchone()
                        except Exception:
                            db.rollback()

                if self._cancel.is_set():
                    return

                with db.cursor() as cur:
                    cur.execute(
                        pgsql.SQL('''
                            SELECT {col}::text, COUNT(*) AS freq
                            FROM {schema}.{table}
                            WHERE {col} IS NOT NULL
                            GROUP BY {col}
                            ORDER BY freq DESC
                            LIMIT 5
                        ''').format(schema=schema_id, table=table_id, col=col_id)
                    )
                    top_values = cur.fetchall()

            GLib.idle_add(self._on_data_ready, basic, numeric, top_values)
        except Exception as e:
            if not self._cancel.is_set():
                GLib.idle_add(self._on_error, str(e))

    def _on_data_ready(self, basic, numeric, top_values):
        total, not_null, null_count, distinct_count, min_val, max_val = basic
        total = total or 0

        def fmt(v):
            return str(v) if v is not None else '—'

        null_pct = f'{null_count / total * 100:.1f}%' if total else '0%'

        results_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        results_box.set_margin_top(12)
        results_box.set_margin_bottom(20)
        results_box.set_margin_start(16)
        results_box.set_margin_end(16)

        overview = Adw.PreferencesGroup(title='Overview')
        for label, value in [
            ('Total rows', f'{total:,}'),
            ('Not null',   f'{not_null:,}'),
            ('Null',       f'{null_count:,}  ({null_pct})'),
            ('Distinct',   f'{distinct_count:,}'),
        ]:
            row = Adw.ActionRow(title=label)
            row.add_suffix(Gtk.Label(label=value, css_classes=['dim-label']))
            overview.add(row)

        if min_val is not None or max_val is not None:
            for label, value in [('Min', fmt(min_val)), ('Max', fmt(max_val))]:
                row = Adw.ActionRow(title=label)
                lbl = Gtk.Label(label=value)
                lbl.set_ellipsize(3)  # PANGO_ELLIPSIZE_END
                lbl.set_max_width_chars(28)
                lbl.add_css_class('dim-label')
                row.add_suffix(lbl)
                overview.add(row)

        results_box.append(overview)

        if numeric and numeric[0] is not None:
            avg_val, sum_val = numeric
            num_group = Adw.PreferencesGroup(title='Numeric')
            for label, value in [
                ('Average', f'{float(avg_val):.4g}'),
                ('Sum',     fmt(sum_val)),
            ]:
                row = Adw.ActionRow(title=label)
                row.add_suffix(Gtk.Label(label=value, css_classes=['dim-label']))
                num_group.add(row)
            results_box.append(num_group)

        if top_values:
            top_group = Adw.PreferencesGroup(title='Top Values')
            for val, freq in top_values:
                row = Adw.ActionRow(title=val or '(empty)')
                row.add_suffix(Gtk.Label(label=f'{freq:,}', css_classes=['dim-label']))
                top_group.add(row)
            results_box.append(top_group)

        self._cancel_btn.set_visible(False)
        self._toolbar_view.set_content(self._make_results_widget(results_box))

    def _on_error(self, msg):
        error_page = Adw.StatusPage(icon_name='dialog-error-symbolic')
        error_page.set_title('Could not load statistics')
        error_page.set_description(msg)
        self._cancel_btn.set_visible(False)
        self._toolbar_view.set_content(error_page)
