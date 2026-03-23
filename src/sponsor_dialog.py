import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Gtk, Adw, Gdk

_TIERS = {
    'One-time': [
        ('Coffee',    '€5',  'https://buy.stripe.com/14A28saQ95kI9q93qNes003'),
        ('Supporter', '€15', 'https://buy.stripe.com/4gMeVebUddRefOx7H3es004'),
        ('Sponsor',   '€49', 'https://buy.stripe.com/00w6oI2jD7sQeKt7H3es005'),
    ],
    'Monthly': [
        ('Hero Coffee',    '€5/mo',  'https://buy.stripe.com/8x29AU7DXdReeKtaTfes000'),
        ('Hero Supporter', '€15/mo', 'https://buy.stripe.com/9B6bJ2f6p5kI59T2mJes001'),
        ('Hero Sponsor',   '€49/mo', 'https://buy.stripe.com/bJe5kEgat8wUfOx3qNes002'),
    ],
}


class SponsorDialog(Adw.Dialog):
    def __init__(self, win):
        super().__init__(title='Sponsor Tusk', content_width=420)
        self._win = win

        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(Adw.HeaderBar())

        page = Adw.PreferencesPage()

        desc_group = Adw.PreferencesGroup()
        label = Gtk.Label(
            label='Tusk is free and open source.\nIf it\'s useful to you, consider sponsoring its development.',
            wrap=True,
            justify=Gtk.Justification.CENTER,
            margin_top=8,
            margin_bottom=8,
        )
        label.add_css_class('dim-label')
        desc_group.add(label)
        page.add(desc_group)

        for group_title, tiers in _TIERS.items():
            group = Adw.PreferencesGroup(title=group_title)
            for name, price, url in tiers:
                row = Adw.ActionRow(title=name, subtitle=price, activatable=True)
                row.add_suffix(Gtk.Image.new_from_icon_name('go-next-symbolic'))
                row.connect('activated', self._on_tier_activated, url)
                group.add(row)
            page.add(group)

        toolbar_view.set_content(page)
        self.set_child(toolbar_view)

    def _on_tier_activated(self, _row, url):
        Gtk.show_uri(self._win, url, Gdk.CURRENT_TIME)
