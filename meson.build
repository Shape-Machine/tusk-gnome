project('tusk',
  version: '0.1.0',
  meson_version: '>= 0.62.0',
)

gnome = import('gnome')
python = import('python')

py = python.find_installation('python3', required: true)

# Allow version override at configure time: meson setup -Dapp_version=x.y.z
# Falls back to the project version declared above.
app_version_opt = get_option('app_version')
app_version = app_version_opt != '' ? app_version_opt : meson.project_version()

dependency('gtk4', required: true)
dependency('libadwaita-1', version: '>= 1.4', required: true)

subdir('data')
subdir('src')

gnome.post_install(
  glib_compile_schemas: true,
  gtk_update_icon_cache: true,
  update_desktop_database: true,
)
