import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Gtk, Adw, GObject


class ConnectionsImportDialog(Adw.Dialog):
    """Preview dialog for JSON connection import.

    Emits 'import-confirmed' with (conns_to_import, tags_registry) after the
    user clicks Import Selected.
    """

    __gsignals__ = {
        'import-confirmed': (GObject.SignalFlags.RUN_FIRST, None,
                             (GObject.TYPE_PYOBJECT, GObject.TYPE_PYOBJECT)),
    }

    def __init__(self, incoming_conns, incoming_tags, existing_names):
        super().__init__(title='Import Connections', content_width=520, content_height=560)
        self._incoming_tags = incoming_tags
        self._checks = {}   # conn_id → (Gtk.CheckButton, resolved_conn)
        self._build_ui(incoming_conns, existing_names)

    def _build_ui(self, incoming_conns, existing_names):
        header = Adw.HeaderBar()

        list_box = Gtk.ListBox()
        list_box.add_css_class('boxed-list')
        list_box.set_selection_mode(Gtk.SelectionMode.NONE)

        conflict_count = 0
        for conn in incoming_conns:
            resolved = dict(conn)
            clash = conn.get('name', '') in existing_names
            if clash:
                resolved['name'] = conn['name'] + ' (imported)'
                conflict_count += 1
            row = Adw.ActionRow(title=resolved['name'])
            subtitle = f'{conn.get("host", "")}:{conn.get("port", 5432)}/{conn.get("database", "")}'
            if clash:
                subtitle += ' — renamed from "' + conn['name'] + '"'
            row.set_subtitle(subtitle)
            check = Gtk.CheckButton()
            check.set_active(True)
            check.set_valign(Gtk.Align.CENTER)
            row.add_suffix(check)
            row.set_activatable_widget(check)
            list_box.append(row)
            self._checks[conn.get('id', '')] = (check, resolved)

        n = len(incoming_conns)
        summary_parts = [f'{n} connection{"s" if n != 1 else ""} found.']
        if conflict_count:
            summary_parts.append(
                f'{conflict_count} name{"s" if conflict_count != 1 else ""} '
                'already exist and will be imported with "(imported)" suffix.'
            )
        summary = Gtk.Label(label=' '.join(summary_parts))
        summary.add_css_class('dim-label')
        summary.set_wrap(True)
        summary.set_xalign(0)

        import_btn = Gtk.Button(label='Import Selected')
        import_btn.add_css_class('suggested-action')
        import_btn.add_css_class('pill')
        import_btn.set_halign(Gtk.Align.CENTER)
        import_btn.connect('clicked', self._on_import)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_top(16)
        box.set_margin_bottom(20)
        box.set_margin_start(20)
        box.set_margin_end(20)
        box.append(summary)
        box.append(list_box)
        box.append(import_btn)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_propagate_natural_height(True)
        scroll.set_child(box)

        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(header)
        toolbar_view.set_content(scroll)
        self.set_child(toolbar_view)

    def _on_import(self, _btn):
        selected = [resolved for (check, resolved) in self._checks.values() if check.get_active()]
        self.emit('import-confirmed', selected, self._incoming_tags)
        self.close()
