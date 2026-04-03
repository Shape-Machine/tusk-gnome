import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Gtk, Adw, GObject, Gdk, Pango

_ICONS = {
    'table':    'x-office-spreadsheet-symbolic',
    'view':     'view-grid-symbolic',
    'function': 'system-run-symbolic',
    'file':     'text-x-script-symbolic',
}

_TYPE_LABELS = {
    'table':    'table',
    'view':     'view',
    'function': 'fn',
    'file':     'sql',
}

_MAX_RESULTS = 100


def _fuzzy_match(query, text):
    """Return True if every character of query appears in text in order."""
    if not query:
        return True
    text = text.lower()
    qi = 0
    for ch in text:
        if ch == query[qi]:
            qi += 1
            if qi == len(query):
                return True
    return False


class _ResultRow(Gtk.ListBoxRow):
    def __init__(self, conn, schema, name, item_type, label):
        super().__init__()
        self.conn = conn
        self.schema = schema
        self.name = name
        self.item_type = item_type

        box = Gtk.Box(spacing=10)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.set_margin_top(8)
        box.set_margin_bottom(8)

        icon = Gtk.Image.new_from_icon_name(_ICONS.get(item_type, 'dialog-question-symbolic'))
        icon.set_pixel_size(16)
        box.append(icon)

        name_label = Gtk.Label(label=label)
        name_label.set_hexpand(True)
        name_label.set_xalign(0)
        name_label.set_ellipsize(Pango.EllipsizeMode.END)
        box.append(name_label)

        type_badge = Gtk.Label(label=_TYPE_LABELS.get(item_type, item_type))
        type_badge.add_css_class('caption')
        type_badge.add_css_class('dim-label')
        box.append(type_badge)

        self.set_child(box)


class CommandPalette(Adw.Dialog):
    __gsignals__ = {
        'item-activated': (
            GObject.SignalFlags.RUN_FIRST, None,
            (GObject.TYPE_PYOBJECT, str, str, str),  # conn, schema, name, item_type
        ),
    }

    def __init__(self, items):
        """
        items: list of (conn, schema, name, item_type, label) tuples
               label is what to display (e.g. 'schema.name' or 'name(args)')
        """
        super().__init__()
        self._items = items
        self._build_ui()

    def _build_ui(self):
        self.set_title('')
        self.set_content_width(560)
        self.set_content_height(420)
        self.set_follows_content_size(False)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # Search entry
        self._entry = Gtk.SearchEntry()
        self._entry.set_placeholder_text('Jump to table, view or function…')
        self._entry.set_margin_start(12)
        self._entry.set_margin_end(12)
        self._entry.set_margin_top(12)
        self._entry.set_margin_bottom(8)
        self._entry.connect('search-changed', self._on_search_changed)
        self._entry.connect('stop-search', lambda _: self.close())
        outer.append(self._entry)

        outer.append(Gtk.Separator())

        # Results list
        self._listbox = Gtk.ListBox()
        self._listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._listbox.add_css_class('navigation-sidebar')
        self._listbox.connect('row-activated', self._on_row_activated)

        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_child(self._listbox)
        outer.append(scroll)

        # Empty state label
        self._empty_label = Gtk.Label(label='No results')
        self._empty_label.add_css_class('dim-label')
        self._empty_label.set_vexpand(True)
        self._empty_label.set_valign(Gtk.Align.CENTER)
        self._empty_label.set_visible(False)
        outer.append(self._empty_label)

        # Key controller for arrow navigation + Enter + Escape
        key_ctrl = Gtk.EventControllerKey()
        key_ctrl.connect('key-pressed', self._on_key_pressed)
        outer.add_controller(key_ctrl)

        self.set_child(outer)

        # Initial population (all items)
        self._populate('')

        # Select first row
        first = self._listbox.get_row_at_index(0)
        if first:
            self._listbox.select_row(first)

        # Focus entry after dialog is mapped
        self._entry.grab_focus()

    def _populate(self, query):
        # Clear existing rows
        while (row := self._listbox.get_row_at_index(0)):
            self._listbox.remove(row)

        query = query.strip().lower()
        count = 0
        for conn, schema, name, item_type, label in self._items:
            if _fuzzy_match(query, label.lower()):
                self._listbox.append(_ResultRow(conn, schema, name, item_type, label))
                count += 1
                if count >= _MAX_RESULTS:
                    break

        has_results = count > 0
        self._listbox.set_visible(has_results)
        self._empty_label.set_visible(not has_results)

        if has_results:
            first = self._listbox.get_row_at_index(0)
            if first:
                self._listbox.select_row(first)

    def _on_search_changed(self, entry):
        self._populate(entry.get_text())

    def _on_row_activated(self, _listbox, row):
        if isinstance(row, _ResultRow):
            self.emit('item-activated', row.conn, row.schema, row.name, row.item_type)
            self.close()

    def _on_key_pressed(self, _ctrl, keyval, _code, _state):
        if keyval == Gdk.KEY_Escape:
            self.close()
            return True

        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            row = self._listbox.get_selected_row()
            if row:
                self._on_row_activated(self._listbox, row)
            return True

        if keyval == Gdk.KEY_Down:
            selected = self._listbox.get_selected_row()
            if selected:
                nxt = self._listbox.get_row_at_index(selected.get_index() + 1)
                if nxt:
                    self._listbox.select_row(nxt)
                    nxt.grab_focus()
                    self._entry.grab_focus()
            return True

        if keyval == Gdk.KEY_Up:
            selected = self._listbox.get_selected_row()
            if selected and selected.get_index() > 0:
                prev = self._listbox.get_row_at_index(selected.get_index() - 1)
                if prev:
                    self._listbox.select_row(prev)
                    self._entry.grab_focus()
            return True

        return False
