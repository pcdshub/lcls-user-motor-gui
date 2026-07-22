"""
`lcls-user-motor-gui gui` launches the graphical user interface.
"""

import argparse
import sys
from qtpy.QtWidgets import QApplication

DESCRIPTION = __doc__


def build_arg_parser(argparser=None):
    if argparser is None:
        argparser = argparse.ArgumentParser()

    argparser.description = DESCRIPTION
    argparser.formatter_class = argparse.RawTextHelpFormatter

    argparser.add_argument(
        '--ioc-name', '--ioc', dest='ioc_name',
        type=str,
        default=None,
        help='IOC name to load (for example: ioc-lcls-plc-template-user-motors).'
    )

    return argparser


def main(**kwargs):
    from lcls_user_motor_gui.user_motor_gui import MainWindow

    app = QApplication(sys.argv)
    gui = MainWindow(ioc_name=kwargs.get('ioc_name'))
    gui.show()
    sys.exit(app.exec_())