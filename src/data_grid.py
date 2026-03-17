import csv
import io
import json

import gi

gi.require_version('Gtk', '4.0')

from gi.repository import Gtk, Gio, GObject, Pango, Gdk


class _Row(GObject.Object):
    __gtype_name__ = 'TuskRow'

    def __init__(self, raw_values):
        super().__init__()
        self._raw = raw_values
        self._display = ['' if v is None else str(v) for v in raw_values]

    def get(self, i):
        """Display string for label rendering."""
        return self._display[i]

    def raw(self, i):
        """Original Python value (preserves None and types)."""
        return self._raw[i]

    def values(self):
        return self._display


def _to_csv(columns, rows):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(columns)
    w.writerows([r.values() for r in rows])
    return buf.getvalue()


def _to_json(columns, rows):
    return json.dumps(
        [{col: row.raw(i) for i, col in enumerate(columns)} for row in rows],
        indent=2,
        default=str,
    )


def _quote_ident(name):
    return '"' + name.replace('"', '""') + '"'


def _sql_value(v):
    if v is None:
        return 'NULL'
    if isinstance(v, bool):
        return 'TRUE' if v else 'FALSE'
    if isinstance(v, (int, float)):
        return str(v)
    return "'" + str(v).replace("'", "''") + "'"


def _to_insert_sql(columns, rows, table_name):
    quoted_table = '.'.join(_quote_ident(p) for p in table_name.split('.'))
    cols = ', '.join(_quote_ident(c) for c in columns)
    lines = []
    for row in rows:
        vals = ', '.join(_sql_value(row.raw(i)) for i in range(len(columns)))
        lines.append(f'INSERT INTO {quoted_table} ({cols}) VALUES ({vals});')
    return '\n'.join(lines)


def _copy_to_clipboard(text):
    Gdk.Display.get_default().get_clipboard().set(text)


def make_column_view(columns, rows, table_name=None):
    store = Gio.ListStore(item_type=_Row)
    for row in rows:
        store.append(_Row(list(row)))

    selection = Gtk.MultiSelection(model=store)
    col_view = Gtk.ColumnView(model=selection)
    col_view.set_show_row_separators(True)
    col_view.set_show_column_separators(True)
    col_view.set_hexpand(True)

    _right_clicked_cell = [None]
    _cell_clicked = [False]

    for i, name in enumerate(columns):
        factory = Gtk.SignalListItemFactory()

        def on_setup(_factory, list_item):
            label = Gtk.Label()
            label.set_xalign(0)
            label.set_ellipsize(Pango.EllipsizeMode.END)
            label.set_max_width_chars(40)
            cell_gesture = Gtk.GestureClick(button=3)
            def _on_cell_rclick(_g, _n, _x, _y, lbl=label):
                _right_clicked_cell[0] = getattr(lbl, '_raw_value', None)
                _cell_clicked[0] = True
            cell_gesture.connect('pressed', _on_cell_rclick)
            label.add_controller(cell_gesture)
            list_item.set_child(label)

        def on_bind(_factory, list_item, col_idx=i):
            label = list_item.get_child()
            raw = list_item.get_item().raw(col_idx)
            label._raw_value = raw
            if raw is None:
                label.set_text('NULL')
                label.add_css_class('dim-label')
            else:
                label.set_text(list_item.get_item().get(col_idx))
                label.remove_css_class('dim-label')

        factory.connect('setup', on_setup)
        factory.connect('bind', on_bind)

        col = Gtk.ColumnViewColumn(title=name, factory=factory)
        col.set_resizable(True)
        col.set_expand(True)
        col_view.append_column(col)

    # ── Context menu ─────────────────────────────────────────────────────────

    def get_selected_rows():
        bitset = selection.get_selection()
        result = []
        valid, pos, _ = Gtk.BitsetIter.init_first(bitset)
        while valid:
            result.append(store.get_item(pos))
            valid, pos = Gtk.BitsetIter.next(_)
        return result

    def get_all_rows():
        return [store.get_item(i) for i in range(store.get_n_items())]

    ag = Gio.SimpleActionGroup()

    def make_action(name, handler):
        action = Gio.SimpleAction.new(name, None)
        action.connect('activate', handler)
        ag.add_action(action)
        return action

    def _copy_cell():
        v = _right_clicked_cell[0]
        _copy_to_clipboard('' if v is None else str(v))

    cell_action = make_action('cell', lambda *_: _copy_cell())
    cell_action.set_enabled(False)
    sel_csv  = make_action('sel-csv',  lambda *_: _copy_to_clipboard(_to_csv(columns, get_selected_rows())))
    sel_json = make_action('sel-json', lambda *_: _copy_to_clipboard(_to_json(columns, get_selected_rows())))
    all_csv  = make_action('all-csv',  lambda *_: _copy_to_clipboard(_to_csv(columns, get_all_rows())))
    all_json = make_action('all-json', lambda *_: _copy_to_clipboard(_to_json(columns, get_all_rows())))

    sel_actions = [sel_csv, sel_json]

    if table_name:
        sel_sql = make_action('sel-sql', lambda *_: _copy_to_clipboard(_to_insert_sql(columns, get_selected_rows(), table_name)))
        all_sql = make_action('all-sql', lambda *_: _copy_to_clipboard(_to_insert_sql(columns, get_all_rows(), table_name)))
        sel_actions.append(sel_sql)

    for a in sel_actions:
        a.set_enabled(False)

    def on_selection_changed(_sel, _pos, _n):
        has_sel = selection.get_selection().get_size() > 0
        for a in sel_actions:
            a.set_enabled(has_sel)

    selection.connect('selection-changed', on_selection_changed)

    col_view.insert_action_group('copy', ag)

    # Build menu model
    cell_section = Gio.Menu()
    cell_section.append('Copy cell value', 'copy.cell')

    selected_section = Gio.Menu()
    selected_section.append('Copy selected as CSV',  'copy.sel-csv')
    selected_section.append('Copy selected as JSON', 'copy.sel-json')
    if table_name:
        selected_section.append('Copy selected as INSERT SQL', 'copy.sel-sql')

    all_section = Gio.Menu()
    all_section.append('Copy all as CSV',  'copy.all-csv')
    all_section.append('Copy all as JSON', 'copy.all-json')
    if table_name:
        all_section.append('Copy all as INSERT SQL', 'copy.all-sql')

    menu = Gio.Menu()
    menu.append_section(None, cell_section)
    menu.append_section(None, selected_section)
    menu.append_section(None, all_section)

    popover = Gtk.PopoverMenu(menu_model=menu)
    popover.set_has_arrow(False)
    popover.set_parent(col_view)

    def on_right_click(_gesture, _n, x, y):
        cell_action.set_enabled(_cell_clicked[0])
        _cell_clicked[0] = False
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        popover.set_pointing_to(rect)
        popover.popup()

    gesture = Gtk.GestureClick(button=3)
    gesture.connect('pressed', on_right_click)
    col_view.add_controller(gesture)

    return col_view
