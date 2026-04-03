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

        adj = Gtk.Adjustment(value=current, lower=8, upper=20, step_increment=1, page_increment=2)
        row = Adw.SpinRow(title='Size', adjustment=adj)
        row.connect('notify::value', lambda r, _, k=key: self._save(f'{k}_size', int(r.get_value())))
        return row

    def _save(self, key, value):
        prefs.put(key, value)
        self._on_change()
