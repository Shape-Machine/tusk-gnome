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


def show_col_stats(parent_widget, conn, schema, table, col_name, schema_info):
    """Fetch column statistics in the background, then present the dialog.

    The dialog is only presented once data is ready so that Adw.Dialog can
    size itself to the full content in a single pass.  A toast on the parent
    window gives feedback while the fetch is in progress.
    """
    pg_type = next((r[1] for r in (schema_info or []) if r[0] == col_name), None)
    cancel  = threading.Event()

    # Show a non-intrusive toast so the user knows something is happening
    root = parent_widget.get_root()
    toast = Adw.Toast(title=f'Loading statistics for {col_name}…')
    toast.set_timeout(0)   # keep until dismissed
    toast_overlay = _find_toast_overlay(root)
    if toast_overlay:
        toast_overlay.add_toast(toast)

    def fetch():
        try:
            from tunnel import open_db
            with open_db(conn) as db:
                schema_id = pgsql.Identifier(schema)
                table_id  = pgsql.Identifier(table)
                col_id    = pgsql.Identifier(col_name)

                if cancel.is_set():
                    return

                with db.cursor() as cur:
                    cur.execute(
                        pgsql.SQL('''
                            SELECT
                                COUNT(*)                AS total,
                                COUNT({col})            AS not_null,
                                COUNT(*) - COUNT({col}) AS null_count,
                                COUNT(DISTINCT {col})   AS distinct_count,
                                MIN({col}::text)        AS min_val,
                                MAX({col}::text)        AS max_val
                            FROM {schema}.{table}
                        ''').format(schema=schema_id, table=table_id, col=col_id)
                    )
                    basic = cur.fetchone()

                if cancel.is_set():
                    return

                numeric = None
                if pg_type and _is_numeric(pg_type):
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

                if cancel.is_set():
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

            if not cancel.is_set():
                GLib.idle_add(_present_results,
                              parent_widget, col_name, basic, numeric, top_values, toast)
        except Exception as e:
            if not cancel.is_set():
                GLib.idle_add(_present_error, parent_widget, col_name, str(e), toast)

    threading.Thread(target=fetch, daemon=True).start()
    return cancel   # caller can set() this to abort


def _find_toast_overlay(widget):
    """Walk up the widget tree to find an Adw.ToastOverlay."""
    w = widget
    while w:
        if isinstance(w, Adw.ToastOverlay):
            return w
        w = w.get_parent()
    return None


def _make_scroll(child):
    monitor = Gdk.Display.get_default().get_monitors().get_item(0)
    max_h = int(monitor.get_geometry().height * 0.75) if monitor else 800
    scroll = Gtk.ScrolledWindow()
    scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    scroll.set_propagate_natural_height(True)
    scroll.set_max_content_height(max_h)
    clamp = Adw.Clamp(maximum_size=380)
    clamp.set_child(child)
    scroll.set_child(clamp)
    return scroll


def _present_results(parent_widget, col_name, basic, numeric, top_values, toast):
    toast.dismiss()

    total, not_null, null_count, distinct_count, min_val, max_val = basic
    total = total or 0

    def fmt(v):
        return str(v) if v is not None else '—'

    null_pct = f'{null_count / total * 100:.1f}%' if total else '0%'

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
    box.set_margin_top(12)
    box.set_margin_bottom(20)
    box.set_margin_start(16)
    box.set_margin_end(16)

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
            lbl.set_ellipsize(3)
            lbl.set_max_width_chars(28)
            lbl.add_css_class('dim-label')
            row.add_suffix(lbl)
            overview.add(row)

    box.append(overview)

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
        box.append(num_group)

    if top_values:
        top_group = Adw.PreferencesGroup(title='Top Values')
        for val, freq in top_values:
            row = Adw.ActionRow(title=val or '(empty)')
            row.add_suffix(Gtk.Label(label=f'{freq:,}', css_classes=['dim-label']))
            top_group.add(row)
        box.append(top_group)

    header = Adw.HeaderBar()
    toolbar_view = Adw.ToolbarView()
    toolbar_view.add_top_bar(header)
    toolbar_view.set_content(_make_scroll(box))

    dlg = Adw.Dialog(title=f'Statistics: {col_name}', content_width=400)
    dlg.set_child(toolbar_view)
    dlg.present(parent_widget)


def _present_error(parent_widget, col_name, msg, toast):
    toast.dismiss()

    error_page = Adw.StatusPage(icon_name='dialog-error-symbolic')
    error_page.set_title('Could not load statistics')
    error_page.set_description(msg)

    header = Adw.HeaderBar()
    toolbar_view = Adw.ToolbarView()
    toolbar_view.add_top_bar(header)
    toolbar_view.set_content(error_page)

    dlg = Adw.Dialog(title=f'Statistics: {col_name}', content_width=400)
    dlg.set_child(toolbar_view)
    dlg.present(parent_widget)
