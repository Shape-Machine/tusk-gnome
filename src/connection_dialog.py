import threading
import uuid

import gi
import keyring

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Gtk, Adw, GObject, GLib

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
            default_width=440,
            resizable=False,
        )
        self._connection = connection
        self._duplicate = duplicate
        self._build_ui()

    def _build_ui(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        header = Adw.HeaderBar()
        box.append(header)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        content.set_margin_top(12)
        content.set_margin_bottom(20)
        content.set_margin_start(16)
        content.set_margin_end(16)

        conn = self._connection

        # ── Name ─────────────────────────────────────────────────────────────
        name_group = Adw.PreferencesGroup()

        self._name_row = Adw.EntryRow(title='Connection Name')
        name_group.add(self._name_row)

        # ── Database ─────────────────────────────────────────────────────────
        details_group = Adw.PreferencesGroup(title='Database')

        self._host_row = Adw.EntryRow(title='Host')
        self._port_row = Adw.EntryRow(title='Port')
        self._database_row = Adw.EntryRow(title='Database')

        # Browse databases button
        browse_db_btn = Gtk.Button(icon_name='folder-symbolic')
        browse_db_btn.add_css_class('flat')
        browse_db_btn.set_valign(Gtk.Align.CENTER)
        browse_db_btn.set_tooltip_text('Browse available databases…')
        browse_db_btn.connect('clicked', self._on_browse_database)
        self._database_row.add_suffix(browse_db_btn)

        # Popover for database list
        self._db_popover = Gtk.Popover()
        self._db_popover.set_parent(browse_db_btn)
        self._db_popover.set_has_arrow(True)

        db_popover_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        db_popover_box.set_margin_top(6)
        db_popover_box.set_margin_bottom(6)
        db_popover_box.set_margin_start(6)
        db_popover_box.set_margin_end(6)

        self._db_browse_spinner = Gtk.Spinner()
        self._db_browse_spinner.set_halign(Gtk.Align.CENTER)

        self._db_browse_error = Gtk.Label()
        self._db_browse_error.add_css_class('error')
        self._db_browse_error.set_wrap(True)
        self._db_browse_error.set_max_width_chars(28)
        self._db_browse_error.set_xalign(0)
        self._db_browse_error.set_visible(False)

        self._db_browse_list = Gtk.ListBox()
        self._db_browse_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self._db_browse_list.add_css_class('boxed-list')
        self._db_browse_list.connect('row-activated', self._on_db_row_activated)
        self._db_browse_list.set_visible(False)

        db_scroll = Gtk.ScrolledWindow()
        db_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        db_scroll.set_max_content_height(200)
        db_scroll.set_propagate_natural_height(True)
        db_scroll.set_child(self._db_browse_list)

        db_popover_box.append(self._db_browse_spinner)
        db_popover_box.append(self._db_browse_error)
        db_popover_box.append(db_scroll)
        self._db_popover.set_child(db_popover_box)

        details_group.add(self._host_row)
        details_group.add(self._port_row)
        details_group.add(self._database_row)

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
            subtitle='Prevents accidental writes to this database',
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

        content.append(name_group)
        content.append(details_group)
        content.append(auth_group)
        content.append(self._keyring_warning)
        content.append(options_group)
        content.append(ssh_group)

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
        self._test_label.set_max_width_chars(35)

        status_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        status_box.set_halign(Gtk.Align.CENTER)
        status_box.append(self._test_spinner)
        status_box.append(self._test_label)

        self._test_bar.set_start_widget(self._test_btn)
        self._test_bar.set_end_widget(status_box)
        content.append(self._test_bar)

        self._save_btn = Gtk.Button(label='Save Connection')
        self._save_btn.add_css_class('suggested-action')
        self._save_btn.add_css_class('pill')
        self._save_btn.connect('clicked', self._on_save)
        content.append(self._save_btn)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_propagate_natural_height(True)

        clamp = Adw.Clamp(maximum_size=400)
        clamp.set_child(content)
        scroll.set_child(clamp)
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

    def _on_browse_database(self, _btn):
        # Clear previous state
        child = self._db_browse_list.get_first_child()
        while child:
            next_child = child.get_next_sibling()
            self._db_browse_list.remove(child)
            child = next_child
        self._db_browse_list.set_visible(False)
        self._db_browse_error.set_visible(False)
        self._db_browse_spinner.start()
        self._db_popover.popup()

        params = self._current_params()
        params['database'] = 'postgres'
        threading.Thread(target=self._fetch_databases, args=(params,), daemon=True).start()

    def _fetch_databases(self, params):
        try:
            import psycopg
            from tunnel import open_tunnel

            with open_tunnel(params) as (host, port):
                with psycopg.connect(
                    host=host,
                    port=port,
                    dbname='postgres',
                    user=params['username'],
                    password=params['password'],
                    connect_timeout=10,
                ) as db:
                    with db.cursor() as cur:
                        cur.execute("""
                            SELECT datname FROM pg_database
                            WHERE datistemplate = false
                              AND datname NOT IN ('template0', 'template1')
                              AND has_database_privilege(current_user, datname, 'CONNECT')
                            ORDER BY datname
                        """)
                        databases = [r[0] for r in cur.fetchall()]
            GLib.idle_add(self._on_databases_fetched, databases)
        except Exception as e:
            GLib.idle_add(self._on_databases_fetch_error, str(e))

    def _on_databases_fetched(self, databases):
        self._db_browse_spinner.stop()
        for name in databases:
            row = Gtk.ListBoxRow()
            row._dbname = name
            label = Gtk.Label(label=name, xalign=0)
            label.set_margin_top(6)
            label.set_margin_bottom(6)
            label.set_margin_start(8)
            label.set_margin_end(8)
            row.set_child(label)
            self._db_browse_list.append(row)
        self._db_browse_list.set_visible(bool(databases))

    def _on_databases_fetch_error(self, error):
        self._db_browse_spinner.stop()
        self._db_browse_error.set_label(error)
        self._db_browse_error.set_visible(True)

    def _on_db_row_activated(self, _listbox, row):
        self._database_row.set_text(row._dbname)
        self._db_popover.popdown()

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
