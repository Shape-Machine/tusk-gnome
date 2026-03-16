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
            quit_action = Gio.SimpleAction.new('quit', None)
            quit_action.connect('activate', lambda *_: app.quit())
            self.add_action(quit_action)
        win.present()
