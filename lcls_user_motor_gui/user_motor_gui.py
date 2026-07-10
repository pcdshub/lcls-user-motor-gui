import logging
import re
import sys
from os import path
from pathlib import Path

import epics

# import epics
from epics import PV, caget, caput
from pcdsutils.qt.designer_display import DesignerDisplay
from pydm.widgets.label import PyDMLabel
from pydm.widgets.line_edit import PyDMLineEdit
from PyQt5.QtWidgets import QWidget
from qtpy.QtWidgets import (
    QApplication,
    QDialog,
    QListWidget,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QWidget,
)
from qtpy.uic import loadUi

from .processing.discover_pvs import discover_pvs
from .processing.parse_pvs import (
    axis_wcib_to_id,
    fake_caget,
    identify_axis,
    identify_coe_drive_params,
    identify_coe_enc_params,
    identify_dg_params,
    identify_drive,
    identify_enc,
    identify_inputs,
    identify_nc_params,
    strip_key,
    what_can_i_be,
)
from .qt_helpers import ThreadWorker
from .utils.dict_tools import (
    find_unique_keys,
    identify_di,
    identify_drv,
    identify_enc,
    strip_axis_id,
    val_to_key,
)
from .widgets.diagnostics import DiagnosticsWindow
from .widgets.expert import ExpertWindow
from .widgets.linker import LinkerWindow
from .widgets.user_input import UserInputWindow

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)

logger = logging.getLogger(__name__)


class QPlainTextEditLoggerHandler(logging.Handler):
    def __init__(self, text_edit):
        super().__init__()
        self.text_edit = text_edit

    def emit(self, record):
        if record.levelno == logging.INFO:
            msg = self.format(record)
            self.text_edit.appendPlainText(msg)


class MappingWindow(QDialog):
    def __init__(self, parent=None):
        super(MappingWindow, self).__init__(parent)
        loadUi("mapping-window.ui", self)
        self.staged_mappings_list = self.findChild(QListWidget, "staged_mappings_list")
        # for stages in MainWindow(self.staged_mapping):


class SettingsWindow(DesignerDisplay, QWidget):
    filename = "settings_tab.ui"
    ui_dir = Path(__file__).parent / "ui"


class MainWindow(DesignerDisplay, QWidget):
    filename = "main_window_new.ui"
    ui_dir = Path(__file__).parent / "ui"

    # Main Window Widgets
    # load_ioc: QPushButton
    status_logger: QPlainTextEdit
    main_tabs: QTabWidget

    def __init__(
        self,
        parent: QWidget | None = None,
        ioc_name=None,
    ):
        # Pass ONLY parent to super().__init__
        super().__init__(parent)
        self.ioc_name = ioc_name
        # Store macros yourself
        # self.macros = macros

        # Set up logging to status_logger
        handler = QPlainTextEditLoggerHandler(self.status_logger)
        logger.addHandler(handler)

        # user input
        self.user_input_widget = UserInputWindow(self, logger=logger)
        self.main_tabs.addTab(self.user_input_widget, "User Input")

        # linker
        self.linker_widget = LinkerWindow(self, logger=logger)
        self.main_tabs.addTab(self.linker_widget, "Linker")

        # expert
        self.expert_widget = ExpertWindow(self, logger=logger)
        self.main_tabs.addTab(self.expert_widget, "Expert")

        # diagnostic
        self.diagnostic_widget = DiagnosticsWindow(self, logger=logger)
        self.main_tabs.addTab(self.diagnostic_widget, "Diagnostic")

        # setting
        self.setting_widget = SettingsWindow()
        self.main_tabs.addTab(self.setting_widget, "Settings")

        # initialize vars
        self.prefixName = ""
        self.pvDict = {}
        self.axis = []
        self.drives = []
        self.digital_inputs = ["None"]
        self.drives_linker = ["None"]
        self.encoders = ["None"]
        self.list_WCIB = []
        self.ncList = []
        self.coeList = []
        self.wcibList = []
        self.wcibDict = {}

        # self.dg_list = []
        self.ca_nc_list = []
        self.ca_coe_drive_list = []
        self.ca_coe_encoder_list = []
        self.ca_dg_list = []
        self.param_connections = []
        self.ioc_path = "/reg/g/pcds/epics-dev/nlentz/lcls-plc-template-user-motors/iocBoot/ioc-lcls-plc-template-user-motors/lcls_plc_template_user_motors.db"

        self.start_gui()

    def start_gui(self):
        """
        Load IOC pvs from ioc db, setup the tab signals and populate data from loaded db
        """
        logger.info(f"in start_gui")

        self.load_ioc_data()
        self.populate_options()
        self.user_input_widget.populate_collections()

    # def check_duplicate_di_flag(self):
    #     logger.info(f"in check dup di")

    #     self.duplicate_di_cb_flag = self.setting_widget.settings_duplicate_di_warning.isChecked()

    #     logger.debug(f"isDuplicateDIWarning: {self.duplicate_di_cb_flag}")

    # def check_duplicate_drv_flag(self):
    #     logger.info(f"in check dup drv")

    #     self.duplicate_drv_cb_flag = self.setting_widget.settings_duplicate_drv_warning.isChecked()

    #     logger.debug(f"isDuplicateDIWarning: {self.duplicate_drv_cb_flag}")

    # def check_duplicate_enc_flag(self):
    #     logger.info(f"in check dup enc")

    #     self.duplicate_enc_cb_flag = self.setting_widget.settings_duplicate_enc_warning.isChecked()

    #     logger.debug(f"isDuplicateDIWarning: {self.duplicate_enc_cb_flag}")

    # Currently not used
    def when_param_changed(self, idx, pv, lineedit):
        logger.debug(f"in when_param_changed")
        lineedit = self.param_connections[idx]
        logger.debug(f"Value for PV {pv} (index {idx}) is now {lineedit.text()}")

        # Define the function to run in a worker thread
        def caput_check_task(pv):
            pv = self.remove_name_rbv(pv)
            goal_value = epics.caget(pv + ":Goal")
            rbv_value = epics.caget(pv + ":Val_RBV")
            return goal_value == rbv_value, goal_value, rbv_value

        # Define what to do when the worker finishes
        def on_result(result):
            is_match, goal, rbv = result
            if is_match:
                logger.debug(f"goal and rbv match: {goal}, {rbv}")
            else:
                logger.debug(f"goal and rbv DO NOT match: {goal}, {rbv}")
            logger.debug(f"bool: {is_match}")

        # Define what to do on error
        def on_error(exception):
            logger.debug(f"Exception in caput_check_task: {exception}")

        # Start the thread worker
        worker = ThreadWorker(caput_check_task, pv)
        worker.returned.connect(on_result)
        worker.error_raised.connect(on_error)
        # Keep a reference alive! Otherwise it might get garbage-collected!
        if not hasattr(self, "_workers"):
            self._workers = []
        self._workers.append(worker)
        worker.start()

    # def ui_filename(self):
    #     filename = "traj.ui"
    #     ui_dir = Path(__file__).parent / "ui"
    #     return 'ui/main_window.ui'

    # def ui_filepath(self):
    #     return path.join(path.dirname(path.realpath(__file__)), self.ui_filename())

    def load_ioc_data(self):
        """
        load pvs from a db file, find the prefix, sort pvs by type and make a dictionaries or lists from the cagets
        """

        logger.info(f"in load test list")

        # find using ioc name, discover pvs has other options for find the ioc information
        self.pvList = discover_pvs(self.ioc_name, plc_flag=True, find_makefile=True)

        # for testing only
        # Save self.pvList to a file
        # with open("pvlist.txt", "w") as f:
        #     for pv in self.pvList:
        #         f.write(pv + "\n")

        # finding prefix at element 0
        self.prefixName = self.pvList[0]
        self.user_input_widget.prefixName = self.prefixName
        self.linker_widget.prefixName = self.prefixName
        self.expert_widget.prefixName = self.prefixName
        self.diagnostic_widget.prefixName = self.prefixName
        logger.debug(self.prefixName)
        self.pvList = self.pvList[1:-1]

        logger.debug(f"prefixName: {self.prefixName}")

        # caget whole list

        for item in self.pvList:
            if re.search(r"NC", item):
                self.ncList.append(item)
            elif re.search(r"COE", item):
                # logger.debug(f'item: {item}')
                self.coeList.append(item)
            elif re.search(r"WCIB", item):
                self.wcibList.append(item)
        # pv_caget_list = epics.caget_many(self.pvList, as_string=True)
        ca_wcib_list = epics.caget_many(self.wcibList, as_string=True)
        ca_nc_list = epics.caget_many(self.ncList, as_string=True)
        logger.debug(f"len self.coeList: {len(self.coeList)}")
        ca_coe_list = epics.caget_many(self.coeList, as_string=True)
        # put pvs and cagets into a dictionary
        # self.pvDict = dict(zip(self.pvList, pv_caget_list))
        self.ncDict = dict(zip(self.ncList, ca_nc_list))
        self.coeDict = dict(zip(self.coeList, ca_coe_list))
        self.wcibDict = dict(zip(self.wcibList, ca_wcib_list))
        # selfcoevDict = dict(zip(self.pvList, pv_caget_list))
        self.expert_widget.nc_list = self.ncList.copy()
        self.expert_widget.coe_drive_list = self.coeList.copy()
        self.expert_widget.coe_encoder_list = self.coeList.copy()
        self.user_input_widget.ncList = self.ncList.copy()
        self.user_input_widget.pvDict = self.pvDict
        self.linker_widget.pvDict = self.pvDict
        self.expert_widget.pvDict = self.pvDict
        self.diagnostic_widget.dg_list = self.coeList.copy()
        self.diagnostic_widget.ca_coe_list = self.coeDict.copy()
        # logger.debug(self.pvDict)

    def populate_options(self):
        """
        Called from load_ioc
        ---
        Calls WCIB
        """
        logger.info(f"in populate options")
        # identify WCIB PVs
        self.identify_WCIB()

    def identify_WCIB(self):
        """
        Given a a dictionary of WCIB PVs we move this into a list then sort by the type:
        SA - Software Axis
        DI - Digital Input
        DRV - Drive
        ENC - Encoder

        Once sorted, copy to each of the seperate widgets
        """
        logger.info(f"in identify_WCIB'")
        self.clear_items()
        # self.list_WCIB = []

        # for pv in self.pvDict:
        for pv in self.wcibDict:
            # logger.debug(f"pv: {pv}")
            if re.search(r".*:WCIB_RBV", pv):
                # logger.debug(f"wcib pv: {pv}")
                self.list_WCIB.append(pv)
        for pv in self.list_WCIB:
            # fake_caget output is of type string seperated by comma
            # device_type = epics.caget(pv, as_string=True)
            # device_type = fake_caget(self.pvDict, pv)
            device_type = fake_caget(self.wcibDict, pv)
            logger.debug(f"device_type: {device_type}, pv: {pv}")
            if isinstance(device_type, str) and re.search(r"SA", device_type):
                # logger.debug(f"axis: {pv}")
                self.axis.append(pv)
            if isinstance(device_type, str) and re.search(r"DI", device_type):
                self.linker_widget.digital_inputs_linker.append(pv)
                self.user_input_widget.digital_inputs_ui.append(pv)
            if isinstance(device_type, str) and re.search(r"DRV", device_type):
                self.linker_widget.drives_linker.append(pv)
                self.user_input_widget.drives_ui.append(pv)
            if isinstance(device_type, str) and re.search(r"ENC", device_type):
                self.linker_widget.encoders_linker.append(pv)
                self.user_input_widget.encoders_ui.append(pv)

        # Loading Axis
        # self.axis = axis_wcib_to_id(self.pvDict, self.axis)
        logger.debug(f"num of axis: {len(self.axis)}")
        self.axis = axis_wcib_to_id(self.axis)
        self.user_input_widget.axis = self.axis
        self.user_input_widget.publish_axis_ui()
        self.linker_widget.axis = self.axis
        self.linker_widget.publish_axis()
        self.user_input_widget.publish_axis_ui()
        self.expert_widget.axis = self.axis
        self.expert_widget.publish_axis_expert()
        self.diagnostic_widget.axis = self.axis
        self.diagnostic_widget.publish_axis_diagnostic()

        # Loading DIs
        # self.load_di()
        # self.linker_widget.load_axis_di()
        # self.user_input_widget.load_axis_di_ui()
        self.linker_widget.load_di()
        self.user_input_widget.load_di_ui()

        # Loading DRVs
        self.linker_widget.load_drives()
        self.user_input_widget.load_drives_ui()

        # Loading ENCs
        self.linker_widget.load_encoders()
        self.user_input_widget.load_encoders_ui()

    def clear_items(self):
        """
        Clears the WCIB list, both digitial input lists in ui/linker and the drives and encoders in linker
        """
        logger.info("in clear_items")
        self.list_WCIB.clear()
        self.digital_inputs.clear()
        self.user_input_widget.digital_inputs_ui.clear()
        self.drives_linker.clear()
        self.encoders.clear()


if __name__ == "__main__":
    app = QApplication([])
    gui = MainWindow()
    gui.show()
    sys.exit(app.exec_())
