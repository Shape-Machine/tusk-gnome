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
        self._initial_values = dict(initial_values) if initial_values else {}

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
        self._modified_dots = {}  # col_name → Gtk.Label (edit mode only)

        for col in columns:
            info = info_by_col.get(col)
            if info:
                col_name, data_type, is_nullable, default_val = info
            else:
                col_name, data_type, is_nullable, default_val = col, '', 'YES', ''

            init_val = initial_values.get(col) if initial_values else None
            is_pk = col in pk_cols

            if data_type == 'boolean':
                is_required_bool = (mode == 'insert') and (col in self._required)
                bool_title = f'{col_name} *' if is_required_bool else col_name
                widget = Adw.SwitchRow(title=bool_title, subtitle='boolean')
                # In insert mode with a default, start unset so the DB uses its default.
                # For required booleans (NOT NULL, no default), force a choice: default to False.
                if is_required_bool:
                    widget._starts_as_unset = False
                    widget._user_touched = True
                    widget.set_active(False)
                else:
                    widget._starts_as_unset = (init_val is None) or (mode == 'insert' and bool(default_val))
                    widget._user_touched = False
                    if init_val is not None:
                        widget.set_active(bool(init_val))
                def _on_active(_w, _p, w=widget):
                    w._user_touched = True
                widget.connect('notify::active', _on_active)
            else:
                # In insert mode: mark required fields with asterisk
                if mode == 'insert' and col in self._required:
                    row_title = f'{col_name} *'
                else:
                    row_title = col_name

                widget = Adw.EntryRow(title=row_title)

                if init_val is not None:
                    widget.set_text(str(init_val))

                # (auto) hint for insert-mode columns that have a DB default
                if mode == 'insert' and default_val and col not in self._required:
                    auto_label = Gtk.Label(label='(auto)')
                    auto_label.add_css_class('dim-label')
                    auto_label.add_css_class('caption')
                    widget.add_suffix(auto_label)

                type_label = Gtk.Label(label=data_type)
                type_label.add_css_class('caption')
                type_label.add_css_class('dim-label')
                widget.add_suffix(type_label)

                if mode == 'edit':
                    if is_pk:
                        # Lock primary key fields — mark as read-only visually
                        lock = Gtk.Image.new_from_icon_name('changes-prevent-symbolic')
                        lock.add_css_class('dim-label')
                        lock.set_tooltip_text('Primary key — cannot be changed')
                        widget.add_suffix(lock)
                    else:
                        # Modified indicator: accent dot shown when value differs from original
                        dot = Gtk.Label(label='●')
                        dot.add_css_class('accent')
                        dot.set_tooltip_text('Modified')
                        dot.set_visible(False)
                        widget.add_suffix(dot)
                        self._modified_dots[col] = dot

                widget.connect('changed', self._on_changed)

            self._widgets[col] = widget
            group.add(widget)

        page.add(group)
        toolbar_view.set_content(page)
        self.set_child(toolbar_view)
        self._update_save()

    def _on_changed(self, widget):
        self._update_save()
        if self._mode == 'edit' and self._modified_dots:
            for col, dot in self._modified_dots.items():
                w = self._widgets.get(col)
                if w is widget and isinstance(w, Adw.EntryRow):
                    orig = self._initial_values.get(col)
                    current = w.get_text().strip()
                    orig_str = str(orig) if orig is not None else ''
                    dot.set_visible(current != orig_str)
                    break

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
