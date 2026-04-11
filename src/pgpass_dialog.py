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

    try:
        # Warn if permissions are too open (psql refuses to use it if world-readable)
        st = os.stat(path)
        if st.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
            mode_str = oct(stat.S_IMODE(st.st_mode))
            warnings.append(
                f'.pgpass permissions are {mode_str} — they should be 0600. '
                'Credentials may be exposed to other users on this system.'
            )

        with open(path, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                fields = _split_pgpass_line(line)
                if len(fields) != 5:
                    continue
                hostname, port_str, database, username, password = fields

                # Skip entries with wildcards in any field — they can't map to a
                # specific connection without manual completion
                if '*' in (hostname, port_str, database, username, password):
                    continue

                try:
                    port = int(port_str)
                except ValueError:
                    port = 5432

                entries.append({
                    'hostname': hostname,
                    'port': port,
                    'database': database,
                    'username': username,
                    'password': password,
                })
    except (OSError, UnicodeDecodeError) as e:
        warnings.append(f'Could not read ~/.pgpass: {e}')

    return entries, warnings


class PgpassImportDialog(Adw.Dialog):
    __gsignals__ = {
        'entries-selected': (GObject.SignalFlags.RUN_FIRST, None, (GObject.TYPE_PYOBJECT,))
    }

    def __init__(self, parent, entries, warnings, existing_names=None):
        super().__init__(title='Import from .pgpass', content_width=460)
        self.add_css_class('tusk-main')
        self._entries = entries
        self._switches = []
        self._existing_names = existing_names or set()
        self._build_ui(warnings)

    def _build_ui(self, warnings):
        header = Adw.HeaderBar()

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        content.set_margin_top(12)
        content.set_margin_bottom(20)
        content.set_margin_start(16)
        content.set_margin_end(16)

        for warning in warnings:
            banner = Adw.Banner(title=warning)
            banner.set_revealed(True)
            content.append(banner)

        # Compute overlap with existing connections
        overlap_count = 0
        entry_names = []
        for entry in self._entries:
            name = f'{entry["username"]}@{entry["hostname"]}/{entry["database"]}'
            entry_names.append(name)
            if name in self._existing_names:
                overlap_count += 1

        n = len(self._entries)
        summary_parts = [f'Importing {n} {"connection" if n == 1 else "connections"}.']
        if overlap_count > 0:
            summary_parts.append(
                f'{overlap_count} existing {"connection" if overlap_count == 1 else "connections"} '
                'with matching names will be replaced.'
            )
        summary_label = Gtk.Label(label=' '.join(summary_parts))
        summary_label.add_css_class('dim-label')
        summary_label.set_wrap(True)
        summary_label.set_xalign(0)
        content.append(summary_label)

        entries_group = Adw.PreferencesGroup(title='Entries')

        for entry, entry_name in zip(self._entries, entry_names):
            port_str = f':{entry["port"]}' if entry['port'] != 5432 else ''
            title = f'{entry["hostname"]}{port_str}/{entry["database"]}'
            if entry_name in self._existing_names:
                subtitle = f'User: {entry["username"]} — will replace existing connection'
            else:
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

        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(header)
        toolbar_view.set_content(scroll)
        self.set_child(toolbar_view)

    def _on_import(self, _btn):
        selected = [sw._pgpass_entry for sw in self._switches if sw.get_active()]
        self.emit('entries-selected', selected)
        self.close()
