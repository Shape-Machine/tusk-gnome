import sys
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from application import TuskApplication


def main():
    app = TuskApplication()
    return app.run(sys.argv)


if __name__ == '__main__':
    sys.exit(main())
