"""Azure Database for PostgreSQL discovery dialog — Flexible Server via az CLI."""

import threading

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Gtk, Adw, GObject, GLib

import azure_discovery


class AzureDiscoveryDialog(Adw.Dialog):
    """Discover and import Azure Database for PostgreSQL Flexible Server instances.

    Emits 'import-confirmed' with a list of connection dicts when the user
    clicks Import Selected.
    """

    __gsignals__ = {
        'import-confirmed': (GObject.SignalFlags.RUN_FIRST, None, (GObject.TYPE_PYOBJECT,)),
    }

    def __init__(self, existing_instance_ids=None):
        super().__init__(title='Import from Azure', content_width=540)
        self.add_css_class('tusk-main')
        self._existing_ids = set(existing_instance_ids or [])
        self._conns = []
        self._checks = {}           # idx → (Gtk.CheckButton, conn_dict)
        self._subscription_rows = []  # list of (check_button, subscription_dict)
        self._build_ui()

    # ── UI skeleton ────────────────────────────────────────────────────────────

    def _build_ui(self):
        self._header = Adw.HeaderBar()

        self._stack = Gtk.Stack()
        self._stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)

        self._loading_label_widget = Gtk.Label(label='Checking az CLI…')
        self._loading_label_widget.add_css_class('dim-label')
        self._loading_page = self._build_loading_page(self._loading_label_widget)
        self._stack.add_named(self._loading_page, 'loading')

        self._subscription_page = self._build_subscription_page()
        self._stack.add_named(self._subscription_page, 'subscription')

        self._results_page = self._build_results_page()
        self._stack.add_named(self._results_page, 'results')

        self._error_page = self._build_error_page()
        self._stack.add_named(self._error_page, 'error')

        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(self._header)
        toolbar_view.set_content(self._stack)
        self.set_child(toolbar_view)

        threading.Thread(target=self._check_azure, daemon=True).start()

    def _build_loading_page(self, label_widget):
        spinner = Gtk.Spinner()
        spinner.set_size_request(32, 32)
        spinner.start()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_halign(Gtk.Align.CENTER)
        box.set_valign(Gtk.Align.CENTER)
        box.set_vexpand(True)
        box.append(spinner)
        box.append(label_widget)
        return box

    def _build_subscription_page(self):
        self._sub_list_box = Gtk.ListBox()
        self._sub_list_box.add_css_class('boxed-list')
        self._sub_list_box.set_selection_mode(Gtk.SelectionMode.NONE)
        self._sub_list_box.set_filter_func(self._sub_filter_func)

        self._sub_search = Gtk.SearchEntry(placeholder_text='Filter subscriptions…')
        self._sub_search.connect('search-changed',
                                 lambda _: self._sub_list_box.invalidate_filter())

        self._sub_list_scroll = Gtk.ScrolledWindow()
        self._sub_list_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._sub_list_scroll.set_min_content_height(200)
        self._sub_list_scroll.set_max_content_height(320)
        self._sub_list_scroll.set_propagate_natural_height(True)
        self._sub_list_scroll.set_child(self._sub_list_box)

        list_group = Adw.PreferencesGroup(
            title='Azure Subscriptions',
            description='Select a subscription to discover databases in.',
        )

        discover_btn = Gtk.Button(label='Discover Databases')
        discover_btn.add_css_class('suggested-action')
        discover_btn.add_css_class('pill')
        discover_btn.set_halign(Gtk.Align.CENTER)
        discover_btn.connect('clicked', self._on_subscription_confirm)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        box.set_margin_top(20)
        box.set_margin_bottom(20)
        box.set_margin_start(20)
        box.set_margin_end(20)
        box.set_vexpand(True)
        box.append(list_group)
        box.append(self._sub_search)
        box.append(self._sub_list_scroll)
        box.append(discover_btn)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_propagate_natural_height(True)
        scroll.set_child(box)
        return scroll

    def _build_results_page(self):
        self._summary_label = Gtk.Label()
        self._summary_label.add_css_class('dim-label')
        self._summary_label.set_wrap(True)
        self._summary_label.set_xalign(0)

        self._results_list = Gtk.ListBox()
        self._results_list.add_css_class('boxed-list')
        self._results_list.set_selection_mode(Gtk.SelectionMode.NONE)

        self._import_btn = Gtk.Button(label='Import Selected')
        self._import_btn.add_css_class('suggested-action')
        self._import_btn.add_css_class('pill')
        self._import_btn.set_halign(Gtk.Align.CENTER)
        self._import_btn.connect('clicked', self._on_import)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_top(16)
        box.set_margin_bottom(20)
        box.set_margin_start(20)
        box.set_margin_end(20)
        box.append(self._summary_label)
        box.append(self._results_list)
        box.append(self._import_btn)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_propagate_natural_height(True)
        scroll.set_child(box)
        return scroll

    def _build_error_page(self):
        self._error_back_btn = Gtk.Button(label='Choose Different Subscription')
        self._error_back_btn.add_css_class('pill')
        self._error_back_btn.set_halign(Gtk.Align.CENTER)
        self._error_back_btn.set_visible(False)
        self._error_back_btn.connect('clicked',
                                     lambda _: self._stack.set_visible_child_name('subscription'))

        self._error_status = Adw.StatusPage(
            icon_name='dialog-error-symbolic',
            title='Discovery Failed',
            child=self._error_back_btn,
        )
        return self._error_status

    # ── Background checks ──────────────────────────────────────────────────────

    def _check_azure(self):
        if not azure_discovery.azcli_available():
            GLib.idle_add(self._show_error,
                'az CLI not found',
                'Install the Azure CLI and sign in before using this feature.\n\n'
                'Download: https://learn.microsoft.com/cli/azure/install-azure-cli')
            return

        GLib.idle_add(self._loading_label_widget.set_text, 'Checking credentials…')
        try:
            azure_discovery.get_active_account()
        except RuntimeError as e:
            GLib.idle_add(self._show_error, 'Not authenticated', str(e))
            return

        GLib.idle_add(self._loading_label_widget.set_text, 'Fetching subscriptions…')
        try:
            subscriptions = azure_discovery.list_subscriptions()
        except RuntimeError as e:
            GLib.idle_add(self._show_error, 'Could not list subscriptions', str(e))
            return

        if not subscriptions:
            GLib.idle_add(self._show_error,
                'No subscriptions found',
                'No enabled Azure subscriptions are accessible with your account.\n\n'
                'Make sure your account has access to at least one subscription.')
            return

        GLib.idle_add(self._on_azure_checked, subscriptions)

    def _on_azure_checked(self, subscriptions):
        # If there's exactly one subscription, skip the picker and go straight to discovery
        if len(subscriptions) == 1:
            self._start_discovery(subscriptions[0])
            return
        self._populate_subscription_list(subscriptions)
        self._stack.set_visible_child_name('subscription')

    def _populate_subscription_list(self, subscriptions):
        self._subscription_rows = []

        while True:
            row = self._sub_list_box.get_first_child()
            if row is None:
                break
            self._sub_list_box.remove(row)

        for sub in subscriptions:
            self._add_subscription_row(sub, checked=sub.get('isDefault', False))

        # Pre-select first if none is default
        if self._subscription_rows and not any(
            check.get_active() for check, _ in self._subscription_rows
        ):
            self._subscription_rows[0][0].set_active(True)

    def _add_subscription_row(self, sub, checked=False):
        sub_id = sub.get('id', '')
        sub_name = sub.get('name', sub_id)
        row = Adw.ActionRow(title=sub_name, subtitle=sub_id)
        check = Gtk.CheckButton()
        check.set_active(checked)
        check.set_valign(Gtk.Align.CENTER)
        # Radio-button behaviour: only one subscription at a time
        if self._subscription_rows:
            check.set_group(self._subscription_rows[0][0])
        check.connect('toggled', self._on_sub_check_toggled)
        row.add_suffix(check)
        row.set_activatable_widget(check)
        self._sub_list_box.append(row)
        self._subscription_rows.append((check, sub))

    def _sub_filter_func(self, row):
        text = self._sub_search.get_text().lower()
        if not text:
            return True
        title = (row.get_title() or '').lower()
        subtitle = (row.get_subtitle() or '').lower()
        return text in title or text in subtitle

    def _on_sub_check_toggled(self, check):
        if check.get_active():
            self._sub_list_box.remove_css_class('error')

    def _on_subscription_confirm(self, _btn):
        selected = [sub for check, sub in self._subscription_rows if check.get_active()]
        if not selected:
            self._sub_list_box.add_css_class('error')
            return
        self._sub_list_box.remove_css_class('error')
        self._start_discovery(selected[0])

    def _start_discovery(self, subscription):
        sub_name = subscription.get('name', subscription.get('id', ''))
        self._loading_label_widget.set_text(f'Discovering databases in {sub_name}…')
        self._stack.set_visible_child_name('loading')
        threading.Thread(
            target=self._run_discovery,
            args=(subscription,),
            daemon=True,
        ).start()

    def _run_discovery(self, subscription):
        sub_id = subscription.get('id', '')
        conns = []
        errors = []
        single_server_names = []

        cert_path = azure_discovery.get_azure_ca_cert()
        if cert_path is None:
            errors.append(
                'Azure CA certificate download failed — connections will use ssl_mode=require '
                'without a pinned certificate.'
            )

        try:
            servers = azure_discovery.discover_azure_postgres(sub_id)
            for server in servers:
                conns.append(azure_discovery.build_azure_conn(server, sub_id, cert_path=cert_path))
        except RuntimeError as e:
            errors.append(f'Flexible Server discovery: {e}')

        # Best-effort Single Server detection for deprecation warning
        single_servers = azure_discovery.detect_single_server(sub_id)
        single_server_names = [s.get('name', '') for s in single_servers if s.get('name')]

        GLib.idle_add(self._show_results, conns, errors, single_server_names)

    # ── Results rendering ──────────────────────────────────────────────────────

    def _show_results(self, conns, errors, single_server_names):
        self._conns = conns
        self._checks = {}

        while True:
            row = self._results_list.get_first_child()
            if row is None:
                break
            self._results_list.remove(row)

        if not conns:
            msg = 'No PostgreSQL Flexible Server instances found in the selected subscription.'
            if errors:
                msg += '\n\nErrors:\n' + '\n'.join(errors)
            self._show_error('No instances found', msg, show_back=True)
            return

        # Group by resource group, then location
        groups = {}  # (resource_group, location) → [conn]
        for conn in conns:
            key = (
                conn.get('_azure_resource_group', ''),
                conn.get('_azure_location', ''),
            )
            groups.setdefault(key, []).append(conn)

        idx = 0
        for (resource_group, location), group_conns in sorted(groups.items()):
            title = resource_group or 'Unknown resource group'
            subtitle = location or ''
            header_row = Adw.ActionRow(title=title, subtitle=subtitle)
            header_row.set_activatable(False)
            header_row.add_css_class('dim-label')
            self._results_list.append(header_row)

            for conn in group_conns:
                already = conn.get('cloud_instance_id', '') in self._existing_ids
                row = Adw.ActionRow(title=conn['name'])
                host = conn.get('host', '')
                port = conn.get('port', 5432)
                endpoint_str = f'{host}:{port}' if host else ''
                subtitle_parts = [conn.get('_azure_version', ''), endpoint_str]
                if already:
                    subtitle_parts.append('Already imported')
                row.set_subtitle(' · '.join(p for p in subtitle_parts if p))
                row.set_sensitive(not already)

                check = Gtk.CheckButton()
                check.set_active(not already)
                check.set_sensitive(not already)
                check.set_valign(Gtk.Align.CENTER)
                row.add_suffix(check)
                row.set_activatable_widget(check)
                self._results_list.append(row)
                self._checks[idx] = (check, conn)
                idx += 1

        total = len(conns)
        already_count = sum(
            1 for c in conns if c.get('cloud_instance_id', '') in self._existing_ids
        )
        summary = f'{total} Flexible Server instance{"s" if total != 1 else ""} found.'
        if already_count:
            summary += f' {already_count} already imported.'
        self._summary_label.set_text(summary)

        if single_server_names:
            n = len(single_server_names)
            names_str = ', '.join(single_server_names[:5])
            if n > 5:
                names_str += f' and {n - 5} more'
            expander = Adw.ExpanderRow(
                title=f'{n} deprecated Single Server instance{"s" if n != 1 else ""} detected',
                subtitle='Single Server is retired — migrate to Flexible Server',
            )
            expander.set_icon_name('dialog-warning-symbolic')
            warning_label = Gtk.Label(
                label=(
                    f'The following Single Server instance{"s" if n != 1 else ""} '
                    f'{"were" if n != 1 else "was"} found: {names_str}.\n\n'
                    'Azure Database for PostgreSQL Single Server is retired. '
                    'Please migrate to Flexible Server.\n\n'
                    'Only Flexible Server instances are imported by Tusk.'
                )
            )
            warning_label.set_wrap(True)
            warning_label.set_xalign(0)
            warning_label.add_css_class('dim-label')
            warning_label.set_selectable(True)
            warning_label.set_margin_start(12)
            warning_label.set_margin_end(12)
            warning_label.set_margin_top(8)
            warning_label.set_margin_bottom(8)
            warning_row = Gtk.ListBoxRow()
            warning_row.set_selectable(False)
            warning_row.set_activatable(False)
            warning_row.set_child(warning_label)
            expander.add_row(warning_row)
            self._results_list.append(expander)

        if errors:
            n = len(errors)
            expander = Adw.ExpanderRow(
                title=f'{n} discovery warning{"s" if n != 1 else ""}',
                subtitle='Some resources could not be queried — click to expand',
            )
            expander.set_icon_name('dialog-warning-symbolic')
            error_label = Gtk.Label(label='\n\n'.join(errors))
            error_label.set_wrap(True)
            error_label.set_xalign(0)
            error_label.add_css_class('dim-label')
            error_label.set_selectable(True)
            error_label.set_margin_start(12)
            error_label.set_margin_end(12)
            error_label.set_margin_top(8)
            error_label.set_margin_bottom(8)
            error_row = Gtk.ListBoxRow()
            error_row.set_selectable(False)
            error_row.set_activatable(False)
            error_row.set_child(error_label)
            expander.add_row(error_row)
            self._results_list.append(expander)

        self._stack.set_visible_child_name('results')

    def _show_error(self, title, description, show_back=False):
        self._error_status.set_title(title)
        self._error_status.set_description(description)
        self._error_back_btn.set_visible(show_back)
        self._stack.set_visible_child_name('error')

    # ── Import ─────────────────────────────────────────────────────────────────

    def _on_import(self, _btn):
        selected = [
            conn for (check, conn) in self._checks.values()
            if check.get_active()
        ]
        if not selected:
            return
        # Strip internal _azure_* keys before emitting
        clean = [{k: v for k, v in c.items() if not k.startswith('_azure_')} for c in selected]
        self.emit('import-confirmed', clean)
        self.close()
