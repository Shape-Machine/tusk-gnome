pkgdatadir = get_option('prefix') / get_option('datadir') / meson.project_name()

conf = configuration_data()
conf.set('PYTHON', py.full_path())
conf.set('VERSION', app_version)
conf.set('APP_ID', 'xyz.shapemachine.tusk-gnome')
conf.set('pkgdatadir', pkgdatadir)

configure_file(
  input: 'tusk.in',
  output: 'tusk',
  configuration: conf,
  install: true,
  install_dir: get_option('bindir'),
  install_mode: 'rwxr-xr-x',
)

configure_file(
  input: 'config.py.in',
  output: 'config.py',
  configuration: conf,
  install: true,
  install_dir: pkgdatadir,
)

tusk_sources = [
  'main.py',
  'application.py',
  'window.py',
  'connection_dialog.py',
  'connections.py',
  'connections_import_dialog.py',
  'data_grid.py',
  'activity_panel.py',
  'command_palette.py',
  'db_browser.py',
  'table_panel.py',
  'table_view.py',
  'role_panel.py',
  'sql_editor.py',
  'explain_graph.py',
  'file_explorer.py',
  'function_editor.py',
  'prefs.py',
  'prefs_dialog.py',
  'sponsor_dialog.py',
  'row_edit_dialog.py',
  'tunnel.py',
  'pgpass_dialog.py',
  'col_stats_dialog.py',
  'column_dialogs.py',
  'pg_errors.py',
  'style.py',
  'stale_dialog.py',
  'tags_dialog.py',
  'gcp_discovery.py',
  'gcp_discovery_dialog.py',
  'aws_discovery.py',
  'aws_discovery_dialog.py',
]

install_data(tusk_sources, install_dir: pkgdatadir)
