import threading

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Gtk, Adw, GLib, Gio, GObject, Gdk, Pango

from pg_errors import friendly_pg_error as _friendly_pg_error

_REFRESH_INTERVAL_MS = 5000
_css_loaded = False

_QUERY_SQL = """
SELECT
    pid,
    COALESCE(usename, '') AS usename,
    COALESCE(datname, '') AS datname,
    COALESCE(state, '') AS state,
    CASE
        WHEN query_start IS NOT NULL
        THEN EXTRACT(EPOCH FROM (now() - query_start))::int
        ELSE -1
    END AS duration_s,
    COALESCE(wait_event_type || ': ' || wait_event, '') AS wait_event,
    COALESCE(query, '') AS query
FROM pg_stat_activity
WHERE pid <> pg_backend_pid()
ORDER BY COALESCE(query_start, backend_start) DESC NULLS LAST
"""

_CSS = b"""
.activity-warn  { background-color: rgba(229, 165, 10, 0.25); }
.activity-critical { background-color: rgba(192, 28, 40, 0.30); }
"""


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


def _row_css_class(state, duration_s):
    if state == 'active' and duration_s >= 0:
        if duration_s >= 300:
            return 'activity-critical'
        if duration_s >= 30:
            return 'activity-warn'
    return None


class _ActivityRow(GObject.Object):
    __gtype_name__ = 'TuskActivityRow'

    def __init__(self, pid, user, db, state, duration_s, wait, query):
        super().__init__()
        self.pid = pid
        self.user = user
        self.db = db
        self.state = state
        self.duration_s = duration_s
        self.wait = wait
        self.query = query
        self.css_class = _row_css_class(state, duration_s)


def _make_text_col(title, getter, mono=False, expand=False):
    """Build a ColumnViewColumn with a label factory using *getter(row) -> str*."""
    factory = Gtk.SignalListItemFactory()

    def on_setup(_f, item):
        label = Gtk.Label()
        label.set_xalign(0)
        label.set_ellipsize(Pango.EllipsizeMode.END)
        label.set_max_width_chars(40)
        if mono:
            label.add_css_class('monospace')
        item.set_child(label)

    def on_bind(_f, item):
        row = item.get_item()
        label = item.get_child()
        text = getter(row)
        label.set_text(text)
        label.set_tooltip_text(text if len(text) > 40 else None)
        label._activity_row = row
        for cls in ('activity-warn', 'activity-critical'):
            label.remove_css_class(cls)
        if row.css_class:
            label.add_css_class(row.css_class)

    factory.connect('setup', on_setup)
    factory.connect('bind', on_bind)

    col = Gtk.ColumnViewColumn(title=title, factory=factory)
    col.set_resizable(True)
    if expand:
        col.set_expand(True)
    return col


class ActivityPanel(Gtk.Box):
    def __init__(self, conn):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._conn = conn
        self._refresh_id = 0
        self._filter_debounce_id = None
        self._alive = True
        self._fetching = False
        self._apply_css()
        self._build_ui()
        self.connect('destroy', self._on_destroy)
        self._refresh()

    def _apply_css(self):
        global _css_loaded
        if _css_loaded:
            return
        provider = Gtk.CssProvider()
        provider.load_from_data(_CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )
        _css_loaded = True

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

        # ── Error banner ─────────────────────────────────────────────────────
        self._error_banner = Adw.Banner(title='')
        self._error_banner.set_revealed(False)
        self.append(self._error_banner)

        # ── ColumnView ───────────────────────────────────────────────────────
        self._store = Gio.ListStore(item_type=_ActivityRow)

        self._filter_model = Gtk.FilterListModel(model=self._store)
        self._custom_filter = Gtk.CustomFilter.new(self._row_visible, None)
        self._filter_model.set_filter(self._custom_filter)

        sort_model = Gtk.SortListModel(model=self._filter_model)
        selection = Gtk.SingleSelection(model=sort_model)
        self._selection = selection

        col_view = Gtk.ColumnView(model=selection)
        col_view.set_show_row_separators(True)
        col_view.set_show_column_separators(True)
        col_view.set_hexpand(True)
        col_view.set_vexpand(True)
        sort_model.set_sorter(col_view.get_sorter())
        self._col_view = col_view

        col_view.append_column(_make_text_col('PID',      lambda r: str(r.pid)))
        col_view.append_column(_make_text_col('User',     lambda r: r.user))
        col_view.append_column(_make_text_col('Database', lambda r: r.db))
        col_view.append_column(_make_text_col('State',    lambda r: r.state))
        col_view.append_column(_make_text_col('Duration', lambda r: _duration_label(r.duration_s)))
        col_view.append_column(_make_text_col('Wait',     lambda r: r.wait))
        col_view.append_column(_make_text_col('Query',    lambda r: r.query, mono=True, expand=True))

        # Right-click → terminate
        click = Gtk.GestureClick(button=3)
        click.connect('pressed', self._on_right_click)
        col_view.add_controller(click)

        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        scroll.set_child(col_view)
        self.append(scroll)

        # Auto-refresh timer
        self._refresh_id = GLib.timeout_add(_REFRESH_INTERVAL_MS, self._on_refresh_tick)

    # ── Filter ───────────────────────────────────────────────────────────────

    def _row_visible(self, row, _user_data):
        query = self._filter_entry.get_text().strip().lower()
        if not query:
            return True
        return (query in row.user.lower() or
                query in row.db.lower() or
                query in row.state.lower())

    def _on_filter_changed(self, _entry):
        if self._filter_debounce_id is not None:
            GLib.source_remove(self._filter_debounce_id)
        self._filter_debounce_id = GLib.timeout_add(300, self._do_filter)

    def _do_filter(self):
        self._filter_debounce_id = None
        self._custom_filter.changed(Gtk.FilterChange.DIFFERENT)
        return False

    # ── Data fetch ───────────────────────────────────────────────────────────

    def _refresh(self):
        if self._fetching:
            return
        self._fetching = True
        self._status_label.set_label('Refreshing…')
        threading.Thread(target=self._fetch, daemon=True).start()

    def _on_refresh_tick(self):
        self._refresh()
        return True

    def _fetch(self):
        try:
            from tunnel import open_db
            with open_db(self._conn) as db:
                with db.cursor() as cur:
                    cur.execute(_QUERY_SQL)
                    rows = cur.fetchall()
            if self._alive:
                GLib.idle_add(self._populate, rows)
        except Exception as e:
            if self._alive:
                GLib.idle_add(self._on_fetch_error, _friendly_pg_error(e))
        finally:
            self._fetching = False

    def _populate(self, rows):
        if not self._alive:
            return

        # Build a {pid: row_tuple} map for the incoming data
        incoming = {
            r[0]: r for r in rows  # r[0] is pid
        }

        # Collect existing PIDs for the append-new step
        existing_pids = {
            self._store.get_item(i).pid for i in range(self._store.get_n_items())
        }

        # Update changed rows and remove gone ones (iterate in reverse to keep indices stable)
        any_changed = False
        for i in range(self._store.get_n_items() - 1, -1, -1):
            item = self._store.get_item(i)
            if item.pid not in incoming:
                self._store.remove(i)
                any_changed = True
            else:
                pid, user, db, state, duration_s, wait, query = incoming[item.pid]
                if (item.user != user or item.db != db or item.state != state
                        or item.duration_s != duration_s or item.wait != wait
                        or item.query != query):
                    self._store.splice(i, 1, [_ActivityRow(pid, user, db, state, duration_s, wait, query)])
                    any_changed = True

        # Append new PIDs (not in existing store)
        for pid, r in incoming.items():
            if pid not in existing_pids:
                self._store.append(_ActivityRow(*r))
                any_changed = True

        # Reorder store to exactly match the SQL result order.
        # This is simpler and more correct than a client-side comparator: it handles
        # all order changes (new sessions, state transitions, backend_start ordering
        # for idle sessions) without needing extra columns.
        if any_changed:
            row_order = {r[0]: i for i, r in enumerate(rows)}
            self._store.sort(lambda a, b: row_order.get(a.pid, len(rows)) - row_order.get(b.pid, len(rows)))

        n = len(rows)
        self._status_label.set_label(f'{n} session{"s" if n != 1 else ""}')
        self._error_banner.set_revealed(False)

    def _on_fetch_error(self, msg):
        if not self._alive:
            return
        if 'permission denied' in msg.lower() or '42501' in msg:
            msg = ('Permission denied. Viewing Server Activity requires '
                   'superuser or membership in pg_monitor.')
        self._status_label.set_label('')
        self._error_banner.set_title(msg)
        self._error_banner.set_revealed(True)

    # ── Terminate session ────────────────────────────────────────────────────

    def _on_right_click(self, gesture, _n, x, y):
        widget = self._col_view.pick(x, y, Gtk.PickFlags.DEFAULT)
        while widget and widget is not self._col_view:
            row = getattr(widget, '_activity_row', None)
            if row is not None:
                self._show_terminate_menu(row.pid, x, y)
                return
            widget = widget.get_parent()

    def _show_terminate_menu(self, pid, x, y):
        menu = Gio.Menu()
        menu.append(f'Terminate session (PID {pid})', 'activity.terminate')

        action_group = Gio.SimpleActionGroup()
        action = Gio.SimpleAction.new('terminate', None)
        action.connect('activate', lambda *_: self._confirm_terminate(pid))
        action_group.add_action(action)
        self._col_view.insert_action_group('activity', action_group)

        popover = Gtk.PopoverMenu.new_from_model(menu)
        popover.set_parent(self._col_view)
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
            if self._alive:
                GLib.idle_add(self._on_terminated, pid, success)
        except Exception as e:
            if self._alive:
                GLib.idle_add(self._on_terminate_error, _friendly_pg_error(e))

    def _on_terminated(self, pid, success):
        if not self._alive:
            return
        msg = f'Session {pid} terminated' if success else f'Session {pid} not found'
        self._status_label.set_label(msg)
        self._refresh()

    def _on_terminate_error(self, msg):
        if not self._alive:
            return
        self._error_banner.set_title(f'Terminate failed: {msg}')
        self._error_banner.set_revealed(True)

    def _on_destroy(self, _widget):
        self._alive = False
        if self._refresh_id:
            GLib.source_remove(self._refresh_id)
            self._refresh_id = 0
        if self._filter_debounce_id is not None:
            GLib.source_remove(self._filter_debounce_id)
            self._filter_debounce_id = None
