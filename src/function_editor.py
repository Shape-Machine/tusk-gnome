import threading

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Gtk, Adw, GLib, GObject, Gdk

try:
    gi.require_version('GtkSource', '5')
    from gi.repository import GtkSource
    _HAS_SOURCE = True
except (ValueError, ImportError):
    _HAS_SOURCE = False


def _make_source_view():
    if _HAS_SOURCE:
        buf = GtkSource.Buffer()
        lang = GtkSource.LanguageManager.get_default().get_language('sql')
        if lang:
            buf.set_language(lang)
        view = GtkSource.View.new_with_buffer(buf)
        view.set_show_line_numbers(True)
        view.set_tab_width(4)
        return buf, view
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


_FETCH_SQL = """
    SELECT pg_get_functiondef(p.oid)
    FROM pg_proc p
    JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname = %s
      AND p.proname = %s
      AND pg_get_function_arguments(p.oid) = %s
"""


class FunctionEditor(Gtk.Box):
    __gsignals__ = {
        'modified-changed': (GObject.SignalFlags.RUN_FIRST, None, (bool,)),
    }

    def __init__(self, conn, schema, fn_name, fn_args):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._conn = conn
        self._schema = schema
        self._fn_name = fn_name
        self._fn_args = fn_args
        self._dark_handler_id = 0
        self._saving = False
        self._build_ui()
        self.connect('destroy', self._on_destroy)

        if _HAS_SOURCE:
            style_mgr = Adw.StyleManager.get_default()
            _apply_scheme(self._buffer, style_mgr.get_dark())
            self._dark_handler_id = style_mgr.connect(
                'notify::dark', lambda m, _: _apply_scheme(self._buffer, m.get_dark())
            )

        self._load()

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
        toolbar.append(self._modified_dot)

        save_btn = Gtk.Button(icon_name='document-save-symbolic')
        save_btn.add_css_class('flat')
        save_btn.set_tooltip_text('Save to database  Ctrl+S')
        save_btn.connect('clicked', lambda _: self._save())
        toolbar.append(save_btn)

        self._reload_btn = Gtk.Button(icon_name='view-refresh-symbolic')
        self._reload_btn.add_css_class('flat')
        self._reload_btn.set_tooltip_text('Reload from database')
        self._reload_btn.set_visible(False)
        self._reload_btn.connect('clicked', lambda _: self._load())
        toolbar.append(self._reload_btn)

        self._status_label = Gtk.Label()
        self._status_label.add_css_class('caption')
        self._status_label.add_css_class('dim-label')
        self._status_label.set_hexpand(True)
        self._status_label.set_xalign(0)
        toolbar.append(self._status_label)

        self.append(toolbar)
        self.append(Gtk.Separator())

        # ── Error banner ──────────────────────────────────────────────────────
        self._error_banner = Adw.Banner(title='')
        self._error_banner.set_button_label('Dismiss')
        self._error_banner.set_revealed(False)
        self._error_banner.connect('button-clicked',
            lambda _: self._error_banner.set_revealed(False))
        self.append(self._error_banner)

        # ── Source view ───────────────────────────────────────────────────────
        self._buffer, view = _make_source_view()
        view.set_monospace(True)
        view.set_vexpand(True)
        view.set_editable(False)  # read-only until loaded
        self._view = view

        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        scroll.set_child(view)
        self.append(scroll)

        # ── Keyboard shortcut Ctrl+S ──────────────────────────────────────────
        key_ctrl = Gtk.EventControllerKey()
        key_ctrl.connect('key-pressed', self._on_key_pressed)
        self.add_controller(key_ctrl)

        # Watch buffer changes for dirty flag
        self._buffer.connect('changed', self._on_buffer_changed)
        self._loading = True  # suppress dirty flag while loading

    def _on_key_pressed(self, _ctrl, keyval, _code, state):
        if keyval == Gdk.KEY_s and (state & Gdk.ModifierType.CONTROL_MASK):
            self._save()
            return True
        return False

    def _on_buffer_changed(self, _buf):
        if not self._loading:
            self._set_modified(True)

    def _set_modified(self, value):
        self._modified_dot.set_visible(value)
        self.emit('modified-changed', value)

    def _load(self):
        self._status_label.set_label('Loading…')
        threading.Thread(target=self._fetch, daemon=True).start()

    def _fetch(self):
        try:
            from tunnel import open_db
            with open_db(self._conn) as db:
                with db.cursor() as cur:
                    cur.execute(_FETCH_SQL, [self._schema, self._fn_name, self._fn_args])
                    row = cur.fetchone()
            definition = row[0] if row else f'-- Could not load definition for {self._fn_name}'
            GLib.idle_add(self._on_loaded, definition)
        except Exception as e:
            GLib.idle_add(self._on_load_error, str(e))

    def _on_loaded(self, definition):
        self._loading = True
        self._buffer.set_text(definition)
        self._loading = False
        self._view.set_editable(True)
        self._set_modified(False)
        self._reload_btn.set_visible(False)
        self._status_label.set_label('')
        self._error_banner.set_revealed(False)

    def _on_load_error(self, msg):
        self._loading = False
        self._status_label.set_label(f'Error: {msg}')
        self._reload_btn.set_visible(True)

    def _save(self):
        if self._saving:
            return
        start = self._buffer.get_start_iter()
        end = self._buffer.get_end_iter()
        sql = self._buffer.get_text(start, end, False).strip()
        if not sql:
            return
        self._saving = True
        self._status_label.set_label('Saving…')
        self._error_banner.set_revealed(False)
        threading.Thread(target=self._do_save, args=(sql,), daemon=True).start()

    def _do_save(self, sql):
        try:
            from tunnel import open_db
            with open_db(self._conn) as db:
                with db.cursor() as cur:
                    cur.execute(sql)
                db.commit()
            GLib.idle_add(self._on_saved)
        except Exception as e:
            GLib.idle_add(self._on_save_error, str(e))

    def _on_saved(self):
        self._saving = False
        self._set_modified(False)
        self._status_label.set_label('Saved')
        GLib.timeout_add(2000, self._clear_status)

    def _clear_status(self):
        self._status_label.set_label('')
        return False

    def _on_save_error(self, msg):
        self._saving = False
        self._status_label.set_label('')
        self._error_banner.set_title(msg)
        self._error_banner.set_revealed(True)

    def _on_destroy(self, _widget):
        if self._dark_handler_id:
            Adw.StyleManager.get_default().disconnect(self._dark_handler_id)
            self._dark_handler_id = 0
