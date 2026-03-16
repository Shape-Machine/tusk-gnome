import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw

from window import TuskWindow


class TuskApplication(Adw.Application):
    def __init__(self):
        super().__init__(application_id='io.tusk.Tusk')
        self.connect('activate', self._on_activate)

    def _on_activate(self, app):
        win = self.props.active_window
        if not win:
            win = TuskWindow(application=self)
        win.present()
