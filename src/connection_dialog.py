import threading
import uuid
from urllib.parse import urlparse, unquote, quote

import gi
import keyring

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Gtk, Adw, GObject, GLib, Gdk

from connections import KEYRING_SERVICE


class ConnectionDialog(Adw.Window):
    __gsignals__ = {
        'connection-saved': (GObject.SignalFlags.RUN_FIRST, None, (GObject.TYPE_PYOBJECT,))
    }

    def __init__(self, parent, connection=None, duplicate=False):
        if duplicate:
            title = 'Duplicate Connection'
        elif connection is None:
            title = 'New Connection'
        else:
            title = 'Edit Connection'
        super().__init__(
            title=title,
            transient_for=parent,
            modal=True,
            default_width=820,
            resizable=False,
        )
        self._connection = connection
        self._duplicate = duplicate
        self._build_ui()
        if duplicate:
            self.connect('map', lambda _: self._name_row.grab_focus())
        self.connect('close-request', self._on_close_request)

    def _build_ui(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        header = Adw.HeaderBar()
        box.append(header)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        content.set_margin_top(12)
        content.set_margin_bottom(20)
        content.set_margin_start(20)
        content.set_margin_end(20)

        conn = self._connection

        # ── URI import ────────────────────────────────────────────────────────
        uri_group = Adw.PreferencesGroup()

        self._uri_row = Adw.EntryRow(title='Paste PostgreSQL URI')
        parse_btn = Gtk.Button(icon_name='go-next-symbolic')
        parse_btn.add_css_class('flat')
        parse_btn.set_valign(Gtk.Align.CENTER)
        parse_btn.set_tooltip_text('Parse URI and fill form')
        parse_btn.connect('clicked', self._on_parse_uri)
        self._uri_row.add_suffix(parse_btn)
        self._uri_row.connect('entry-activated', self._on_parse_uri)
        uri_group.add(self._uri_row)

        # ── Name ─────────────────────────────────────────────────────────────
        name_group = Adw.PreferencesGroup()

        self._name_row = Adw.EntryRow(title='Connection Name')
        name_group.add(self._name_row)

        # ── Database ─────────────────────────────────────────────────────────
        details_group = Adw.PreferencesGroup(title='Database')

        self._host_row = Adw.EntryRow(title='Host')
        self._port_row = Adw.EntryRow(title='Port')
        self._database_row = Adw.EntryRow(title='Database')

        # ── Connection string preview ─────────────────────────────────────────
        self._uri_preview_row = Adw.ActionRow()
        self._uri_preview_row.set_subtitle_selectable(True)

        copy_uri_btn = Gtk.Button(icon_name='edit-copy-symbolic')
        copy_uri_btn.add_css_class('flat')
        copy_uri_btn.set_valign(Gtk.Align.CENTER)
        copy_uri_btn.set_tooltip_text('Copy URI')
        copy_uri_btn.connect('clicked', self._on_copy_preview_uri)
        self._uri_preview_row.add_suffix(copy_uri_btn)
        self._copy_uri_btn = copy_uri_btn

        uri_expander = Adw.ExpanderRow(title='Connection String')
        uri_expander.add_row(self._uri_preview_row)

        details_group.add(self._host_row)
        details_group.add(self._port_row)
        details_group.add(self._database_row)
        details_group.add(uri_expander)

        # ── Authentication ────────────────────────────────────────────────────
        auth_group = Adw.PreferencesGroup(title='Authentication')

        self._username_row = Adw.EntryRow(title='Username')
        self._password_row = Adw.PasswordEntryRow(title='Password')

        auth_group.add(self._username_row)
        auth_group.add(self._password_row)

        # ── Options ───────────────────────────────────────────────────────────
        options_group = Adw.PreferencesGroup(title='Options')

        self._readonly_row = Adw.SwitchRow(
            title='Read-only',
            subtitle='Prevents accidental writes. Recommended for production databases.',
        )
        self._readonly_row.set_active(conn.get('read_only', False) if conn else False)
        options_group.add(self._readonly_row)

        self._default_schema_row = Adw.EntryRow(title='Default Schema')
        self._default_schema_row.set_tooltip_text(
            'Optional. Sets search_path on connect and expands this schema in the browser.'
        )
        options_group.add(self._default_schema_row)

        # ── SSH Tunnel ────────────────────────────────────────────────────────
        ssh_group = Adw.PreferencesGroup(title='SSH Tunnel')

        self._ssh_row = Adw.ExpanderRow(title='Use SSH Tunnel')
        self._ssh_row.set_show_enable_switch(True)
        self._ssh_row.set_subtitle('Use if your PostgreSQL server is behind an SSH bastion host.')

        self._ssh_host_row = Adw.EntryRow(title='SSH Host')
        self._ssh_port_row = Adw.EntryRow(title='SSH Port')
        self._ssh_user_row = Adw.EntryRow(title='SSH User')

        # Key path row with browse button
        self._ssh_key_row = Adw.EntryRow(title='Private Key Path')
        browse_btn = Gtk.Button(icon_name='document-open-symbolic')
        browse_btn.add_css_class('flat')
        browse_btn.set_valign(Gtk.Align.CENTER)
        browse_btn.set_tooltip_text('Browse…')
        browse_btn.connect('clicked', self._on_browse_key)
        self._ssh_key_row.add_suffix(browse_btn)

        self._ssh_passphrase_row = Adw.PasswordEntryRow(title='Key Passphrase')

        self._ssh_row.add_row(self._ssh_host_row)
        self._ssh_row.add_row(self._ssh_port_row)
        self._ssh_row.add_row(self._ssh_user_row)
        self._ssh_row.add_row(self._ssh_key_row)
        self._ssh_row.add_row(self._ssh_passphrase_row)

        ssh_group.add(self._ssh_row)

        # ── Populate values ───────────────────────────────────────────────────
        if conn and self._duplicate:
            self._name_row.set_text(conn['name'] + ' copy')
        else:
            self._name_row.set_text(conn['name'] if conn else '')
        self._host_row.set_text(conn['host'] if conn else 'localhost')
        self._port_row.set_text(str(conn['port']) if conn else '5432')
        self._database_row.set_text(conn['database'] if conn else 'postgres')
        self._username_row.set_text(conn['username'] if conn else 'postgres')

        keyring_failed = False

        try:
            db_password = (keyring.get_password(KEYRING_SERVICE, conn['id']) if conn else '') or ''
        except Exception:
            db_password = ''
            keyring_failed = True
        self._password_row.set_text(db_password)

        ssh_enabled = conn.get('ssh_enabled', False) if conn else False
        self._ssh_row.set_enable_expansion(ssh_enabled)
        self._ssh_row.set_expanded(ssh_enabled)
        self._ssh_host_row.set_text(conn.get('ssh_host', '') if conn else '')
        self._ssh_port_row.set_text(str(conn.get('ssh_port', 22)) if conn else '22')
        self._ssh_user_row.set_text(conn.get('ssh_user', '') if conn else '')
        self._ssh_key_row.set_text(conn.get('ssh_key_path', '') if conn else '')

        try:
            ssh_passphrase = (
                keyring.get_password(KEYRING_SERVICE, f"{conn['id']}:ssh") if conn else ''
            ) or ''
        except Exception:
            ssh_passphrase = ''
            keyring_failed = True
        self._ssh_passphrase_row.set_text(ssh_passphrase)
        self._default_schema_row.set_text(conn.get('default_schema', '') if conn else '')

        self._keyring_warning = Gtk.Label(
            label='Could not load passwords from keyring. Make sure a secrets service is running.'
        )
        self._keyring_warning.add_css_class('warning')
        self._keyring_warning.set_wrap(True)
        self._keyring_warning.set_xalign(0)
        self._keyring_warning.set_visible(keyring_failed)

        # Connect live preview signals
        for row in (self._host_row, self._port_row, self._database_row, self._username_row):
            row.connect('notify::text', self._update_uri_preview)
        self._update_uri_preview()

        # ── 2-column layout ───────────────────────────────────────────────────
        left_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        left_col.set_hexpand(True)
        left_col.append(name_group)
        left_col.append(details_group)
        left_col.append(auth_group)

        right_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        right_col.set_hexpand(True)
        right_col.append(options_group)
        right_col.append(ssh_group)

        two_col = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=20)
        two_col.append(left_col)
        two_col.append(right_col)

        self._uri_error_label = Gtk.Label()
        self._uri_error_label.add_css_class('error')
        self._uri_error_label.set_xalign(0)
        self._uri_error_label.set_wrap(True)
        self._uri_error_label.set_visible(False)

        content.append(uri_group)
        content.append(self._uri_error_label)
        content.append(two_col)
        content.append(self._keyring_warning)

        # ── Test / Save ───────────────────────────────────────────────────────
        self._test_bar = Gtk.CenterBox()

        self._test_btn = Gtk.Button(label='Test Connection')
        self._test_btn.add_css_class('pill')
        self._test_btn.connect('clicked', self._on_test)

        self._test_spinner = Gtk.Spinner()
        self._test_spinner.set_size_request(16, 16)

        self._test_label = Gtk.Label()
        self._test_label.set_xalign(0)
        self._test_label.set_wrap(True)
        self._test_label.set_max_width_chars(60)

        status_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        status_box.set_halign(Gtk.Align.CENTER)
        status_box.append(self._test_spinner)
        status_box.append(self._test_label)

        self._save_btn = Gtk.Button(label='Save Connection')
        self._save_btn.add_css_class('suggested-action')
        self._save_btn.add_css_class('pill')
        self._save_btn.connect('clicked', self._on_save)

        self._test_bar.set_start_widget(self._test_btn)
        self._test_bar.set_center_widget(status_box)
        self._test_bar.set_end_widget(self._save_btn)
        content.append(self._test_bar)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_propagate_natural_height(True)
        scroll.set_child(content)
        box.append(scroll)

        self.set_content(box)

    def _on_browse_key(self, _btn):
        dialog = Gtk.FileChooserNative(
            title='Select Private Key',
            transient_for=self,
            action=Gtk.FileChooserAction.OPEN,
        )
        dialog.connect('response', self._on_key_chosen)
        dialog.present()

    def _on_key_chosen(self, dialog, response):
        if response == Gtk.ResponseType.ACCEPT:
            self._ssh_key_row.set_text(dialog.get_file().get_path())

    def _on_parse_uri(self, *_):
        uri_text = self._uri_row.get_text().strip()
        if not uri_text:
            return
        try:
            parsed = urlparse(uri_text)
            if parsed.scheme not in ('postgresql', 'postgres'):
                raise ValueError('URI must start with postgresql:// or postgres://')
            host = parsed.hostname or 'localhost'
            port = parsed.port or 5432
            database = unquote(parsed.path.lstrip('/')) or 'postgres'
            username = unquote(parsed.username or '')
            password = unquote(parsed.password or '')
        except Exception as e:
            self._uri_row.add_css_class('error')
            self._uri_error_label.set_text(str(e))
            self._uri_error_label.set_visible(True)
            return

        self._uri_row.remove_css_class('error')
        self._uri_error_label.set_visible(False)
        self._host_row.set_text(host)
        self._port_row.set_text(str(port))
        self._database_row.set_text(database)
        self._username_row.set_text(username)
        self._password_row.set_text(password)
        if not self._name_row.get_text().strip():
            self._name_row.set_text(
                f'{username}@{host}/{database}' if username else f'{host}/{database}'
            )
        self._uri_row.set_text('')

    def _update_uri_preview(self, *_):
        host = self._host_row.get_text().strip() or 'host'
        try:
            port = int(self._port_row.get_text().strip())
        except ValueError:
            port = 5432
        database = quote(self._database_row.get_text().strip() or 'database', safe='')
        username = self._username_row.get_text().strip()
        if username:
            uri = f'postgresql://{quote(username, safe="")}@{host}:{port}/{database}'
        else:
            uri = f'postgresql://{host}:{port}/{database}'
        self._uri_preview_row.set_subtitle(uri)

    def _on_copy_preview_uri(self, btn):
        uri = self._uri_preview_row.get_subtitle()
        Gdk.Display.get_default().get_clipboard().set(uri)
        btn.set_icon_name('object-select-symbolic')
        GLib.timeout_add(1500, lambda: btn.set_icon_name('edit-copy-symbolic') or False)

    def _on_close_request(self, _):
        return False

    def _current_params(self):
        try:
            port = int(self._port_row.get_text().strip())
        except ValueError:
            port = 5432
        try:
            ssh_port = int(self._ssh_port_row.get_text().strip())
        except ValueError:
            ssh_port = 22

        params = {
            'host': self._host_row.get_text().strip() or 'localhost',
            'port': port,
            'database': self._database_row.get_text().strip() or 'postgres',
            'username': self._username_row.get_text().strip(),
            'password': self._password_row.get_text(),
            'read_only': self._readonly_row.get_active(),
            'ssh_enabled': self._ssh_row.get_enable_expansion(),
            'ssh_host': self._ssh_host_row.get_text().strip(),
            'ssh_port': ssh_port,
            'ssh_user': self._ssh_user_row.get_text().strip(),
            'ssh_key_path': self._ssh_key_row.get_text().strip(),
            'ssh_passphrase': self._ssh_passphrase_row.get_text(),
        }
        default_schema = self._default_schema_row.get_text().strip()
        if default_schema:
            params['default_schema'] = default_schema
        return params

    def _on_test(self, _btn):
        self._test_btn.set_sensitive(False)
        self._save_btn.set_sensitive(False)
        self._test_label.set_label('Connecting…')
        self._test_label.remove_css_class('success')
        self._test_label.remove_css_class('error')
        self._test_spinner.start()
        threading.Thread(
            target=self._run_test, args=(self._current_params(),), daemon=True
        ).start()

    def _run_test(self, params):
        try:
            import psycopg
            from tunnel import open_tunnel

            with open_tunnel(params) as (host, port):
                with psycopg.connect(
                    host=host,
                    port=port,
                    dbname=params['database'],
                    user=params['username'],
                    password=params['password'],
                    connect_timeout=10,
                ):
                    pass
            GLib.idle_add(self._on_test_result, True, None)
        except Exception as e:
            GLib.idle_add(self._on_test_result, False, str(e))

    def _on_test_result(self, success, error):
        self._test_spinner.stop()
        self._test_btn.set_sensitive(True)
        self._save_btn.set_sensitive(True)
        if success:
            self._test_label.set_label('Connected successfully')
            self._test_label.add_css_class('success')
            self._test_label.remove_css_class('error')
        else:
            self._test_label.set_label(error or 'Connection failed')
            self._test_label.add_css_class('error')
            self._test_label.remove_css_class('success')

    def _on_save(self, _btn):
        name = self._name_row.get_text().strip()
        host = self._host_row.get_text().strip()
        username = self._username_row.get_text().strip()

        valid = True
        for row, value in (
            (self._name_row, name),
            (self._host_row, host),
            (self._username_row, username),
        ):
            if value:
                row.remove_css_class('error')
            else:
                row.add_css_class('error')
                valid = False

        if not valid:
            return

        try:
            port = int(self._port_row.get_text().strip())
        except ValueError:
            port = 5432
        try:
            ssh_port = int(self._ssh_port_row.get_text().strip())
        except ValueError:
            ssh_port = 22

        conn = {
            'id': str(uuid.uuid4()) if self._duplicate else (
                self._connection['id'] if self._connection else str(uuid.uuid4())
            ),
            'name': name,
            'host': host,
            'port': port,
            'database': self._database_row.get_text().strip() or 'postgres',
            'username': username,
            'password': self._password_row.get_text(),
            'read_only': self._readonly_row.get_active(),
            'ssh_enabled': self._ssh_row.get_enable_expansion(),
            'ssh_host': self._ssh_host_row.get_text().strip(),
            'ssh_port': ssh_port,
            'ssh_user': self._ssh_user_row.get_text().strip(),
            'ssh_key_path': self._ssh_key_row.get_text().strip(),
            'ssh_passphrase': self._ssh_passphrase_row.get_text(),
        }
        default_schema = self._default_schema_row.get_text().strip()
        if default_schema:
            conn['default_schema'] = default_schema
        self.emit('connection-saved', conn)
        self.close()
