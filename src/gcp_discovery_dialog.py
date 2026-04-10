import shutil
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
        super().__init__(title='Import from GCP', content_width=540)
        self._existing_ids = set(existing_instance_ids or [])
        self._conns = []   # discovered connection dicts with internal _gcp_* keys
        self._checks = {}  # idx → (Gtk.CheckButton, conn_dict)
        self._project_rows = []  # list of (check_button, project_id)
        self._missing_proxy_binaries = set()
        self._build_ui()

    # ── UI skeleton ────────────────────────────────────────────────────────────

    def _build_ui(self):
        self._header = Adw.HeaderBar()

        self._stack = Gtk.Stack()
        self._stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)

        # Page: checking gcloud / discovering
        self._loading_label_widget = Gtk.Label(label='Checking gcloud…')
        self._loading_label_widget.add_css_class('dim-label')
        self._loading_page = self._build_loading_page_with_label(self._loading_label_widget)
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

    def _build_loading_page_with_label(self, label_widget):
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

    def _build_loading_page(self, label_text):
        label = Gtk.Label(label=label_text)
        label.add_css_class('dim-label')
        return self._build_loading_page_with_label(label)

    def _build_project_page(self):
        # ── Project list (checkbox rows) ──────────────────────────────────────
        self._project_list_box = Gtk.ListBox()
        self._project_list_box.add_css_class('boxed-list')
        self._project_list_box.set_selection_mode(Gtk.SelectionMode.NONE)
        self._project_list_box.set_filter_func(self._project_filter_func)

        self._project_search = Gtk.SearchEntry(placeholder_text='Filter projects…')
        self._project_search.connect('search-changed',
                                     lambda _: self._project_list_box.invalidate_filter())

        self._project_list_scroll = Gtk.ScrolledWindow()
        self._project_list_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._project_list_scroll.set_min_content_height(240)
        self._project_list_scroll.set_max_content_height(360)
        self._project_list_scroll.set_propagate_natural_height(True)
        self._project_list_scroll.set_child(self._project_list_box)

        self._project_fetch_error = Gtk.Label(label='Could not fetch project list')
        self._project_fetch_error.add_css_class('dim-label')
        self._project_fetch_error.set_halign(Gtk.Align.START)
        self._project_fetch_error.set_visible(False)

        list_group = Adw.PreferencesGroup(
            title='GCP Projects',
            description='Select one or more projects to discover databases in.',
        )

        # ── Manual add row ────────────────────────────────────────────────────
        manual_group = Adw.PreferencesGroup()
        self._manual_entry = Adw.EntryRow(title='Add project ID manually')
        self._manual_entry.connect('entry-activated', self._on_manual_add)

        add_btn = Gtk.Button()
        add_btn.set_icon_name('list-add-symbolic')
        add_btn.set_tooltip_text('Add project')
        add_btn.add_css_class('flat')
        add_btn.set_valign(Gtk.Align.CENTER)
        add_btn.connect('clicked', self._on_manual_add)
        self._manual_entry.add_suffix(add_btn)
        manual_group.add(self._manual_entry)

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
        box.set_vexpand(True)
        box.append(list_group)
        box.append(self._project_search)
        box.append(self._project_list_scroll)
        box.append(self._project_fetch_error)
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

        self._proxy_banner = Adw.Banner(
            title='Cloud SQL Auth Proxy required for some instances — install it before connecting.',
            button_label='How to install',
        )
        self._proxy_banner.set_revealed(False)
        self._proxy_banner.connect('button-clicked', self._on_proxy_banner_clicked)

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
        box.append(self._proxy_banner)
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

        GLib.idle_add(self._loading_label_widget.set_text, 'Checking authentication…')
        account = gcp_discovery.get_active_account()
        if not account:
            GLib.idle_add(self._show_error,
                'Not authenticated',
                'No active gcloud credentials found.\n\nRun `gcloud auth login` in a terminal and try again.')
            return

        GLib.idle_add(self._loading_label_widget.set_text, 'Fetching project list…')
        active_project = gcp_discovery.get_active_project()
        try:
            projects = gcp_discovery.list_projects()
        except Exception:
            projects = []
        GLib.idle_add(self._on_gcloud_checked, active_project, projects)

    def _on_gcloud_checked(self, active_project, projects):
        self._populate_project_list(projects, active_project)
        self._stack.set_visible_child_name('project')

    def _populate_project_list(self, projects, active_project):
        """Fill the project ListBox with checkbox rows. Pre-check active_project if found."""
        self._project_rows = []

        # Clear any existing rows
        while True:
            row = self._project_list_box.get_first_child()
            if row is None:
                break
            self._project_list_box.remove(row)

        if not projects:
            self._project_search.set_visible(False)
            self._project_list_scroll.set_visible(False)
            self._project_fetch_error.set_visible(True)
            return

        self._project_search.set_visible(True)
        self._project_list_scroll.set_visible(True)
        self._project_fetch_error.set_visible(False)

        for p in projects:
            self._add_project_row(p['id'], p['name'],
                                  checked=(p['id'] == active_project))

    def _add_project_row(self, project_id, label=None, checked=False):
        """Append a checkbox row for project_id to the project ListBox."""
        row = Adw.ActionRow(title=label or project_id)
        if label and label != project_id:
            row.set_subtitle(project_id)
        check = Gtk.CheckButton()
        check.set_active(checked)
        check.set_valign(Gtk.Align.CENTER)
        row.add_suffix(check)
        row.set_activatable_widget(check)
        self._project_list_box.append(row)
        self._project_rows.append((check, project_id))

    def _project_filter_func(self, row):
        # row is the Adw.ActionRow itself (it's a Gtk.ListBoxRow subclass)
        text = self._project_search.get_text().lower()
        if not text:
            return True
        title = row.get_title().lower() if hasattr(row, 'get_title') else ''
        subtitle = row.get_subtitle().lower() if hasattr(row, 'get_subtitle') else ''
        return text in title or text in subtitle

    def _on_manual_add(self, _widget):
        project_id = self._manual_entry.get_text().strip()
        if not project_id:
            return
        # Avoid duplicates
        existing_ids = {pid for _, pid in self._project_rows}
        if project_id in existing_ids:
            self._manual_entry.set_text('')
            return
        self._project_search.set_visible(True)
        self._project_list_scroll.set_visible(True)
        self._add_project_row(project_id, checked=True)
        self._manual_entry.set_text('')

    def _on_project_confirm(self, _btn):
        selected = [pid for check, pid in self._project_rows if check.get_active()]
        if not selected:
            return
        self._start_discovery(selected)

    def _start_discovery(self, projects):
        label = projects[0] if len(projects) == 1 else f'{len(projects)} projects'
        self._loading_label_widget.set_text(f'Discovering databases in {label}…')
        self._stack.set_visible_child_name('loading')
        threading.Thread(target=self._run_discovery, args=(projects,), daemon=True).start()

    def _run_discovery(self, projects):
        conns = []
        errors = []

        for project in projects:
            # Cloud SQL
            try:
                instances = gcp_discovery.discover_cloud_sql(project)
                for inst in instances:
                    conn = gcp_discovery.build_cloud_sql_conn(inst, project, fetch_cert=True)
                    conns.append(conn)
            except RuntimeError as e:
                errors.append(f'{project} / Cloud SQL: {e}')

            # AlloyDB
            try:
                pairs = gcp_discovery.discover_alloydb(project)
                for cluster, inst in pairs:
                    conn = gcp_discovery.build_alloydb_conn(cluster, inst, project, fetch_cert=True)
                    conns.append(conn)
            except RuntimeError as e:
                errors.append(f'{project} / AlloyDB: {e}')

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
            msg = 'No PostgreSQL instances found in the selected project(s).'
            if errors:
                msg += '\n\nErrors:\n' + '\n'.join(errors)
            self._show_error('No instances found', msg)
            return

        # Determine whether results span multiple projects
        projects_in_results = {conn.get('_gcp_project', '') for conn in conns}
        multi_project = len(projects_in_results) > 1

        # Group by project, service, region
        groups = {}  # (project, service, region) → [conn]
        for conn in conns:
            key = (conn.get('_gcp_project', ''),
                   conn.get('_gcp_service', ''),
                   conn.get('_gcp_region', ''))
            groups.setdefault(key, []).append(conn)

        idx = 0
        for (project, service, region), group_conns in sorted(groups.items()):
            # Section header row
            if multi_project and project:
                title = f'{project} / {service} — {region}' if region else f'{project} / {service}'
            else:
                title = f'{service} — {region}' if region else service
            header_row = Adw.ActionRow(title=title)
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
                row.set_subtitle(' · '.join(part for part in subtitle_parts if part))
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

        # Show proxy banner if any instances need a proxy binary that isn't installed
        _PROXY_BINARY = {
            'gcp-cloudsql': 'cloud-sql-proxy',
            'gcp-alloydb': 'alloydb-auth-proxy',
        }
        _PROXY_NAME = {
            'cloud-sql-proxy': 'Cloud SQL Auth Proxy',
            'alloydb-auth-proxy': 'AlloyDB Auth Proxy',
        }
        self._missing_proxy_binaries = {
            _PROXY_BINARY[c['cloud_provider']]
            for c in conns
            if c.get('cloud_proxy_enabled') and c.get('cloud_provider') in _PROXY_BINARY
            and not shutil.which(_PROXY_BINARY[c['cloud_provider']])
        }
        if self._missing_proxy_binaries:
            names = ' and '.join(
                _PROXY_NAME.get(b, b) for b in sorted(self._missing_proxy_binaries)
            )
            self._proxy_banner.set_title(
                f'{names} required for some instances — install before connecting.'
            )
            self._proxy_banner.set_revealed(True)
        else:
            self._proxy_banner.set_revealed(False)

        self._stack.set_visible_child_name('results')

    def _show_error(self, title, description):
        self._error_status.set_title(title)
        self._error_status.set_description(description)
        self._stack.set_visible_child_name('error')

    def _on_proxy_banner_clicked(self, _banner):
        _INSTALL_URLS = {
            'cloud-sql-proxy': ('Cloud SQL Auth Proxy',
                                'https://cloud.google.com/sql/docs/postgres/sql-proxy#install'),
            'alloydb-auth-proxy': ('AlloyDB Auth Proxy',
                                   'https://cloud.google.com/alloydb/docs/auth-proxy/connect#install-proxy'),
        }
        missing = getattr(self, '_missing_proxy_binaries', {'cloud-sql-proxy'})
        lines = [f'{name}:\n{url}' for b in sorted(missing)
                 for name, url in [_INSTALL_URLS.get(b, (b, ''))]]
        heading = 'Install Required Proxy' if len(missing) == 1 else 'Install Required Proxies'
        dialog = Adw.AlertDialog(
            heading=heading,
            body='See the installation guides for the latest downloads:\n\n' + '\n\n'.join(lines),
        )
        dialog.add_response('ok', 'OK')
        dialog.set_default_response('ok')
        dialog.set_close_response('ok')
        dialog.present(self)

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
