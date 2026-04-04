import csv
import io
import json
import threading

import gi

gi.require_version('Gtk', '4.0')

from gi.repository import Gtk, Gio, GObject, Pango, Gdk, GLib


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


def update_column_view(col_view, rows):
    """Replace the data in an existing ColumnView without recreating it.

    Preserves column widths and other per-widget state.
    Works with both plain Gtk.ColumnView and PinColumnView.
    """
    if isinstance(col_view, PinColumnView):
        col_view.update_rows(rows)
        return
    # Model chain: MultiSelection → SortListModel → ListStore
    store = col_view.get_model().get_model().get_model()
    store.remove_all()
    for row in rows:
        store.append(_Row(list(row)))


def make_column_view(columns, rows, table_name=None):
    store = Gio.ListStore(item_type=_Row)
    for row in rows:
        store.append(_Row(list(row)))

    col_view = Gtk.ColumnView()
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
                label.set_markup('<i>null</i>')
                label.add_css_class('dim-label')
                label.set_tooltip_text('NULL')
            else:
                text = list_item.get_item().get(col_idx)
                label.set_text(text)
                label.remove_css_class('dim-label')
                label.set_tooltip_text(text if len(text) > 40 else None)

        factory.connect('setup', on_setup)
        factory.connect('bind', on_bind)

        col = Gtk.ColumnViewColumn(title=name, factory=factory)
        col.set_resizable(True)
        col.set_expand(True)

        def _cmp(a, b, *_, idx=i):
            ra, rb = a.raw(idx), b.raw(idx)
            if ra is None and rb is None:
                return 0
            if ra is None:
                return -1
            if rb is None:
                return 1
            if isinstance(ra, (int, float)) and isinstance(rb, (int, float)):
                return (ra > rb) - (ra < rb)
            sa, sb = a.get(idx), b.get(idx)
            return (sa > sb) - (sa < sb)
        sorter = Gtk.CustomSorter.new(_cmp)
        col.set_sorter(sorter)

        col_view.append_column(col)

    sort_model = Gtk.SortListModel(model=store, sorter=col_view.get_sorter())
    selection = Gtk.MultiSelection(model=sort_model)
    col_view.set_model(selection)

    # ── Context menu ─────────────────────────────────────────────────────────

    def get_selected_rows():
        bitset = selection.get_selection()
        result = []
        valid, it, pos = Gtk.BitsetIter.init_first(bitset)
        while valid:
            result.append(sort_model.get_item(pos))
            valid, pos = Gtk.BitsetIter.next(it)
        return result

    def get_all_rows():
        return [sort_model.get_item(i) for i in range(sort_model.get_n_items())]

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

    def _save_to_file(fmt):
        ext = 'sql' if fmt == 'sql' else fmt
        dialog = Gtk.FileDialog()
        dialog.set_initial_name(f'export.{ext}')
        def _on_save(d, result):
            try:
                gfile = d.save_finish(result)
            except Exception:
                return  # user cancelled
            def _write():
                try:
                    if fmt == 'csv':
                        text = _to_csv(columns, get_all_rows())
                    elif fmt == 'json':
                        text = _to_json(columns, get_all_rows())
                    else:
                        text = _to_insert_sql(columns, get_all_rows(), table_name)
                    gfile.replace_contents(
                        text.encode(), None, False,
                        Gio.FileCreateFlags.REPLACE_DESTINATION, None,
                    )
                except Exception as e:
                    GLib.idle_add(_show_export_error, str(e))
            threading.Thread(target=_write, daemon=True).start()
        dialog.save(col_view.get_root(), None, _on_save)

    def _show_export_error(msg):
        alert = Gtk.AlertDialog(message='Export failed', detail=msg)
        alert.show(col_view.get_root())

    make_action('export-csv',  lambda *_: _save_to_file('csv'))
    make_action('export-json', lambda *_: _save_to_file('json'))
    if table_name:
        make_action('export-sql', lambda *_: _save_to_file('sql'))

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
    all_section.append('Copy all as CSV',   'copy.all-csv')
    all_section.append('Copy all as JSON',  'copy.all-json')
    if table_name:
        all_section.append('Copy all as INSERT SQL', 'copy.all-sql')
    all_section.append('Export page as CSV…',  'copy.export-csv')
    all_section.append('Export page as JSON…', 'copy.export-json')
    if table_name:
        all_section.append('Export page as INSERT SQL…', 'copy.export-sql')

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


# ── PinColumnView ─────────────────────────────────────────────────────────────

class PinColumnView(Gtk.Box):
    """A ColumnView wrapper that supports pinning (freezing) columns on the left.

    Pinned columns are shown in a separate non-scrolling ColumnView on the left;
    unpinned columns are shown in the main horizontally-scrollable ColumnView on
    the right.  Both views share the same underlying SortListModel so data and
    sort order stay in sync.  Vertical scrolling is synchronised via a shared
    vadjustment.
    """

    __gtype_name__ = 'TuskPinColumnView'

    __gsignals__ = {
        'activate':    (GObject.SignalFlags.RUN_FIRST, None, (int,)),
        'cell-edited': (GObject.SignalFlags.RUN_FIRST, None,
                        (GObject.TYPE_PYOBJECT, int, GObject.TYPE_PYOBJECT)),
        # (row_item, col_idx, new_value)
    }

    def __init__(self, columns, rows, table_name=None):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL)
        self._columns = list(columns)
        self._table_name = table_name
        self._pinned = []   # ordered list of original column indices that are pinned
        self._inline_edit = False
        self._boolean_cols = set()  # col names with data_type == 'boolean'

        # Underlying store shared by both views
        self._store = Gio.ListStore(item_type=_Row)
        for row in rows:
            self._store.append(_Row(list(row)))

        # Main (scrollable) ColumnView
        self._main_cv = Gtk.ColumnView()
        self._main_cv.set_show_row_separators(True)
        self._main_cv.set_show_column_separators(True)
        self._main_cv.set_hexpand(True)

        self._sort_model = Gtk.SortListModel(model=self._store, sorter=self._main_cv.get_sorter())
        self._selection = Gtk.MultiSelection(model=self._sort_model)
        self._main_cv.set_model(self._selection)
        self._main_cv.connect('activate', lambda _cv, pos: self.emit('activate', pos))

        # Pinned (frozen) ColumnView — uses NoSelection, same sort model
        self._pin_cv = Gtk.ColumnView()
        self._pin_cv.set_show_row_separators(True)
        self._pin_cv.set_show_column_separators(True)
        self._pin_cv.set_model(Gtk.NoSelection(model=self._sort_model))

        # Scroll areas
        self._main_scroll = Gtk.ScrolledWindow()
        self._main_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self._main_scroll.set_vexpand(True)
        self._main_scroll.set_hexpand(True)
        self._main_scroll.set_child(self._main_cv)

        self._pin_scroll = Gtk.ScrolledWindow()
        self._pin_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.EXTERNAL)
        self._pin_scroll.set_child(self._pin_cv)
        self._pin_scroll.set_visible(False)

        # Share vadjustment so both views scroll vertically together
        self._pin_scroll.set_vadjustment(self._main_scroll.get_vadjustment())

        # Vertical separator between pin and main (only shown when columns are pinned)
        self._sep = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        self._sep.set_visible(False)

        # Action group for pin/unpin actions (parametrised by column index)
        ag = Gio.SimpleActionGroup()
        pin_act = Gio.SimpleAction.new('pin', GLib.VariantType.new('i'))
        pin_act.connect('activate', lambda _a, p: self._pin_column(p.unpack()))
        ag.add_action(pin_act)
        unpin_act = Gio.SimpleAction.new('unpin', GLib.VariantType.new('i'))
        unpin_act.connect('activate', lambda _a, p: self._unpin_column(p.unpack()))
        ag.add_action(unpin_act)
        self._main_cv.insert_action_group('pincol', ag)
        self._pin_cv.insert_action_group('pincol', ag)

        self.append(self._pin_scroll)
        self.append(self._sep)
        self.append(self._main_scroll)

        # Context menu (copy cell / selection / all) — lives on main_cv only
        self._right_clicked_cell = [None]
        self._cell_clicked = [False]
        self._attach_context_menu(table_name)

        # Build initial columns (all unpinned)
        self._rebuild_columns()

    # ── Public interface ──────────────────────────────────────────────────────

    def get_model(self):
        """Return the MultiSelection model (for selection-changed connections)."""
        return self._selection

    def update_rows(self, rows):
        """Replace all rows without recreating columns."""
        self._store.remove_all()
        for row in rows:
            self._store.append(_Row(list(row)))

    def enable_inline_edit(self, schema_info):
        """Enable double-click inline editing.

        schema_info is [(col_name, data_type, is_nullable, default_val)].
        Call immediately after construction; rebuilds columns so cells pick up
        the double-click gesture.
        """
        self._inline_edit = True
        self._boolean_cols = {r[0] for r in schema_info if r[1] == 'boolean'}
        # No rebuild needed — factories read _inline_edit lazily when cells
        # are first created, which hasn't happened yet at this call site.

    # ── Inline edit ───────────────────────────────────────────────────────────

    def _activate_inline_edit(self, label, col_idx):
        """Called on double-click of a data cell when inline editing is enabled."""
        row_item = getattr(label, '_row_item', None)
        if row_item is None:
            return
        raw = getattr(label, '_raw_value', None)
        col_name = self._columns[col_idx]

        if col_name in self._boolean_cols:
            # Toggle boolean immediately — no popover needed
            new_value = not bool(raw)
            self.emit('cell-edited', row_item, col_idx, new_value)
            return

        # Text entry popover for all other types
        entry = Gtk.Entry()
        entry.set_text('' if raw is None else str(raw))
        entry.set_width_chars(max(24, len(str(raw)) if raw is not None else 0))

        # Parent the popover to self (the PinColumnView box), not to the
        # recycled cell label — parenting to a factory cell causes GTK CSS
        # node assertion failures.  Translate coordinates to self's space.
        popover = Gtk.Popover()
        popover.set_has_arrow(True)
        popover.set_parent(self)
        coords = label.translate_coordinates(self, 0, 0)
        if coords:
            tx, ty = coords
            rect = Gdk.Rectangle()
            rect.x, rect.y = tx, ty
            rect.width, rect.height = label.get_width(), label.get_height()
            popover.set_pointing_to(rect)
        popover.set_child(entry)

        committed = [False]
        cancelled = [False]

        def _commit():
            if committed[0]:
                return
            committed[0] = True
            text = entry.get_text()
            new_value = None if text == '' else text
            popover.popdown()
            self.emit('cell-edited', row_item, col_idx, new_value)

        def _cancel():
            cancelled[0] = True
            committed[0] = True  # prevent focus-out from committing
            popover.popdown()

        # Enter via the Entry's own activate signal (most reliable in GTK 4)
        entry.connect('activate', lambda _e: _commit())

        # Escape — use CAPTURE phase so the popover doesn't consume it first
        esc_ctrl = Gtk.EventControllerKey()
        esc_ctrl.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        def _on_key_pressed(_ctrl, keyval, _code, _state):
            if keyval == Gdk.KEY_Escape:
                _cancel()
                return True
            return False
        esc_ctrl.connect('key-pressed', _on_key_pressed)
        entry.add_controller(esc_ctrl)

        # Blur / focus-out → commit (clicking away saves the edit)
        focus_ctrl = Gtk.EventControllerFocus()
        focus_ctrl.connect('leave', lambda _: _commit())
        entry.add_controller(focus_ctrl)

        popover.popup()
        # Defer grab_focus until the popover is mapped
        GLib.idle_add(entry.grab_focus)

    # ── Column management ─────────────────────────────────────────────────────

    def _pin_column(self, col_idx):
        if col_idx not in self._pinned:
            self._pinned.append(col_idx)
            self._rebuild_columns()

    def _unpin_column(self, col_idx):
        if col_idx in self._pinned:
            self._pinned.remove(col_idx)
            self._rebuild_columns()

    def _rebuild_columns(self):
        # Snapshot active sort state before tearing down columns
        sort_col_idx = None
        sort_type = Gtk.SortType.ASCENDING
        sorter = self._main_cv.get_sorter()
        if hasattr(sorter, 'get_n_sort_columns') and sorter.get_n_sort_columns() > 0:
            old_col, sort_type = sorter.get_nth_sort_column(0)
            sort_col_idx = getattr(old_col, '_col_idx', None)

        # Clear both views
        while self._pin_cv.get_columns().get_n_items():
            self._pin_cv.remove_column(self._pin_cv.get_columns().get_item(0))
        while self._main_cv.get_columns().get_n_items():
            self._main_cv.remove_column(self._main_cv.get_columns().get_item(0))

        # Add pinned columns to pin_cv (in pin order)
        for col_idx in self._pinned:
            col = self._build_column(col_idx, pinned=True)
            self._pin_cv.append_column(col)

        # Add unpinned columns to main_cv (in original order)
        for col_idx, name in enumerate(self._columns):
            if col_idx not in self._pinned:
                col = self._build_column(col_idx, pinned=False)
                self._main_cv.append_column(col)

        has_pinned = bool(self._pinned)
        self._pin_scroll.set_visible(has_pinned)
        self._sep.set_visible(has_pinned)

        # Restore sort state on the rebuilt column (if it's still unpinned)
        if sort_col_idx is not None:
            cols = self._main_cv.get_columns()
            for i in range(cols.get_n_items()):
                c = cols.get_item(i)
                if getattr(c, '_col_idx', None) == sort_col_idx:
                    self._main_cv.sort_by_column(c, sort_type)
                    break

    def _build_column(self, col_idx, pinned):
        name = self._columns[col_idx]
        factory = Gtk.SignalListItemFactory()

        def on_setup(_f, item):
            label = Gtk.Label()
            label.set_xalign(0)
            label.set_ellipsize(Pango.EllipsizeMode.END)
            label.set_max_width_chars(40)
            cell_gesture = Gtk.GestureClick(button=3)
            def _on_cell_rclick(_g, _n, _x, _y, lbl=label):
                self._right_clicked_cell[0] = getattr(lbl, '_raw_value', None)
                self._cell_clicked[0] = True
            cell_gesture.connect('pressed', _on_cell_rclick)
            label.add_controller(cell_gesture)
            if self._inline_edit:
                dbl_click = Gtk.GestureClick(button=1)
                def _on_dbl_click(_g, n_press, _x, _y, lbl=label, cidx=col_idx):
                    if n_press == 2:
                        self._activate_inline_edit(lbl, cidx)
                dbl_click.connect('pressed', _on_dbl_click)
                label.add_controller(dbl_click)
            item.set_child(label)

        def on_bind(_f, item, idx=col_idx):
            label = item.get_child()
            row = item.get_item()
            raw = row.raw(idx)
            label._raw_value = raw
            label._row_item = row
            if raw is None:
                label.set_markup('<i>null</i>')
                label.add_css_class('dim-label')
                label.set_tooltip_text('NULL')
            else:
                text = row.get(idx)
                label.set_text(text)
                label.remove_css_class('dim-label')
                label.set_tooltip_text(text if len(text) > 40 else None)

        factory.connect('setup', on_setup)
        factory.connect('bind', on_bind)

        col = Gtk.ColumnViewColumn(title=name, factory=factory)
        col.set_resizable(True)
        col.set_expand(not pinned)
        col._col_idx = col_idx

        def _cmp(a, b, *_, idx=col_idx):
            ra, rb = a.raw(idx), b.raw(idx)
            if ra is None and rb is None:
                return 0
            if ra is None:
                return -1
            if rb is None:
                return 1
            if isinstance(ra, (int, float)) and isinstance(rb, (int, float)):
                return (ra > rb) - (ra < rb)
            return (a.get(idx) > b.get(idx)) - (a.get(idx) < b.get(idx))

        col.set_sorter(Gtk.CustomSorter.new(_cmp))

        # Header menu
        header_menu = Gio.Menu()
        item = Gio.MenuItem.new('Unpin Column' if pinned else 'Pin Column', None)
        action = 'pincol.unpin' if pinned else 'pincol.pin'
        item.set_action_and_target_value(action, GLib.Variant('i', col_idx))
        header_menu.append_item(item)
        col.set_header_menu(header_menu)

        return col

    # ── Context menu (on main_cv) ─────────────────────────────────────────────

    def _attach_context_menu(self, table_name):
        columns = self._columns

        def get_selected_rows():
            bitset = self._selection.get_selection()
            result = []
            valid, it, pos = Gtk.BitsetIter.init_first(bitset)
            while valid:
                result.append(self._sort_model.get_item(pos))
                valid, pos = Gtk.BitsetIter.next(it)
            return result

        def get_all_rows():
            return [self._sort_model.get_item(i)
                    for i in range(self._sort_model.get_n_items())]

        ag = Gio.SimpleActionGroup()

        def make_action(name, handler):
            action = Gio.SimpleAction.new(name, None)
            action.connect('activate', handler)
            ag.add_action(action)
            return action

        def _copy_cell():
            v = self._right_clicked_cell[0]
            _copy_to_clipboard('' if v is None else str(v))

        cell_action = make_action('cell', lambda *_: _copy_cell())
        cell_action.set_enabled(False)
        sel_csv  = make_action('sel-csv',  lambda *_: _copy_to_clipboard(_to_csv(columns, get_selected_rows())))
        sel_json = make_action('sel-json', lambda *_: _copy_to_clipboard(_to_json(columns, get_selected_rows())))
        all_csv  = make_action('all-csv',  lambda *_: _copy_to_clipboard(_to_csv(columns, get_all_rows())))
        all_json = make_action('all-json', lambda *_: _copy_to_clipboard(_to_json(columns, get_all_rows())))

        def _save_to_file(fmt):
            ext = 'sql' if fmt == 'sql' else fmt
            dialog = Gtk.FileDialog()
            dialog.set_initial_name(f'export.{ext}')
            def _on_save(d, result):
                try:
                    gfile = d.save_finish(result)
                except Exception:
                    return
                def _write():
                    try:
                        if fmt == 'csv':
                            text = _to_csv(columns, get_all_rows())
                        elif fmt == 'json':
                            text = _to_json(columns, get_all_rows())
                        else:
                            text = _to_insert_sql(columns, get_all_rows(), table_name)
                        gfile.replace_contents(
                            text.encode(), None, False,
                            Gio.FileCreateFlags.REPLACE_DESTINATION, None,
                        )
                    except Exception as e:
                        GLib.idle_add(_show_export_error, str(e))
                threading.Thread(target=_write, daemon=True).start()
            dialog.save(self.get_root(), None, _on_save)

        def _show_export_error(msg):
            alert = Gtk.AlertDialog(message='Export failed', detail=msg)
            alert.show(self.get_root())

        make_action('export-csv',  lambda *_: _save_to_file('csv'))
        make_action('export-json', lambda *_: _save_to_file('json'))
        if table_name:
            make_action('export-sql', lambda *_: _save_to_file('sql'))

        sel_actions = [sel_csv, sel_json]
        if table_name:
            sel_sql = make_action('sel-sql', lambda *_: _copy_to_clipboard(
                _to_insert_sql(columns, get_selected_rows(), table_name)))
            make_action('all-sql', lambda *_: _copy_to_clipboard(
                _to_insert_sql(columns, get_all_rows(), table_name)))
            sel_actions.append(sel_sql)

        for a in sel_actions:
            a.set_enabled(False)

        self._selection.connect('selection-changed', lambda _s, _p, _n: (
            [a.set_enabled(self._selection.get_selection().get_size() > 0)
             for a in sel_actions]
        ))

        self._main_cv.insert_action_group('copy', ag)

        cell_section = Gio.Menu()
        cell_section.append('Copy cell value', 'copy.cell')
        selected_section = Gio.Menu()
        selected_section.append('Copy selected as CSV',  'copy.sel-csv')
        selected_section.append('Copy selected as JSON', 'copy.sel-json')
        if table_name:
            selected_section.append('Copy selected as INSERT SQL', 'copy.sel-sql')
        all_section = Gio.Menu()
        all_section.append('Copy all as CSV',   'copy.all-csv')
        all_section.append('Copy all as JSON',  'copy.all-json')
        if table_name:
            all_section.append('Copy all as INSERT SQL', 'copy.all-sql')
        all_section.append('Export page as CSV…',  'copy.export-csv')
        all_section.append('Export page as JSON…', 'copy.export-json')
        if table_name:
            all_section.append('Export page as INSERT SQL…', 'copy.export-sql')

        menu = Gio.Menu()
        menu.append_section(None, cell_section)
        menu.append_section(None, selected_section)
        menu.append_section(None, all_section)

        def _popup_menu(popover, x, y):
            cell_action.set_enabled(self._cell_clicked[0])
            self._cell_clicked[0] = False
            rect = Gdk.Rectangle()
            rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
            popover.set_pointing_to(rect)
            popover.popup()

        for cv in (self._main_cv, self._pin_cv):
            popover = Gtk.PopoverMenu(menu_model=menu)
            popover.set_has_arrow(False)
            popover.set_parent(cv)
            gesture = Gtk.GestureClick(button=3)
            gesture.connect('pressed', lambda _g, _n, x, y, p=popover: _popup_menu(p, x, y))
            cv.add_controller(gesture)


def make_pinnable_column_view(columns, rows, table_name=None):
    """Create a PinColumnView — a data grid with column-pinning support."""
    return PinColumnView(columns, rows, table_name=table_name)
