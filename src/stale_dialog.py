import datetime

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Gtk, Adw, GObject

import prefs


def _is_stale(conn, threshold_days):
    ts = conn.get('last_connected')
    if not ts:
        return True
    try:
        dt = datetime.datetime.fromisoformat(ts.rstrip('Z')).replace(tzinfo=datetime.timezone.utc)
        age = (datetime.datetime.now(datetime.timezone.utc) - dt).days
        return age >= threshold_days
    except (ValueError, TypeError):
        return True


class StaleConnectionsDialog(Adw.Dialog):
    __gsignals__ = {
        'connections-deleted': (GObject.SignalFlags.RUN_FIRST, None, (GObject.TYPE_PYOBJECT,)),
    }

    def __init__(self, store, conn_health=None):
        super().__init__(title='Clean Up Stale Connections', content_width=520, content_height=560)
        self.add_css_class('tusk-main')
        self._store = store
        self._conn_health = conn_health or {}
        self._checks = {}  # conn_id → Gtk.CheckButton
        self._threshold = prefs.get('stale_days', 30)
        self._build_ui()

    def _build_ui(self):
        header = Adw.HeaderBar()

        self._list_box = Gtk.ListBox()
        self._list_box.add_css_class('boxed-list')
        self._list_box.set_selection_mode(Gtk.SelectionMode.NONE)

        stale = [c for c in self._store.list() if _is_stale(c, self._threshold)]

        if not stale:
            empty = Adw.StatusPage(
                title='No Stale Connections',
                description=f'All connections have been used within the last {self._threshold} days.',
                icon_name='emblem-ok-symbolic',
            )
            toolbar_view = Adw.ToolbarView()
            toolbar_view.add_top_bar(header)
            toolbar_view.set_content(empty)
            self.set_child(toolbar_view)
            return

        for conn in stale:
            self._list_box.append(self._build_row(conn))

        delete_btn = Gtk.Button(label='Delete Selected')
        delete_btn.add_css_class('destructive-action')
        delete_btn.add_css_class('pill')
        delete_btn.set_halign(Gtk.Align.CENTER)
        delete_btn.connect('clicked', self._on_delete_clicked)

        summary = Gtk.Label(
            label=f'{len(stale)} connection{"s" if len(stale) != 1 else ""} '
                  f'unused for {self._threshold}+ days or never connected'
        )
        summary.add_css_class('dim-label')
        summary.set_wrap(True)
        summary.set_xalign(0)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_top(16)
        box.set_margin_bottom(20)
        box.set_margin_start(20)
        box.set_margin_end(20)
        box.append(summary)
        box.append(self._list_box)
        box.append(delete_btn)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_propagate_natural_height(True)
        scroll.set_child(box)

        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(header)
        toolbar_view.set_content(scroll)
        self.set_child(toolbar_view)

    def _build_row(self, conn):
        row = Adw.ActionRow(title=conn['name'])
        row.set_subtitle(self._last_connected_label(conn))

        # Health dot
        health = self._conn_health.get(conn['id'], {})
        status = health.get('status', 'unknown')
        color = {'ok': '#33d17a', 'error': '#e01b24', 'tunnel': '#e5a50a'}.get(status, '#888888')
        dot = Gtk.Label()
        dot.set_markup(f'<span foreground="{color}">⬤</span>')
        dot.set_valign(Gtk.Align.CENTER)
        row.add_prefix(dot)

        check = Gtk.CheckButton()
        check.set_active(True)
        check.set_valign(Gtk.Align.CENTER)
        row.add_suffix(check)
        row.set_activatable_widget(check)
        self._checks[conn['id']] = check
        return row

    @staticmethod
    def _last_connected_label(conn):
        ts = conn.get('last_connected')
        if not ts:
            return 'Never connected'
        try:
            dt = datetime.datetime.fromisoformat(ts.rstrip('Z')).replace(tzinfo=datetime.timezone.utc)
            delta = datetime.datetime.now(datetime.timezone.utc) - dt
            days = delta.days
            return f'Last connected {days} day{"s" if days != 1 else ""} ago'
        except (ValueError, TypeError):
            return 'Never connected'

    def _on_delete_clicked(self, _btn):
        selected_ids = [cid for cid, cb in self._checks.items() if cb.get_active()]
        if not selected_ids:
            return
        n = len(selected_ids)
        dialog = Adw.AlertDialog(
            heading=f'Delete {n} connection{"s" if n != 1 else ""}?',
            body='This cannot be undone.',
        )
        dialog.add_response('cancel', 'Cancel')
        dialog.add_response('delete', f'Delete {n}')
        dialog.set_response_appearance('delete', Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response('cancel')
        dialog.set_close_response('cancel')
        dialog.connect('response', self._on_confirmed, selected_ids)
        dialog.present(self)

    def _on_confirmed(self, _dialog, response, selected_ids):
        if response != 'delete':
            return
        self.emit('connections-deleted', selected_ids)
        self.close()
