import re

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Gtk, Adw, GObject, GLib

_COLOR_RE = re.compile(r'^#[0-9a-fA-F]{6}$')
_DEFAULT_COLOR = '#aaaaaa'


class TagsDialog(Adw.Dialog):
    __gsignals__ = {
        'tags-changed': (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, store):
        super().__init__(title='Manage Tags', content_width=480)
        self._store = store
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
        expander = Adw.ExpanderRow(title=name)
        expander._tag_name = name

        # Colour swatch prefix
        swatch = Gtk.Label(label='⬤')
        swatch.set_valign(Gtk.Align.CENTER)
        swatch.add_css_class('dim-label')
        self._apply_swatch_color(swatch, meta.get('color', _DEFAULT_COLOR))
        expander.add_prefix(swatch)
        expander._swatch = swatch

        # Color entry
        color_row = Adw.EntryRow(title='Color (hex)')
        color_row.set_text(meta.get('color', _DEFAULT_COLOR))
        color_row.connect('notify::text', self._on_color_changed, expander)
        expander.add_row(color_row)
        expander._color_row = color_row

        # Warn on connect switch
        warn_row = Adw.SwitchRow(
            title='Warn on connect',
            subtitle='Show a confirmation prompt before connecting',
        )
        warn_row.set_active(meta.get('warn_on_connect', False))
        warn_row.connect('notify::active', self._on_warn_changed, expander)
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

    def _apply_swatch_color(self, swatch, color):
        # Remove old inline CSS and apply new one via a per-widget provider
        if not hasattr(swatch, '_css_provider'):
            provider = Gtk.CssProvider()
            swatch._css_provider = provider
            from gi.repository import Gdk
            Gtk.StyleContext.add_provider_for_display(
                Gdk.Display.get_default(), provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
            )
        safe = color if _COLOR_RE.match(color or '') else _DEFAULT_COLOR
        swatch._css_provider.load_from_string(
            f'label {{ color: {safe}; }}'
        )

    def _on_color_changed(self, row, _param, expander):
        color = row.get_text().strip()
        self._apply_swatch_color(expander._swatch, color)

    def _on_warn_changed(self, _row, _param, _expander):
        pass  # handled on Save

    def _on_save_tag(self, _row, expander):
        name = expander._tag_name
        color = expander._color_row.get_text().strip()
        if not _COLOR_RE.match(color):
            expander._color_row.add_css_class('error')
            return
        expander._color_row.remove_css_class('error')
        warn = expander._warn_row.get_active()
        self._store.set_tag(name, color, warn)
        expander.set_expanded(False)
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
        # Remove from registry
        self._store.remove_tag(name)
        # Remove from all connections
        for conn in self._store.list():
            if name in conn.get('tags', []):
                updated = {**conn, 'tags': [t for t in conn['tags'] if t != name]}
                try:
                    self._store.update(updated)
                except Exception:
                    pass
        self._load_tags()
        self.emit('tags-changed')

    def _on_add_tag(self, _row):
        dialog = Adw.AlertDialog(heading='New Tag')
        dialog.add_response('cancel', 'Cancel')
        dialog.add_response('add', 'Add')
        dialog.set_response_appearance('add', Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response('add')
        dialog.set_close_response('cancel')

        entry = Adw.EntryRow(title='Tag name')
        color_entry = Adw.EntryRow(title='Color (hex)')
        color_entry.set_text(_DEFAULT_COLOR)

        box = Gtk.ListBox()
        box.add_css_class('boxed-list')
        box.set_selection_mode(Gtk.SelectionMode.NONE)
        box.append(entry)
        box.append(color_entry)
        dialog.set_extra_child(box)

        dialog.connect('response', self._on_add_confirmed, entry, color_entry)
        dialog.present(self)

    def _on_add_confirmed(self, _dialog, response, entry, color_entry):
        if response != 'add':
            return
        name = entry.get_text().strip()
        color = color_entry.get_text().strip()
        if not name:
            return
        if not _COLOR_RE.match(color):
            color = _DEFAULT_COLOR
        if name not in self._store.get_tags_registry():
            self._store.set_tag(name, color, False)
        self._load_tags()
        self.emit('tags-changed')
