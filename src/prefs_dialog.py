import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Gtk, Adw

import prefs

FONT_LABELS  = ['System Default', 'Sans-serif', 'Serif', 'Monospace']
FONT_DEFAULT = 0
SIZE_DEFAULT = 10


class PrefsDialog(Adw.PreferencesDialog):
    def __init__(self, on_change):
        super().__init__()
        self._on_change = on_change
        self._build_ui()

    def _build_ui(self):
        page = Adw.PreferencesPage(
            title='Appearance',
            icon_name='preferences-desktop-symbolic',
        )
        self.add(page)

        for key, title in [('sidebar', 'Sidebar'), ('main', 'Main Content')]:
            group = Adw.PreferencesGroup(title=title)
            page.add(group)
            group.add(self._font_combo_row(key))
            group.add(self._size_slider_row(key))

    def _font_combo_row(self, key):
        model = Gtk.StringList()
        for label in FONT_LABELS:
            model.append(label)

        row = Adw.ComboRow(title='Font', model=model)
        row.set_selected(prefs.get(f'{key}_font', FONT_DEFAULT))
        row.connect('notify::selected', lambda r, _, k=key: self._save(f'{k}_font', r.get_selected()))
        return row

    def _size_slider_row(self, key):
        current = prefs.get(f'{key}_size', SIZE_DEFAULT)

        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 8, 20, 1)
        scale.set_hexpand(True)
        scale.set_draw_value(False)
        scale.set_size_request(180, -1)
        scale.set_valign(Gtk.Align.CENTER)
        for s in [8, 10, 12, 14, 16, 18, 20]:
            scale.add_mark(s, Gtk.PositionType.BOTTOM, str(s) if s in (8, 14, 20) else None)
        scale.set_value(current)

        size_label = Gtk.Label(label=f'{current} pt')
        size_label.set_width_chars(5)
        size_label.set_xalign(1.0)
        size_label.set_valign(Gtk.Align.CENTER)

        def on_value_changed(s, lbl=size_label, k=key):
            v = int(s.get_value())
            lbl.set_label(f'{v} pt')
            self._save(f'{k}_size', v)

        scale.connect('value-changed', on_value_changed)

        row = Adw.ActionRow(title='Size')
        row.add_suffix(scale)
        row.add_suffix(size_label)
        return row

    def _save(self, key, value):
        prefs.put(key, value)
        self._on_change()
