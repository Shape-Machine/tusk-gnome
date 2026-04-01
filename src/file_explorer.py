import os

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Gtk, Adw, GObject, Gio, Gdk

import prefs

COL_ICON = 0
COL_NAME = 1
COL_PATH = 2
COL_IS_DIR = 3
COL_SENSITIVE = 4


class FileExplorer(Gtk.Box):
    __gsignals__ = {
        'file-activated': (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        'file-deleted':   (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        'file-renamed':   (GObject.SignalFlags.RUN_FIRST, None, (str, str)),
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
        self._store = Gtk.ListStore(str, str, str, GObject.TYPE_BOOLEAN, GObject.TYPE_BOOLEAN)

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
        col.add_attribute(icon_r, 'sensitive', COL_SENSITIVE)
        col.add_attribute(text_r, 'text', COL_NAME)
        col.add_attribute(text_r, 'sensitive', COL_SENSITIVE)
        self._tree.append_column(col)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        scroll.set_child(self._tree)

        self._error_label = Gtk.Label()
        self._error_label.add_css_class('error')
        self._error_label.set_halign(Gtk.Align.CENTER)

        home_recovery_btn = Gtk.Button(label='Go to Home Folder')
        home_recovery_btn.add_css_class('pill')
        home_recovery_btn.set_halign(Gtk.Align.CENTER)
        home_recovery_btn.connect('clicked', lambda _: self._navigate_to(os.path.expanduser('~')))

        error_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        error_box.set_valign(Gtk.Align.CENTER)
        error_box.set_vexpand(True)
        error_box.append(self._error_label)
        error_box.append(home_recovery_btn)

        self._list_stack = Gtk.Stack()
        self._list_stack.set_vexpand(True)
        self._list_stack.add_named(scroll, 'list')
        self._list_stack.add_named(error_box, 'error')
        self.append(self._list_stack)

        # ── Context menu ──────────────────────────────────────────────────────
        self._ctx_path = None
        self._ctx_is_dir = False

        menu = Gio.Menu()
        menu.append('Rename', 'ctx.rename')
        menu.append('Delete', 'ctx.delete')

        ag = Gio.SimpleActionGroup()
        rename_action = Gio.SimpleAction.new('rename', None)
        rename_action.connect('activate', lambda *_: self._prompt_rename())
        ag.add_action(rename_action)
        delete_action = Gio.SimpleAction.new('delete', None)
        delete_action.connect('activate', lambda *_: self._confirm_delete())
        ag.add_action(delete_action)
        self._tree.insert_action_group('ctx', ag)

        self._context_popover = Gtk.PopoverMenu(menu_model=menu)
        self._context_popover.set_has_arrow(False)
        self._context_popover.set_parent(self._tree)

        key_ctrl = Gtk.EventControllerKey()
        key_ctrl.connect('key-pressed', self._on_key_pressed)
        self._tree.add_controller(key_ctrl)

        right_click = Gtk.GestureClick(button=3)
        right_click.connect('pressed', self._on_right_click)
        self._tree.add_controller(right_click)

    def _can_select(self, _sel, model, path, _current):
        it = model.get_iter(path)
        return model.get_value(it, COL_SENSITIVE)

    def _refresh(self):
        self._store.clear()
        self._list_stack.set_visible_child_name('list')
        self._path_label.set_label(self._current_dir)
        self._up_btn.set_sensitive(os.path.dirname(self._current_dir) != self._current_dir)

        try:
            parent = os.path.dirname(self._current_dir)
            if parent != self._current_dir:
                self._store.append(['go-up-symbolic', '..', parent, True, True])

            entries = sorted(
                os.scandir(self._current_dir),
                key=lambda e: (not e.is_dir(), e.name.lower()),
            )
            for entry in entries:
                if entry.name.startswith('.'):
                    continue
                if entry.is_dir():
                    self._store.append(['folder-symbolic', entry.name, entry.path, True, True])
                elif entry.name.endswith('.sql'):
                    self._store.append(['x-office-document-symbolic', entry.name, entry.path, False, True])
                else:
                    self._store.append(['text-x-generic-symbolic', entry.name, entry.path, False, False])
        except OSError as e:
            self._error_label.set_label('Permission denied' if isinstance(e, PermissionError) else str(e))
            self._list_stack.set_visible_child_name('error')

    def _navigate_to(self, path):
        self._current_dir = path
        prefs.put('last_folder', path)
        self._refresh()

    def _on_go_up(self, _btn):
        parent = os.path.dirname(self._current_dir)
        if parent != self._current_dir:
            self._navigate_to(parent)

    def _on_key_pressed(self, _ctrl, keyval, _code, _state):
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            _model, it = self._tree.get_selection().get_selected()
            if it:
                path = self._store.get_path(it)
                self._on_row_activated(self._tree, path, None)
                return True
        if keyval == Gdk.KEY_BackSpace:
            self._on_go_up(None)
            return True
        return False

    def _on_row_activated(self, _tree, path, _col):
        it = self._store.get_iter(path)
        if not self._store.get_value(it, COL_SENSITIVE):
            return
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
        if '/' in name:
            self._show_create_error('Invalid Name', "Name cannot contain '/'.")
            return
        if name.startswith('.'):
            self._show_create_error('Invalid Name', "Name cannot start with '.'.")
            return
        if kind == 'folder':
            path = os.path.join(self._current_dir, name)
            try:
                os.makedirs(path, exist_ok=True)
                self._refresh()
                self._select_path(path)
            except OSError as e:
                self._show_create_error('Could Not Create Folder', str(e))
        else:
            if not name.endswith('.sql'):
                name += '.sql'
            path = os.path.join(self._current_dir, name)
            try:
                open(path, 'a').close()
                self._refresh()
                self._select_path(path)
                self.emit('file-activated', path)
            except OSError as e:
                self._show_create_error('Could Not Create File', str(e))

    def _select_path(self, path):
        it = self._store.get_iter_first()
        while it:
            if self._store.get_value(it, COL_PATH) == path:
                tree_path = self._store.get_path(it)
                self._tree.get_selection().select_path(tree_path)
                self._tree.scroll_to_cell(tree_path, None, False, 0, 0)
                return
            it = self._store.iter_next(it)

    def _on_right_click(self, _gesture, _n, x, y):
        result = self._tree.get_path_at_pos(int(x), int(y))
        if not result:
            return
        it = self._store.get_iter(result[0])
        if not self._store.get_value(it, COL_SENSITIVE):
            return
        if self._store.get_value(it, COL_NAME) == '..':
            return
        tree_path, _col, _cx, _cy = result
        self._tree.get_selection().select_path(tree_path)
        it = self._store.get_iter(tree_path)
        self._ctx_path = self._store.get_value(it, COL_PATH)
        self._ctx_is_dir = self._store.get_value(it, COL_IS_DIR)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        self._context_popover.set_pointing_to(rect)
        self._context_popover.popup()

    def _prompt_rename(self):
        # Snapshot at dialog-open time so a subsequent right-click can't overwrite
        ctx_path = self._ctx_path
        ctx_is_dir = self._ctx_is_dir
        if not ctx_path:
            return
        old_name = os.path.basename(ctx_path)
        kind = 'folder' if ctx_is_dir else 'file'
        stem = old_name[:-4] if (not ctx_is_dir and old_name.endswith('.sql')) else old_name

        entry = Gtk.Entry()
        entry.set_text(stem)
        entry.select_region(0, -1)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.set_margin_top(8)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.set_margin_bottom(4)
        box.append(entry)

        dialog = Adw.MessageDialog(
            transient_for=self.get_root(),
            heading='Rename Folder' if kind == 'folder' else 'Rename File',
        )
        dialog.set_extra_child(box)
        dialog.add_response('cancel', 'Cancel')
        dialog.add_response('rename', 'Rename')
        dialog.set_response_appearance('rename', Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response('rename')
        dialog.connect('response', self._on_rename_response, entry, kind, ctx_path)
        entry.connect('activate', lambda _: dialog.response('rename'))
        dialog.present()
        entry.grab_focus()

    def _on_rename_response(self, dialog, response, entry, kind, ctx_path):
        dialog.close()
        if response != 'rename':
            return
        name = entry.get_text().strip()
        if not name:
            return
        if '/' in name:
            self._show_create_error('Invalid Name', "Name cannot contain '/'.")
            return
        if name.startswith('.'):
            self._show_create_error('Invalid Name', "Name cannot start with '.'.")
            return
        if kind == 'file' and not name.endswith('.sql'):
            name += '.sql'
        new_path = os.path.join(os.path.dirname(ctx_path), name)
        try:
            os.rename(ctx_path, new_path)
            if kind == 'file':
                self.emit('file-renamed', ctx_path, new_path)
            self._refresh()
            self._select_path(new_path)
        except OSError as e:
            self._show_create_error('Could Not Rename', str(e))

    def _confirm_delete(self):
        # Snapshot at dialog-open time so a subsequent right-click can't overwrite
        ctx_path = self._ctx_path
        ctx_is_dir = self._ctx_is_dir
        if not ctx_path:
            return
        name = os.path.basename(ctx_path)
        if ctx_is_dir:
            try:
                if os.listdir(ctx_path):
                    self._show_create_error(
                        'Folder Not Empty',
                        f'"{name}" cannot be deleted because it is not empty.',
                    )
                    return
            except OSError as e:
                self._show_create_error('Could Not Delete', str(e))
                return

        dialog = Adw.MessageDialog(
            transient_for=self.get_root(),
            heading='Delete Folder?' if ctx_is_dir else 'Delete File?',
            body=f'"{name}" will be permanently deleted.',
        )
        dialog.add_response('cancel', 'Cancel')
        dialog.add_response('delete', 'Delete')
        dialog.set_response_appearance('delete', Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response('cancel')
        dialog.set_close_response('cancel')
        dialog.connect('response', self._on_delete_response, ctx_path, ctx_is_dir)
        dialog.present()

    def _on_delete_response(self, dialog, response, ctx_path, ctx_is_dir):
        dialog.close()
        if response != 'delete':
            return
        try:
            if ctx_is_dir:
                os.rmdir(ctx_path)
            else:
                os.unlink(ctx_path)
                self.emit('file-deleted', ctx_path)
            self._refresh()
        except OSError as e:
            self._show_create_error('Could Not Delete', str(e))

    def _show_create_error(self, heading, body):
        dialog = Adw.MessageDialog(
            transient_for=self.get_root(),
            heading=heading,
            body=body,
        )
        dialog.add_response('ok', 'OK')
        dialog.set_default_response('ok')
        dialog.present()
