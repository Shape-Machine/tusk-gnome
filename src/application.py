import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gio, GLib, Gtk, Gdk

import config
from window import TuskWindow


class TuskApplication(Adw.Application):
    def __init__(self):
        super().__init__(application_id='xyz.shapemachine.tusk-gnome')
        self.connect('activate', self._on_activate)
        self.set_resource_base_path('/xyz/shapemachine/tusk-gnome')
        self._register_accels()
        self._css_applied = False

    def _register_accels(self):
        self.set_accels_for_action('app.preferences',     ['<Control>comma'])
        self.set_accels_for_action('app.quit',            ['<Control>q'])
        self.set_accels_for_action('win.quick-open',      ['<Control>p'])
        self.set_accels_for_action('win.close-tab',       ['<Control>w'])
        self.set_accels_for_action('win.next-tab',        ['<Control>Tab'])
        self.set_accels_for_action('win.prev-tab',        ['<Control><Shift>Tab'])
        self.set_accels_for_action('win.refresh-tab',     ['<Control>r'])
        for i in range(1, 10):
            self.set_accels_for_action(f'win.goto-tab-{i}', [f'<Alt>{i}'])

    def _on_activate(self, app):
        if not self._css_applied:
            self._apply_css()
            self._css_applied = True
        win = self.props.active_window
        if not win:
            win = TuskWindow(application=self)
            self._add_app_actions(win)
        win.present()

    def _apply_css(self):
        # GtkPopoverMenu adds an internal GtkScrolledWindow that gets a
        # non-zero min-height from the theme when the popover's parent widget
        # is inside a GtkScrolledWindow, causing spurious scrollbars in
        # right-click context menus and MenuButton popovers. Reset it to 0.
        css = b'popover > contents scrolledwindow { min-height: 0; }'
        provider = Gtk.CssProvider()
        provider.load_from_data(css)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

    def _add_app_actions(self, win):
        quit_action = Gio.SimpleAction.new('quit', None)
        quit_action.connect('activate', lambda *_: self.quit())
        self.add_action(quit_action)

        about_action = Gio.SimpleAction.new('about', None)
        about_action.connect('activate', lambda *_: self._show_about(win))
        self.add_action(about_action)

        sponsor_action = Gio.SimpleAction.new('sponsor', None)
        sponsor_action.connect('activate', lambda *_: self._show_sponsor(win))
        self.add_action(sponsor_action)

        prefs_action = Gio.SimpleAction.new('preferences', None)
        prefs_action.connect('activate', lambda *_: self._show_prefs(win))
        self.add_action(prefs_action)

        focus_editor_action = Gio.SimpleAction.new('focus-editor', GLib.VariantType('s'))
        focus_editor_action.connect('activate', self._on_focus_editor)
        self.add_action(focus_editor_action)

    def _on_focus_editor(self, _action, param):
        win = self.props.active_window
        if not win:
            return
        win.present()
        win.focus_editor_tab(param.get_string())

    def _show_prefs(self, win):
        from prefs_dialog import PrefsDialog
        PrefsDialog(on_change=win._apply_fonts).present(win)

    def _show_sponsor(self, win):
        from sponsor_dialog import SponsorDialog
        SponsorDialog(win).present(win)

    def _show_about(self, win):
        dialog = Adw.AboutDialog(
            application_name='Tusk',
            application_icon=config.APP_ID,
            developer_name='Sri Rang',
            version=config.VERSION,
            website='https://shapemachine.xyz/tusk',
            issue_url='https://github.com/Shape-Machine/tusk-gnome/issues',
            support_url='https://buy.stripe.com/14A28saQ95kI9q93qNes003',
            comments='PostgreSQL client for GNOME',
            copyright='© 2026 Shape Machine',
        )
        dialog.present(win)
