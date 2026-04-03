import threading

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Gtk, Adw, GLib, Gio, Gdk, Pango

_REFRESH_INTERVAL_MS = 5000

_QUERY_SQL = """
SELECT
    pid,
    COALESCE(usename, '') AS usename,
    COALESCE(datname, '') AS datname,
    COALESCE(state, '') AS state,
    CASE
        WHEN query_start IS NOT NULL
        THEN EXTRACT(EPOCH FROM (now() - query_start))::int
        ELSE NULL
    END AS duration_s,
    COALESCE(wait_event_type || ': ' || wait_event, '') AS wait_event,
    COALESCE(LEFT(query, 200), '') AS query
FROM pg_stat_activity
WHERE pid <> pg_backend_pid()
ORDER BY COALESCE(query_start, backend_start) DESC NULLS LAST
"""

_COL_PID      = 0
_COL_USER     = 1
_COL_DB       = 2
_COL_STATE    = 3
_COL_DURATION = 4  # int seconds, -1 = none
_COL_WAIT     = 5
_COL_QUERY    = 6
_COL_BG       = 7  # background colour string or None


def _duration_label(secs):
    if secs < 0:
        return ''
    if secs < 60:
        return f'{secs}s'
    m, s = divmod(secs, 60)
    if m < 60:
        return f'{m}m {s}s'
    h, m = divmod(m, 60)
    return f'{h}h {m}m'


def _row_bg(state, duration_s):
    if state == 'active' and duration_s >= 0:
        if duration_s >= 300:
            return '#c01c28'   # red (> 5 min)
        if duration_s >= 30:
            return '#e5a50a'   # amber (> 30 s)
    return None


class ActivityPanel(Gtk.Box):
    def __init__(self, conn):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._conn = conn
        self._refresh_id = 0
        self._build_ui()
        self.connect('destroy', self._on_destroy)
        self._refresh()

    def _build_ui(self):
        # ── Toolbar ──────────────────────────────────────────────────────────
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        toolbar.set_margin_start(10)
        toolbar.set_margin_end(10)
        toolbar.set_margin_top(6)
        toolbar.set_margin_bottom(6)

        refresh_btn = Gtk.Button(icon_name='view-refresh-symbolic')
        refresh_btn.add_css_class('flat')
        refresh_btn.set_tooltip_text('Refresh now')
        refresh_btn.connect('clicked', lambda _: self._refresh())
        toolbar.append(refresh_btn)

        self._filter_entry = Gtk.SearchEntry()
        self._filter_entry.set_placeholder_text('Filter by user, database or state…')
        self._filter_entry.set_hexpand(True)
        self._filter_entry.connect('search-changed', self._on_filter_changed)
        toolbar.append(self._filter_entry)

        self._status_label = Gtk.Label()
        self._status_label.add_css_class('caption')
        self._status_label.add_css_class('dim-label')
        toolbar.append(self._status_label)

        self.append(toolbar)
        self.append(Gtk.Separator())

        # ── Error / permission banner ────────────────────────────────────────
        self._error_banner = Adw.Banner(title='')
        self._error_banner.set_revealed(False)
        self.append(self._error_banner)

        # ── Tree store + view ────────────────────────────────────────────────
        # Columns: pid(int), user, db, state, duration_s(int), wait, query, bg
        self._store = Gtk.ListStore(int, str, str, str, int, str, str, str)

        self._filter_model = self._store.filter_new()
        self._filter_model.set_visible_func(self._row_visible)

        view = Gtk.TreeView(model=self._filter_model)
        view.set_vexpand(True)
        view.set_headers_visible(True)
        view.set_enable_search(False)
        self._view = view

        def _text_col(title, col_idx, expand=False, mono=False):
            renderer = Gtk.CellRendererText()
            renderer.set_property('ellipsize', Pango.EllipsizeMode.END)
            if mono:
                renderer.set_property('font', 'Monospace')
            col = Gtk.TreeViewColumn(title, renderer, text=col_idx, background=_COL_BG)
            col.set_resizable(True)
            if expand:
                col.set_expand(True)
            return col

        def _duration_col():
            renderer = Gtk.CellRendererText()
            col = Gtk.TreeViewColumn('Duration', renderer, background=_COL_BG)
            col.set_cell_data_func(renderer, self._render_duration)
            col.set_resizable(True)
            return col

        view.append_column(_text_col('PID',      _COL_PID))
        view.append_column(_text_col('User',     _COL_USER))
        view.append_column(_text_col('Database', _COL_DB))
        view.append_column(_text_col('State',    _COL_STATE))
        view.append_column(_duration_col())
        view.append_column(_text_col('Wait',     _COL_WAIT))
        view.append_column(_text_col('Query',    _COL_QUERY, expand=True, mono=True))

        # Right-click to terminate
        click = Gtk.GestureClick()
        click.set_button(3)
        click.connect('pressed', self._on_right_click)
        view.add_controller(click)

        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        scroll.set_child(view)
        self.append(scroll)

        # Schedule auto-refresh
        self._refresh_id = GLib.timeout_add(
            _REFRESH_INTERVAL_MS, self._on_refresh_tick
        )

    def _render_duration(self, col, renderer, model, it, _data):
        secs = model.get_value(it, _COL_DURATION)
        renderer.set_property('text', _duration_label(secs))
        bg = model.get_value(it, _COL_BG)
        renderer.set_property('background', bg or '')
        renderer.set_property('background-set', bool(bg))

    def _row_visible(self, model, it, _data):
        query = self._filter_entry.get_text().strip().lower()
        if not query:
            return True
        for col in (_COL_USER, _COL_DB, _COL_STATE):
            if query in model.get_value(it, col).lower():
                return True
        return False

    def _on_filter_changed(self, _entry):
        self._filter_model.refilter()

    # ── Data fetch ───────────────────────────────────────────────────────────

    def _refresh(self):
        self._status_label.set_label('Refreshing…')
        threading.Thread(target=self._fetch, daemon=True).start()

    def _on_refresh_tick(self):
        self._refresh()
        return True  # keep timer running

    def _fetch(self):
        try:
            from tunnel import open_db
            with open_db(self._conn) as db:
                with db.cursor() as cur:
                    cur.execute(_QUERY_SQL)
                    rows = cur.fetchall()
            GLib.idle_add(self._populate, rows)
        except Exception as e:
            GLib.idle_add(self._on_fetch_error, str(e))

    def _populate(self, rows):
        self._store.clear()
        for pid, user, db, state, dur, wait, query in rows:
            duration_s = int(dur) if dur is not None else -1
            bg = _row_bg(state, duration_s) or ''
            self._store.append([pid, user, db, state, duration_s, wait, query, bg])
        n = len(rows)
        self._status_label.set_label(f'{n} session{"s" if n != 1 else ""}')
        self._error_banner.set_revealed(False)

    def _on_fetch_error(self, msg):
        self._status_label.set_label('')
        self._error_banner.set_title(msg)
        self._error_banner.set_revealed(True)

    # ── Terminate session ────────────────────────────────────────────────────

    def _on_right_click(self, gesture, _n, x, y):
        result = self._view.get_path_at_pos(int(x), int(y))
        if not result:
            return
        path, _col, _cx, _cy = result
        it = self._filter_model.get_iter(path)
        pid = self._filter_model.get_value(it, _COL_PID)
        self._show_terminate_menu(pid, x, y)

    def _show_terminate_menu(self, pid, x, y):
        menu = Gio.Menu()
        menu.append(f'Terminate session (PID {pid})', 'activity.terminate')

        action_group = Gio.SimpleActionGroup()
        terminate_action = Gio.SimpleAction.new('terminate', None)
        terminate_action.connect('activate', lambda *_: self._confirm_terminate(pid))
        action_group.add_action(terminate_action)
        self._view.insert_action_group('activity', action_group)

        popover = Gtk.PopoverMenu.new_from_model(menu)
        popover.set_parent(self._view)
        rect = Gdk.Rectangle()
        rect.x = int(x)
        rect.y = int(y)
        rect.width = 1
        rect.height = 1
        popover.set_pointing_to(rect)
        popover.popup()

    def _confirm_terminate(self, pid):
        dialog = Adw.AlertDialog(
            heading=f'Terminate session {pid}?',
            body='This will immediately close the backend connection. Any open transaction will be rolled back.',
        )
        dialog.add_response('cancel', 'Cancel')
        dialog.add_response('terminate', 'Terminate')
        dialog.set_response_appearance('terminate', Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect('response', self._on_terminate_response, pid)
        dialog.present(self.get_root())

    def _on_terminate_response(self, _dialog, response, pid):
        if response != 'terminate':
            return
        threading.Thread(target=self._do_terminate, args=(pid,), daemon=True).start()

    def _do_terminate(self, pid):
        try:
            from tunnel import open_db
            with open_db(self._conn) as db:
                with db.cursor() as cur:
                    cur.execute('SELECT pg_terminate_backend(%s)', [pid])
                    row = cur.fetchone()
                db.commit()
            success = row and row[0]
            GLib.idle_add(self._on_terminated, pid, success)
        except Exception as e:
            GLib.idle_add(self._on_terminate_error, str(e))

    def _on_terminated(self, pid, success):
        if success:
            self._status_label.set_label(f'Session {pid} terminated')
        else:
            self._status_label.set_label(f'Session {pid} not found (already gone?)')
        self._refresh()

    def _on_terminate_error(self, msg):
        self._error_banner.set_title(f'Terminate failed: {msg}')
        self._error_banner.set_revealed(True)

    def _on_destroy(self, _widget):
        if self._refresh_id:
            GLib.source_remove(self._refresh_id)
            self._refresh_id = 0
