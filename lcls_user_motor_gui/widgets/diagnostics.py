import re
from functools import partial
from os import path
from pathlib import Path

import epics
from pcdsutils.qt.designer_display import DesignerDisplay
from pydm.widgets.label import PyDMLabel
from pydm.widgets.line_edit import PyDMLineEdit
from qtpy import QtCore, uic
from qtpy.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QCompleter,
    QDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..processing.parse_pvs import (
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
from .filtered_list import FilteredListWidget


class DiagnosticsWindow(DesignerDisplay, QWidget):
    filename = "diagnostic_tab.ui"
    ui_dir = Path(__file__).parent / "./../ui"

    # Diagnostic Tab
    diagnostic_axis_selection: QComboBox
    diagnostic_hardware_selection: QListWidget
    diagnostic_groupbox: QGroupBox
    diagnostic_params_groupbox: QGroupBox

    def __init__(self, main_window, parent=None, logger=None):
        """
        Initialize the DiagnosticsWindow.

        Args:
            main_window: The main window instance.
            parent: Parent widget, defaults to None.
            logger: Logger instance for logging, defaults to None.
        """
        super().__init__(parent)
        self.logger = logger
        self.main_window = main_window
        self.prefixName = ""
        self.pvDict = {}
        self.axis = []
        self.dg_list = []
        self.ca_coe_list = []

        self.diagnostic_param_filter = FilteredListWidget(self.diagnostic_groupbox)
        self.diagnostic_groupbox.layout().addWidget(self.diagnostic_param_filter)

        # Setting up widget signals
        self.diagnostic_hardware_selection.currentRowChanged.connect(
            self.populate_diagnostic_coe
        )

        self.diagnostic_param_filter.currentIndexChanged.connect(
            self.populate_diagnostic_widget
        )
        self.diagnostic_axis_selection.currentIndexChanged.connect(
            self.populate_diagnostic_hardware
        )

    def publish_axis_diagnostic(self):
        """
        Populate the diagnostic axis selection combo box with available axes.

        Clears the current items and adds all axes from self.axis, then enables the widget.
        """
        # update enum with axis pulled from .db file
        self.logger.info(f"in publish_axis_diagnostic")
        self.diagnostic_axis_selection.clear()
        self.diagnostic_axis_selection.addItems(self.axis)
        if not self.diagnostic_axis_selection.isEnabled():
            self.diagnostic_axis_selection.setEnabled(True)
        self.logger.debug(f"caput to: self.axis_selection")

    def populate_diagnostic_hardware(self):
        """
        Populate the diagnostic hardware selection list with drive and encoder hardware IDs.

        Retrieves the hardware IDs for the currently selected axis from with caget,
        processes them, and adds the formatted hardware strings to the list widget.
        """
        self.logger.info(f"in populate_diagnostic_hardware")
        # Get current axis
        self.diagnostic_hardware_selection.clear()
        axis_index = self.diagnostic_axis_selection.currentIndex()
        axis = f"{self.prefixName}:{(axis_index + 1):02}"
        self.logger.debug(f"axis: {axis}")
        self.logger.debug(f'caget: {axis + ":SelG:ENC:Id_RBV"}')
        string_hardwareDrvId = (
            f"{self.prefixName}:AXIS:{(axis_index+1):02}:SelG:DRV:Id_RBV"
        )
        string_hardwareEncId = (
            f"{self.prefixName}:AXIS:{(axis_index+1):02}:SelG:ENC:Id_RBV"
        )
        hardwareDrvId = epics.caget(string_hardwareDrvId, as_string=True)
        hardwareEncId = epics.caget(string_hardwareEncId, as_string=True)
        self.logger.debug(f"drv id: {hardwareDrvId}, enc id: {hardwareEncId}")
        if hardwareDrvId:
            if "_" in hardwareDrvId:
                hardwareDrvId = hardwareDrvId.split("_", 1)[0]
        else:
            hardwareDrvId = "None"

        if hardwareEncId:
            if "_" in hardwareEncId:
                hardwareEncId = hardwareEncId.split("_", 1)[0]
        else:
            hardwareEncId = "None"

        axis_w_drv = f"{self.prefixName}:{hardwareDrvId}:{(axis_index + 1):02}"
        axis_w_enc = f"{self.prefixName}:{hardwareEncId}:{(axis_index + 1):02}"
        self.diagnostic_hardware_selection.addItems([axis_w_drv, axis_w_enc])

    def populate_diagnostic_coe(self):
        """
        Populate the diagnostic parameter filter with COE DG parameters for the selected hardware.

        Filters DG parameters matching the current hardware selection, fetches their values
        from with caget, and populates the diagnostic parameter filter widget.
        """
        self.logger.info(f"in populate_diagnostic_coe")
        currHardware = self.diagnostic_hardware_selection.currentItem().text()
        dgPrefix = currHardware + ":COE:DG:"
        string_drive_regex = f"{dgPrefix}[^:]+:Name_RBV"
        self.logger.debug(f"string_drive_regex: {string_drive_regex}")
        stripped_dg = []
        self.logger.debug(f"coe len: {len(self.dg_list)}")
        for pv in self.dg_list:
            self.logger.debug(f"pv: {pv}")
            if re.search(string_drive_regex, pv):
                self.logger.debug(f"stripped_dg, param: {pv}")
                stripped_dg.append(pv.strip())

        self.logger.debug(f"dg list size: {len(stripped_dg)}")

        # Clear previous items
        self.diagnostic_param_filter.clear_items()
        self.dg_list.clear()

        self.ca_dg_list = epics.caget_many(stripped_dg, as_string=True)

        # Add items (filter out None just in case)
        items = [item for item in self.ca_dg_list if item]
        self.diagnostic_param_filter.add_items(items)

        # Enable widget if needed
        self.diagnostic_param_filter.setEnabled(True)

    def populate_diagnostic_widget(self):
        """
        Populate the diagnostic parameters group box with a widget for the selected parameter.

        Finds the selected parameter in the ca_coe_list, loads a diagnostics UI widget,
        configures it with the parameter data, and adds it to the group box.
        """
        self.logger.info(f"in populate_diagnostic_widget")
        self.logger.debug(f"current Item: {self.diagnostic_param_filter.currentText()}")
        self.clear_layout(self.diagnostic_params_groupbox.layout())

        current_text = self.diagnostic_param_filter.currentText()
        for index, (key, value) in enumerate(self.ca_coe_list.items()):
            if current_text == value:
                pv_index = index
                print(f"current pv: {pv_index} ({current_text})")
                thing = key
                self.logger.debug(f"item: {thing}")
                name = self.remove_name_rbv(thing)
                param_widget = uic.loadUi(
                    str(Path(__file__).parent / "./../ui" / "diagnostics.ui")
                )
                self.configure_diagnostic_widgets(param_widget, name)
                self.diagnostic_params_groupbox.layout().addWidget(param_widget)
                break

    def clear_layout(self, layout):
        """Remove all items from a layout immediately."""
        if layout is None:
            return

        while layout.count():
            child = layout.takeAt(0)
            child_layout = child.layout()
            child_widget = child.widget()

            if child_layout is not None:
                self.clear_layout(child_layout)

            if child_widget is not None:
                layout.removeWidget(child_widget)
                child_widget.setParent(None)
                child_widget.deleteLater()

    def configure_diagnostic_widgets(self, widget: QWidget, nc_pv):
        """
        Configure a diagnostics widget with with caget channel values.

        Fetches diagnostic values for the given PV and sets them in the widget's labels.

        Args:
            widget (QWidget): The diagnostics widget to configure.
            nc_pv (str): The base PV name for the diagnostic parameter.
        """
        vals = [
            nc_pv + ":Goal_RBV",
            nc_pv + ":Goal",
            nc_pv + ":Acc_RBV",
            nc_pv + ":Desc_RBV",
            nc_pv + ":TLastUp_RBV",
            nc_pv + ":EU_RBV",
        ]
        ca_vals = epics.caget_many(vals, as_string=True)
        goal = widget.findChild(PyDMLabel, "goal")
        goal.setText(ca_vals[0])
        value = widget.findChild(PyDMLabel, "value")
        value.setText(ca_vals[1])
        access = widget.findChild(PyDMLabel, "access")
        access.setText(ca_vals[2])
        description = widget.findChild(PyDMLabel, "description")
        description.setText(ca_vals[3])
        tlastup = widget.findChild(PyDMLabel, "tlastup")
        tlastup.setText(ca_vals[4])
        eu = widget.findChild(PyDMLabel, "eu")
        eu.setText(ca_vals[5])

    def remove_name_rbv(self, pv_name):
        """
        Remove the ':Name_RBV' suffix from a PV name if present.

        Args:
            pv_name (str): The PV name to process.

        Returns:
            str: The PV name with ':Name_RBV' removed, or the original if not present.
        """
        suffix = ":Name_RBV"
        if pv_name.endswith(suffix):
            return pv_name[: -len(suffix)]
        return pv_name
