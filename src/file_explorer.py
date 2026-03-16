import os

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Gtk, Adw, GObject

import prefs

COL_ICON = 0
COL_NAME = 1
COL_PATH = 2
COL_IS_DIR = 3


class FileExplorer(Gtk.Box):
    __gsignals__ = {
        'file-activated': (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        start = prefs.get('last_folder', os.path.expanduser('~'))
        self._current_dir = start if os.path.isdir(start) else os.path.expanduser('~')
        self._build_ui()
        self._refresh()

    def _build_ui(self):
        # ── Nav bar ───────────────────────────────────────────────────────────
        nav = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        nav.set_margin_start(4)
        nav.set_margin_end(4)
        nav.set_margin_top(4)
        nav.set_margin_bottom(4)

        self._up_btn = Gtk.Button(icon_name='go-up-symbolic')
        self._up_btn.add_css_class('flat')
        self._up_btn.set_tooltip_text('Go up')
        self._up_btn.connect('clicked', self._on_go_up)

        home_btn = Gtk.Button(icon_name='go-home-symbolic')
        home_btn.add_css_class('flat')
        home_btn.set_tooltip_text('Home directory')
        home_btn.connect('clicked', lambda _: self._navigate_to(os.path.expanduser('~')))

        self._path_label = Gtk.Label()
        self._path_label.set_hexpand(True)
        self._path_label.set_xalign(0)
        self._path_label.set_ellipsize(3)
        self._path_label.add_css_class('caption')
        self._path_label.add_css_class('dim-label')

        new_folder_btn = Gtk.Button(icon_name='folder-new-symbolic')
        new_folder_btn.add_css_class('flat')
        new_folder_btn.set_tooltip_text('New folder')
        new_folder_btn.connect('clicked', lambda _: self._prompt_create('folder'))

        new_file_btn = Gtk.Button(icon_name='document-new-symbolic')
        new_file_btn.add_css_class('flat')
        new_file_btn.set_tooltip_text('New SQL file')
        new_file_btn.connect('clicked', lambda _: self._prompt_create('file'))

        nav.append(self._up_btn)
        nav.append(home_btn)
        nav.append(self._path_label)
        nav.append(new_folder_btn)
        nav.append(new_file_btn)

        self.append(nav)
        self.append(Gtk.Separator())

        # ── File tree ─────────────────────────────────────────────────────────
        self._store = Gtk.ListStore(str, str, str, GObject.TYPE_BOOLEAN)

        self._tree = Gtk.TreeView(model=self._store)
        self._tree.set_headers_visible(False)
        self._tree.set_activate_on_single_click(False)
        self._tree.connect('row-activated', self._on_row_activated)
        self._tree.get_selection().set_select_function(self._can_select)

        icon_r = Gtk.CellRendererPixbuf()
        text_r = Gtk.CellRendererText()
        text_r.set_property('ellipsize', 3)

        col = Gtk.TreeViewColumn()
        col.pack_start(icon_r, False)
        col.pack_start(text_r, True)
        col.add_attribute(icon_r, 'icon-name', COL_ICON)
        col.add_attribute(text_r, 'text', COL_NAME)
        self._tree.append_column(col)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        scroll.set_child(self._tree)
        self.append(scroll)

    def _can_select(self, _sel, model, path, _current):
        it = model.get_iter(path)
        return (model.get_value(it, COL_IS_DIR) or
                model.get_value(it, COL_NAME).endswith('.sql'))

    def _refresh(self):
        self._store.clear()
        self._path_label.set_label(self._current_dir)
        self._up_btn.set_sensitive(os.path.dirname(self._current_dir) != self._current_dir)

        try:
            entries = sorted(
                os.scandir(self._current_dir),
                key=lambda e: (not e.is_dir(), e.name.lower()),
            )
            for entry in entries:
                if entry.name.startswith('.'):
                    continue
                if entry.is_dir():
                    self._store.append(['folder-symbolic', entry.name, entry.path, True])
                elif entry.name.endswith('.sql'):
                    self._store.append(['x-office-document-symbolic', entry.name, entry.path, False])
        except PermissionError:
            pass

    def _navigate_to(self, path):
        self._current_dir = path
        prefs.put('last_folder', path)
        self._refresh()

    def _on_go_up(self, _btn):
        parent = os.path.dirname(self._current_dir)
        if parent != self._current_dir:
            self._navigate_to(parent)

    def _on_row_activated(self, _tree, path, _col):
        it = self._store.get_iter(path)
        is_dir = self._store.get_value(it, COL_IS_DIR)
        fpath = self._store.get_value(it, COL_PATH)
        if is_dir:
            self._navigate_to(fpath)
        else:
            self.emit('file-activated', fpath)

    def _prompt_create(self, kind):
        title = 'New Folder' if kind == 'folder' else 'New SQL File'
        placeholder = 'folder_name' if kind == 'folder' else 'query.sql'

        entry = Gtk.Entry()
        entry.set_placeholder_text(placeholder)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.set_margin_top(8)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.set_margin_bottom(4)
        box.append(entry)

        dialog = Adw.MessageDialog(
            transient_for=self.get_root(),
            heading=title,
        )
        dialog.set_extra_child(box)
        dialog.add_response('cancel', 'Cancel')
        dialog.add_response('create', 'Create')
        dialog.set_response_appearance('create', Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response('create')
        dialog.connect('response', self._on_create_response, entry, kind)
        entry.connect('activate', lambda _: dialog.response('create'))
        dialog.present()
        entry.grab_focus()

    def _on_create_response(self, dialog, response, entry, kind):
        dialog.close()
        if response != 'create':
            return
        name = entry.get_text().strip()
        if not name:
            return
        if kind == 'folder':
            path = os.path.join(self._current_dir, name)
            try:
                os.makedirs(path, exist_ok=True)
                self._refresh()
            except OSError as e:
                self._show_create_error('Could Not Create Folder', str(e))
        else:
            if not name.endswith('.sql'):
                name += '.sql'
            path = os.path.join(self._current_dir, name)
            try:
                open(path, 'a').close()
                self._refresh()
                self.emit('file-activated', path)
            except OSError as e:
                self._show_create_error('Could Not Create File', str(e))

    def _show_create_error(self, heading, body):
        dialog = Adw.MessageDialog(
            transient_for=self.get_root(),
            heading=heading,
            body=body,
        )
        dialog.add_response('ok', 'OK')
        dialog.set_default_response('ok')
        dialog.present()
