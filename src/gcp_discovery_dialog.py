import threading

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Gtk, Adw, GObject, GLib

import gcp_discovery


class GcpDiscoveryDialog(Adw.Dialog):
    """Discover and import GCP Cloud SQL / AlloyDB PostgreSQL instances.

    Emits 'import-confirmed' with a list of connection dicts when the user
    clicks Import Selected.
    """

    __gsignals__ = {
        'import-confirmed': (GObject.SignalFlags.RUN_FIRST, None, (GObject.TYPE_PYOBJECT,)),
    }

    def __init__(self, existing_instance_ids=None):
        super().__init__(title='Import from GCP', content_width=540, content_height=580)
        self._existing_ids = set(existing_instance_ids or [])
        self._conns = []   # discovered connection dicts with internal _gcp_* keys
        self._checks = {}  # idx → (Gtk.CheckButton, conn_dict)
        self._project = None
        self._build_ui()

    # ── UI skeleton ────────────────────────────────────────────────────────────

    def _build_ui(self):
        self._header = Adw.HeaderBar()

        self._stack = Gtk.Stack()
        self._stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)

        # Page: checking gcloud
        self._loading_page = self._build_loading_page('Checking gcloud…')
        self._stack.add_named(self._loading_page, 'loading')

        # Page: project entry (shown if no active project)
        self._project_page = self._build_project_page()
        self._stack.add_named(self._project_page, 'project')

        # Page: discovery results
        self._results_page = self._build_results_page()
        self._stack.add_named(self._results_page, 'results')

        # Page: error
        self._error_page = self._build_error_page()
        self._stack.add_named(self._error_page, 'error')

        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(self._header)
        toolbar_view.set_content(self._stack)
        self.set_child(toolbar_view)

        # Start checking gcloud availability
        threading.Thread(target=self._check_gcloud, daemon=True).start()

    def _build_loading_page(self, label_text):
        spinner = Gtk.Spinner()
        spinner.set_size_request(32, 32)
        spinner.start()
        label = Gtk.Label(label=label_text)
        label.add_css_class('dim-label')
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_halign(Gtk.Align.CENTER)
        box.set_valign(Gtk.Align.CENTER)
        box.set_vexpand(True)
        box.append(spinner)
        box.append(label)
        return box

    def _build_project_page(self):
        group = Adw.PreferencesGroup(
            title='GCP Project',
            description='No active project found. Enter the GCP project ID to discover databases in.',
        )
        self._project_entry = Adw.EntryRow(title='Project ID')
        group.add(self._project_entry)

        discover_btn = Gtk.Button(label='Discover Databases')
        discover_btn.add_css_class('suggested-action')
        discover_btn.add_css_class('pill')
        discover_btn.set_halign(Gtk.Align.CENTER)
        discover_btn.connect('clicked', self._on_project_confirm)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        box.set_margin_top(20)
        box.set_margin_bottom(20)
        box.set_margin_start(20)
        box.set_margin_end(20)
        box.set_valign(Gtk.Align.CENTER)
        box.set_vexpand(True)
        box.append(group)
        box.append(discover_btn)
        return box

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
        self._error_status = Adw.StatusPage(
            icon_name='dialog-error-symbolic',
            title='Discovery Failed',
        )
        return self._error_status

    # ── Background checks ──────────────────────────────────────────────────────

    def _check_gcloud(self):
        if not gcp_discovery.gcloud_available():
            GLib.idle_add(self._show_error,
                'gcloud not found',
                'Install the Google Cloud CLI and run `gcloud auth login` before using this feature.\n\n'
                'Download: https://cloud.google.com/sdk/docs/install')
            return

        account = gcp_discovery.get_active_account()
        if not account:
            GLib.idle_add(self._show_error,
                'Not authenticated',
                'No active gcloud credentials found.\n\nRun `gcloud auth login` in a terminal and try again.')
            return

        project = gcp_discovery.get_active_project()
        GLib.idle_add(self._on_gcloud_ready, project)

    def _on_gcloud_ready(self, project):
        if project:
            self._project = project
            self._start_discovery(project)
        else:
            self._stack.set_visible_child_name('project')

    def _on_project_confirm(self, _btn):
        project = self._project_entry.get_text().strip()
        if not project:
            self._project_entry.add_css_class('error')
            return
        self._project_entry.remove_css_class('error')
        self._project = project

        # Replace project page with a loading spinner
        loading = self._build_loading_page(f'Discovering databases in {project}…')
        self._stack.add_named(loading, 'loading2')
        self._stack.set_visible_child_name('loading2')
        self._start_discovery(project)

    def _start_discovery(self, project):
        loading = self._build_loading_page(f'Discovering databases in {project}…')
        existing = self._stack.get_child_by_name('loading_discovery')
        if existing:
            self._stack.remove(existing)
        self._stack.add_named(loading, 'loading_discovery')
        self._stack.set_visible_child_name('loading_discovery')
        threading.Thread(target=self._run_discovery, args=(project,), daemon=True).start()

    def _run_discovery(self, project):
        conns = []
        errors = []

        # Cloud SQL
        try:
            instances = gcp_discovery.discover_cloud_sql(project)
            for inst in instances:
                conn = gcp_discovery.build_cloud_sql_conn(inst, project, fetch_cert=True)
                conns.append(conn)
        except RuntimeError as e:
            errors.append(f'Cloud SQL: {e}')

        # AlloyDB
        try:
            pairs = gcp_discovery.discover_alloydb(project)
            for cluster, inst in pairs:
                conn = gcp_discovery.build_alloydb_conn(cluster, inst, project, fetch_cert=True)
                conns.append(conn)
        except RuntimeError as e:
            errors.append(f'AlloyDB: {e}')

        GLib.idle_add(self._show_results, conns, errors)

    # ── Results rendering ──────────────────────────────────────────────────────

    def _show_results(self, conns, errors):
        self._conns = conns
        self._checks = {}

        # Clear existing rows
        while True:
            row = self._results_list.get_first_child()
            if row is None:
                break
            self._results_list.remove(row)

        if not conns:
            msg = 'No PostgreSQL instances found in this project.'
            if errors:
                msg += '\n\nErrors:\n' + '\n'.join(errors)
            self._show_error('No instances found', msg)
            return

        # Group by service then region
        groups = {}  # (service, region) → [conn]
        for conn in conns:
            key = (conn.get('_gcp_service', ''), conn.get('_gcp_region', ''))
            groups.setdefault(key, []).append(conn)

        idx = 0
        for (service, region), group_conns in sorted(groups.items()):
            # Section header row
            header_row = Adw.ActionRow(
                title=f'{service} — {region}' if region else service,
            )
            header_row.set_activatable(False)
            header_row.add_css_class('dim-label')
            self._results_list.append(header_row)

            for conn in group_conns:
                already = conn.get('cloud_instance_id', '') in self._existing_ids
                row = Adw.ActionRow(title=conn['name'])
                subtitle_parts = [conn.get('_gcp_version', '')]
                if conn.get('cloud_proxy_enabled'):
                    subtitle_parts.append('Auth Proxy')
                if conn.get('cloud_auth_mode') == 'iam':
                    subtitle_parts.append('IAM auth')
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
        if errors:
            summary += f' (Errors: {"; ".join(errors)})'
        self._summary_label.set_text(summary)

        self._stack.set_visible_child_name('results')

    def _show_error(self, title, description):
        self._error_status.set_title(title)
        self._error_status.set_description(description)
        self._stack.set_visible_child_name('error')

    # ── Import ─────────────────────────────────────────────────────────────────

    def _on_import(self, _btn):
        selected = [
            conn for (check, conn) in self._checks.values()
            if check.get_active()
        ]
        if not selected:
            return
        # Strip internal _gcp_* keys before emitting
        clean = [{k: v for k, v in c.items() if not k.startswith('_gcp_')} for c in selected]
        self.emit('import-confirmed', clean)
        self.close()
