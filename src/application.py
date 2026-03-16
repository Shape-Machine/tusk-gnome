import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gio

from window import TuskWindow


class TuskApplication(Adw.Application):
    def __init__(self):
        super().__init__(application_id='io.tusk.Tusk')
        self.connect('activate', self._on_activate)
        self.set_resource_base_path('/io/tusk/Tusk')
        self._register_accels()

    def _register_accels(self):
        self.set_accels_for_action('app.preferences',     ['<Control>comma'])
        self.set_accels_for_action('app.quit',            ['<Control>q'])
        self.set_accels_for_action('win.close-tab',       ['<Control>w'])
        self.set_accels_for_action('win.next-tab',        ['<Control>Tab'])
        self.set_accels_for_action('win.prev-tab',        ['<Control><Shift>Tab'])
        for i in range(1, 10):
            self.set_accels_for_action(f'win.goto-tab-{i}', [f'<Alt>{i}'])

    def _on_activate(self, app):
        win = self.props.active_window
        if not win:
            win = TuskWindow(application=self)
            self._add_app_actions(win)
        win.present()

    def _add_app_actions(self, win):
        quit_action = Gio.SimpleAction.new('quit', None)
        quit_action.connect('activate', lambda *_: self.quit())
        self.add_action(quit_action)

        about_action = Gio.SimpleAction.new('about', None)
        about_action.connect('activate', lambda *_: self._show_about(win))
        self.add_action(about_action)

        prefs_action = Gio.SimpleAction.new('preferences', None)
        prefs_action.connect('activate', lambda *_: self._show_prefs(win))
        self.add_action(prefs_action)

    def _show_prefs(self, win):
        from prefs_dialog import PrefsDialog
        PrefsDialog(on_change=win._apply_fonts).present(win)

    def _show_about(self, win):
        dialog = Adw.AboutDialog(
            application_name='Tusk',
            application_icon='io.tusk.Tusk',
            developer_name='Shape Machine',
            version='0.1.0',
        )
        dialog.present(win)
