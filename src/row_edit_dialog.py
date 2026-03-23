import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Gtk, Adw


class RowEditDialog(Adw.Dialog):
    """Dialog for inserting or editing a single table row.

    mode          – 'insert' or 'edit'
    columns       – ordered list of column names (matches data grid order)
    schema_info   – list of (col_name, data_type, is_nullable, default_val)
    pk_cols       – list of primary-key column names
    initial_values– dict {col_name: value} pre-filled in edit mode; None for insert
    on_save       – callback(values: dict[str, value | None])
    """

    def __init__(self, mode, columns, schema_info, pk_cols, initial_values, on_save):
        title = 'Insert Row' if mode == 'insert' else 'Edit Row'
        super().__init__(title=title, content_width=460)

        self._mode = mode
        self._on_save = on_save

        info_by_col = {row[0]: row for row in schema_info}

        # Required = NOT NULL + no default in insert mode
        self._required = set()
        if mode == 'insert':
            for col in columns:
                info = info_by_col.get(col)
                if info:
                    _, _, is_nullable, default_val = info
                    if is_nullable == 'NO' and not default_val:
                        self._required.add(col)

        header = Adw.HeaderBar()
        self._save_btn = Gtk.Button(label='Save')
        self._save_btn.add_css_class('suggested-action')
        self._save_btn.connect('clicked', self._on_save_clicked)
        header.pack_end(self._save_btn)

        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(header)

        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup()
        self._widgets = {}  # col_name → widget

        for col in columns:
            info = info_by_col.get(col)
            if info:
                col_name, data_type, is_nullable, default_val = info
            else:
                col_name, data_type, is_nullable, default_val = col, '', 'YES', ''

            init_val = initial_values.get(col) if initial_values else None

            if data_type == 'boolean':
                widget = Adw.SwitchRow(title=col_name, subtitle='boolean')
                # Track whether this switch started in an "unset" state so that
                # saving without interaction preserves NULL (edit) or skips the
                # column to let PostgreSQL apply the default (insert).
                widget._starts_as_unset = (init_val is None) or (mode == 'insert' and bool(default_val))
                widget._user_touched = False
                if init_val is not None:
                    widget.set_active(bool(init_val))
                def _on_active(_w, _p, w=widget):
                    w._user_touched = True
                widget.connect('notify::active', _on_active)
            else:
                widget = Adw.EntryRow(title=col_name)
                if init_val is not None:
                    widget.set_text(str(init_val))
                type_label = Gtk.Label(label=data_type)
                type_label.add_css_class('caption')
                type_label.add_css_class('dim-label')
                widget.add_suffix(type_label)
                widget.connect('changed', self._on_changed)

            self._widgets[col] = widget
            group.add(widget)

        page.add(group)
        toolbar_view.set_content(page)
        self.set_child(toolbar_view)
        self._update_save()

    def _on_changed(self, _widget):
        self._update_save()

    def _update_save(self):
        for col in self._required:
            w = self._widgets.get(col)
            if isinstance(w, Adw.EntryRow) and not w.get_text().strip():
                self._save_btn.set_sensitive(False)
                return
        self._save_btn.set_sensitive(True)

    def _on_save_clicked(self, _btn):
        values = {}
        for col, widget in self._widgets.items():
            if isinstance(widget, Adw.SwitchRow):
                if getattr(widget, '_starts_as_unset', False) and not getattr(widget, '_user_touched', False):
                    values[col] = None
                else:
                    values[col] = widget.get_active()
            else:
                text = widget.get_text().strip()
                values[col] = text if text else None
        self.close()
        self._on_save(values)
