import gi

gi.require_version('Gtk', '4.0')

from gi.repository import Gtk, Gio, GObject, Pango


class _Row(GObject.Object):
    __gtype_name__ = 'TuskRow'

    def __init__(self, values):
        super().__init__()
        self._values = values

    def get(self, i):
        return self._values[i]


def make_column_view(columns, rows):
    store = Gio.ListStore(item_type=_Row)
    for row in rows:
        store.append(_Row(['' if v is None else str(v) for v in row]))

    col_view = Gtk.ColumnView(model=Gtk.SingleSelection(model=store))
    col_view.set_show_row_separators(True)
    col_view.set_show_column_separators(True)
    col_view.set_hexpand(True)

    for i, name in enumerate(columns):
        factory = Gtk.SignalListItemFactory()

        def on_setup(_factory, list_item):
            label = Gtk.Label()
            label.set_xalign(0)
            label.set_ellipsize(Pango.EllipsizeMode.END)
            label.set_max_width_chars(40)
            list_item.set_child(label)

        def on_bind(_factory, list_item, col_idx=i):
            list_item.get_child().set_text(list_item.get_item().get(col_idx))

        factory.connect('setup', on_setup)
        factory.connect('bind', on_bind)

        col = Gtk.ColumnViewColumn(title=name, factory=factory)
        col.set_resizable(True)
        col_view.append_column(col)

    return col_view
