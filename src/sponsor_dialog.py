import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Gtk, Adw, Gdk

_SPONSOR_URL = 'https://buy.stripe.com/14A28saQ95kI9q93qNes003'


class SponsorDialog(Adw.Dialog):
    def __init__(self, win):
        super().__init__(title='Sponsor Tusk', content_width=380)
        self._win = win

        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(Adw.HeaderBar())

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        box.set_margin_top(32)
        box.set_margin_bottom(32)
        box.set_margin_start(32)
        box.set_margin_end(32)
        box.set_valign(Gtk.Align.CENTER)

        label = Gtk.Label(
            label='Tusk is free and open source.\nIf it\'s useful to you, consider sponsoring its development.',
            wrap=True,
            justify=Gtk.Justification.CENTER,
        )
        label.add_css_class('dim-label')
        box.append(label)

        btn = Gtk.Button(label='Sponsor Tusk')
        btn.add_css_class('suggested-action')
        btn.add_css_class('pill')
        btn.set_halign(Gtk.Align.CENTER)
        btn.connect('clicked', self._on_sponsor_clicked)
        box.append(btn)

        toolbar_view.set_content(box)
        self.set_child(toolbar_view)

    def _on_sponsor_clicked(self, _btn):
        Gtk.show_uri(self._win, _SPONSOR_URL, Gdk.CURRENT_TIME)
