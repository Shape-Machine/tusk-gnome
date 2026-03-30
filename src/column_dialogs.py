import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Gtk, Adw, Gio, GObject, Pango, Gdk

try:
    gi.require_version('GtkSource', '5')
    from gi.repository import GtkSource
    _HAS_SOURCE = True
except (ValueError, ImportError):
    _HAS_SOURCE = False


# ---------------------------------------------------------------------------
# PostgreSQL type catalogue
# ---------------------------------------------------------------------------

_PG_TYPES = [
    # (display_name, description)
    ('text',             'Variable-length string'),
    ('varchar',          'Variable-length string with limit'),
    ('char',             'Fixed-length string'),
    ('integer',          '4-byte signed integer'),
    ('bigint',           '8-byte signed integer'),
    ('smallint',         '2-byte signed integer'),
    ('serial',           'Auto-incrementing 4-byte integer'),
    ('bigserial',        'Auto-incrementing 8-byte integer'),
    ('boolean',          'True/false value'),
    ('numeric',          'Exact decimal number'),
    ('real',             '4-byte floating-point number'),
    ('double precision', '8-byte floating-point number'),
    ('uuid',             'Universally unique identifier'),
    ('jsonb',            'JSON data (binary, indexed)'),
    ('json',             'JSON data (text storage)'),
    ('timestamptz',      'Timestamp with time zone'),
    ('timestamp',        'Timestamp without time zone'),
    ('date',             'Calendar date'),
    ('time',             'Time of day'),
    ('interval',         'Time span'),
    ('bytea',            'Binary data'),
    ('inet',             'IPv4 or IPv6 address'),
    ('cidr',             'IPv4 or IPv6 network'),
    ('macaddr',          'MAC address'),
]

_PG_TYPE_NAMES = [t[0] for t in _PG_TYPES]


# ---------------------------------------------------------------------------
# Type picker popover helper
# ---------------------------------------------------------------------------

def _attach_type_picker(entry_row):
    """Attach a type-picker popover button to an Adw.EntryRow.

    The button opens a popover listing common PostgreSQL types.  Clicking a
    type fills the entry and closes the popover.  The entry still accepts any
    free-form text for custom types.
    """
    btn = Gtk.MenuButton(icon_name='pan-down-symbolic')
    btn.add_css_class('flat')
    btn.set_tooltip_text('Pick a PostgreSQL type')
    btn.set_valign(Gtk.Align.CENTER)
    entry_row.add_suffix(btn)

    popover = Gtk.Popover()
    popover.set_has_arrow(False)
    popover.set_position(Gtk.PositionType.BOTTOM)
    btn.set_popover(popover)

    outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    outer.set_size_request(260, -1)

    search = Gtk.SearchEntry()
    search.set_placeholder_text('Search types…')
    search.set_margin_top(8)
    search.set_margin_bottom(4)
    search.set_margin_start(8)
    search.set_margin_end(8)

    scroll = Gtk.ScrolledWindow()
    scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    scroll.set_max_content_height(280)
    scroll.set_propagate_natural_height(True)

    list_box = Gtk.ListBox()
    list_box.set_selection_mode(Gtk.SelectionMode.NONE)
    list_box.add_css_class('boxed-list-separate')

    def _make_row(name, desc):
        row = Gtk.ListBoxRow()
        row._type_name = name
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.set_margin_top(6)
        box.set_margin_bottom(6)
        box.set_margin_start(10)
        box.set_margin_end(10)
        name_lbl = Gtk.Label(label=name)
        name_lbl.set_xalign(0)
        name_lbl.set_halign(Gtk.Align.START)
        desc_lbl = Gtk.Label(label=desc)
        desc_lbl.set_xalign(0)
        desc_lbl.add_css_class('caption')
        desc_lbl.add_css_class('dim-label')
        box.append(name_lbl)
        box.append(desc_lbl)
        row.set_child(box)
        return row

    all_rows = []
    for type_name, desc in _PG_TYPES:
        r = _make_row(type_name, desc)
        list_box.append(r)
        all_rows.append(r)

    def _on_search(entry):
        text = entry.get_text().strip().lower()
        for r in all_rows:
            r.set_visible(not text or text in r._type_name.lower())

    search.connect('search-changed', _on_search)

    def _on_row_activated(_lb, row):
        entry_row.set_text(row._type_name)
        popover.popdown()

    list_box.connect('row-activated', _on_row_activated)

    scroll.set_child(list_box)
    outer.append(search)
    outer.append(scroll)
    popover.set_child(outer)

    return btn


# ---------------------------------------------------------------------------
# SQL preview helpers (shared by CreateTableDialog and AddColumnDialog)
# ---------------------------------------------------------------------------

def _make_sql_preview_view():
    """Create a read-only SQL text view. Returns (buffer, view)."""
    if _HAS_SOURCE:
        buf = GtkSource.Buffer()
        lang = GtkSource.LanguageManager.get_default().get_language('sql')
        if lang:
            buf.set_language(lang)
        mgr = GtkSource.StyleSchemeManager.get_default()
        from gi.repository import Adw as _Adw
        dark = _Adw.StyleManager.get_default().get_dark()
        scheme = mgr.get_scheme('Adwaita-dark' if dark else 'Adwaita') or mgr.get_scheme('classic')
        if scheme:
            buf.set_style_scheme(scheme)
        view = GtkSource.View.new_with_buffer(buf)
        view.set_show_line_numbers(False)
        view.set_tab_width(4)
    else:
        buf = Gtk.TextBuffer()
        view = Gtk.TextView(buffer=buf)
    view.set_editable(False)
    view.set_monospace(True)
    view.set_wrap_mode(Gtk.WrapMode.NONE)
    view.set_top_margin(8)
    view.set_left_margin(8)
    view.set_bottom_margin(8)
    view.set_right_margin(8)
    return buf, view


def _make_type_picker_popover(entry):
    """Create a type-picker MenuButton for a plain Gtk.Entry. Returns the button."""
    btn = Gtk.MenuButton(icon_name='pan-down-symbolic')
    btn.add_css_class('flat')
    btn.set_tooltip_text('Pick a PostgreSQL type')
    btn.set_valign(Gtk.Align.CENTER)

    popover = Gtk.Popover()
    popover.set_has_arrow(False)
    popover.set_position(Gtk.PositionType.BOTTOM)
    btn.set_popover(popover)

    outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    outer.set_size_request(260, -1)

    search = Gtk.SearchEntry()
    search.set_placeholder_text('Search types…')
    search.set_margin_top(8)
    search.set_margin_bottom(4)
    search.set_margin_start(8)
    search.set_margin_end(8)

    scroll = Gtk.ScrolledWindow()
    scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    scroll.set_max_content_height(280)
    scroll.set_propagate_natural_height(True)

    list_box = Gtk.ListBox()
    list_box.set_selection_mode(Gtk.SelectionMode.NONE)
    list_box.add_css_class('boxed-list-separate')

    all_rows = []
    for type_name, desc in _PG_TYPES:
        row = Gtk.ListBoxRow()
        row._type_name = type_name
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.set_margin_top(6)
        box.set_margin_bottom(6)
        box.set_margin_start(10)
        box.set_margin_end(10)
        name_lbl = Gtk.Label(label=type_name)
        name_lbl.set_xalign(0)
        desc_lbl = Gtk.Label(label=desc)
        desc_lbl.set_xalign(0)
        desc_lbl.add_css_class('caption')
        desc_lbl.add_css_class('dim-label')
        box.append(name_lbl)
        box.append(desc_lbl)
        row.set_child(box)
        list_box.append(row)
        all_rows.append(row)

    def _on_search(e):
        text = e.get_text().strip().lower()
        for r in all_rows:
            r.set_visible(not text or text in r._type_name.lower())

    search.connect('search-changed', _on_search)

    def _on_row_activated(_lb, row):
        entry.set_text(row._type_name)
        popover.popdown()

    list_box.connect('row-activated', _on_row_activated)

    scroll.set_child(list_box)
    outer.append(search)
    outer.append(scroll)
    popover.set_child(outer)

    return btn


# ---------------------------------------------------------------------------
# Rename dialog  (#87)
# ---------------------------------------------------------------------------

class RenameDialog(Adw.Dialog):
    """Small dialog for renaming a table or column.

    current_name – the name to pre-fill
    on_rename(new_name) – callback called with the new name on confirm
    title – dialog title (e.g. 'Rename Table' or 'Rename Column')
    """

    def __init__(self, current_name, on_rename, title='Rename'):
        super().__init__(title=title, content_width=380)
        self._on_rename = on_rename

        header = Adw.HeaderBar()
        self._apply_btn = Gtk.Button(label='Rename')
        self._apply_btn.add_css_class('suggested-action')
        self._apply_btn.set_sensitive(False)
        self._apply_btn.connect('clicked', self._on_apply)
        header.pack_end(self._apply_btn)

        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(header)

        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup()
        self._name_row = Adw.EntryRow(title='New name')
        self._name_row.set_text(current_name)
        self._name_row.connect('changed', self._on_changed)
        self._name_row.connect('entry-activated', self._on_entry_activated)
        group.add(self._name_row)
        page.add(group)
        toolbar_view.set_content(page)
        self.set_child(toolbar_view)

        self._on_changed()

    def _on_changed(self, *_):
        self._apply_btn.set_sensitive(bool(self._name_row.get_text().strip()))

    def _on_entry_activated(self, *_):
        if self._apply_btn.get_sensitive():
            self._on_apply()

    def _on_apply(self, *_):
        self._on_rename(self._name_row.get_text().strip())
        self.close()


# ---------------------------------------------------------------------------
# Add Column dialog  (#83, #111)
# ---------------------------------------------------------------------------

class AddColumnDialog(Adw.Dialog):
    """Dialog for adding a new column to a table.

    existing_columns – list of current column names (for 'After column' dropdown)
    on_save(name, pg_type, nullable, default, after_col) – callback on confirm
        after_col is None if not specified
    """

    def __init__(self, existing_columns, on_save, schema=None, table=None):
        super().__init__(title='Add Column', content_width=420)
        self._on_save = on_save
        self._preview_buf = None

        header = Adw.HeaderBar()
        self._add_btn = Gtk.Button(label='Add')
        self._add_btn.add_css_class('suggested-action')
        self._add_btn.set_sensitive(False)
        self._add_btn.connect('clicked', self._on_add_clicked)
        header.pack_end(self._add_btn)

        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(header)

        page = Adw.PreferencesPage()

        # ── Column definition group ─────────────────────────────────────────
        def_group = Adw.PreferencesGroup(title='Column Definition')

        self._name_row = Adw.EntryRow(title='Name')
        self._name_row.connect('changed', self._update_add_btn)
        def_group.add(self._name_row)

        self._type_row = Adw.EntryRow(title='Type')
        self._type_row.set_text('text')
        _attach_type_picker(self._type_row)
        self._type_row.connect('changed', self._update_add_btn)
        def_group.add(self._type_row)

        self._nullable_row = Adw.SwitchRow(title='Nullable')
        self._nullable_row.set_active(True)
        def_group.add(self._nullable_row)

        self._default_row = Adw.EntryRow(title='Default value')
        self._default_row.set_tooltip_text('Leave empty for no default. Supports expressions like now(), gen_random_uuid().')
        def_group.add(self._default_row)

        page.add(def_group)

        # ── Position group ──────────────────────────────────────────────────
        if existing_columns:
            pos_group = Adw.PreferencesGroup(
                title='Position',
                description='PostgreSQL always appends columns physically. '
                            'Selecting a column records the intended position as a comment.',
            )

            after_model = Gtk.StringList.new(['(end of table)'] + existing_columns)
            self._after_row = Adw.ComboRow(title='After column', model=after_model)
            pos_group.add(self._after_row)
            page.add(pos_group)
        else:
            self._after_row = None

        toolbar_view.set_content(page)

        if schema and table:
            self._schema = schema
            self._table = table
            self._preview_buf, preview_view = _make_sql_preview_view()
            self._name_row.connect('changed', self._update_preview)
            self._type_row.connect('changed', self._update_preview)
            self._nullable_row.connect('notify::active', self._update_preview)
            self._default_row.connect('changed', self._update_preview)

            preview_scroll = Gtk.ScrolledWindow()
            preview_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
            preview_scroll.set_min_content_height(56)
            preview_scroll.set_child(preview_view)

            preview_frame = Gtk.Frame()
            preview_frame.set_child(preview_scroll)

            preview_inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            preview_inner.set_margin_top(8)
            preview_inner.set_margin_start(8)
            preview_inner.set_margin_end(8)
            preview_inner.set_margin_bottom(8)
            preview_inner.append(preview_frame)

            self._preview_revealer = Gtk.Revealer()
            self._preview_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_UP)
            self._preview_revealer.set_reveal_child(False)
            self._preview_revealer.set_child(preview_inner)

            toggle_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            toggle_row.set_margin_start(12)
            toggle_row.set_margin_end(8)
            toggle_row.set_margin_top(2)
            toggle_row.set_margin_bottom(2)

            preview_toggle_lbl = Gtk.Label(label='Preview SQL')
            preview_toggle_lbl.add_css_class('caption')
            toggle_row.append(preview_toggle_lbl)

            copy_btn = Gtk.Button(label='Copy')
            copy_btn.add_css_class('flat')
            copy_btn.set_tooltip_text('Copy SQL to clipboard')
            copy_btn.connect('clicked', self._copy_preview)
            toggle_row.append(copy_btn)

            spacer = Gtk.Box()
            spacer.set_hexpand(True)
            toggle_row.append(spacer)

            self._preview_chevron = Gtk.Image.new_from_icon_name('pan-up-symbolic')
            toggle_row.append(self._preview_chevron)

            row_gesture = Gtk.GestureClick(button=1)
            row_gesture.connect('released', lambda g, _n, _x, _y: self._toggle_preview(g))
            toggle_row.add_controller(row_gesture)

            bottom_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            bottom_box.append(Gtk.Separator())
            bottom_box.append(self._preview_revealer)
            bottom_box.append(toggle_row)

            toolbar_view.add_bottom_bar(bottom_box)

        self.set_child(toolbar_view)

    def _toggle_preview(self, _):
        revealed = not self._preview_revealer.get_reveal_child()
        self._preview_revealer.set_reveal_child(revealed)
        self._preview_chevron.set_from_icon_name(
            'pan-down-symbolic' if revealed else 'pan-up-symbolic'
        )

    def _copy_preview(self, _btn):
        text = self._preview_buf.get_text(
            self._preview_buf.get_start_iter(),
            self._preview_buf.get_end_iter(),
            False,
        )
        Gdk.Display.get_default().get_clipboard().set(text)

    def _update_preview(self, *_):
        if self._preview_buf is None:
            return
        name = self._name_row.get_text().strip()
        pg_type = self._type_row.get_text().strip() or 'text'
        nullable = self._nullable_row.get_active()
        default = self._default_row.get_text().strip()

        def qi(n):
            return '"' + n.replace('"', '""') + '"'

        if not name:
            self._preview_buf.set_text('')
            return

        parts = [
            f'ALTER TABLE {qi(self._schema)}.{qi(self._table)}',
            f'ADD COLUMN {qi(name)} {pg_type}',
        ]
        if not nullable:
            parts.append('NOT NULL')
        if default:
            parts.append(f'DEFAULT {default}')
        self._preview_buf.set_text(' '.join(parts) + ';')

    def _update_add_btn(self, *_):
        name = self._name_row.get_text().strip()
        pg_type = self._type_row.get_text().strip()
        self._add_btn.set_sensitive(bool(name) and bool(pg_type))

    def _on_add_clicked(self, _btn):
        name = self._name_row.get_text().strip()
        pg_type = self._type_row.get_text().strip()
        nullable = self._nullable_row.get_active()
        default = self._default_row.get_text().strip() or None

        after_col = None
        if self._after_row is not None:
            idx = self._after_row.get_selected()
            if idx > 0:  # 0 is '(end of table)'
                after_model = self._after_row.get_model()
                after_col = after_model.get_string(idx)

        self.close()
        self._on_save(name, pg_type, nullable, default, after_col)


# ---------------------------------------------------------------------------
# Change Type dialog  (#106)
# ---------------------------------------------------------------------------

class ChangeTypeDialog(Adw.Dialog):
    """Dialog for changing a column's data type.

    col_name    – column name (display only)
    current_type – pre-filled in the type picker
    on_save(new_type, using_expr) – callback; using_expr may be None
    """

    def __init__(self, col_name, current_type, on_save):
        super().__init__(title=f'Change Type: {col_name}', content_width=420)
        self._on_save = on_save

        header = Adw.HeaderBar()
        self._apply_btn = Gtk.Button(label='Apply')
        self._apply_btn.add_css_class('suggested-action')
        self._apply_btn.connect('clicked', self._on_apply_clicked)
        header.pack_end(self._apply_btn)

        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(header)

        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup()

        self._type_row = Adw.EntryRow(title='New type')
        self._type_row.set_text(current_type or '')
        _attach_type_picker(self._type_row)
        group.add(self._type_row)

        self._using_row = Adw.EntryRow(title='USING expression')
        self._using_row.set_tooltip_text(
            'Required when the cast is not implicit, e.g.  col::integer  or  to_date(col, \'YYYY-MM-DD\')'
        )
        group.add(self._using_row)

        page.add(group)
        toolbar_view.set_content(page)
        self.set_child(toolbar_view)

    def _on_apply_clicked(self, _btn):
        new_type = self._type_row.get_text().strip()
        using = self._using_row.get_text().strip() or None
        if not new_type:
            return
        self.close()
        self._on_save(new_type, using)


# ---------------------------------------------------------------------------
# Set Default dialog  (#107)
# ---------------------------------------------------------------------------

class SetDefaultDialog(Adw.Dialog):
    """Dialog for setting or dropping a column's default value.

    col_name     – column name (display only)
    current_default – current default expression (may be empty string)
    on_save(default_expr) – callback; None means DROP DEFAULT
    """

    def __init__(self, col_name, current_default, on_save):
        super().__init__(title=f'Set Default: {col_name}', content_width=420)
        self._on_save = on_save

        header = Adw.HeaderBar()
        apply_btn = Gtk.Button(label='Apply')
        apply_btn.add_css_class('suggested-action')
        apply_btn.connect('clicked', self._on_apply_clicked)
        header.pack_end(apply_btn)

        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(header)

        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup(
            description='Enter a default expression (e.g. now(), gen_random_uuid(), 0). '
                        'Leave empty and click Apply to drop the current default.'
        )

        self._default_row = Adw.EntryRow(title='Default expression')
        if current_default:
            self._default_row.set_text(current_default)
        group.add(self._default_row)

        page.add(group)
        toolbar_view.set_content(page)
        self.set_child(toolbar_view)

    def _on_apply_clicked(self, _btn):
        expr = self._default_row.get_text().strip() or None
        self.close()
        self._on_save(expr)


# ---------------------------------------------------------------------------
# Reorder Columns dialog  (#109)
# ---------------------------------------------------------------------------

class ReorderColumnsDialog(Adw.Dialog):
    """Dialog for reordering table columns and generating a migration script.

    schema  – schema name
    table   – table name
    columns – list of column names in current order

    The dialog only generates and copies the migration SQL; it does not execute
    it directly.  The generated CREATE TABLE ... AS SELECT script does not
    preserve constraints, indexes, triggers, or defaults, so it must be reviewed
    and augmented before running.
    """

    def __init__(self, schema, table, columns, on_execute=None):
        super().__init__(title='Reorder Columns', content_width=500)
        self._schema = schema
        self._table = table
        self._original_order = list(columns)
        self._current_order = list(columns)

        header = Adw.HeaderBar()
        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(header)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        outer.set_margin_top(12)
        outer.set_margin_bottom(12)
        outer.set_margin_start(12)
        outer.set_margin_end(12)

        # ── Column list with Up/Down buttons ───────────────────────────────
        list_frame = Gtk.Frame()
        list_frame.add_css_class('view')
        self._list_box = Gtk.ListBox()
        self._list_box.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._list_box.add_css_class('boxed-list')
        list_frame.set_child(self._list_box)

        self._col_rows = []
        for col in columns:
            self._list_box.append(self._make_col_row(col))

        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        btn_box.set_margin_top(8)
        btn_box.set_halign(Gtk.Align.END)

        self._up_btn = Gtk.Button(label='Move Up')
        self._up_btn.set_icon_name('go-up-symbolic')
        self._up_btn.connect('clicked', self._move_up)

        self._down_btn = Gtk.Button(label='Move Down')
        self._down_btn.set_icon_name('go-down-symbolic')
        self._down_btn.connect('clicked', self._move_down)

        btn_box.append(self._up_btn)
        btn_box.append(self._down_btn)

        outer.append(list_frame)
        outer.append(btn_box)

        # ── Migration SQL preview ───────────────────────────────────────────
        self._gen_btn = Gtk.Button(label='Generate Migration SQL')
        self._gen_btn.set_margin_top(16)
        self._gen_btn.connect('clicked', self._generate_sql)
        outer.append(self._gen_btn)

        self._sql_frame = Gtk.Frame()
        self._sql_frame.set_margin_top(8)
        self._sql_frame.set_visible(False)

        self._sql_buf = Gtk.TextBuffer()
        sql_view = Gtk.TextView(buffer=self._sql_buf)
        sql_view.set_editable(False)
        sql_view.set_monospace(True)
        sql_view.set_wrap_mode(Gtk.WrapMode.NONE)
        sql_view.set_top_margin(8)
        sql_view.set_left_margin(8)
        sql_view.set_bottom_margin(8)
        sql_view.set_right_margin(8)
        sql_scroll = Gtk.ScrolledWindow()
        sql_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        sql_scroll.set_min_content_height(180)
        sql_scroll.set_child(sql_view)
        self._sql_frame.set_child(sql_scroll)
        outer.append(self._sql_frame)

        action_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        action_box.set_margin_top(8)
        action_box.set_halign(Gtk.Align.END)
        action_box.set_visible(False)
        self._action_box = action_box

        copy_btn = Gtk.Button(label='Copy SQL')
        copy_btn.connect('clicked', self._copy_sql)

        action_box.append(copy_btn)
        outer.append(action_box)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_propagate_natural_height(True)
        scroll.set_child(outer)

        toolbar_view.set_content(scroll)
        self.set_child(toolbar_view)

    def _make_col_row(self, col_name):
        row = Gtk.ListBoxRow()
        row._col_name = col_name
        lbl = Gtk.Label(label=col_name)
        lbl.set_xalign(0)
        lbl.set_margin_top(8)
        lbl.set_margin_bottom(8)
        lbl.set_margin_start(12)
        row.set_child(lbl)
        self._col_rows.append(row)
        return row

    def _selected_index(self):
        row = self._list_box.get_selected_row()
        if row is None:
            return -1
        return self._current_order.index(row._col_name)

    def _rebuild_list(self):
        # Remove all rows
        child = self._list_box.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self._list_box.remove(child)
            child = nxt
        self._col_rows = []
        for col in self._current_order:
            self._list_box.append(self._make_col_row(col))

    def _move_up(self, _btn):
        idx = self._selected_index()
        if idx <= 0:
            return
        self._current_order.insert(idx - 1, self._current_order.pop(idx))
        self._rebuild_list()
        # Re-select the moved row
        self._list_box.select_row(self._col_rows[idx - 1])

    def _move_down(self, _btn):
        idx = self._selected_index()
        if idx < 0 or idx >= len(self._current_order) - 1:
            return
        self._current_order.insert(idx + 1, self._current_order.pop(idx))
        self._rebuild_list()
        self._list_box.select_row(self._col_rows[idx + 1])

    def _generate_sql(self, _btn):
        schema = self._schema
        table = self._table
        cols = self._current_order

        def qi(name):
            return '"' + name.replace('"', '""') + '"'

        tmp = f'{table}_reorder_tmp'
        col_list = ', '.join(qi(c) for c in cols)
        sql = (
            f'-- WARNING: This script does NOT preserve constraints, indexes,\n'
            f'-- triggers, defaults, sequences, or grants. Review and add them\n'
            f'-- back manually before executing.\n\n'
            f'BEGIN;\n\n'
            f'-- Step 1: rename original table to a temp name\n'
            f'ALTER TABLE {qi(schema)}.{qi(table)} RENAME TO {qi(tmp)};\n\n'
            f'-- Step 2: create new table with desired column order\n'
            f'CREATE TABLE {qi(schema)}.{qi(table)} AS\n'
            f'  SELECT {col_list}\n'
            f'  FROM {qi(schema)}.{qi(tmp)};\n\n'
            f'-- Step 3: drop the temp table\n'
            f'DROP TABLE {qi(schema)}.{qi(tmp)};\n\n'
            f'COMMIT;\n'
        )
        self._sql_buf.set_text(sql)
        self._sql_frame.set_visible(True)
        self._action_box.set_visible(True)
        self._gen_btn.set_label('Regenerate Migration SQL')

    def _copy_sql(self, _btn):
        from gi.repository import Gdk
        text = self._sql_buf.get_text(
            self._sql_buf.get_start_iter(),
            self._sql_buf.get_end_iter(),
            False,
        )
        Gdk.Display.get_default().get_clipboard().set(text)


# ---------------------------------------------------------------------------
# Add Index dialog  (#98)
# ---------------------------------------------------------------------------

_INDEX_TYPES = ['btree', 'hash', 'gin', 'gist', 'brin']

_FK_ACTIONS = ['NO ACTION', 'RESTRICT', 'CASCADE', 'SET NULL', 'SET DEFAULT']


class AddIndexDialog(Adw.Dialog):
    """Dialog for creating a new index on a table.

    table_name   – bare table name (used for name suggestion)
    col_names    – ordered list of column names from the schema
    on_save(name, cols, idx_type, unique, concurrently) – callback on confirm
        cols is an ordered list of selected column names
    """

    def __init__(self, table_name, col_names, on_save):
        super().__init__(title='Add Index', content_width=420)
        self._on_save = on_save
        self._col_names = col_names
        self._table_name = table_name

        header = Adw.HeaderBar()
        self._create_btn = Gtk.Button(label='Create')
        self._create_btn.add_css_class('suggested-action')
        self._create_btn.set_sensitive(False)
        self._create_btn.connect('clicked', self._on_create_clicked)
        header.pack_end(self._create_btn)

        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(header)

        page = Adw.PreferencesPage()

        # ── Index definition ────────────────────────────────────────────────
        def_group = Adw.PreferencesGroup(title='Index Definition')

        self._name_row = Adw.EntryRow(title='Index name')
        self._name_row.connect('changed', self._update_create_btn)
        def_group.add(self._name_row)

        type_model = Gtk.StringList.new(_INDEX_TYPES)
        self._type_row = Adw.ComboRow(title='Index type', model=type_model)
        def_group.add(self._type_row)

        self._unique_row = Adw.SwitchRow(title='Unique')
        def_group.add(self._unique_row)

        self._concurrent_row = Adw.SwitchRow(
            title='CONCURRENTLY',
            subtitle='Avoids locking the table during creation',
        )
        self._concurrent_row.set_active(True)
        def_group.add(self._concurrent_row)

        page.add(def_group)

        # ── Column selection ────────────────────────────────────────────────
        col_group = Adw.PreferencesGroup(
            title='Columns',
            description='Columns are included in the order they appear here.',
        )
        self._col_checks = {}
        for col in col_names:
            row = Adw.SwitchRow(title=col)
            row.connect('notify::active', self._on_col_toggled)
            col_group.add(row)
            self._col_checks[col] = row

        page.add(col_group)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_propagate_natural_height(True)
        scroll.set_child(page)

        toolbar_view.set_content(scroll)
        self.set_child(toolbar_view)

    def _on_col_toggled(self, row, _param):
        # Auto-suggest name from first selected column
        selected = [c for c in self._col_names if self._col_checks[c].get_active()]
        if selected and not self._name_row.get_text().strip():
            self._name_row.set_text(f'idx_{self._table_name}_{selected[0]}')
        self._update_create_btn()

    def _update_create_btn(self, *_):
        name = self._name_row.get_text().strip()
        selected = [c for c in self._col_names if self._col_checks[c].get_active()]
        self._create_btn.set_sensitive(bool(name) and bool(selected))

    def _on_create_clicked(self, _btn):
        name = self._name_row.get_text().strip()
        cols = [c for c in self._col_names if self._col_checks[c].get_active()]
        idx_type = _INDEX_TYPES[self._type_row.get_selected()]
        unique = self._unique_row.get_active()
        concurrently = self._concurrent_row.get_active()
        self.close()
        self._on_save(name, cols, idx_type, unique, concurrently)


# ---------------------------------------------------------------------------
# Add Constraint dialog  (#99)
# ---------------------------------------------------------------------------

_CONSTRAINT_TYPES = ['PRIMARY KEY', 'UNIQUE', 'CHECK', 'FOREIGN KEY']


class AddConstraintDialog(Adw.Dialog):
    """Dialog for adding a constraint to a table.

    table_name  – bare table name (used for name suggestion)
    col_names   – ordered list of column names from the schema
    on_save(name, constraint_sql) – callback; constraint_sql is the fragment
        after ADD CONSTRAINT <name>, e.g. 'PRIMARY KEY (id)'
    """

    def __init__(self, table_name, col_names, on_save):
        super().__init__(title='Add Constraint', content_width=440)
        self._on_save = on_save
        self._table_name = table_name
        self._col_names = col_names

        header = Adw.HeaderBar()
        self._add_btn = Gtk.Button(label='Add')
        self._add_btn.add_css_class('suggested-action')
        self._add_btn.set_sensitive(False)
        self._add_btn.connect('clicked', self._on_add_clicked)
        header.pack_end(self._add_btn)

        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(header)

        self._page = Adw.PreferencesPage()

        # ── Type & name ─────────────────────────────────────────────────────
        top_group = Adw.PreferencesGroup()

        type_model = Gtk.StringList.new(_CONSTRAINT_TYPES)
        self._type_row = Adw.ComboRow(title='Type', model=type_model)
        self._type_row.connect('notify::selected', self._on_type_changed)
        top_group.add(self._type_row)

        self._name_row = Adw.EntryRow(title='Constraint name')
        self._name_row.connect('changed', self._update_add_btn)
        top_group.add(self._name_row)

        self._page.add(top_group)

        # ── Type-specific groups (only one visible at a time) ───────────────

        # PK / UNIQUE columns
        self._pk_col_group = Adw.PreferencesGroup(
            title='Columns',
            description='Columns are included in the order they appear here.',
        )
        self._col_checks = {}
        for col in col_names:
            row = Adw.SwitchRow(title=col)
            row.connect('notify::active', lambda *_: self._update_add_btn())
            self._pk_col_group.add(row)
            self._col_checks[col] = row
        self._page.add(self._pk_col_group)

        # CHECK expression
        self._check_group = Adw.PreferencesGroup(title='CHECK Expression')
        self._check_row = Adw.EntryRow(title='Expression')
        self._check_row.set_tooltip_text('e.g.  price > 0  or  length(name) > 0')
        self._check_row.connect('changed', self._update_add_btn)
        self._check_group.add(self._check_row)
        self._page.add(self._check_group)
        self._check_group.set_visible(False)

        # FOREIGN KEY
        self._fk_group = Adw.PreferencesGroup(title='Foreign Key')

        fk_col_model = Gtk.StringList.new(col_names)
        self._fk_local_row = Adw.ComboRow(title='Local column', model=fk_col_model)
        self._fk_group.add(self._fk_local_row)

        self._fk_ref_table_row = Adw.EntryRow(title='Referenced table')
        self._fk_ref_table_row.set_tooltip_text('e.g.  public.users  or just  users')
        self._fk_ref_table_row.connect('changed', self._update_add_btn)
        self._fk_group.add(self._fk_ref_table_row)

        self._fk_ref_col_row = Adw.EntryRow(title='Referenced column')
        self._fk_ref_col_row.connect('changed', self._update_add_btn)
        self._fk_group.add(self._fk_ref_col_row)

        on_update_model = Gtk.StringList.new(_FK_ACTIONS)
        self._fk_on_update_row = Adw.ComboRow(title='ON UPDATE', model=on_update_model)
        self._fk_group.add(self._fk_on_update_row)

        on_delete_model = Gtk.StringList.new(_FK_ACTIONS)
        self._fk_on_delete_row = Adw.ComboRow(title='ON DELETE', model=on_delete_model)
        self._fk_group.add(self._fk_on_delete_row)

        self._page.add(self._fk_group)
        self._fk_group.set_visible(False)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_propagate_natural_height(True)
        scroll.set_child(self._page)

        toolbar_view.set_content(scroll)
        self.set_child(toolbar_view)

        # Set initial name suggestion
        self._suggest_name()

    def _on_type_changed(self, _row, _param):
        idx = self._type_row.get_selected()
        ct = _CONSTRAINT_TYPES[idx]
        self._pk_col_group.set_visible(ct in ('PRIMARY KEY', 'UNIQUE'))
        self._check_group.set_visible(ct == 'CHECK')
        self._fk_group.set_visible(ct == 'FOREIGN KEY')
        self._suggest_name()
        self._update_add_btn()

    def _suggest_name(self):
        if self._name_row.get_text().strip():
            return
        idx = self._type_row.get_selected()
        ct = _CONSTRAINT_TYPES[idx]
        prefix = {
            'PRIMARY KEY': 'pk',
            'UNIQUE': 'uq',
            'CHECK': 'chk',
            'FOREIGN KEY': 'fk',
        }.get(ct, 'con')
        self._name_row.set_text(f'{prefix}_{self._table_name}')

    def _update_add_btn(self, *_):
        idx = self._type_row.get_selected()
        ct = _CONSTRAINT_TYPES[idx]
        name = self._name_row.get_text().strip()
        if not name:
            self._add_btn.set_sensitive(False)
            return
        if ct in ('PRIMARY KEY', 'UNIQUE'):
            ok = any(r.get_active() for r in self._col_checks.values())
        elif ct == 'CHECK':
            ok = bool(self._check_row.get_text().strip())
        else:  # FOREIGN KEY
            ok = (bool(self._fk_ref_table_row.get_text().strip()) and
                  bool(self._fk_ref_col_row.get_text().strip()))
        self._add_btn.set_sensitive(ok)

    def _on_add_clicked(self, _btn):
        idx = self._type_row.get_selected()
        ct = _CONSTRAINT_TYPES[idx]
        name = self._name_row.get_text().strip()

        def qi(n):
            return '"' + n.replace('"', '""') + '"'

        if ct in ('PRIMARY KEY', 'UNIQUE'):
            cols = [c for c in self._col_names if self._col_checks[c].get_active()]
            col_list = ', '.join(qi(c) for c in cols)
            constraint_sql = f'{ct} ({col_list})'
        elif ct == 'CHECK':
            expr = self._check_row.get_text().strip()
            constraint_sql = f'CHECK ({expr})'
        else:  # FOREIGN KEY
            local_col = self._col_names[self._fk_local_row.get_selected()]
            ref_table = self._fk_ref_table_row.get_text().strip()
            ref_col = self._fk_ref_col_row.get_text().strip()
            on_upd = _FK_ACTIONS[self._fk_on_update_row.get_selected()]
            on_del = _FK_ACTIONS[self._fk_on_delete_row.get_selected()]
            constraint_sql = (
                f'FOREIGN KEY ({qi(local_col)}) REFERENCES {ref_table} ({qi(ref_col)})'
                f' ON UPDATE {on_upd} ON DELETE {on_del}'
            )

        self.close()
        self._on_save(name, constraint_sql)


# ---------------------------------------------------------------------------
# Create Table dialog  (#82, #85)
# ---------------------------------------------------------------------------

class CreateTableDialog(Adw.Dialog):
    """Dialog for creating a new PostgreSQL table from the database browser.

    schemas        – list of schema names available on the connection
    default_schema – schema to pre-select
    on_save(ddl)   – called with the full CREATE TABLE SQL string on confirm
    """

    def __init__(self, schemas, default_schema, on_save,
                 prefill_name=None, prefill_columns=None):
        """
        prefill_name    – optional initial value for the table name field
        prefill_columns – optional list of dicts: {name, type, nullable, default, is_pk}
                          used by Clone Structure to pre-populate column rows
        """
        super().__init__(title='Create Table', content_width=540, content_height=520)
        self._on_save = on_save
        self._schemas = schemas if schemas else ['public']
        self._col_rows = []
        self._pk_updating = False

        header = Adw.HeaderBar()
        self._create_btn = Gtk.Button(label='Create')
        self._create_btn.add_css_class('suggested-action')
        self._create_btn.set_sensitive(False)
        self._create_btn.connect('clicked', self._on_create_clicked)
        header.pack_end(self._create_btn)

        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(header)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        outer.set_margin_top(12)
        outer.set_margin_bottom(12)
        outer.set_margin_start(12)
        outer.set_margin_end(12)

        # ── Table definition ────────────────────────────────────────────────
        table_group = Adw.PreferencesGroup()

        self._name_row = Adw.EntryRow(title='Table name')
        self._name_row.connect('changed', self._on_form_changed)
        table_group.add(self._name_row)

        if len(self._schemas) > 1:
            schema_model = Gtk.StringList.new(self._schemas)
            self._schema_combo = Adw.ComboRow(title='Schema', model=schema_model)
            if default_schema in self._schemas:
                self._schema_combo.set_selected(self._schemas.index(default_schema))
            self._schema_combo.connect('notify::selected', self._on_form_changed)
            table_group.add(self._schema_combo)
        else:
            self._schema_combo = None

        outer.append(table_group)

        # ── Column list ─────────────────────────────────────────────────────
        col_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        col_header.set_margin_bottom(4)
        col_lbl = Gtk.Label(label='Columns')
        col_lbl.add_css_class('heading')
        col_lbl.set_xalign(0)
        col_lbl.set_hexpand(True)

        add_col_btn = Gtk.Button(icon_name='list-add-symbolic')
        add_col_btn.add_css_class('flat')
        add_col_btn.set_tooltip_text('Add column')
        add_col_btn.connect('clicked', lambda _: self._add_col_row())
        col_header.append(col_lbl)
        col_header.append(add_col_btn)
        outer.append(col_header)

        self._col_list = Gtk.ListBox()
        self._col_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._col_list.add_css_class('boxed-list')
        col_frame = Gtk.Frame()
        col_frame.set_child(self._col_list)
        outer.append(col_frame)

        reorder_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        reorder_box.set_halign(Gtk.Align.START)
        self._up_btn = Gtk.Button(icon_name='go-up-symbolic')
        self._up_btn.add_css_class('flat')
        self._up_btn.set_tooltip_text('Move column up')
        self._up_btn.connect('clicked', self._move_up)
        self._down_btn = Gtk.Button(icon_name='go-down-symbolic')
        self._down_btn.add_css_class('flat')
        self._down_btn.set_tooltip_text('Move column down')
        self._down_btn.connect('clicked', self._move_down)
        reorder_box.append(self._up_btn)
        reorder_box.append(self._down_btn)
        outer.append(reorder_box)

        # ── DDL preview (collapsible) ───────────────────────────────────────
        self._preview_buf, preview_view = _make_sql_preview_view()

        preview_scroll = Gtk.ScrolledWindow()
        preview_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        preview_scroll.set_min_content_height(120)
        preview_scroll.set_child(preview_view)

        preview_frame = Gtk.Frame()
        preview_frame.set_child(preview_scroll)

        preview_inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        preview_inner.set_margin_top(8)
        preview_inner.set_margin_start(8)
        preview_inner.set_margin_end(8)
        preview_inner.set_margin_bottom(8)
        preview_inner.append(preview_frame)

        self._preview_revealer = Gtk.Revealer()
        self._preview_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_UP)
        self._preview_revealer.set_reveal_child(False)
        self._preview_revealer.set_child(preview_inner)

        # Toggle bar — entire row is clickable via GestureClick on the box
        toggle_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        toggle_row.set_margin_start(12)
        toggle_row.set_margin_end(8)
        toggle_row.set_margin_top(2)
        toggle_row.set_margin_bottom(2)

        preview_toggle_lbl = Gtk.Label(label='Preview SQL')
        preview_toggle_lbl.set_xalign(0)
        preview_toggle_lbl.add_css_class('caption')
        toggle_row.append(preview_toggle_lbl)

        copy_btn = Gtk.Button(label='Copy')
        copy_btn.set_tooltip_text('Copy SQL to clipboard')
        copy_btn.add_css_class('flat')
        copy_btn.connect('clicked', self._copy_preview)
        toggle_row.append(copy_btn)

        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        toggle_row.append(spacer)

        self._preview_chevron = Gtk.Image.new_from_icon_name('pan-up-symbolic')
        toggle_row.append(self._preview_chevron)

        row_gesture = Gtk.GestureClick(button=1)
        row_gesture.connect('released', lambda g, _n, _x, _y: self._toggle_preview(g))
        toggle_row.add_controller(row_gesture)

        bottom_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        bottom_box.append(Gtk.Separator())
        bottom_box.append(self._preview_revealer)
        bottom_box.append(toggle_row)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        scroll.set_child(outer)

        toolbar_view.set_content(scroll)
        toolbar_view.add_bottom_bar(bottom_box)
        self.set_child(toolbar_view)

        # Pre-fill name and columns (Clone Structure) or start with one empty row
        if prefill_name:
            self._name_row.set_text(prefill_name)
        if prefill_columns:
            for col in prefill_columns:
                self._add_col_row(
                    name=col.get('name', ''),
                    pg_type=col.get('type', 'text'),
                    nullable=col.get('nullable', True),
                    default=col.get('default', ''),
                    is_pk=col.get('is_pk', False),
                )
        else:
            self._add_col_row()

    def _toggle_preview(self, _):
        revealed = not self._preview_revealer.get_reveal_child()
        self._preview_revealer.set_reveal_child(revealed)
        self._preview_chevron.set_from_icon_name(
            'pan-down-symbolic' if revealed else 'pan-up-symbolic'
        )

    def _get_schema(self):
        if self._schema_combo is not None:
            idx = self._schema_combo.get_selected()
            return self._schemas[idx] if 0 <= idx < len(self._schemas) else self._schemas[0]
        return self._schemas[0]

    def _add_col_row(self, name='', pg_type='text', nullable=True, default='', is_pk=False):
        row = Gtk.ListBoxRow()

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        box.set_margin_top(6)
        box.set_margin_bottom(6)
        box.set_margin_start(8)
        box.set_margin_end(8)

        name_entry = Gtk.Entry()
        name_entry.set_placeholder_text('column_name')
        name_entry.set_hexpand(True)
        name_entry.set_text(name)
        name_entry.connect('changed', self._on_form_changed)

        type_entry = Gtk.Entry()
        type_entry.set_text(pg_type or 'text')
        type_entry.set_width_chars(8)
        type_entry.connect('changed', self._on_form_changed)

        type_btn = _make_type_picker_popover(type_entry)

        null_btn = Gtk.ToggleButton(label='NULL')
        null_btn.set_tooltip_text('Allow NULL values')
        null_btn.set_active(nullable)
        null_btn.add_css_class('flat')
        null_btn.connect('toggled', self._on_form_changed)

        pk_btn = Gtk.ToggleButton(label='PK')
        pk_btn.set_tooltip_text('Set as primary key')
        pk_btn.set_active(is_pk)
        pk_btn.add_css_class('flat')
        pk_btn.connect('toggled', self._on_pk_toggled, row)

        default_entry = Gtk.Entry()
        default_entry.set_placeholder_text('default')
        default_entry.set_width_chars(7)
        default_entry.set_text(default or '')
        default_entry.set_tooltip_text('DEFAULT value (optional)')
        default_entry.connect('changed', self._on_form_changed)

        rm_btn = Gtk.Button(icon_name='list-remove-symbolic')
        rm_btn.add_css_class('flat')
        rm_btn.set_tooltip_text('Remove column')
        rm_btn.connect('clicked', self._remove_col_row, row)

        box.append(name_entry)
        box.append(type_entry)
        box.append(type_btn)
        box.append(null_btn)
        box.append(pk_btn)
        box.append(default_entry)
        box.append(rm_btn)
        row.set_child(box)

        row._name_entry = name_entry
        row._type_entry = type_entry
        row._null_btn = null_btn
        row._pk_btn = pk_btn
        row._default_entry = default_entry

        self._col_list.append(row)
        self._col_rows.append(row)
        self._on_form_changed()
        if not name:
            name_entry.grab_focus()

    def _remove_col_row(self, _btn, row):
        self._col_list.remove(row)
        self._col_rows.remove(row)
        self._on_form_changed()

    def _on_pk_toggled(self, btn, row):
        if self._pk_updating:
            return
        self._pk_updating = True
        if btn.get_active():
            for r in self._col_rows:
                if r is not row and r._pk_btn.get_active():
                    r._pk_btn.set_active(False)
        self._pk_updating = False
        self._on_form_changed()

    def _selected_idx(self):
        row = self._col_list.get_selected_row()
        if row is None or row not in self._col_rows:
            return -1
        return self._col_rows.index(row)

    def _move_up(self, _btn):
        idx = self._selected_idx()
        if idx <= 0:
            return
        self._col_rows.insert(idx - 1, self._col_rows.pop(idx))
        self._rebuild_col_list(idx - 1)

    def _move_down(self, _btn):
        idx = self._selected_idx()
        if idx < 0 or idx >= len(self._col_rows) - 1:
            return
        self._col_rows.insert(idx + 1, self._col_rows.pop(idx))
        self._rebuild_col_list(idx + 1)

    def _rebuild_col_list(self, select_idx=None):
        child = self._col_list.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self._col_list.remove(child)
            child = nxt
        for row in self._col_rows:
            self._col_list.append(row)
        if select_idx is not None and 0 <= select_idx < len(self._col_rows):
            self._col_list.select_row(self._col_rows[select_idx])
        self._on_form_changed()

    def _generate_ddl(self):
        def qi(name):
            return '"' + name.replace('"', '""') + '"'

        schema = self._get_schema()
        table = self._name_row.get_text().strip()
        if not table:
            return ''

        col_defs = []
        pk_cols = []
        for row in self._col_rows:
            name = row._name_entry.get_text().strip()
            pg_type = row._type_entry.get_text().strip() or 'text'
            nullable = row._null_btn.get_active()
            is_pk = row._pk_btn.get_active()
            default = row._default_entry.get_text().strip()
            if not name:
                continue
            parts = [f'{qi(name)} {pg_type}']
            if not nullable:
                parts.append('NOT NULL')
            if default:
                parts.append(f'DEFAULT {default}')
            col_defs.append('    ' + ' '.join(parts))
            if is_pk:
                pk_cols.append(name)

        if pk_cols:
            col_defs.append(f'    PRIMARY KEY ({", ".join(qi(c) for c in pk_cols)})')

        if not col_defs:
            return f'CREATE TABLE {qi(schema)}.{qi(table)}\n(\n);'

        return (
            f'CREATE TABLE {qi(schema)}.{qi(table)}\n(\n'
            + ',\n'.join(col_defs)
            + '\n);'
        )

    def _on_form_changed(self, *_):
        table = self._name_row.get_text().strip()
        has_named_col = any(r._name_entry.get_text().strip() for r in self._col_rows)
        self._create_btn.set_sensitive(bool(table) and has_named_col)
        self._preview_buf.set_text(self._generate_ddl())

    def _copy_preview(self, _btn):
        text = self._preview_buf.get_text(
            self._preview_buf.get_start_iter(),
            self._preview_buf.get_end_iter(),
            False,
        )
        Gdk.Display.get_default().get_clipboard().set(text)

    def _on_create_clicked(self, _btn):
        forbidden = (';', '--', '/*', '*/', '\x00')
        for row in self._col_rows:
            pg_type = row._type_entry.get_text().strip()
            for token in forbidden:
                if token in pg_type:
                    dlg = Adw.AlertDialog(
                        heading='Invalid Column Type',
                        body=f'Column type contains disallowed characters: "{token}"',
                    )
                    dlg.add_response('ok', 'OK')
                    dlg.present(self)
                    return
        ddl = self._generate_ddl()
        self._create_btn.set_sensitive(False)
        self._on_save(ddl, self._on_execute_done)

    def _on_execute_done(self, error=None):
        if error is None:
            self.close()
        else:
            self._create_btn.set_sensitive(True)
            err_dlg = Adw.AlertDialog(heading='Create Table Failed', body=error)
            err_dlg.add_response('ok', 'OK')
            err_dlg.present(self)


# ---------------------------------------------------------------------------
# Create Schema dialog  (#97)
# ---------------------------------------------------------------------------

class CreateSchemaDialog(Adw.Dialog):
    """Dialog for creating a new PostgreSQL schema.

    on_save(schema_name, on_done) — on_done(error=None) called on completion
    """

    def __init__(self, on_save):
        super().__init__(title='New Schema', content_width=380)
        self._on_save = on_save

        header = Adw.HeaderBar()
        self._create_btn = Gtk.Button(label='Create')
        self._create_btn.add_css_class('suggested-action')
        self._create_btn.set_sensitive(False)
        self._create_btn.connect('clicked', self._on_create_clicked)
        header.pack_end(self._create_btn)

        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(header)

        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup()
        self._name_row = Adw.EntryRow(title='Schema name')
        self._name_row.connect('changed', self._on_changed)
        self._name_row.connect('entry-activated', self._on_entry_activated)
        group.add(self._name_row)
        page.add(group)
        toolbar_view.set_content(page)
        self.set_child(toolbar_view)

    def _on_changed(self, *_):
        self._create_btn.set_sensitive(bool(self._name_row.get_text().strip()))

    def _on_entry_activated(self, *_):
        if self._create_btn.get_sensitive():
            self._on_create_clicked()

    def _on_create_clicked(self, *_):
        self._create_btn.set_sensitive(False)
        self._on_save(self._name_row.get_text().strip(), self._on_done)

    def _on_done(self, error=None):
        if error is None:
            self.close()
        else:
            self._create_btn.set_sensitive(True)
            err_dlg = Adw.AlertDialog(heading='Create Schema Failed', body=error)
            err_dlg.add_response('ok', 'OK')
            err_dlg.present(self)


# ---------------------------------------------------------------------------
# Create View dialog  (#95)
# ---------------------------------------------------------------------------

class CreateViewDialog(Adw.Dialog):
    """Dialog for creating a new PostgreSQL view.

    on_save(schema, name, sql_def, on_done) — on_done(error=None) called on completion
    schema — the schema to create the view in
    """

    def __init__(self, schema, on_save):
        super().__init__(title='New View', content_width=560, content_height=480)
        self._on_save = on_save
        self._schema = schema

        header = Adw.HeaderBar()
        self._create_btn = Gtk.Button(label='Create')
        self._create_btn.add_css_class('suggested-action')
        self._create_btn.set_sensitive(False)
        self._create_btn.connect('clicked', self._on_create_clicked)
        header.pack_end(self._create_btn)

        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(header)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        outer.set_margin_top(12)
        outer.set_margin_bottom(12)
        outer.set_margin_start(12)
        outer.set_margin_end(12)

        name_group = Adw.PreferencesGroup()
        self._name_row = Adw.EntryRow(title='View name')
        self._name_row.connect('changed', self._on_changed)
        name_group.add(self._name_row)
        outer.append(name_group)

        sql_label = Gtk.Label(label='SELECT definition')
        sql_label.set_xalign(0)
        sql_label.add_css_class('caption')
        sql_label.add_css_class('dim-label')
        outer.append(sql_label)

        self._sql_buf, sql_view = _make_sql_preview_view()
        self._sql_buf.connect('changed', self._on_changed)
        # Make the view editable
        sql_view.set_editable(True)
        sql_view.set_cursor_visible(True)
        placeholder = 'SELECT col1, col2\nFROM ...\nWHERE ...'
        self._sql_buf.set_text(placeholder)
        # Select all placeholder text so user can just start typing
        sql_view.connect('realize', lambda v: (
            v.get_buffer().select_range(
                v.get_buffer().get_start_iter(),
                v.get_buffer().get_end_iter(),
            )
        ))

        sql_scroll = Gtk.ScrolledWindow()
        sql_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        sql_scroll.set_vexpand(True)
        sql_scroll.set_child(sql_view)

        sql_frame = Gtk.Frame()
        sql_frame.set_child(sql_scroll)
        sql_frame.set_vexpand(True)
        outer.append(sql_frame)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        scroll.set_child(outer)

        toolbar_view.set_content(scroll)
        self.set_child(toolbar_view)

    def _on_changed(self, *_):
        name = self._name_row.get_text().strip()
        sql = self._sql_buf.get_text(
            self._sql_buf.get_start_iter(), self._sql_buf.get_end_iter(), False
        ).strip()
        self._create_btn.set_sensitive(bool(name) and bool(sql))

    def _on_create_clicked(self, *_):
        self._create_btn.set_sensitive(False)
        name = self._name_row.get_text().strip()
        sql = self._sql_buf.get_text(
            self._sql_buf.get_start_iter(), self._sql_buf.get_end_iter(), False
        ).strip()
        self._on_save(self._schema, name, sql, self._on_done)

    def _on_done(self, error=None):
        if error is None:
            self.close()
        else:
            self._create_btn.set_sensitive(True)
            err_dlg = Adw.AlertDialog(heading='Create View Failed', body=error)
            err_dlg.add_response('ok', 'OK')
            err_dlg.present(self)
