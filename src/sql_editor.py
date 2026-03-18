import os
import re
import threading

import prefs

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Gtk, Adw, GObject, GLib, Gdk, Gio

from data_grid import make_column_view

# Optional GtkSourceView for syntax highlighting
try:
    gi.require_version('GtkSource', '5')
    from gi.repository import GtkSource
    _HAS_SOURCE = True

    pass  # GtkSource available — completion set up in SqlEditor.__init__

except (ValueError, ImportError):
    _HAS_SOURCE = False

_AUTOSAVE_DELAY_MS = 800

_SQL_KEYWORDS = [
    'SELECT', 'FROM', 'WHERE', 'JOIN', 'LEFT', 'RIGHT', 'INNER', 'OUTER',
    'FULL', 'CROSS', 'ON', 'AS', 'AND', 'OR', 'NOT', 'IN', 'EXISTS',
    'BETWEEN', 'LIKE', 'ILIKE', 'IS', 'NULL', 'TRUE', 'FALSE', 'DISTINCT',
    'GROUP', 'BY', 'ORDER', 'HAVING', 'LIMIT', 'OFFSET', 'UNION', 'ALL',
    'INTERSECT', 'EXCEPT', 'WITH', 'RETURNING', 'INSERT', 'INTO', 'VALUES',
    'UPDATE', 'SET', 'DELETE', 'CREATE', 'DROP', 'ALTER', 'TABLE', 'VIEW',
    'INDEX', 'SCHEMA', 'BEGIN', 'COMMIT', 'ROLLBACK', 'CASE', 'WHEN',
    'THEN', 'ELSE', 'END', 'ASC', 'DESC', 'NULLS', 'FIRST', 'LAST',
    'COUNT', 'SUM', 'AVG', 'MIN', 'MAX', 'COALESCE', 'NULLIF', 'CAST',
    'EXTRACT', 'NOW', 'CURRENT_DATE', 'CURRENT_TIMESTAMP', 'PRIMARY', 'KEY',
    'FOREIGN', 'REFERENCES', 'UNIQUE', 'DEFAULT', 'NOT NULL', 'CONSTRAINT',
]


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


class SqlEditor(Gtk.Box):
    __gsignals__ = {
        'run-sql':          (GObject.SignalFlags.RUN_FIRST, None, ()),
        'run-selected-sql': (GObject.SignalFlags.RUN_FIRST, None, ()),
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

        self._cancel_btn = Gtk.Button(label='Cancel')
        self._cancel_btn.set_icon_name('media-playback-stop-symbolic')
        self._cancel_btn.add_css_class('destructive-action')
        self._cancel_btn.add_css_class('pill')
        self._cancel_btn.set_tooltip_text('Cancel running query')
        self._cancel_btn.set_visible(False)
        self._cancel_btn.connect('clicked', self._on_cancel)

        toolbar.append(self._modified_dot)
        toolbar.append(self._save_label)
        toolbar.append(save_btn)
        toolbar.append(spacer)
        toolbar.append(self._conn_label)
        toolbar.append(self._run_sel_btn)
        toolbar.append(self._run_btn)
        toolbar.append(self._cancel_btn)

        self.append(toolbar)
        self.append(Gtk.Separator())

        # ── Editor ────────────────────────────────────────────────────────────
        self._buffer, self._editor = _make_editor()
        self._buffer.connect('changed', self._on_changed)
        self._schema_buf = None
        if _HAS_SOURCE:
            # Hidden buffer holding SQL keywords + schema objects for word completion
            self._schema_buf = GtkSource.Buffer()
            self._schema_buf.set_text(' '.join(w.lower() for w in _SQL_KEYWORDS))
            provider = GtkSource.CompletionWords.new('SQL')
            provider.props.minimum_word_size = 1
            provider.register(self._buffer)       # words typed in the editor
            provider.register(self._schema_buf)   # keywords + schema objects
            self._editor.get_completion().add_provider(provider)

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

        # Tab view — "Results" is always the first (pinned) tab;
        # SELECT query results appear as additional tabs beside it.
        self._results_tab_view = Adw.TabView()
        self._results_tab_view.set_vexpand(True)
        self._results_tab_view.connect('close-page', self._on_results_close_page)

        self._results_page = self._results_tab_view.append(self._results_stack)
        self._results_page.set_title('Results')
        self._results_tab_view.set_page_pinned(self._results_page, True)

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
        return False

    # ── Connection ────────────────────────────────────────────────────────────

    def set_connection(self, conn):
        self._connection = conn
        if conn:
            self._conn_label.set_label(conn['name'])
            self._run_btn.set_sensitive(True)
            self._run_sel_btn.set_sensitive(True)
            if self._schema_buf is not None:
                threading.Thread(
                    target=self._fetch_schema_for_completion,
                    args=(dict(conn),),
                    daemon=True,
                ).start()
        else:
            self._conn_label.set_label('')
            self._run_btn.set_sensitive(False)
            self._run_sel_btn.set_sensitive(False)
            if self._schema_buf is not None:
                GLib.idle_add(self._schema_buf.set_text, ' '.join(w.lower() for w in _SQL_KEYWORDS))

    def _fetch_schema_for_completion(self, conn):
        try:
            import psycopg
            from tunnel import open_tunnel

            with open_tunnel(conn) as (host, port), psycopg.connect(
                host=host,
                port=port,
                dbname=conn['database'],
                user=conn['username'],
                password=conn['password'],
                connect_timeout=10,
            ) as db:
                with db.cursor() as cur:
                    cur.execute("""
                        SELECT DISTINCT table_schema, table_name, column_name
                        FROM information_schema.columns
                        WHERE table_schema NOT IN ('information_schema', 'pg_catalog')
                        ORDER BY table_schema, table_name, column_name
                    """)
                    rows = cur.fetchall()

            schemas = list(dict.fromkeys(r[0] for r in rows))
            tables  = list(dict.fromkeys(r[1] for r in rows))
            columns = list(dict.fromkeys(r[2] for r in rows))
            words = ' '.join([w.lower() for w in _SQL_KEYWORDS] + schemas + tables + columns)
            GLib.idle_add(self._schema_buf.set_text, words)
        except Exception:
            pass  # completion still works with keywords only

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

    def _start_run(self, sql):

        if not sql:
            return

        self._cancel_event.clear()
        self._clear_result_tabs()
        self._results_tab_view.set_selected_page(self._results_page)
        self._run_btn.set_visible(False)
        self._run_sel_btn.set_visible(False)
        self._cancel_btn.set_visible(True)
        self._results_meta.set_label('')
        self._results_spinner.start()
        self._results_stack.set_visible_child_name('message')
        self._results_message.set_label('Running…')
        self._results_message.remove_css_class('error')

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
        self._cancel_btn.set_visible(False)
        self._run_btn.set_visible(True)
        self._run_sel_btn.set_visible(True)
        has_conn = self._connection is not None
        self._run_btn.set_sensitive(has_conn)
        self._run_sel_btn.set_sensitive(has_conn)

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
            from tunnel import open_tunnel

            with open_tunnel(conn) as (host, port), psycopg.connect(
                host=host,
                port=port,
                dbname=conn['database'],
                user=conn['username'],
                password=conn['password'],
                connect_timeout=10,
            ) as db:
                self._active_conn = db
                try:
                    with db.cursor() as cur:
                        for stmt in stmts:
                            if self._cancel_event.is_set():
                                results.append({'stmt': stmt, 'kind': 'cancelled'})
                                break
                            try:
                                cur.execute(stmt)
                                if cur.description:
                                    cols = [d.name for d in cur.description]
                                    rows = cur.fetchall()
                                    results.append({'stmt': stmt, 'kind': 'select',
                                                    'cols': cols, 'rows': rows})
                                else:
                                    count = cur.rowcount
                                    results.append({'stmt': stmt, 'kind': 'status',
                                                    'count': count})
                            except psycopg.errors.QueryCanceled:
                                results.append({'stmt': stmt, 'kind': 'cancelled'})
                                break
                            except psycopg.Error as e:
                                msg = e.diag.message_primary or str(e) if hasattr(e, 'diag') else str(e)
                                if hasattr(e, 'diag') and e.diag.message_detail:
                                    msg += f'\nDetail: {e.diag.message_detail}'
                                if hasattr(e, 'diag') and e.diag.message_hint:
                                    msg += f'\nHint: {e.diag.message_hint}'
                                results.append({'stmt': stmt, 'kind': 'error', 'msg': msg})
                                break  # transaction is aborted; stop here
                    db.commit()
                finally:
                    self._active_conn = None
        except Exception as e:
            results.append({'stmt': '', 'kind': 'error', 'msg': str(e)})

        GLib.idle_add(self._show_multi_results, results)

    def _execute_single(self, conn, sql):
        try:
            import psycopg
            from tunnel import open_tunnel

            with open_tunnel(conn) as (host, port), psycopg.connect(
                host=host,
                port=port,
                dbname=conn['database'],
                user=conn['username'],
                password=conn['password'],
                connect_timeout=10,
            ) as db:
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

    def show_results(self, columns, rows):
        self._finish_run()
        self._results_meta.set_label(f'{len(rows)} row{"s" if len(rows) != 1 else ""}')

        if not rows:
            self._results_message.set_label('Query returned 0 rows')
            self._results_message.remove_css_class('error')
            self._results_stack.set_visible_child_name('message')
            return

        self._results_scroll.set_child(make_column_view(columns, rows))
        self._results_stack.set_visible_child_name('grid')

    def show_message(self, text):
        self._finish_run()
        self._results_message.set_label(text)
        self._results_message.remove_css_class('error')
        self._results_stack.set_visible_child_name('message')

    def show_error(self, text):
        self._finish_run()
        self._results_message.set_label(text)
        self._results_message.add_css_class('error')
        self._results_stack.set_visible_child_name('message')

    def _show_multi_results(self, results):
        self._finish_run()

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
