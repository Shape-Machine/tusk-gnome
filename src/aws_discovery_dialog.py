"""AWS database discovery dialog — RDS PostgreSQL and Aurora via aws CLI."""

import threading

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Gtk, Adw, GObject, GLib

import aws_discovery


class AwsDiscoveryDialog(Adw.Dialog):
    """Discover and import AWS RDS / Aurora PostgreSQL instances.

    Emits 'import-confirmed' with a list of connection dicts when the user
    clicks Import Selected.
    """

    __gsignals__ = {
        'import-confirmed': (GObject.SignalFlags.RUN_FIRST, None, (GObject.TYPE_PYOBJECT,)),
    }

    def __init__(self, existing_instance_ids=None):
        super().__init__(title='Import from AWS', content_width=540)
        self._existing_ids = set(existing_instance_ids or [])
        self._conns = []
        self._checks = {}       # idx → (Gtk.CheckButton, conn_dict)
        self._region_rows = []  # list of (check_button, region_str)
        self._build_ui()

    # ── UI skeleton ────────────────────────────────────────────────────────────

    def _build_ui(self):
        self._header = Adw.HeaderBar()

        self._stack = Gtk.Stack()
        self._stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)

        self._loading_label_widget = Gtk.Label(label='Checking aws CLI…')
        self._loading_label_widget.add_css_class('dim-label')
        self._loading_page = self._build_loading_page(self._loading_label_widget)
        self._stack.add_named(self._loading_page, 'loading')

        self._region_page = self._build_region_page()
        self._stack.add_named(self._region_page, 'region')

        self._results_page = self._build_results_page()
        self._stack.add_named(self._results_page, 'results')

        self._error_page = self._build_error_page()
        self._stack.add_named(self._error_page, 'error')

        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(self._header)
        toolbar_view.set_content(self._stack)
        self.set_child(toolbar_view)

        threading.Thread(target=self._check_aws, daemon=True).start()

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

    def _build_region_page(self):
        self._region_list_box = Gtk.ListBox()
        self._region_list_box.add_css_class('boxed-list')
        self._region_list_box.set_selection_mode(Gtk.SelectionMode.NONE)
        self._region_list_box.set_filter_func(self._region_filter_func)

        self._region_search = Gtk.SearchEntry(placeholder_text='Filter regions…')
        self._region_search.connect('search-changed',
                                    lambda _: self._region_list_box.invalidate_filter())

        self._region_list_scroll = Gtk.ScrolledWindow()
        self._region_list_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._region_list_scroll.set_min_content_height(200)
        self._region_list_scroll.set_max_content_height(320)
        self._region_list_scroll.set_propagate_natural_height(True)
        self._region_list_scroll.set_child(self._region_list_box)

        self._region_fetch_error = Gtk.Label(label='Could not fetch region list')
        self._region_fetch_error.add_css_class('dim-label')
        self._region_fetch_error.set_halign(Gtk.Align.START)
        self._region_fetch_error.set_visible(False)

        list_group = Adw.PreferencesGroup(
            title='AWS Regions',
            description='Select one or more regions to discover databases in.',
        )

        manual_group = Adw.PreferencesGroup()
        self._manual_entry = Adw.EntryRow(title='Add region manually (e.g. us-east-1)')
        self._manual_entry.connect('entry-activated', self._on_manual_add)
        add_btn = Gtk.Button()
        add_btn.set_icon_name('list-add-symbolic')
        add_btn.set_tooltip_text('Add region')
        add_btn.add_css_class('flat')
        add_btn.set_valign(Gtk.Align.CENTER)
        add_btn.connect('clicked', self._on_manual_add)
        self._manual_entry.add_suffix(add_btn)
        manual_group.add(self._manual_entry)

        discover_btn = Gtk.Button(label='Discover Databases')
        discover_btn.add_css_class('suggested-action')
        discover_btn.add_css_class('pill')
        discover_btn.set_halign(Gtk.Align.CENTER)
        discover_btn.connect('clicked', self._on_region_confirm)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        box.set_margin_top(20)
        box.set_margin_bottom(20)
        box.set_margin_start(20)
        box.set_margin_end(20)
        box.set_vexpand(True)
        box.append(list_group)
        box.append(self._region_search)
        box.append(self._region_list_scroll)
        box.append(self._region_fetch_error)
        box.append(manual_group)
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
        self._error_back_btn = Gtk.Button(label='Choose Different Regions')
        self._error_back_btn.add_css_class('pill')
        self._error_back_btn.set_halign(Gtk.Align.CENTER)
        self._error_back_btn.set_visible(False)
        self._error_back_btn.connect('clicked',
                                     lambda _: self._stack.set_visible_child_name('region'))

        self._error_status = Adw.StatusPage(
            icon_name='dialog-error-symbolic',
            title='Discovery Failed',
            child=self._error_back_btn,
        )
        return self._error_status

    # ── Background checks ──────────────────────────────────────────────────────

    def _check_aws(self):
        if not aws_discovery.awscli_available():
            GLib.idle_add(self._show_error,
                'aws CLI not found',
                'Install the AWS CLI and configure credentials before using this feature.\n\n'
                'Download: https://docs.aws.amazon.com/cli/latest/userguide/install-cliv2.html')
            return

        GLib.idle_add(self._loading_label_widget.set_text, 'Checking credentials…')
        try:
            aws_discovery.get_caller_identity()
        except RuntimeError as e:
            GLib.idle_add(self._show_error, 'Not authenticated', str(e))
            return

        GLib.idle_add(self._loading_label_widget.set_text, 'Fetching region list…')
        default_region = aws_discovery.get_default_region()
        try:
            regions = aws_discovery.list_regions()
            fetch_error = None
        except Exception as e:
            regions = []
            fetch_error = str(e)
        GLib.idle_add(self._on_aws_checked, default_region, regions, fetch_error)

    def _on_aws_checked(self, default_region, regions, fetch_error=None):
        self._populate_region_list(regions, default_region, fetch_error)
        self._stack.set_visible_child_name('region')

    def _populate_region_list(self, regions, default_region, fetch_error=None):
        self._region_rows = []

        while True:
            row = self._region_list_box.get_first_child()
            if row is None:
                break
            self._region_list_box.remove(row)

        if not regions:
            self._region_search.set_visible(False)
            self._region_list_scroll.set_visible(False)
            error_msg = f'Could not fetch region list: {fetch_error}' if fetch_error else 'Could not fetch region list'
            self._region_fetch_error.set_text(error_msg)
            self._region_fetch_error.set_visible(True)
            # Still show a pre-checked default region if we have one
            if default_region:
                self._region_search.set_visible(True)
                self._region_list_scroll.set_visible(True)
                self._region_fetch_error.set_visible(False)
                self._add_region_row(default_region, checked=True)
            return

        self._region_search.set_visible(True)
        self._region_list_scroll.set_visible(True)
        self._region_fetch_error.set_visible(False)

        for region in regions:
            self._add_region_row(region, checked=(region == default_region))

    def _add_region_row(self, region, checked=False):
        row = Adw.ActionRow(title=region)
        check = Gtk.CheckButton()
        check.set_active(checked)
        check.set_valign(Gtk.Align.CENTER)
        check.connect('toggled', self._on_region_check_toggled)
        row.add_suffix(check)
        row.set_activatable_widget(check)
        self._region_list_box.append(row)
        self._region_rows.append((check, region))

    def _region_filter_func(self, row):
        text = self._region_search.get_text().lower()
        if not text:
            return True
        return text in (row.get_title() or '').lower()

    def _on_region_check_toggled(self, check):
        if check.get_active():
            self._region_list_box.remove_css_class('error')

    def _on_manual_add(self, _widget):
        self._manual_entry.remove_css_class('error')
        region = self._manual_entry.get_text().strip()
        if not region:
            return
        existing = {r for _, r in self._region_rows}
        if region in existing:
            self._manual_entry.add_css_class('error')
            return
        self._region_search.set_visible(True)
        self._region_list_scroll.set_visible(True)
        self._region_fetch_error.set_visible(False)
        self._add_region_row(region, checked=True)
        self._manual_entry.set_text('')

    def _on_region_confirm(self, _btn):
        selected = [r for check, r in self._region_rows if check.get_active()]
        if not selected:
            self._region_list_box.add_css_class('error')
            return
        self._region_list_box.remove_css_class('error')
        self._start_discovery(selected)

    def _start_discovery(self, regions):
        label = regions[0] if len(regions) == 1 else f'{len(regions)} regions'
        self._loading_label_widget.set_text(f'Discovering databases in {label}…')
        self._stack.set_visible_child_name('loading')
        threading.Thread(target=self._run_discovery, args=(regions,), daemon=True).start()

    def _run_discovery(self, regions):
        conns = []
        errors = []

        cert_path = aws_discovery.get_rds_ca_bundle()
        if cert_path is None:
            errors.append(
                'RDS CA bundle download failed — connections will use ssl_mode=require '
                'without a pinned certificate.'
            )

        for region in regions:
            try:
                instances = aws_discovery.discover_rds(region)
                for inst in instances:
                    conns.append(aws_discovery.build_rds_conn(inst, region, cert_path=cert_path))
            except RuntimeError as e:
                errors.append(f'{region} / RDS: {e}')

            try:
                clusters = aws_discovery.discover_aurora(region)
                for cluster in clusters:
                    conns.append(
                        aws_discovery.build_aurora_conn(cluster, region, cert_path=cert_path)
                    )
            except RuntimeError as e:
                errors.append(f'{region} / Aurora: {e}')

        GLib.idle_add(self._show_results, conns, errors)

    # ── Results rendering ──────────────────────────────────────────────────────

    def _show_results(self, conns, errors):
        self._conns = conns
        self._checks = {}

        while True:
            row = self._results_list.get_first_child()
            if row is None:
                break
            self._results_list.remove(row)

        if not conns:
            msg = 'No PostgreSQL instances found in the selected region(s).'
            if errors:
                msg += '\n\nErrors:\n' + '\n'.join(errors)
            self._show_error('No instances found', msg, show_back=True)
            return

        # Group by service, then region
        groups = {}  # (service, region) → [conn]
        for conn in conns:
            key = (conn.get('_aws_service', ''), conn.get('_aws_region', ''))
            groups.setdefault(key, []).append(conn)

        idx = 0
        for (service, region), group_conns in sorted(groups.items()):
            title = f'{service} — {region}' if region else service
            header_row = Adw.ActionRow(title=title)
            header_row.set_activatable(False)
            header_row.add_css_class('dim-label')
            self._results_list.append(header_row)

            for conn in group_conns:
                already = conn.get('cloud_instance_id', '') in self._existing_ids
                row = Adw.ActionRow(title=conn['name'])
                host = conn.get('host', '')
                port = conn.get('port', 5432)
                endpoint_str = f'{host}:{port}' if host else ''
                subtitle_parts = [conn.get('_aws_version', ''), endpoint_str]
                if conn.get('cloud_auth_mode') == 'iam':
                    subtitle_parts.append('IAM auth')
                if conn.get('secondary_endpoint'):
                    subtitle_parts.append('reader endpoint included')
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
        summary = f'{total} instance{"s" if total != 1 else ""} found.'
        if already_count:
            summary += f' {already_count} already imported.'
        self._summary_label.set_text(summary)

        if errors:
            n = len(errors)
            expander = Adw.ExpanderRow(
                title=f'{n} discovery warning{"s" if n != 1 else ""}',
                subtitle='Some regions could not be queried — click to expand',
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
        # Strip internal _aws_* keys before emitting
        clean = [{k: v for k, v in c.items() if not k.startswith('_aws_')} for c in selected]
        self.emit('import-confirmed', clean)
        self.close()
