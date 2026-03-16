pkgdatadir = get_option('prefix') / get_option('datadir') / meson.project_name()

conf = configuration_data()
conf.set('PYTHON', py.full_path())
conf.set('VERSION', meson.project_version())
conf.set('pkgdatadir', pkgdatadir)

configure_file(
  input: 'tusk.in',
  output: 'tusk',
  configuration: conf,
  install: true,
  install_dir: get_option('bindir'),
  install_mode: 'rwxr-xr-x',
)

tusk_sources = [
  'main.py',
  'application.py',
  'window.py',
  'connection_dialog.py',
  'connections.py',
  'db_browser.py',
  'table_panel.py',
  'sql_editor.py',
  'file_explorer.py',
  'prefs.py',
  'prefs_dialog.py',
  'tunnel.py',
]

install_data(tusk_sources, install_dir: pkgdatadir)
