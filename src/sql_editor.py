import os
import threading

import prefs

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Gtk, Adw, GObject, GLib, Gdk

from data_grid import make_column_view

# Optional GtkSourceView for syntax highlighting
try:
    gi.require_version('GtkSource', '5')
    from gi.repository import GtkSource
    _HAS_SOURCE = True
except (ValueError, ImportError):
    _HAS_SOURCE = False

_AUTOSAVE_DELAY_MS = 800


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
        'run-sql': (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, file_path):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.file_path = file_path
        self._modified = False
        self._connection = None
        self._autosave_timer = 0
        self._save_label_timer = 0
        self._dark_handler_id = 0
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

        self._run_btn = Gtk.Button(label='Run')
        self._run_btn.set_icon_name('media-playback-start-symbolic')
        self._run_btn.add_css_class('suggested-action')
        self._run_btn.add_css_class('pill')
        self._run_btn.set_sensitive(False)
        self._run_btn.set_tooltip_text('Run SQL  F5 / Ctrl+Enter')
        self._run_btn.connect('clicked', lambda _: self.emit('run-sql'))

        toolbar.append(self._modified_dot)
        toolbar.append(self._save_label)
        toolbar.append(save_btn)
        toolbar.append(spacer)
        toolbar.append(self._conn_label)
        toolbar.append(self._run_btn)

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
        results_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        results_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        results_header.set_margin_start(10)
        results_header.set_margin_top(6)
        results_header.set_margin_bottom(6)

        results_title = Gtk.Label(label='Results')
        results_title.add_css_class('heading')
        results_title.set_hexpand(True)
        results_title.set_xalign(0)

        self._results_meta = Gtk.Label()
        self._results_meta.add_css_class('caption')
        self._results_meta.add_css_class('dim-label')
        self._results_meta.set_margin_end(10)

        self._results_spinner = Gtk.Spinner()
        self._results_spinner.set_size_request(16, 16)
        self._results_spinner.set_margin_end(10)

        results_header.append(results_title)
        results_header.append(self._results_spinner)
        results_header.append(self._results_meta)

        results_box.append(Gtk.Separator())
        results_box.append(results_header)
        results_box.append(Gtk.Separator())

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

        results_box.append(self._results_stack)

        self._paned = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL)
        self._paned.set_vexpand(True)
        self._paned.set_shrink_start_child(False)
        self._paned.set_shrink_end_child(False)
        self._paned.set_start_child(editor_scroll)
        self._paned.set_end_child(results_box)
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
        if state & Gdk.ModifierType.CONTROL_MASK and keyval == Gdk.KEY_s:
            self._save_now()
            return True
        if keyval in (Gdk.KEY_F5, Gdk.KEY_Return, Gdk.KEY_KP_Enter) and \
                self._run_btn.get_sensitive() and \
                (keyval == Gdk.KEY_F5 or state & Gdk.ModifierType.CONTROL_MASK):
            self.emit('run-sql')
            return True
        return False

    # ── Connection ────────────────────────────────────────────────────────────

    def set_connection(self, conn):
        self._connection = conn
        if conn:
            self._conn_label.set_label(conn['name'])
            self._run_btn.set_sensitive(True)
        else:
            self._conn_label.set_label('')
            self._run_btn.set_sensitive(False)

    def is_modified(self):
        return self._modified

    # ── Run ───────────────────────────────────────────────────────────────────

    def run(self):
        if not self._connection:
            return

        bounds = self._buffer.get_selection_bounds()
        if bounds:
            sql = self._buffer.get_text(bounds[0], bounds[1], False).strip()
        else:
            start = self._buffer.get_start_iter()
            end = self._buffer.get_end_iter()
            sql = self._buffer.get_text(start, end, False).strip()

        if not sql:
            return

        self._run_btn.set_sensitive(False)
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

    def _execute(self, conn, sql):
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
        except Exception as e:
            try:
                import psycopg as _pg
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
        self._results_spinner.stop()
        self._run_btn.set_sensitive(self._connection is not None)
        self._results_meta.set_label(f'{len(rows)} row{"s" if len(rows) != 1 else ""}')

        if not rows:
            self._results_message.set_label('Query returned 0 rows')
            self._results_message.remove_css_class('error')
            self._results_stack.set_visible_child_name('message')
            return

        self._results_scroll.set_child(make_column_view(columns, rows))
        self._results_stack.set_visible_child_name('grid')

    def show_message(self, text):
        self._results_spinner.stop()
        self._run_btn.set_sensitive(self._connection is not None)
        self._results_message.set_label(text)
        self._results_message.remove_css_class('error')
        self._results_stack.set_visible_child_name('message')

    def show_error(self, text):
        self._results_spinner.stop()
        self._run_btn.set_sensitive(self._connection is not None)
        self._results_message.set_label(text)
        self._results_message.add_css_class('error')
        self._results_stack.set_visible_child_name('message')
