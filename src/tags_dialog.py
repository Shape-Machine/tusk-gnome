import re

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Gtk, Adw, GObject, GLib, Gdk

_COLOR_RE = re.compile(r'^#[0-9a-fA-F]{6}$')
_DEFAULT_COLOR = '#aaaaaa'

_PRESET_COLORS = [
    '#e74c3c',  # Red
    '#e67e22',  # Orange
    '#f1c40f',  # Yellow
    '#2ecc71',  # Green
    '#1abc9c',  # Teal
    '#3498db',  # Blue
    '#9b59b6',  # Purple
    '#e91e63',  # Pink
    '#607d8b',  # Blue-grey
    '#795548',  # Brown
    '#aaaaaa',  # Grey
    '#4285f4',  # GCP Blue
]

_PALETTE_CSS = """
.swatch-selected {
    outline: 3px solid @accent_bg_color;
    outline-offset: 1px;
    border-radius: 50%;
}
"""


class TagsDialog(Adw.Dialog):
    __gsignals__ = {
        'tags-changed': (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, store):
        super().__init__(title='Manage Tags', content_width=480, content_height=560)
        self._store = store
        self._css_provider = Gtk.CssProvider()
        self._css_provider.load_from_string(_PALETTE_CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            self._css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )
        self._build_ui()
        self._load_tags()

    def _build_ui(self):
        header = Adw.HeaderBar()

        self._list_box = Gtk.ListBox()
        self._list_box.add_css_class('boxed-list')
        self._list_box.set_selection_mode(Gtk.SelectionMode.NONE)

        add_btn = Gtk.Button(label='Add Tag')
        add_btn.set_icon_name('list-add-symbolic')
        add_btn.add_css_class('suggested-action')
        add_btn.add_css_class('pill')
        add_btn.set_halign(Gtk.Align.CENTER)
        add_btn.connect('clicked', self._on_add_tag)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        box.set_margin_top(16)
        box.set_margin_bottom(20)
        box.set_margin_start(20)
        box.set_margin_end(20)
        box.append(self._list_box)
        box.append(add_btn)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_propagate_natural_height(True)
        scroll.set_child(box)

        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(header)
        toolbar_view.set_content(scroll)
        self.set_child(toolbar_view)

    def _load_tags(self):
        while child := self._list_box.get_first_child():
            self._list_box.remove(child)
        registry = self._store.get_tags_registry()
        for name in sorted(registry):
            meta = registry[name]
            self._list_box.append(self._build_tag_row(name, meta))

    def _build_tag_row(self, name, meta):
        color = meta.get('color', _DEFAULT_COLOR)
        expander = Adw.ExpanderRow(title=name)
        expander._tag_name = name
        expander._selected_color = color
        expander._palette_btns = {}

        # Colour swatch prefix
        swatch = Gtk.Label()
        swatch.set_valign(Gtk.Align.CENTER)
        self._apply_swatch_markup(swatch, color)
        expander.add_prefix(swatch)
        expander._swatch = swatch

        # Name entry
        name_row = Adw.EntryRow(title='Name')
        name_row.set_text(name)
        expander.add_row(name_row)
        expander._name_row = name_row

        # Colour palette row
        palette_list_row = self._build_palette_list_row(expander, color)
        expander.add_row(palette_list_row)

        # Warn on connect switch
        warn_row = Adw.SwitchRow(
            title='Warn on connect',
            subtitle='Show a confirmation prompt before connecting',
        )
        warn_row.set_active(meta.get('warn_on_connect', False))
        expander.add_row(warn_row)
        expander._warn_row = warn_row

        # Save / Delete buttons
        save_row = Adw.ButtonRow(title='Save')
        save_row.add_css_class('suggested-action')
        save_row.connect('activated', self._on_save_tag, expander)
        expander.add_row(save_row)

        delete_row = Adw.ButtonRow(title='Delete Tag')
        delete_row.add_css_class('destructive-action')
        delete_row.connect('activated', self._on_delete_tag, expander)
        expander.add_row(delete_row)

        return expander

    def _build_palette_list_row(self, expander, selected_color):
        """Build a ListBoxRow containing the colour swatch grid + Custom button."""
        caption = Gtk.Label(label='Color', xalign=0)
        caption.add_css_class('caption')
        caption.add_css_class('dim-label')

        flow = Gtk.FlowBox()
        flow.set_selection_mode(Gtk.SelectionMode.NONE)
        flow.set_max_children_per_line(6)
        flow.set_min_children_per_line(6)
        flow.set_homogeneous(True)
        flow.set_column_spacing(4)
        flow.set_row_spacing(4)

        for color in _PRESET_COLORS:
            btn = self._build_swatch_btn(color, color == selected_color)
            btn.connect('clicked', self._on_palette_clicked, color, expander)
            expander._palette_btns[color] = btn
            flow.append(btn)

        custom_btn = Gtk.Button(label='Custom…')
        custom_btn.add_css_class('flat')
        custom_btn.set_halign(Gtk.Align.START)
        custom_btn.connect('clicked', self._on_custom_color, expander)

        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        inner.set_margin_top(8)
        inner.set_margin_bottom(8)
        inner.set_margin_start(12)
        inner.set_margin_end(12)
        inner.append(caption)
        inner.append(flow)
        inner.append(custom_btn)

        row = Gtk.ListBoxRow()
        row.set_selectable(False)
        row.set_activatable(False)
        row.set_child(inner)
        return row

    @staticmethod
    def _build_swatch_btn(color, selected):
        btn = Gtk.Button()
        btn.add_css_class('flat')
        btn.add_css_class('circular')
        lbl = Gtk.Label()
        lbl.set_markup(f'<span foreground="{color}" size="xx-large">⬤</span>')
        btn.set_child(lbl)
        if selected:
            btn.add_css_class('swatch-selected')
        return btn

    @staticmethod
    def _apply_swatch_markup(label, color):
        safe = color if _COLOR_RE.match(color or '') else _DEFAULT_COLOR
        label.set_markup(f'<span foreground="{safe}">⬤</span>')

    def _on_palette_clicked(self, _btn, color, expander):
        # Deselect all, select clicked
        for c, b in expander._palette_btns.items():
            if c == color:
                b.add_css_class('swatch-selected')
            else:
                b.remove_css_class('swatch-selected')
        expander._selected_color = color
        self._apply_swatch_markup(expander._swatch, color)

    def _on_custom_color(self, _btn, expander):
        dialog = Gtk.ColorDialog()
        dialog.set_with_alpha(False)
        # Parse current hex to RGBA
        color = expander._selected_color
        rgba = Gdk.RGBA()
        rgba.parse(color if _COLOR_RE.match(color or '') else _DEFAULT_COLOR)
        dialog.choose_rgba(self, rgba, None, self._on_custom_color_done, expander)

    def _on_custom_color_done(self, dialog, result, expander):
        try:
            rgba = dialog.choose_rgba_finish(result)
        except Exception:
            return
        if rgba is None:
            return
        # Convert RGBA to #rrggbb
        r = int(rgba.red * 255)
        g = int(rgba.green * 255)
        b = int(rgba.blue * 255)
        color = f'#{r:02x}{g:02x}{b:02x}'
        # Deselect all preset swatches (custom color isn't in palette)
        for btn in expander._palette_btns.values():
            btn.remove_css_class('swatch-selected')
        expander._selected_color = color
        self._apply_swatch_markup(expander._swatch, color)

    def _on_save_tag(self, _row, expander):
        old_name = expander._tag_name
        new_name = expander._name_row.get_text().strip()

        if not new_name:
            expander._name_row.add_css_class('error')
            return
        expander._name_row.remove_css_class('error')

        # Duplicate name check (allow same name)
        if new_name != old_name and new_name in self._store.get_tags_registry():
            expander._name_row.add_css_class('error')
            return

        warn = expander._warn_row.get_active()
        color = expander._selected_color

        if new_name != old_name:
            self._store.rename_tag(old_name, new_name)

        self._store.set_tag(new_name, color, warn)
        expander.set_expanded(False)
        self._load_tags()
        self.emit('tags-changed')

    def _on_delete_tag(self, _row, expander):
        name = expander._tag_name
        dialog = Adw.AlertDialog(
            heading=f'Delete tag "{name}"?',
            body='It will be removed from all connections.',
        )
        dialog.add_response('cancel', 'Cancel')
        dialog.add_response('delete', 'Delete')
        dialog.set_response_appearance('delete', Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response('cancel')
        dialog.set_close_response('cancel')
        dialog.connect('response', self._on_delete_confirmed, name)
        dialog.present(self)

    def _on_delete_confirmed(self, _dialog, response, name):
        if response != 'delete':
            return
        self._store.remove_tag(name)
        self._store.remove_tag_from_connections(name)
        self._load_tags()
        self.emit('tags-changed')

    def _on_add_tag(self, _btn):
        dialog = Adw.AlertDialog(heading='New Tag')
        dialog.add_response('cancel', 'Cancel')
        dialog.add_response('add', 'Add')
        dialog.set_response_appearance('add', Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response('add')
        dialog.set_close_response('cancel')

        name_entry = Adw.EntryRow(title='Tag name')

        # Compact palette for add dialog
        selected = [_DEFAULT_COLOR]  # mutable container so lambda can mutate
        palette_btns = {}

        flow = Gtk.FlowBox()
        flow.set_selection_mode(Gtk.SelectionMode.NONE)
        flow.set_max_children_per_line(6)
        flow.set_min_children_per_line(6)
        flow.set_homogeneous(True)
        flow.set_column_spacing(4)
        flow.set_row_spacing(4)

        def on_swatch_clicked(_btn, color):
            for c, b in palette_btns.items():
                if c == color:
                    b.add_css_class('swatch-selected')
                else:
                    b.remove_css_class('swatch-selected')
            selected[0] = color

        for color in _PRESET_COLORS:
            btn = self._build_swatch_btn(color, color == _DEFAULT_COLOR)
            btn.connect('clicked', on_swatch_clicked, color)
            palette_btns[color] = btn
            flow.append(btn)

        caption = Gtk.Label(label='Color', xalign=0)
        caption.add_css_class('caption')
        caption.add_css_class('dim-label')

        palette_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        palette_box.set_margin_top(8)
        palette_box.set_margin_bottom(4)
        palette_box.set_margin_start(12)
        palette_box.set_margin_end(12)
        palette_box.append(caption)
        palette_box.append(flow)

        entries_box = Gtk.ListBox()
        entries_box.add_css_class('boxed-list')
        entries_box.set_selection_mode(Gtk.SelectionMode.NONE)
        entries_box.append(name_entry)

        palette_row = Gtk.ListBoxRow()
        palette_row.set_selectable(False)
        palette_row.set_activatable(False)
        palette_row.set_child(palette_box)
        entries_box.append(palette_row)

        dialog.set_extra_child(entries_box)
        dialog.connect('response', self._on_add_confirmed, name_entry, selected)
        dialog.present(self)

    def _on_add_confirmed(self, _dialog, response, name_entry, selected):
        if response != 'add':
            return
        name = name_entry.get_text().strip()
        if not name:
            return
        color = selected[0] if _COLOR_RE.match(selected[0] or '') else _DEFAULT_COLOR
        if name not in self._store.get_tags_registry():
            self._store.set_tag(name, color, False)
        self._load_tags()
        self.emit('tags-changed')
