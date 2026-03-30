import json
import os
import re
import threading
import time

import prefs

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Gtk, Adw, GObject, GLib, Gdk, Gio

from data_grid import make_column_view

_HISTORY_LIMIT = 50

# Optional GtkSourceView for syntax highlighting
try:
    gi.require_version('GtkSource', '5')
    from gi.repository import GtkSource
    _HAS_SOURCE = True

except (ValueError, ImportError):
    _HAS_SOURCE = False

_AUTOSAVE_DELAY_MS = 800


def _split_statements(sql):
    """Split SQL text into individual non-empty statements.

    Splits on semicolons while respecting:
    - Single-quoted strings  ('...')
    - Double-quoted identifiers  ("...")
    - PostgreSQL dollar-quoted strings  ($$...$$, $tag$...$tag$)
    - Line comments  (-- ...)
    - Block comments  (/* ... */)
    """
    statements = []
    current = []
    i = 0
    n = len(sql)

    while i < n:
        c = sql[i]

        # Line comment — consume to end of line
        if c == '-' and i + 1 < n and sql[i + 1] == '-':
            end = sql.find('\n', i)
            if end == -1:
                current.append(sql[i:])
                i = n
            else:
                current.append(sql[i:end + 1])
                i = end + 1

        # Block comment
        elif c == '/' and i + 1 < n and sql[i + 1] == '*':
            end = sql.find('*/', i + 2)
            if end == -1:
                current.append(sql[i:])
                i = n
            else:
                current.append(sql[i:end + 2])
                i = end + 2

        # Dollar-quoted string (PostgreSQL)
        elif c == '$':
            tag_end = sql.find('$', i + 1)
            if tag_end != -1:
                tag = sql[i:tag_end + 1]
                close = sql.find(tag, tag_end + 1)
                if close != -1:
                    current.append(sql[i:close + len(tag)])
                    i = close + len(tag)
                else:
                    current.append(c)
                    i += 1
            else:
                current.append(c)
                i += 1

        # Single-quoted string
        elif c == "'":
            j = i + 1
            while j < n:
                if sql[j] == "'":
                    if j + 1 < n and sql[j + 1] == "'":
                        j += 2  # escaped quote
                    else:
                        j += 1
                        break
                elif sql[j] == '\\':
                    j += 2
                else:
                    j += 1
            current.append(sql[i:j])
            i = j

        # Double-quoted identifier
        elif c == '"':
            j = i + 1
            while j < n:
                if sql[j] == '"':
                    if j + 1 < n and sql[j + 1] == '"':
                        j += 2
                    else:
                        j += 1
                        break
                else:
                    j += 1
            current.append(sql[i:j])
            i = j

        # Statement terminator
        elif c == ';':
            stmt = ''.join(current).strip()
            if stmt:
                statements.append(stmt)
            current = []
            i += 1

        else:
            current.append(c)
            i += 1

    # Trailing statement without semicolon
    stmt = ''.join(current).strip()
    if stmt:
        statements.append(stmt)

    return [s for s in statements if not _is_comment_only(s)]


_COMMENT_ONLY_RE = re.compile(r'(--[^\n]*|/\*.*?\*/)', re.DOTALL)


def _is_comment_only(stmt):
    return not _COMMENT_ONLY_RE.sub('', stmt).strip()


def _statement_at_offset(sql, offset):
    """Return the SQL statement whose extent contains the given character offset.

    Uses the same quoting/comment rules as _split_statements.  Falls back to
    the nearest preceding statement when the cursor sits in whitespace between
    statements (e.g. after a semicolon).
    """
    statements = []   # list of (stmt_text, raw_start, raw_end)
    current = []
    stmt_start = 0
    i = 0
    n = len(sql)

    while i < n:
        c = sql[i]

        if c == '-' and i + 1 < n and sql[i + 1] == '-':
            end = sql.find('\n', i)
            if end == -1:
                current.append(sql[i:])
                i = n
            else:
                current.append(sql[i:end + 1])
                i = end + 1

        elif c == '/' and i + 1 < n and sql[i + 1] == '*':
            end = sql.find('*/', i + 2)
            if end == -1:
                current.append(sql[i:])
                i = n
            else:
                current.append(sql[i:end + 2])
                i = end + 2

        elif c == '$':
            tag_end = sql.find('$', i + 1)
            if tag_end != -1:
                tag = sql[i:tag_end + 1]
                close = sql.find(tag, tag_end + 1)
                if close != -1:
                    current.append(sql[i:close + len(tag)])
                    i = close + len(tag)
                else:
                    current.append(c)
                    i += 1
            else:
                current.append(c)
                i += 1

        elif c == "'":
            j = i + 1
            while j < n:
                if sql[j] == "'":
                    if j + 1 < n and sql[j + 1] == "'":
                        j += 2
                    else:
                        j += 1
                        break
                elif sql[j] == '\\':
                    j += 2
                else:
                    j += 1
            current.append(sql[i:j])
            i = j

        elif c == '"':
            j = i + 1
            while j < n:
                if sql[j] == '"':
                    if j + 1 < n and sql[j + 1] == '"':
                        j += 2
                    else:
                        j += 1
                        break
                else:
                    j += 1
            current.append(sql[i:j])
            i = j

        elif c == ';':
            stmt = ''.join(current).strip()
            if stmt and not _is_comment_only(stmt):
                statements.append((stmt, stmt_start, i))
            current = []
            stmt_start = i + 1
            i += 1

        else:
            current.append(c)
            i += 1

    stmt = ''.join(current).strip()
    if stmt and not _is_comment_only(stmt):
        statements.append((stmt, stmt_start, n))

    if not statements:
        return ''

    # Return the statement whose raw range contains the cursor
    for text, start, end in statements:
        if start <= offset <= end:
            return text

    # Cursor is past all statements — return the last one
    return statements[-1][0]


def _make_editor():
    """Return (buffer, view) using GtkSourceView if available."""
    if _HAS_SOURCE:
        buf = GtkSource.Buffer()
        lang = GtkSource.LanguageManager.get_default().get_language('sql')
        if lang:
            buf.set_language(lang)
        view = GtkSource.View.new_with_buffer(buf)
        view.set_show_line_numbers(True)
        view.set_highlight_current_line(True)
        view.set_tab_width(4)
        view.set_indent_width(4)
        view.set_insert_spaces_instead_of_tabs(True)
        return buf, view
    else:
        buf = Gtk.TextBuffer()
        view = Gtk.TextView(buffer=buf)
        return buf, view


def _apply_scheme(buf, dark):
    if not _HAS_SOURCE:
        return
    mgr = GtkSource.StyleSchemeManager.get_default()
    name = 'Adwaita-dark' if dark else 'Adwaita'
    scheme = mgr.get_scheme(name) or mgr.get_scheme('classic')
    if scheme:
        buf.set_style_scheme(scheme)


_DDL_RE = re.compile(r'\b(CREATE|DROP|ALTER)\b', re.IGNORECASE)
_AUTOCOMMIT_RE = re.compile(r'^\s*(CREATE|DROP)\s+DATABASE\b', re.IGNORECASE)


class SqlEditor(Gtk.Box):
    __gsignals__ = {
        'run-sql':          (GObject.SignalFlags.RUN_FIRST, None, ()),
        'run-selected-sql': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'ddl-executed':     (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, file_path):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.file_path = file_path
        self._modified = False
        self._connection = None
        self._autosave_timer = 0
        self._save_label_timer = 0
        self._dark_handler_id = 0
        self._active_conn = None
        self._cancel_event = threading.Event()
        self._last_sql = ''
        self._history = []
        self._run_start_time = time.monotonic()
        self._explain_last_sql = ''
        self._explain_last_conn = None
        self._explain_is_analyze = False
        self._explain_json_cache = None
        self._build_ui()
        self._load_file()
        self.connect('destroy', self._on_destroy)

        # Track system dark/light for scheme updates
        if _HAS_SOURCE:
            style_mgr = Adw.StyleManager.get_default()
            _apply_scheme(self._buffer, style_mgr.get_dark())
            self._dark_handler_id = style_mgr.connect(
                'notify::dark', lambda m, _: _apply_scheme(self._buffer, m.get_dark()))

    def _build_ui(self):
        # ── Toolbar ───────────────────────────────────────────────────────────
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        toolbar.set_margin_start(10)
        toolbar.set_margin_end(10)
        toolbar.set_margin_top(6)
        toolbar.set_margin_bottom(6)

        self._modified_dot = Gtk.Label(label='●')
        self._modified_dot.add_css_class('accent')
        self._modified_dot.set_visible(False)
        self._modified_dot.set_tooltip_text('Unsaved changes')

        self._save_label = Gtk.Label(label='Saved')
        self._save_label.add_css_class('caption')
        self._save_label.add_css_class('dim-label')
        self._save_label.set_visible(False)

        save_btn = Gtk.Button(icon_name='document-save-symbolic')
        save_btn.add_css_class('flat')
        save_btn.set_tooltip_text('Save now  Ctrl+S')
        save_btn.connect('clicked', lambda _: self._save_now())

        spacer = Gtk.Box()
        spacer.set_hexpand(True)

        self._conn_label = Gtk.Label()
        self._conn_label.add_css_class('caption')
        self._conn_label.add_css_class('dim-label')

        self._run_sel_btn = Gtk.Button(label='Run Selected')
        self._run_sel_btn.set_icon_name('media-playback-start-symbolic')
        self._run_sel_btn.add_css_class('pill')
        self._run_sel_btn.set_sensitive(False)
        self._run_sel_btn.set_tooltip_text('Run selected / at cursor  Ctrl+Enter')
        self._run_sel_btn.connect('clicked', lambda _: self.emit('run-selected-sql'))

        self._run_btn = Gtk.Button(label='Run All')
        self._run_btn.set_icon_name('media-skip-forward-symbolic')
        self._run_btn.add_css_class('suggested-action')
        self._run_btn.add_css_class('pill')
        self._run_btn.set_sensitive(False)
        self._run_btn.set_tooltip_text('Run all  F5')
        self._run_btn.connect('clicked', lambda _: self.emit('run-sql'))

        # Explain split button: [Explain | ▾]
        self._explain_btn = Gtk.Button(label='Explain')
        self._explain_btn.add_css_class('flat')
        self._explain_btn.set_sensitive(False)
        self._explain_btn.set_tooltip_text('EXPLAIN current query')
        self._explain_btn.connect('clicked', lambda _: self._run_explain('explain'))

        explain_menu = Gio.Menu()
        explain_menu.append('Explain', 'explain.run-explain')
        explain_menu.append('Explain Analyze', 'explain.run-explain-analyze')

        self._explain_menu_btn = Gtk.MenuButton()
        self._explain_menu_btn.set_icon_name('pan-down-symbolic')
        self._explain_menu_btn.add_css_class('flat')
        self._explain_menu_btn.set_sensitive(False)
        self._explain_menu_btn.set_menu_model(explain_menu)

        explain_ag = Gio.SimpleActionGroup()
        a_explain = Gio.SimpleAction.new('run-explain', None)
        a_explain.connect('activate', lambda *_: self._run_explain('explain'))
        explain_ag.add_action(a_explain)
        a_analyze = Gio.SimpleAction.new('run-explain-analyze', None)
        a_analyze.connect('activate', lambda *_: self._confirm_explain_analyze())
        explain_ag.add_action(a_analyze)
        self._explain_btn.insert_action_group('explain', explain_ag)
        self._explain_menu_btn.insert_action_group('explain', explain_ag)

        explain_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        explain_box.add_css_class('linked')
        explain_box.append(self._explain_btn)
        explain_box.append(self._explain_menu_btn)

        self._cancel_btn = Gtk.Button(label='Cancel')
        self._cancel_btn.set_icon_name('media-playback-stop-symbolic')
        self._cancel_btn.add_css_class('destructive-action')
        self._cancel_btn.add_css_class('pill')
        self._cancel_btn.set_tooltip_text('Cancel running query')
        self._cancel_btn.connect('clicked', self._on_cancel)

        run_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        run_box.append(explain_box)
        run_box.append(self._run_sel_btn)
        run_box.append(self._run_btn)

        cancel_box = Gtk.Box()
        cancel_box.append(self._cancel_btn)

        # Stack avoids set_visible() on siblings, which triggers a GTK CSS node bug
        self._run_stack = Gtk.Stack()
        self._run_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._run_stack.set_transition_duration(80)
        self._run_stack.add_named(run_box, 'run')
        self._run_stack.add_named(cancel_box, 'cancel')

        toolbar.append(self._modified_dot)
        toolbar.append(self._save_label)
        toolbar.append(save_btn)
        toolbar.append(spacer)
        toolbar.append(self._conn_label)
        toolbar.append(self._run_stack)

        self.append(toolbar)
        self.append(Gtk.Separator())

        # ── Editor ────────────────────────────────────────────────────────────
        self._buffer, self._editor = _make_editor()
        self._buffer.connect('changed', self._on_changed)

        self._editor.set_monospace(True)
        self._editor.set_wrap_mode(Gtk.WrapMode.NONE)
        self._editor.set_top_margin(12)
        self._editor.set_bottom_margin(12)
        self._editor.set_left_margin(12)
        self._editor.set_right_margin(12)

        key_ctrl = Gtk.EventControllerKey()
        key_ctrl.connect('key-pressed', self._on_key_pressed)
        self._editor.add_controller(key_ctrl)

        editor_scroll = Gtk.ScrolledWindow()
        editor_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        editor_scroll.set_vexpand(True)
        editor_scroll.set_child(self._editor)

        # ── Results pane ──────────────────────────────────────────────────────
        # Spinner + meta shown as tab bar end-action widgets
        self._results_spinner = Gtk.Spinner()
        self._results_spinner.set_size_request(16, 16)
        self._results_spinner.set_margin_end(4)

        self._results_meta = Gtk.Label()
        self._results_meta.add_css_class('caption')
        self._results_meta.add_css_class('dim-label')
        self._results_meta.set_margin_end(8)

        meta_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        meta_box.append(self._results_spinner)
        meta_box.append(self._results_meta)

        # Content stack (used by the permanent "Results" tab)
        self._results_stack = Gtk.Stack()

        self._results_message = Gtk.Label()
        self._results_message.set_xalign(0)
        self._results_message.set_margin_start(12)
        self._results_message.set_margin_top(10)
        self._results_message.set_wrap(True)
        self._results_stack.add_named(self._results_message, 'message')

        self._results_scroll = Gtk.ScrolledWindow()
        self._results_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self._results_scroll.set_vexpand(True)
        self._results_stack.add_named(self._results_scroll, 'grid')

        self._results_log = Gtk.ListBox()
        self._results_log.set_selection_mode(Gtk.SelectionMode.NONE)
        self._results_log.add_css_class('boxed-list')
        self._results_log.set_margin_start(12)
        self._results_log.set_margin_end(12)
        self._results_log.set_margin_top(10)
        self._results_log.set_margin_bottom(10)
        log_scroll = Gtk.ScrolledWindow()
        log_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        log_scroll.set_vexpand(True)
        log_scroll.set_child(self._results_log)
        self._results_stack.add_named(log_scroll, 'log')

        # ── EXPLAIN results pane ──────────────────────────────────────────────
        self._explain_copy_confirm = Gtk.Label()
        self._explain_copy_confirm.add_css_class('caption')
        self._explain_copy_confirm.add_css_class('success')
        self._explain_copy_confirm.set_visible(False)
        self._explain_copy_confirm_timer = 0

        copy_text_btn = Gtk.Button(label='Copy Text')
        copy_text_btn.add_css_class('flat')
        copy_text_btn.set_icon_name('edit-copy-symbolic')
        copy_text_btn.connect('clicked', self._on_explain_copy_text)

        copy_json_btn = Gtk.Button(label='Copy JSON')
        copy_json_btn.add_css_class('flat')
        copy_json_btn.set_icon_name('edit-copy-symbolic')
        copy_json_btn.connect('clicked', self._on_explain_copy_json)

        self._explain_tree_toggle = Gtk.ToggleButton(label='Tree')
        self._explain_tree_toggle.add_css_class('flat')
        self._explain_tree_toggle.set_icon_name('view-list-tree-symbolic')
        self._explain_tree_toggle.connect('toggled', self._on_explain_tree_toggled)

        self._explain_analyze_warning = Gtk.Label()
        self._explain_analyze_warning.add_css_class('caption')
        self._explain_analyze_warning.add_css_class('warning')
        self._explain_analyze_warning.set_label('⚠ EXPLAIN ANALYZE executed the query')
        self._explain_analyze_warning.set_visible(False)
        self._explain_analyze_warning.set_hexpand(True)
        self._explain_analyze_warning.set_xalign(0)
        self._explain_analyze_warning.set_margin_start(8)

        explain_toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        explain_toolbar.set_margin_start(6)
        explain_toolbar.set_margin_end(6)
        explain_toolbar.set_margin_top(4)
        explain_toolbar.set_margin_bottom(4)
        explain_toolbar.append(copy_text_btn)
        explain_toolbar.append(copy_json_btn)
        explain_toolbar.append(self._explain_tree_toggle)
        explain_toolbar.append(self._explain_analyze_warning)
        explain_toolbar.append(self._explain_copy_confirm)

        self._explain_text_buf = Gtk.TextBuffer()
        self._explain_text_view = Gtk.TextView(buffer=self._explain_text_buf)
        self._explain_text_view.set_editable(False)
        self._explain_text_view.set_monospace(True)
        self._explain_text_view.set_top_margin(8)
        self._explain_text_view.set_bottom_margin(8)
        self._explain_text_view.set_left_margin(10)
        self._explain_text_view.set_right_margin(10)
        self._explain_text_view.set_wrap_mode(Gtk.WrapMode.NONE)
        explain_text_scroll = Gtk.ScrolledWindow()
        explain_text_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        explain_text_scroll.set_vexpand(True)
        explain_text_scroll.set_child(self._explain_text_view)

        self._explain_tree_scroll = Gtk.ScrolledWindow()
        self._explain_tree_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self._explain_tree_scroll.set_vexpand(True)

        self._explain_inner_stack = Gtk.Stack()
        self._explain_inner_stack.add_named(explain_text_scroll, 'text')
        self._explain_inner_stack.add_named(self._explain_tree_scroll, 'tree')

        explain_outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        explain_outer.append(explain_toolbar)
        explain_outer.append(Gtk.Separator())
        explain_outer.append(self._explain_inner_stack)
        self._results_stack.add_named(explain_outer, 'explain')

        # Tab view — "Results" is always the first (pinned) tab;
        # SELECT query results appear as additional tabs beside it.
        self._results_tab_view = Adw.TabView()
        self._results_tab_view.set_vexpand(True)
        self._results_tab_view.connect('close-page', self._on_results_close_page)

        self._results_page = self._results_tab_view.append(self._results_stack)
        self._results_page.set_title('Results')
        self._results_tab_view.set_page_pinned(self._results_page, True)

        # ── History tab ───────────────────────────────────────────────────────
        self._history_list = Gtk.ListBox()
        self._history_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self._history_list.add_css_class('boxed-list')
        self._history_list.set_margin_start(12)
        self._history_list.set_margin_end(12)
        self._history_list.set_margin_top(10)
        self._history_list.set_margin_bottom(10)
        history_scroll = Gtk.ScrolledWindow()
        history_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        history_scroll.set_vexpand(True)
        history_scroll.set_child(self._history_list)

        self._history_page = self._results_tab_view.append(history_scroll)
        self._history_page.set_title('History')
        self._results_tab_view.set_page_pinned(self._history_page, True)

        results_tab_bar = Adw.TabBar()
        results_tab_bar.set_view(self._results_tab_view)
        results_tab_bar.set_autohide(True)
        results_tab_bar.set_end_action_widget(meta_box)

        results_outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        results_outer.append(Gtk.Separator())
        results_outer.append(results_tab_bar)
        results_outer.append(self._results_tab_view)

        self._paned = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL)
        self._paned.set_vexpand(True)
        self._paned.set_shrink_start_child(False)
        self._paned.set_shrink_end_child(False)
        self._paned.set_start_child(editor_scroll)
        self._paned.set_end_child(results_outer)
        self._paned.set_position(prefs.get('sql_pane_pos', 400))
        self._paned.connect('notify::position',
                            lambda p, _: prefs.put('sql_pane_pos', p.get_position()))

        self.append(self._paned)

    # ── File I/O ──────────────────────────────────────────────────────────────

    def _load_file(self):
        try:
            with open(self.file_path) as f:
                content = f.read()
        except OSError:
            content = ''
        self._buffer.set_text(content)
        self._set_modified(False)

    def _on_destroy(self, _widget):
        if self._autosave_timer:
            GLib.source_remove(self._autosave_timer)
            self._autosave_timer = 0
            if os.path.exists(self.file_path):
                self._do_save()
        if self._save_label_timer:
            GLib.source_remove(self._save_label_timer)
            self._save_label_timer = 0
        if self._explain_copy_confirm_timer:
            GLib.source_remove(self._explain_copy_confirm_timer)
            self._explain_copy_confirm_timer = 0
        if self._dark_handler_id:
            Adw.StyleManager.get_default().disconnect(self._dark_handler_id)
            self._dark_handler_id = 0

    def _save_now(self):
        if self._autosave_timer:
            GLib.source_remove(self._autosave_timer)
            self._autosave_timer = 0
        self._do_save()

    def _do_save(self):
        self._autosave_timer = 0  # timer fired and consumed itself; clear stale ID
        try:
            start = self._buffer.get_start_iter()
            end = self._buffer.get_end_iter()
            text = self._buffer.get_text(start, end, False)
            with open(self.file_path, 'w') as f:
                f.write(text)
            self._set_modified(False)
            if self._save_label_timer:
                GLib.source_remove(self._save_label_timer)
            self._save_label.set_visible(True)
            self._save_label_timer = GLib.timeout_add(2000, self._hide_save_label)
        except OSError as e:
            self.show_error(str(e))
        return False  # for GLib.timeout_add

    def _hide_save_label(self):
        self._save_label.set_visible(False)
        self._save_label_timer = 0
        return False

    def _set_modified(self, value):
        self._modified = value
        self._modified_dot.set_visible(value)

    def _on_changed(self, _buf):
        self._set_modified(True)
        if self._autosave_timer:
            GLib.source_remove(self._autosave_timer)
        self._autosave_timer = GLib.timeout_add(_AUTOSAVE_DELAY_MS, self._do_save)

    def _on_key_pressed(self, _ctrl, keyval, _code, state):
        ctrl = state & Gdk.ModifierType.CONTROL_MASK
        if ctrl and keyval == Gdk.KEY_s:
            self._save_now()
            return True
        if keyval == Gdk.KEY_F5 and self._run_btn.get_sensitive():
            self.emit('run-sql')
            return True
        if ctrl and keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and \
                self._run_sel_btn.get_sensitive():
            self.emit('run-selected-sql')
            return True
        if ctrl and keyval == Gdk.KEY_slash:
            self._toggle_comment()
            return True
        return False

    def _toggle_comment(self):
        buf = self._buffer
        bounds = buf.get_selection_bounds()
        if bounds:
            start_iter, end_iter = bounds
        else:
            cursor = buf.get_iter_at_mark(buf.get_insert())
            start_iter = cursor.copy()
            end_iter = cursor.copy()

        start_line = start_iter.get_line()
        end_line = end_iter.get_line()
        # If selection ends exactly at the start of a line (column 0), don't include it
        if bounds and end_iter.get_line_offset() == 0 and end_line > start_line:
            end_line -= 1

        # Collect line texts
        lines = []
        for ln in range(start_line, end_line + 1):
            _, it = buf.get_iter_at_line(ln)
            end_it = it.copy()
            if not end_it.ends_line():
                end_it.forward_to_line_end()
            lines.append(buf.get_text(it, end_it, False))

        # Determine toggle direction:
        # - All non-empty lines commented → remove comments from all
        # - Otherwise → add comments only to uncommented lines (never double-comment)
        non_empty = [l for l in lines if l.strip()]
        all_commented = non_empty and all(l.lstrip().startswith('--') for l in non_empty)

        buf.begin_user_action()
        for i, (ln, line_text) in enumerate(zip(range(start_line, end_line + 1), lines)):
            stripped = line_text.lstrip()
            is_commented = stripped.startswith('--')
            leading = len(line_text) - len(stripped)

            if all_commented:
                # Remove leading '--' (with optional space after)
                if stripped.startswith('-- '):
                    new_line = line_text[:leading] + stripped[3:]
                elif stripped.startswith('--'):
                    new_line = line_text[:leading] + stripped[2:]
                else:
                    new_line = line_text
            else:
                # Add '-- ' only to lines that are not already commented
                if is_commented or not stripped:
                    new_line = line_text  # leave commented/empty lines unchanged
                else:
                    new_line = line_text[:leading] + '-- ' + stripped

            _, it = buf.get_iter_at_line(ln)
            end_it = it.copy()
            if not end_it.ends_line():
                end_it.forward_to_line_end()
            buf.delete(it, end_it)
            _, insert_it = buf.get_iter_at_line(ln)
            buf.insert(insert_it, new_line)
        buf.end_user_action()

    # ── Connection ────────────────────────────────────────────────────────────

    def set_connection(self, conn):
        self._connection = conn
        if conn:
            self._conn_label.set_label(conn['name'])
            self._run_btn.set_sensitive(True)
            self._run_sel_btn.set_sensitive(True)
            self._explain_btn.set_sensitive(True)
            self._explain_menu_btn.set_sensitive(True)
        else:
            self._conn_label.set_label('')
            self._run_btn.set_sensitive(False)
            self._run_sel_btn.set_sensitive(False)
            self._explain_btn.set_sensitive(False)
            self._explain_menu_btn.set_sensitive(False)

    def is_modified(self):
        return self._modified

    # ── Results tab helpers ───────────────────────────────────────────────────

    def _on_results_close_page(self, view, page):
        # Pinned "Results" tab cannot be closed; all query-result tabs can be.
        view.close_page_finish(page, page is not self._results_page)
        return True

    def _clear_result_tabs(self):
        pages = self._results_tab_view.get_pages()
        to_close = [pages.get_item(i) for i in range(pages.get_n_items())
                    if pages.get_item(i) is not self._results_page]
        for page in to_close:
            self._results_tab_view.close_page(page)

    # ── Run ───────────────────────────────────────────────────────────────────

    def run(self):
        """Run All — always executes the full buffer."""
        if not self._connection:
            return
        start = self._buffer.get_start_iter()
        end = self._buffer.get_end_iter()
        sql = self._buffer.get_text(start, end, False).strip()
        self._start_run(sql)

    def run_selected(self):
        """Run Selected — executes the selection, or the statement at the cursor."""
        if not self._connection:
            return
        bounds = self._buffer.get_selection_bounds()
        if bounds:
            sql = self._buffer.get_text(bounds[0], bounds[1], False).strip()
        else:
            cursor = self._buffer.get_iter_at_mark(self._buffer.get_insert())
            start = self._buffer.get_start_iter()
            end = self._buffer.get_end_iter()
            full_text = self._buffer.get_text(start, end, False)
            sql = _statement_at_offset(full_text, cursor.get_offset())
        self._start_run(sql)

    def _start_run(self, sql, explain_mode=None):

        if not sql:
            return

        self._last_sql = sql
        self._run_start_time = time.monotonic()
        self._cancel_event.clear()
        self._clear_result_tabs()
        self._results_tab_view.set_selected_page(self._results_page)
        self._run_btn.set_sensitive(False)
        self._run_sel_btn.set_sensitive(False)
        self._explain_btn.set_sensitive(False)
        self._explain_menu_btn.set_sensitive(False)
        self._run_stack.set_visible_child_name('cancel')
        self._results_meta.set_label('')
        self._results_spinner.start()
        self._results_stack.set_visible_child_name('message')
        self._results_message.set_label('Running…')
        self._results_message.remove_css_class('error')

        if explain_mode:
            threading.Thread(
                target=self._execute_explain,
                args=(dict(self._connection), sql, explain_mode),
                daemon=True,
            ).start()
        else:
            threading.Thread(
                target=self._execute,
                args=(dict(self._connection), sql),
                daemon=True,
            ).start()

    def _on_cancel(self, _):
        self._cancel_event.set()
        conn = self._active_conn
        if conn:
            try:
                conn.cancel_safe()
            except AttributeError:
                try:
                    conn.cancel()
                except Exception:
                    pass
            except Exception:
                pass

    def _finish_run(self):
        """Restore run buttons; called by all result-display methods."""
        self._results_spinner.stop()
        self._run_stack.set_visible_child_name('run')
        has_conn = self._connection is not None
        self._run_btn.set_sensitive(has_conn)
        self._run_sel_btn.set_sensitive(has_conn)
        self._explain_btn.set_sensitive(has_conn)
        self._explain_menu_btn.set_sensitive(has_conn)

    def _execute(self, conn, sql):
        stmts = _split_statements(sql)
        if not stmts:
            GLib.idle_add(self.show_message, 'Nothing to execute')
            return

        # Single statement — keep existing inline-results behaviour
        if len(stmts) == 1:
            self._execute_single(conn, stmts[0])
            return

        # Multiple statements — collect results then show log
        results = []  # list of dicts: {stmt, kind, data}
        try:
            import psycopg
            from tunnel import open_db

            def _run_stmt(cur_or_db, stmt, autocommit):
                """Execute one statement; return result dict."""
                if autocommit:
                    # Needs its own autocommit connection
                    with open_db(conn, autocommit=True) as ac_db:
                        with ac_db.cursor() as ac_cur:
                            ac_cur.execute(stmt)
                            if ac_cur.description:
                                cols = [d.name for d in ac_cur.description]
                                rows = ac_cur.fetchall()
                                return {'stmt': stmt, 'kind': 'select', 'cols': cols, 'rows': rows}
                            return {'stmt': stmt, 'kind': 'status', 'count': ac_cur.rowcount}
                else:
                    cur_or_db.execute(stmt)
                    if cur_or_db.description:
                        cols = [d.name for d in cur_or_db.description]
                        rows = cur_or_db.fetchall()
                        return {'stmt': stmt, 'kind': 'select', 'cols': cols, 'rows': rows}
                    return {'stmt': stmt, 'kind': 'status', 'count': cur_or_db.rowcount}

            with open_db(conn) as db:
                self._active_conn = db
                cancelled = False
                try:
                    with db.cursor() as cur:
                        for stmt in stmts:
                            if self._cancel_event.is_set():
                                results.append({'stmt': stmt, 'kind': 'cancelled'})
                                cancelled = True
                                break
                            needs_ac = bool(_AUTOCOMMIT_RE.match(stmt))
                            try:
                                if needs_ac:
                                    db.commit()  # flush any pending work first
                                results.append(_run_stmt(cur, stmt, needs_ac))
                            except psycopg.errors.QueryCanceled:
                                results.append({'stmt': stmt, 'kind': 'cancelled'})
                                cancelled = True
                                break
                            except psycopg.Error as e:
                                msg = e.diag.message_primary or str(e) if hasattr(e, 'diag') else str(e)
                                if hasattr(e, 'diag') and e.diag.message_detail:
                                    msg += f'\nDetail: {e.diag.message_detail}'
                                if hasattr(e, 'diag') and e.diag.message_hint:
                                    msg += f'\nHint: {e.diag.message_hint}'
                                results.append({'stmt': stmt, 'kind': 'error', 'msg': msg})
                                break  # transaction is aborted; stop here
                    if cancelled:
                        db.rollback()
                    else:
                        db.commit()
                finally:
                    self._active_conn = None
        except Exception as e:
            results.append({'stmt': '', 'kind': 'error', 'msg': str(e)})

        GLib.idle_add(self._show_multi_results, results)

    def _execute_single(self, conn, sql):
        try:
            import psycopg
            from tunnel import open_db

            with open_db(conn, autocommit=bool(_AUTOCOMMIT_RE.match(sql))) as db:
                self._active_conn = db
                try:
                    with db.cursor() as cur:
                        cur.execute(sql)
                        if cur.description:
                            cols = [d.name for d in cur.description]
                            rows = cur.fetchall()
                            GLib.idle_add(self.show_results, cols, rows)
                        else:
                            count = cur.rowcount
                            msg = f'{count} row{"s" if count != 1 else ""} affected'
                            GLib.idle_add(self.show_message, msg)
                    db.commit()
                finally:
                    self._active_conn = None
        except Exception as e:
            try:
                import psycopg as _pg
                if isinstance(e, _pg.errors.QueryCanceled):
                    GLib.idle_add(self.show_message, 'Query cancelled')
                    return
                if isinstance(e, _pg.Error) and hasattr(e, 'diag'):
                    parts = [e.diag.message_primary or str(e)]
                    if e.diag.message_detail:
                        parts.append(f'Detail: {e.diag.message_detail}')
                    if e.diag.message_hint:
                        parts.append(f'Hint: {e.diag.message_hint}')
                    GLib.idle_add(self.show_error, '\n'.join(parts))
                    return
            except ImportError:
                pass
            GLib.idle_add(self.show_error, str(e))

    # ── Result display ────────────────────────────────────────────────────────

    def _elapsed_ms(self):
        return int((time.monotonic() - self._run_start_time) * 1000)

    def show_results(self, columns, rows):
        self._finish_run()
        if _DDL_RE.search(self._last_sql):
            self.emit('ddl-executed')
        n = len(rows)
        self._results_meta.set_label(f'{n} row{"s" if n != 1 else ""}')
        self._append_history(self._last_sql, self._elapsed_ms(), rows=n)

        if not rows:
            self._results_message.set_label('Query returned 0 rows')
            self._results_message.remove_css_class('error')
            self._results_stack.set_visible_child_name('message')
            return

        self._results_scroll.set_child(make_column_view(columns, rows))
        self._results_stack.set_visible_child_name('grid')

    def show_message(self, text):
        self._finish_run()
        if _DDL_RE.search(self._last_sql):
            self.emit('ddl-executed')
        self._results_message.set_label(text)
        self._results_message.remove_css_class('error')
        self._results_stack.set_visible_child_name('message')
        self._append_history(self._last_sql, self._elapsed_ms())

    def show_error(self, text):
        self._finish_run()
        self._results_message.set_label(text)
        self._results_message.add_css_class('error')
        self._results_stack.set_visible_child_name('message')
        self._append_history(self._last_sql, self._elapsed_ms(), error=text)

    def _show_multi_results(self, results):
        self._finish_run()
        if any(_DDL_RE.search(r['stmt']) for r in results if r['kind'] in ('select', 'status')):
            self.emit('ddl-executed')

        # Clear previous log rows
        while True:
            child = self._results_log.get_first_child()
            if child is None:
                break
            self._results_log.remove(child)

        errors = sum(1 for r in results if r['kind'] == 'error')
        cancelled = any(r['kind'] == 'cancelled' for r in results)
        total = len(results)
        meta = f'{total} statement{"s" if total != 1 else ""}'
        if errors:
            meta += f', {errors} error{"s" if errors != 1 else ""}'
        if cancelled:
            meta += ', cancelled'
        self._results_meta.set_label(meta)

        for i, result in enumerate(results):
            preview = ' '.join(result['stmt'].split())
            if len(preview) > 72:
                preview = preview[:69] + '…'

            row = Adw.ActionRow()
            row.set_title(preview)
            row.add_css_class('monospace')

            if result['kind'] == 'select':
                n = len(result['rows'])
                row.set_subtitle(f'{n} row{"s" if n != 1 else ""}')
                icon = Gtk.Image.new_from_icon_name('emblem-ok-symbolic')
                icon.add_css_class('success')
                row.add_prefix(icon)

                tab_scroll = Gtk.ScrolledWindow()
                tab_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
                tab_scroll.set_vexpand(True)
                tab_scroll.set_child(make_column_view(result['cols'], result['rows']))
                tab_page = self._results_tab_view.append(tab_scroll)
                tab_page.set_title(f'Query {i + 1}')
            elif result['kind'] == 'status':
                c = result['count']
                row.set_subtitle(f'{c} row{"s" if c != 1 else ""} affected')
                icon = Gtk.Image.new_from_icon_name('emblem-ok-symbolic')
                icon.add_css_class('success')
                row.add_prefix(icon)
            elif result['kind'] == 'cancelled':
                row.set_subtitle('Cancelled')
                icon = Gtk.Image.new_from_icon_name('media-playback-stop-symbolic')
                icon.add_css_class('dim-label')
                row.add_prefix(icon)
            else:
                row.set_subtitle(result['msg'])
                icon = Gtk.Image.new_from_icon_name('dialog-error-symbolic')
                icon.add_css_class('error')
                row.add_prefix(icon)

            self._results_log.append(row)

        self._results_stack.set_visible_child_name('log')
        # Record each statement separately in history
        elapsed = self._elapsed_ms()
        for result in results:
            if result['stmt']:
                err = result.get('msg') if result['kind'] == 'error' else None
                rows = len(result.get('rows', [])) if result['kind'] == 'select' else None
                self._append_history(result['stmt'], elapsed, rows=rows, error=err)

    # ── EXPLAIN ───────────────────────────────────────────────────────────────

    def _get_query_for_explain(self):
        """Return the selection or statement at cursor, same logic as run_selected."""
        bounds = self._buffer.get_selection_bounds()
        if bounds:
            return self._buffer.get_text(bounds[0], bounds[1], False).strip()
        cursor = self._buffer.get_iter_at_mark(self._buffer.get_insert())
        start = self._buffer.get_start_iter()
        end = self._buffer.get_end_iter()
        full_text = self._buffer.get_text(start, end, False)
        return _statement_at_offset(full_text, cursor.get_offset())

    def _confirm_explain_analyze(self):
        dialog = Adw.AlertDialog(
            heading='Run EXPLAIN ANALYZE?',
            body='EXPLAIN ANALYZE actually executes the query. DML statements (INSERT, UPDATE, DELETE) will have real side effects.',
        )
        dialog.add_response('cancel', 'Cancel')
        dialog.add_response('run', 'Run Anyway')
        dialog.set_response_appearance('run', Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect('response', lambda d, r: self._run_explain('analyze') if r == 'run' else None)
        dialog.present(self.get_root())

    def _run_explain(self, mode):
        sql = self._get_query_for_explain()
        if not sql or not self._connection:
            return
        self._explain_last_sql = sql
        self._explain_last_conn = dict(self._connection)
        self._explain_is_analyze = (mode == 'analyze')
        self._explain_json_cache = None
        self._explain_tree_toggle.set_active(False)
        self._start_run(sql, explain_mode=mode)

    def _execute_explain(self, conn, sql, mode):
        is_analyze = (mode == 'analyze')
        prefix = 'EXPLAIN (ANALYZE, FORMAT TEXT)' if is_analyze else 'EXPLAIN (FORMAT TEXT)'
        explain_sql = f'{prefix} {sql}'
        try:
            import psycopg
            from tunnel import open_db
            with open_db(conn) as db:
                self._active_conn = db
                try:
                    with db.cursor() as cur:
                        cur.execute(explain_sql)
                        rows = cur.fetchall()
                        plan_text = '\n'.join(r[0] for r in rows)
                    db.rollback()
                finally:
                    self._active_conn = None
            GLib.idle_add(self._show_explain_results, plan_text, is_analyze)
        except Exception as e:
            try:
                import psycopg as _pg
                if isinstance(e, _pg.Error) and hasattr(e, 'diag'):
                    msg = e.diag.message_primary or str(e)
                    if e.diag.message_detail:
                        msg += f'\nDetail: {e.diag.message_detail}'
                    GLib.idle_add(self.show_error, msg)
                    return
            except ImportError:
                pass
            GLib.idle_add(self.show_error, str(e))

    def _show_explain_results(self, plan_text, is_analyze):
        self._finish_run()
        self._explain_text_buf.set_text(plan_text)
        self._explain_analyze_warning.set_visible(is_analyze)
        self._explain_inner_stack.set_visible_child_name('text')
        self._results_stack.set_visible_child_name('explain')
        self._results_meta.set_label(f'{"EXPLAIN ANALYZE" if is_analyze else "EXPLAIN"} — {self._elapsed_ms()} ms')
        self._append_history(
            f'{"EXPLAIN ANALYZE" if is_analyze else "EXPLAIN"} {self._explain_last_sql}',
            self._elapsed_ms(),
        )

    def _on_explain_copy_text(self, _btn):
        start = self._explain_text_buf.get_start_iter()
        end = self._explain_text_buf.get_end_iter()
        text = self._explain_text_buf.get_text(start, end, False)
        Gdk.Display.get_default().get_clipboard().set(text)
        self._show_explain_copy_confirm('Copied')

    def _on_explain_copy_json(self, _btn):
        if self._explain_json_cache is not None:
            Gdk.Display.get_default().get_clipboard().set(
                json.dumps(self._explain_json_cache, indent=2))
            self._show_explain_copy_confirm('Copied JSON')
            return
        # Re-run with FORMAT JSON
        conn = self._explain_last_conn
        sql = self._explain_last_sql
        is_analyze = self._explain_is_analyze
        if not conn or not sql:
            return
        prefix = 'EXPLAIN (ANALYZE, FORMAT JSON)' if is_analyze else 'EXPLAIN (FORMAT JSON)'

        def run():
            try:
                import psycopg
                from tunnel import open_tunnel
                with open_tunnel(conn) as (host, port), psycopg.connect(
                    host=host, port=port,
                    dbname=conn['database'], user=conn['username'],
                    password=conn['password'], connect_timeout=10,
                ) as db:
                    with db.cursor() as cur:
                        cur.execute(f'{prefix} {sql}')
                        rows = cur.fetchall()
                        plan_json = rows[0][0] if rows else []
                    db.rollback()
                self._explain_json_cache = plan_json
                text = json.dumps(plan_json, indent=2)
                def _copy_and_confirm(t=text):
                    Gdk.Display.get_default().get_clipboard().set(t)
                    self._show_explain_copy_confirm('Copied JSON')
                GLib.idle_add(_copy_and_confirm)
            except Exception as e:
                GLib.idle_add(self._show_explain_copy_confirm, f'Error: {e}')

        threading.Thread(target=run, daemon=True).start()

    def _show_explain_copy_confirm(self, msg):
        self._explain_copy_confirm.set_label(msg)
        self._explain_copy_confirm.set_visible(True)
        if self._explain_copy_confirm_timer:
            GLib.source_remove(self._explain_copy_confirm_timer)
        self._explain_copy_confirm_timer = GLib.timeout_add(
            2000, self._hide_explain_copy_confirm)

    def _hide_explain_copy_confirm(self):
        self._explain_copy_confirm.set_visible(False)
        self._explain_copy_confirm_timer = 0
        return False

    def _on_explain_tree_toggled(self, btn):
        if not btn.get_active():
            self._explain_inner_stack.set_visible_child_name('text')
            return

        if self._explain_json_cache is not None:
            self._render_explain_tree(self._explain_json_cache)
            return

        conn = self._explain_last_conn
        sql = self._explain_last_sql
        is_analyze = self._explain_is_analyze
        if not conn or not sql:
            btn.set_active(False)
            return

        prefix = 'EXPLAIN (ANALYZE, FORMAT JSON)' if is_analyze else 'EXPLAIN (FORMAT JSON)'

        def run():
            try:
                import psycopg
                from tunnel import open_tunnel
                with open_tunnel(conn) as (host, port), psycopg.connect(
                    host=host, port=port,
                    dbname=conn['database'], user=conn['username'],
                    password=conn['password'], connect_timeout=10,
                ) as db:
                    with db.cursor() as cur:
                        cur.execute(f'{prefix} {sql}')
                        rows = cur.fetchall()
                        plan_json = rows[0][0] if rows else []
                    db.rollback()
                self._explain_json_cache = plan_json
                GLib.idle_add(self._render_explain_tree, plan_json)
            except Exception as e:
                GLib.idle_add(self._explain_tree_toggle.set_active, False)
                GLib.idle_add(self._show_explain_copy_confirm, f'Tree error: {e}')

        threading.Thread(target=run, daemon=True).start()

    def _render_explain_tree(self, plan_json):
        # plan_json is a list; first element has a "Plan" key
        if not plan_json or not isinstance(plan_json, list):
            return
        top_plan = plan_json[0].get('Plan', {})

        # Find max total cost for highlighting
        max_cost = [0.0]
        def _find_max(node):
            max_cost[0] = max(max_cost[0], node.get('Total Cost', 0.0))
            for child in node.get('Plans', []):
                _find_max(child)
        _find_max(top_plan)

        def _build_row(node, depth=0):
            node_type = node.get('Node Type', 'Unknown')
            total_cost = node.get('Total Cost', 0.0)
            plan_rows = node.get('Plan Rows', '?')
            actual_rows = node.get('Actual Rows')
            actual_time = node.get('Actual Total Time')

            title = node_type
            parts = [f'cost={total_cost:.2f}', f'rows≈{plan_rows}']
            if actual_rows is not None:
                parts.append(f'actual={actual_rows}')
            if actual_time is not None:
                parts.append(f'{actual_time:.2f} ms')
            subtitle = '  ·  '.join(parts)

            is_expensive = total_cost >= max_cost[0] * 0.9 and max_cost[0] > 0
            children = node.get('Plans', [])
            if children:
                expander = Adw.ExpanderRow(title=title, subtitle=subtitle)
                if is_expensive:
                    expander.set_expanded(True)
                    expander.add_css_class('error')
                for child in children:
                    child_row = _build_row(child, depth + 1)
                    expander.add_row(child_row)
                return expander
            else:
                row = Adw.ActionRow(title=title, subtitle=subtitle)
                if is_expensive:
                    row.add_css_class('error')
                return row

        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup()
        group.add(_build_row(top_plan))
        page.add(group)

        self._explain_tree_scroll.set_child(page)
        self._explain_inner_stack.set_visible_child_name('tree')

    # ── History ───────────────────────────────────────────────────────────────

    def _append_history(self, sql, duration_ms, rows=None, error=None):
        import datetime
        ts = datetime.datetime.now().strftime('%H:%M:%S')
        entry = {'sql': sql, 'ts': ts, 'duration_ms': duration_ms, 'rows': rows, 'error': error}

        self._history.insert(0, entry)
        if len(self._history) > _HISTORY_LIMIT:
            self._history.pop()

        preview = ' '.join(sql.split())
        if len(preview) > 80:
            preview = preview[:77] + '…'

        if duration_ms < 1000:
            time_str = f'{duration_ms} ms'
        else:
            time_str = f'{duration_ms / 1000:.1f} s'

        if error:
            subtitle = f'{ts}  ·  {time_str}  ·  error'
        elif rows is not None:
            subtitle = f'{ts}  ·  {time_str}  ·  {rows} row{"s" if rows != 1 else ""}'
        else:
            subtitle = f'{ts}  ·  {time_str}'

        row = Adw.ActionRow(title=preview, subtitle=subtitle, activatable=True)
        row.add_css_class('monospace')
        if error:
            icon = Gtk.Image.new_from_icon_name('dialog-error-symbolic')
            icon.add_css_class('error')
            row.add_prefix(icon)

        rerun_btn = Gtk.Button(icon_name='media-playback-start-symbolic')
        rerun_btn.add_css_class('flat')
        rerun_btn.set_tooltip_text('Re-run this query')
        rerun_btn.set_valign(Gtk.Align.CENTER)
        rerun_btn.connect('clicked', lambda _btn, s=sql: self._history_rerun(s))
        row.add_suffix(rerun_btn)

        row.connect('activated', lambda r, s=sql: self._history_populate(s))

        # Prepend to list (most recent first)
        self._history_list.prepend(row)

        # Trim list widget to cap
        count = 0
        child = self._history_list.get_first_child()
        while child:
            count += 1
            child = child.get_next_sibling()
        if count > _HISTORY_LIMIT:
            last = self._history_list.get_last_child()
            if last:
                self._history_list.remove(last)

    def _history_populate(self, sql):
        self._buffer.set_text(sql)

    def _history_rerun(self, sql):
        if not self._connection:
            return
        self._buffer.set_text(sql)
        self._start_run(sql)
