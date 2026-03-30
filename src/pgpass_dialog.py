import os
import stat

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Gtk, Adw, GObject


def _split_pgpass_line(line):
    """Split a pgpass line on unescaped colons, respecting backslash escapes."""
    fields = []
    current = []
    i = 0
    while i < len(line):
        if line[i] == '\\' and i + 1 < len(line):
            current.append(line[i + 1])
            i += 2
        elif line[i] == ':':
            fields.append(''.join(current))
            current = []
            i += 1
        else:
            current.append(line[i])
            i += 1
    fields.append(''.join(current))
    return fields


def parse_pgpass(path):
    """Parse a pgpass file.

    Returns (entries, warnings):
      entries  — list of dicts with keys hostname, port, database, username, password
      warnings — list of human-readable warning strings
    """
    warnings = []
    entries = []

    if not os.path.exists(path):
        return entries, warnings

    # Warn if permissions are too open (psql refuses to use it if world-readable)
    st = os.stat(path)
    if st.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        mode_str = oct(stat.S_IMODE(st.st_mode))
        warnings.append(
            f'.pgpass permissions are {mode_str} — they should be 0600. '
            'Credentials may be exposed to other users on this system.'
        )

    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            fields = _split_pgpass_line(line)
            if len(fields) != 5:
                continue
            hostname, port_str, database, username, password = fields

            # Skip wildcard hostnames — they can't map to a specific connection
            if hostname == '*':
                continue

            try:
                port = int(port_str) if port_str != '*' else 5432
            except ValueError:
                port = 5432

            entries.append({
                'hostname': hostname,
                'port': port,
                'database': database if database != '*' else 'postgres',
                'username': username,
                'password': password,
            })

    return entries, warnings


class PgpassImportDialog(Adw.Window):
    __gsignals__ = {
        'entries-selected': (GObject.SignalFlags.RUN_FIRST, None, (GObject.TYPE_PYOBJECT,))
    }

    def __init__(self, parent, entries, warnings):
        super().__init__(
            title='Import from .pgpass',
            transient_for=parent,
            modal=True,
            default_width=460,
            resizable=False,
        )
        self._entries = entries
        self._switches = []
        self._build_ui(warnings)

    def _build_ui(self, warnings):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        header = Adw.HeaderBar()
        box.append(header)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        content.set_margin_top(12)
        content.set_margin_bottom(20)
        content.set_margin_start(16)
        content.set_margin_end(16)

        for warning in warnings:
            lbl = Gtk.Label(label=warning)
            lbl.add_css_class('warning')
            lbl.set_wrap(True)
            lbl.set_xalign(0)
            content.append(lbl)

        entries_group = Adw.PreferencesGroup(title='Entries')

        for entry in self._entries:
            port_str = f':{entry["port"]}' if entry['port'] != 5432 else ''
            title = f'{entry["hostname"]}{port_str}/{entry["database"]}'
            subtitle = f'User: {entry["username"]}'

            switch_row = Adw.SwitchRow(title=title, subtitle=subtitle)
            switch_row.set_active(True)
            switch_row._pgpass_entry = entry
            self._switches.append(switch_row)
            entries_group.add(switch_row)

        content.append(entries_group)

        import_btn = Gtk.Button(label='Import Selected')
        import_btn.add_css_class('suggested-action')
        import_btn.add_css_class('pill')
        import_btn.connect('clicked', self._on_import)
        content.append(import_btn)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_propagate_natural_height(True)

        clamp = Adw.Clamp(maximum_size=420)
        clamp.set_child(content)
        scroll.set_child(clamp)
        box.append(scroll)

        self.set_content(box)

    def _on_import(self, _btn):
        selected = [sw._pgpass_entry for sw in self._switches if sw.get_active()]
        self.emit('entries-selected', selected)
        self.close()
