import logging
import time
from pathlib import Path

import epics
from pcdsutils.qt.designer_display import DesignerDisplay
from pydm.widgets.enum_combo_box import PyDMEnumComboBox
from pydm.widgets.label import PyDMLabel
from pydm.widgets.line_edit import PyDMLineEdit
from pydm.widgets.pushbutton import PyDMPushButton
from PyQt5.QtCore import QObject, QThread, pyqtSignal
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
from ..utils.dict_tools import (
    find_unique_keys,
    identify_di,
    identify_drv,
    identify_enc,
    strip_axis_id,
    val_to_key,
)


class CaputWorker(QObject):
    result = pyqtSignal(str, bool, object)  # PV name, success, new value
    finished = pyqtSignal()

    def __init__(self, pv_name, value):
        """
        Initialize a caput worker for asynchronous EPICS PV writes.

        Parameters:
            pv_name (str): Name of the process variable to write to
            value: Value to write to the PV
        """
        super().__init__()
        self.pv_name = pv_name
        self.value = value

    def do_caput(self):
        """
        Execute an asynchronous caput operation and emit results.

        Performs a caput write to the specified PV with the given value,
        reads back the new value, and emits result and finished signals.
        """
        # Perform caput operation
        success = epics.caput(self.pv_name, self.value, wait=True, timeout=2)
        # Optionally read back the value
        pv = epics.PV(self.pv_name)
        new_value = pv.get()
        self.result.emit(self.pv_name, success, new_value)
        self.finished.emit()


class SettingsWindow(DesignerDisplay, QWidget):
    filename = "settings_tab.ui"
    ui_dir = Path(__file__).parent / "./../ui"

    settings_duplicate_di_warning: QCheckBox
    settings_duplicate_drv_warning: QCheckBox
    settings_duplicate_enc_warning: QCheckBox

    def __init__(self, main_window, parent=None, logger=None):
        """
        Initialize the Settings window widget.

        Parameters:
            main_window: Reference to the main application window
            parent: Parent widget (default: None)
            logger: Logger instance for debug/info messages (default: None)
        """
        # Properly call the superclass __init__!
        super().__init__(parent)
        self.logger = logger
        self.main_window = main_window


class MappingWindow(DesignerDisplay, QDialog):
    """
    Dialog window for viewing staged axis-hardware mappings.

    Displays the configured mappings for digital inputs, drives, and encoders
    for the currently selected axis.
    """

    filename = "mapping_window.ui"
    ui_dir = Path(__file__).parent / "./../ui"

    staged_mappings_list: QListWidget


class LinkerWindow(DesignerDisplay, QWidget):
    filename = "linker_tab.ui"
    ui_dir = Path(__file__).parent / "./../ui"

    # Linker Tab
    plc_ioc_list: QComboBox
    plc_ioc_label: PyDMLabel
    axis_list_linker: QListWidget
    digital_input_hardware: QListWidget
    digital_input_main_channels: QListWidget
    digital_input_sub_channels: QListWidget
    digital_input_axis: QListWidget
    drives_list: QListWidget
    drives_channel_list: QListWidget
    encoders_list: QListWidget
    encoders_channel_list: QListWidget
    confirm_mapping: QPushButton
    view_logger: PyDMPushButton
    load_ioc: QPushButton
    stage_mapping: QPushButton
    see_staged_mapping: QPushButton
    clear_mapping: QPushButton
    status_indicators: QLabel

    def __init__(self, main_window, parent=None, logger=None):
        """
        Initialize the Linker window widget.

        Sets up the UI for managing axis-to-hardware linkage configurations including
        digital inputs, drives, and encoders. Initializes data structures for tracking
        staged changes and duplicate detection flags.

        Parameters:
            main_window: Reference to the main application window
            parent: Parent widget (default: None)
            logger: Logger instance for debug/info messages (default: None)
        """
        # Properly call the superclass __init__!
        super().__init__(parent)
        self.logger = logger
        self.main_window = main_window
        self.prefixName = ""
        self.axis = []
        self.pvDict = {}
        self.drives_linker = ["None"]
        self.encoders_linker = ["None"]
        self.digital_inputs_linker = ["None"]
        self.loaded_di_channels_linker = []
        self.staged_mapping = []
        self.staged_channels = []
        self.staged_de = []
        self.duplicate_di_cb_flag = False
        self.duplicate_drv_cb_flag = False
        self.duplicate_enc_cb_flag = False
        self.qCurrAxis = 0
        self.msg = QMessageBox()

        # Setting up the widget signals
        # digitial input handling signals
        self.digital_input_hardware.currentRowChanged.connect(self.load_di_channel)
        self.digital_input_axis.currentRowChanged.connect(self.select_di_channel)
        self.drives_list.currentRowChanged.connect(self.load_drives_channel)
        self.encoders_list.currentRowChanged.connect(self.load_encoders_channel)
        """
        axis signals
        """
        self.axis_list_linker.currentRowChanged.connect(self.isStagedMappingSet)

        """
        mapping signals
        """
        self.stage_mapping.clicked.connect(self.save_stage)
        self.see_staged_mapping.clicked.connect(self.see_stage)
        self.clear_mapping.clicked.connect(self.clear_stage)

        """
        Linking Buttons
        """
        self.confirm_mapping.clicked.connect(self.update_links)

    def isStagedMappingSet(self):
        """
        Check if there are unsaved staged mappings and prompt user if needed.

        Displays a warning dialog if staged changes exist when switching axes,
        allowing the user to discard or keep the changes.
        """
        self.logger.info(f"inStateMapptingSet")
        for stage in range(len(self.staged_mapping)):
            for di in range(len(self.staged_mapping[stage])):
                self.logger.debug(f"di: {self.staged_mapping[stage][di]}")
        for stage in range(len(self.staged_de)):
            for item in range(len(self.staged_de[stage])):
                self.logger.debug(f"item: {self.staged_de[stage][item]}")
        # if there is nothing staged
        # Check if there are any staged mappings
        temp_flag = False
        self.isMsgActive = True
        self.logger.debug(f"curr axis index: {self.qCurrAxis}")
        if not self.status_staged_mappings():
            self.logger.debug("There is nothing staged")
            self.select_axis()
        else:
            self.logger.debug("There are some staged values")

            self.msg.setIcon(QMessageBox.Warning)
            self.msg.setText("You have unsaved staged changes! Discard changes?")
            self.msg.setWindowTitle("Warning")
            self.msg.setStandardButtons(
                QMessageBox.Yes | QMessageBox.No
            )  # Adjusted buttons
            result = self.msg.exec_()

            self.logger.debug(f"current axis: {self.qCurrAxis}")
            self.logger.debug(f"Message box result: {result}")
            if result == QMessageBox.Yes:
                self.logger.debug("switching to select axis")
                self.clear_stage()
                self.select_axis()
            elif result == QMessageBox.No:
                temp_flag = True
        if temp_flag:
            self.logger.debug("attempting to reset axis")
            self.logger.debug(f"resetting row to: {self.qCurrAxis}")
            self.axis_list_linker.setEnabled(True)
            self.axis_list_linker.blockSignals(True)
            self.axis_list_linker.setCurrentRow(self.qCurrAxis)
            self.axis_list_linker.blockSignals(False)

    def status_staged_mappings(self):
        """
        Check if any staged digital input or drive/encoder changes exist.

        Returns:
            bool: True if there are unsaved changes, False otherwise
        """
        self.logger.info(
            f"in status_staged_mapping: checking if there is a staged mapping"
        )
        containsDI = False
        containsDE = False
        for axis in self.staged_mapping:
            if isinstance(axis, list):  # Ensure we're working with a list
                for sublist in axis:
                    self.logger.debug(f"size of staged mapping: {len(sublist)}")
                    self.logger.debug(
                        f"isinstance: {isinstance(sublist, list) and len(sublist) > 1}"
                    )
                    if isinstance(sublist, list) and len(sublist) > 1:
                        self.logger.debug(f"there are stagged di changes")
                        containsDI = True  # Found a non-empty sublist
        for axis in self.staged_de:
            if isinstance(axis, list):  # Ensure we're working with a list
                [self.logger.debug(f"item: {item}") for item in axis]
                self.logger.debug(
                    f"any: {any([(item != ['None'] and item != [''] and item != []) for item in axis])}"
                )
                if any(
                    [
                        (item != ["None"] and item != [""] and item != [])
                        for item in axis
                    ]
                ):
                    self.logger.debug("drive or encoders staged")
                    containsDE = True
        if containsDE or containsDI:
            return True
        else:
            return False

    def check_duplicate_di_flag(self):
        """
        Update the duplicate digital input warning flag from settings.

        Reads the checkbox state from the settings window to determine if
        duplicate digital input warnings should be displayed.
        """
        self.logger.info(f"in check dup di")

        self.duplicate_di_cb_flag = (
            self.settings_window.settings_duplicate_di_warning.isChecked()
        )

        self.logger.debug(f"isDuplicateDIWarning: {self.duplicate_di_cb_flag}")

    def check_duplicate_drv_flag(self):
        """
        Update the duplicate drive warning flag from settings.

        Reads the checkbox state from the settings window to determine if
        duplicate drive warnings should be displayed.
        """
        self.logger.info(f"in check dup drv")

        self.duplicate_drv_cb_flag = self.duplicate_drv_cb.isChecked()

        self.logger.debug(f"isDuplicateDIWarning: {self.duplicate_drv_cb_flag}")

    def check_duplicate_enc_flag(self):
        """
        Update the duplicate encoder warning flag from settings.

        Reads the checkbox state from the settings window to determine if
        duplicate encoder warnings should be displayed.
        """
        self.logger.info(f"in check dup enc")

        self.duplicate_enc_cb_flag = self.duplicate_enc_cb.isChecked()

        self.logger.debug(f"isDuplicateDIWarning: {self.duplicate_enc_cb_flag}")

    def check_duplicate_di(self):
        """
        Detect and warn about duplicate digital input assignments.

        Scans through staged digital input mappings to find duplicate hardware
        or channel assignments and displays warning dialogs if duplicates are found
        and the warning flag is enabled.
        """
        self.logger.info(f"in check for duplicate di")
        # To hold values for duplicate checking
        second_index_values = set()
        third_index_values = set()

        # Track duplicates
        duplicates_second = set()
        duplicates_third = set()

        # Loop through each sublist in the main list
        for sublist in self.staged_mapping:
            for item in sublist:
                if len(item) > 1:  # Check if the sublist has at least 2 elements
                    # Check the 2nd index value
                    second_index_value = item[1]
                    if second_index_value in second_index_values:
                        duplicates_second.add(second_index_value)
                    else:
                        second_index_values.add(second_index_value)

                if len(item) > 2:  # Check if the sublist has at least 3 elements
                    # Check the 3rd index value
                    third_index_value = item[2]
                    if third_index_value in third_index_values:
                        duplicates_third.add(third_index_value)
                    else:
                        third_index_values.add(third_index_value)
        if self.duplicate_di_cb_flag and (duplicates_second or duplicates_third):
            # Prepare the message content
            second_duplicates = (
                ", ".join(duplicates_second) if duplicates_second else "None"
            )
            third_duplicates = (
                ", ".join(duplicates_third) if duplicates_third else "None"
            )

            msg = QMessageBox()
            msg.setIcon(QMessageBox.Warning)
            msg.setText("Duplicate DI")
            msg.setInformativeText(
                f"Duplicate DIs found:\n2nd Index: {second_duplicates}\n3rd Index: {third_duplicates}"
            )
            msg.setWindowTitle("Warning")
            msg.setStandardButtons(QMessageBox.Ok)

            msg.exec_()

        # Print the results
        self.logger.debug(f"Duplicates in the 2nd index: {duplicates_second}")
        self.logger.debug(f"Duplicates in the 3rd index: {duplicates_third}")

    def check_duplicate_drv(self):
        """
        Check for duplicate first index values in staged_de based on the first element of each inner-most list.
        Ignore duplicates for the value 'None'.

        Returns:
            set: A set of duplicate first index values except 'None'.
        """
        self.logger.info(f"in check for duplicate drv")

        # To hold unique values for duplicate checking
        seen_values = []

        # Track duplicates
        duplicates = []
        # Loop through each main list in data
        for axis in self.staged_de:
            # Loop through each sublist in the main list
            # Ensure the sublist is a list and has at least 1 element
            if isinstance(axis, list) and len(axis) > 0:
                # Get the first element value
                first_element_value = axis[0]

                # Ignore None values or empty strings while checking for duplicates
                if (
                    first_element_value is None
                    or first_element_value == "None"
                    or first_element_value == ["None"]
                    or first_element_value == ""
                ):
                    continue

                # Check for duplicates
                if first_element_value in seen_values:
                    duplicates.append(first_element_value)
                else:
                    seen_values.append(first_element_value)

        if self.duplicate_di_cb_flag and len(duplicates) > 0:
            msg = QMessageBox()
            msg.setIcon(QMessageBox.Warning)
            msg.setText("Duplicate DRV")
            msg.setInformativeText(f"Duplicate DRVs found: {duplicates}")
            msg.setWindowTitle("Warning")
            msg.setStandardButtons(QMessageBox.Ok)
            msg.exec_()

        # Print the results
        self.logger.debug(f"Duplicates: {duplicates}")

    def check_duplicate_enc(self):
        """
        Check for duplicate second index values in staged_de based on the second element of each inner-most list.
        Ignore duplicates for the value 'None'.

        Returns:
            set: A set of duplicate first index values except 'None'.
        """
        self.logger.info(f"in check for duplicate enc")

        # To hold unique values for duplicate checking
        seen_values = []

        # Track duplicates
        duplicates = []
        # Loop through each main list in data
        for axis in self.staged_de:
            # Loop through each sublist in the main list
            # for sublist in axis:
            # Ensure the sublist is a list and has at least 1 element
            if isinstance(axis, list) and len(axis) > 0:
                # Get the first element value
                first_element_value = axis[1]

                # Ignore None values or empty strings while checking for duplicates
                if (
                    first_element_value is None
                    or first_element_value == "None"
                    or first_element_value == ["None"]
                    or first_element_value == ""
                ):
                    continue

                # Check for duplicates
                if first_element_value in seen_values:
                    duplicates.append(first_element_value)
                else:
                    seen_values.append(first_element_value)

        if self.duplicate_di_cb_flag and len(duplicates) > 0:
            msg = QMessageBox()
            msg.setIcon(QMessageBox.Warning)
            msg.setText("Duplicate ENC")
            msg.setInformativeText(f"Duplicate ENCs found: {duplicates}")
            msg.setWindowTitle("Warning")
            msg.setStandardButtons(QMessageBox.Ok)
            msg.exec_()

        # Print the results
        self.logger.debug(f"Duplicates: {duplicates}")

    def see_stage(self):
        """
        Display a dialog showing all currently staged mappings.

        Creates a modal window that lists the staged digital input, drive, and
        encoder configurations for the selected axis in a readable format.
        """
        self.logger.info(f"in see_stage")
        mapping_window = MappingWindow(self)
        mapping_window.staged_mappings_list.clear()

        for stage in range(len(self.staged_mapping)):
            if self.staged_mapping[stage]:  # Check if stage is not empty
                row_output = []  # To gather items in rows of three
                for di in range(len(self.staged_mapping[stage])):
                    # Gather each item's corresponding list output
                    if self.staged_mapping[stage][di]:
                        # Append the item to the row output
                        row_output.append(self.staged_mapping[stage][di])
                    else:
                        # Append an empty list for empty entries
                        row_output.append([""])
                if len(self.staged_de[stage][0]) < 1:
                    self.logger.debug("0 is blank")
                    self.staged_de[stage][0] = [""]
                if len(self.staged_de[stage][1]) < 1:
                    self.staged_de[stage][1] = [""]
                    self.logger.debug("1 is blank")
                self.logger.debug(
                    f"self.staged_de[stage][0]: {self.staged_de[stage][0]}"
                )
                self.logger.debug(
                    f"self.staged_de[stage][1]: {self.staged_de[stage][1]}"
                )

                # Print the row output in groups of three
                mapping_window.staged_mappings_list.addItem(
                    f"Axis {int(self.axis_list_linker.currentRow())+1}: DI: {row_output[0]}, {row_output[1]}, {row_output[2]} DRV: {self.staged_de[stage][0]} ENC:{self.staged_de[stage][1]}"
                )
                # self.logger.debug(row_output)  # Printing as one complete list containing the sublists

            else:
                self.logger.debug(
                    f"Stage {stage} was empty"
                )  # Handling completely empty stages
        # #need to setup a way to push info back to the gui is this is wanted
        # mapping_window.show()
        mapping_window.exec_()

    def save_stage(self):
        """
        Save currently selected hardware mappings to staged buffer.

        Collects selections from the UI (digital input hardware, channels, drives,
        and encoders) and stores them in staged structures. Also validates selections
        and checks for duplicate assignments.
        """
        self.logger.info(f"in save_stage")

        # setup holder for stagged mapping
        numStages = self.axis_list_linker.count()
        self.logger.debug(f"numStages count: {numStages}")

        # saving DI components
        self.qCurrAxis = self.axis_list_linker.currentRow()
        self.logger.debug(f"currAxis: {self.qCurrAxis}")
        currAxisDi = self.digital_input_axis.currentRow() + 1
        self.logger.debug(f"currAxisDi: {currAxisDi}")
        currDiHardwareItem = self.digital_input_hardware.currentItem()
        if currDiHardwareItem is None:
            # No hardware selected, prompt user and exit
            msg = QMessageBox()
            msg.setIcon(QMessageBox.Warning)
            msg.setText("Please select a Digital Input Hardware device!")
            msg.setWindowTitle("Selection Required")
            msg.setStandardButtons(QMessageBox.Ok)
            msg.exec_()
        elif currDiHardwareItem is not None and currDiHardwareItem.text() != "None":
            currDiHardware = currDiHardwareItem.text()
        else:
            currDiHardware = ""
        self.logger.debug(f"currDiHardware: {currDiHardwareItem}")

        currDiHardwareMainChan = self.digital_input_main_channels.currentItem()
        if currDiHardwareMainChan is None:
            msg = QMessageBox()
            msg.setIcon(QMessageBox.Warning)
            msg.setText("Please select a Digital Input Hardware Main Channel!")
            msg.setWindowTitle("Selection Required")
            msg.setStandardButtons(QMessageBox.Ok)
            msg.exec_()
        elif currDiHardwareMainChan is not None:
            currDiHardwareMainChan = str(int(currDiHardwareMainChan.text()))
        else:
            currDiHardwareMainChan = ""
        self.logger.debug(f"currDiHardwareMainChan: {currDiHardwareMainChan}")

        currDiHardwareSubChan = self.digital_input_sub_channels.currentItem()
        if currDiHardwareSubChan is None:
            msg = QMessageBox()
            msg.setIcon(QMessageBox.Warning)
            msg.setText("Please select a Digital Input Hardware Sub Channel!")
            msg.setWindowTitle("Selection Required")
            msg.setStandardButtons(QMessageBox.Ok)
            msg.exec_()
        elif currDiHardwareSubChan is not None:
            currDiHardwareSubChan = str(int(currDiHardwareSubChan.text()))
        else:
            currDiHardwareSubChan = ""
        self.logger.debug(f"currDiHardwareSubChan: {currDiHardwareSubChan}")

        currDrvHardwareChan = self.drives_channel_list.currentItem()
        if currDrvHardwareChan is None:
            msg = QMessageBox()
            msg.setIcon(QMessageBox.Warning)
            msg.setText("Please select a Digital Input Hardware Sub Channel!")
            msg.setWindowTitle("Selection Required")
            msg.setStandardButtons(QMessageBox.Ok)
            msg.exec_()
        elif currDrvHardwareChan is not None:
            currDrvHardwareChan = str(int(currDrvHardwareChan.text()))
        else:
            currDrvHardwareChan = ""
        self.logger.debug(f"currDrvHardwareChan: {currDrvHardwareChan}")

        currEncHardwareChan = self.drives_channel_list.currentItem()
        if currEncHardwareChan is None:
            msg = QMessageBox()
            msg.setIcon(QMessageBox.Warning)
            msg.setText("Please select a Digital Input Hardware Sub Channel!")
            msg.setWindowTitle("Selection Required")
            msg.setStandardButtons(QMessageBox.Ok)
            msg.exec_()
        elif currEncHardwareChan is not None:
            currEncHardwareChan = str(int(currEncHardwareChan.text()))
        else:
            currEncHardwareChan = ""
        self.logger.debug(f"currEncHardwareChan: {currEncHardwareChan}")

        if len(self.staged_mapping[0]) and currAxisDi == 1:
            self.staged_mapping[0][0].clear()
        elif len(self.staged_mapping[0]) and currAxisDi == 2:
            self.staged_mapping[0][1].clear()
        elif len(self.staged_mapping[0]) and currAxisDi == 3:
            self.staged_mapping[0][2].clear()

        if currAxisDi == 1:
            self.staged_mapping[0][0].append("0" + str(currAxisDi))
            if currDiHardware != "":
                self.staged_mapping[0][0].append(currDiHardware)
            if currDiHardwareMainChan != "":
                self.staged_mapping[0][0].append(currDiHardwareMainChan)
            if currDiHardwareSubChan != "":
                self.staged_mapping[0][0].append(currDiHardwareSubChan)
        elif currAxisDi == 2:
            self.staged_mapping[0][1].append("0" + str(currAxisDi))
            if currDiHardware != "":
                self.staged_mapping[0][1].append(currDiHardware)
            if currDiHardwareMainChan != "":
                self.staged_mapping[0][1].append(currDiHardwareMainChan)
            if currDiHardwareSubChan != "":
                self.staged_mapping[0][1].append(currDiHardwareSubChan)
        elif currAxisDi == 3:
            self.staged_mapping[0][2].append("0" + str(currAxisDi))
            if currDiHardware != "":
                self.staged_mapping[0][2].append(currDiHardware)
            if currDiHardwareMainChan != "":
                self.staged_mapping[0][2].append(currDiHardwareMainChan)
            if currDiHardwareSubChan != "":
                self.staged_mapping[0][2].append(currDiHardwareSubChan)

        if len(self.staged_de[0][0]):
            self.staged_de[0][0].clear()
        if len(self.staged_de[0][1]):
            self.staged_de[0][1].clear()

        # saving drives and encoders
        if self.drives_list.currentItem() == None:
            self.staged_de[0][0] = ["None"]
        elif self.drives_list.currentItem().text() == "None":
            self.staged_de[0][0] = ["None"]
        else:
            self.staged_de[0][0].append(self.drives_list.currentItem().text())
            self.staged_de[0][0].append(self.drives_channel_list.currentItem().text())
        if self.encoders_list.currentItem() == None:
            self.staged_de[0][1] = ["None"]
        elif self.encoders_list.currentItem().text() == "None":
            self.staged_de[0][1] = ["None"]
        else:
            self.staged_de[0][1].append(self.encoders_list.currentItem().text())
            self.staged_de[0][1].append(self.encoders_channel_list.currentItem().text())

        self.check_duplicate_di()
        self.check_duplicate_drv()
        self.check_duplicate_enc()

        # show mapping
        self.logger.debug(f"staged mapping: {self.staged_mapping}")
        self.logger.debug(f"staged channels: {self.staged_channels}")
        self.logger.debug(f"staged de: {self.staged_de}")

    def clear_stage(self):
        """
        Clear all staged mappings and reset to default empty state.

        Removes all staged changes for digital inputs, drives, and encoders,
        and resets the staged mapping structures to their initial empty state.
        """
        self.logger.info(f"in clear_stage")

        for sublist in self.staged_mapping:
            for inner_list in sublist:
                inner_list.clear()
        for sublist in self.staged_channels:
            sublist.clear()
        for sublist in self.staged_de:
            for inner_list in sublist:
                inner_list.clear()
        self.staged_mapping = [[["01"], ["02"], ["03"]]]
        self.staged_de = [[["None"], ["None"]]]
        self.logger.debug(f"staged mapping: {self.staged_mapping}")
        self.logger.debug(f"staged channels: {self.staged_channels}")
        self.logger.debug(f"staged de: {self.staged_de}")

    def detect_linked_drv(self):
        """
        Detect and display the currently linked drive for the selected axis.

        Reads the EPICS PV to determine which drive hardware is currently linked
        to the selected axis and updates the UI to highlight that selection.
        """
        self.logger.info(f"in detect_linked_drv")
        currAxis = self.axis_list_linker.currentRow()
        self.logger.debug(f"currAxis: {currAxis}")

        detectableDRV = (
            self.prefixName + ":AXIS:0" + str(currAxis + 1) + ":SelG:DRV:Id_RBV"
        )

        drvValue = epics.caget(detectableDRV, as_string=True)
        self.logger.debug(f"detDRV: {detectableDRV}")
        self.logger.debug(f"drvValue: {drvValue}")

        found_drv = False
        for i in range(0, self.drives_list.count()):
            if drvValue == self.drives_list.item(i).text():
                self.logger.debug(f"found drv: {self.drives_list.item(i).text()}")
                self.drives_list.setCurrentRow(i)
                found_drv = True
                break

        # load drive channels
        drive_channel = (
            self.prefixName
            + ":AXIS:0"
            + str(self.axis_list_linker.currentRow() + 1)
            + ":SelG:DRV:MAIN_RBV"
        )
        self.logger.debug(f"drive_channel: {drive_channel}")
        drive_channel_val = epics.caget(drive_channel, as_string=True)
        self.logger.debug(f"drive_channel_val: {drive_channel_val}")
        for i in range(0, self.drives_channel_list.count()):
            item = self.drives_channel_list.item(i)
            if item is not None and drive_channel_val == item.text():
                self.logger.debug(f"found drv chan: {item.text()}")
                self.drives_channel_list.setCurrentRow(i)
                break
            else:
                self.logger.debug(f"channel is none, something went wrong")

        if not found_drv:
            self.logger.debug("No link found, defaulting to None")
            self.drives_list.setCurrentRow(0)

    def detect_linked_enc(self):
        """
        Detect and display the currently linked encoder for the selected axis.

        Reads the EPICS PV to determine which encoder hardware is currently linked
        to the selected axis and updates the UI to highlight that selection.
        """
        self.logger.info(f"in detect_linked_enc")
        currAxis = self.axis_list_linker.currentRow()
        self.logger.debug(f"currAxis: {currAxis}")

        detectableENC = (
            self.prefixName + ":AXIS:0" + str(currAxis + 1) + ":SelG:ENC:Id_RBV"
        )
        encValue = epics.caget(detectableENC, as_string=True)
        self.logger.debug(f"detectableENC: {detectableENC}")
        self.logger.debug(f"encValue: {encValue}")

        found_enc = False
        for i in range(0, self.encoders_list.count()):
            if encValue == self.encoders_list.item(i).text():
                self.logger.debug(f"found enc: {self.encoders_list.item(i).text()}")
                self.encoders_list.setCurrentRow(i)
                found_enc = True
                break

        # load encoder channels
        encoder_channel = (
            self.prefixName
            + ":AXIS:0"
            + str(self.axis_list_linker.currentRow() + 1)
            + ":SelG:ENC:MAIN_RBV"
        )
        self.logger.debug(f"encoder_channel: {encoder_channel}")
        encoder_channel_val = epics.caget(encoder_channel, as_string=True)
        self.logger.debug(f"encoder_channel_val: {encoder_channel_val}")
        for i in range(0, self.encoders_channel_list.count()):
            item = self.encoders_channel_list.item(i)
            if item is not None and encoder_channel_val == item.text():
                self.logger.debug(f"found enc chan: {item.text()}")
                self.encoders_channel_list.setCurrentRow(i)
                break
            else:
                self.logger.debug(f"channel is none, something went wrong")

        if not found_enc:
            self.logger.debug("No link found, defaulting to None")
            self.encoders_channel_list.setCurrentRow(0)

    def load_drives_channel(self):
        """
        Populate available drive hardware channels for the selected drive.

        Clears the drives channel list and adds available channels based on
        the currently selected drive hardware model.
        """
        self.logger.info(f"in load_drives_channel")
        self.drives_channel_list.clear()

        # need to change this to use the num chans pv
        if "7062" in self.drives_list.currentItem().text():
            for i in range(0, 2):
                self.drives_channel_list.addItem(str(i + 1))

    def load_encoders_channel(self):
        """
        Populate available encoder hardware channels for the selected encoder.

        Clears the encoders channel list and adds available channels based on
        the currently selected encoder hardware model.
        """
        self.logger.info(f"in load_encoders_channel_ui")
        self.encoders_channel_list.clear()

        # need to change this to use the num chan pv
        if "7062" in self.encoders_list.currentItem().text():
            for i in range(0, 2):
                self.encoders_channel_list.addItem(str(i + 1))
        elif "5042" in self.encoders_list.currentItem().text():
            for i in range(0, 2):
                self.encoders_channel_list.addItem(str(i + 1))

    def publish_axis_di(self):
        """
        Populate digital input slots for the selected axis.

        Clears and populates the digital input axis list with available DI slots
        (01, 02, 03) and initializes the digital input channel selector.
        """
        self.logger.info(f"in publish_axis_di")
        # if self.axis_di_init:
        self.digital_input_axis.clear()

        # need to make numDI param that does into the for loop
        # numDI = 0

        for i in range(0, 3):
            self.digital_input_axis.addItem("0" + str(1 + i))

        self.select_di_channel()

    def select_axis(self):
        """
        Initialize and display configuration for the newly selected axis.

        Called when user selects a different axis. Detects current linked
        drives and encoders, and populates available digital inputs for the axis.
        """
        self.logger.info(f"in select_axis")
        self.detect_linked_enc()
        self.detect_linked_drv()
        self.publish_axis_di()

    def publish_axis(self):
        """
        Populate available axis options and initialize staging data structures.

        Clears and fills the axis list from the loaded axis data, enables the
        axis selector UI if needed, and resets all staged mapping structures.
        """
        # update enum with axis pulled from .db file
        self.logger.info(f"in populate axis")
        self.axis_list_linker.clear()
        self.axis_list_linker.addItems(self.axis)

        if not self.axis_list_linker.isEnabled():
            self.axis_list_linker.setEnabled(True)

        self.staged_mapping = [[["01"], ["02"], ["03"]]]
        self.staged_channels = [["None"], ["None"]]
        self.staged_de = [[["None"], ["None"]]]

    def load_di(self):
        """
        Populate available digital input hardware options for the selected axis.

        Clears the digital input hardware list and queries EPICS PVs to get all
        available digital input hardware identifiers, then displays them in the UI.
        Also initiates discovery of digital input channels.
        """
        self.logger.info(f"in load_di")
        self.digital_input_hardware.clear()
        self.digital_input_hardware.addItem("None")

        replaced_items = []
        for item in self.digital_inputs_linker[1:]:
            replaced_items.append(item.replace("WCIB_RBV", "Id_RBV"))

        val = epics.caget_many(replaced_items, as_string=True)
        self.digital_inputs_linker[:] = val[0:]
        self.digital_input_hardware.addItems(self.digital_inputs_linker)

        if not self.digital_input_hardware.isEnabled():
            self.digital_input_hardware.setEnabled(True)
        self.discover_di_channel()

    def discover_di_channel(self):
        """
        Discover and store digital input channel PVs from the IOC database.

        Searches through the loaded PV dictionary to find all NUMDI_RBV PVs
        which indicate the number of channels for each digital input module.
        """
        self.logger.info(f"in load_di channel")
        for pv in self.pvDict:
            if pv.endswith("NUMDI_RBV"):
                self.logger.debug(f"pv: {pv}")
                self.loaded_di_channels_linker.append(pv)

    def select_di_channel(self):
        """
        Detect and display currently configured digital input channels for the selected DI.

        Queries the EPICS PVs to find the currently linked digital input hardware,
        main channel, and sub-channel, then updates the UI to display these selections.
        """
        self.logger.info(f" select_di_channel:")

        DI_hardware_Channel = 0
        DI_Hardware_Channel_Main = 0
        axis_di_idx = self.digital_input_axis.currentRow()
        self.logger.debug(f"axis_di_idx: {axis_di_idx}")
        if axis_di_idx < 0:
            self.logger.debug("please select a di")
        else:
            currAxisIdx = self.axis_list_linker.currentRow()
            self.logger.debug(f"currAxisIdx: {currAxisIdx}")
            currAxis = self.prefixName + ":AXIS:0" + str(currAxisIdx + 1)
            detectableDi = (
                currAxis + ":SelG:DI:" + ("0" + str(int(axis_di_idx) + 1)) + ":Id_RBV"
            )
            self.logger.debug(f"link to check: {detectableDi}")
            DI_hardware = epics.caget(detectableDi, as_string=True)
            if DI_hardware == 0:
                DI_hardware = None
            self.logger.debug(f"DI_hardware: {DI_hardware}")

            self.logger.debug("searching for DI hardware")
            # detect DI hardware

            for i in range(0, self.digital_input_hardware.count()):
                if DI_hardware == self.digital_input_hardware.item(i).text():
                    self.logger.debug(
                        f"currItem: {self.digital_input_hardware.item(i).text()}"
                    )
                    self.logger.debug(
                        f"found hardware main: {self.digital_input_hardware.item(i).text()}"
                    )
                    self.digital_input_hardware.setCurrentRow(i)
                elif DI_hardware == "0":
                    self.logger.debug("no hardware detected")
                    self.logger.debug("something went wrong/thinking")
                    self.digital_input_hardware.selectionMode(
                        QAbstractItemView.NoSelection
                    )

            di_chan_slot = (
                currAxis + ":SelG:DI:" + ("0" + str(int(axis_di_idx) + 1)) + ":MAIN_RBV"
            )
            DI_Hardware_Channel_Main = epics.caget(di_chan_slot, as_string=True)
            self.logger.debug(
                f"DI_hardware_Channel Slot: {int(DI_Hardware_Channel_Main)}"
            )
            self.logger.debug("searching for di hardware main channel")

            for i in range(0, self.digital_input_main_channels.count()):
                if (
                    DI_Hardware_Channel_Main
                    == self.digital_input_main_channels.item(i).text()
                ):
                    self.logger.debug(
                        f"found channel main: {self.digital_input_main_channels.item(i).text()}"
                    )
                    self.digital_input_main_channels.setCurrentRow(i)
                elif DI_Hardware_Channel_Main == "0":
                    self.logger.debug("something went wrong, should not be possible")
                    self.digital_input_main_channels.selectionMode(
                        QAbstractItemView.NoSelection
                    )

            self.logger.debug("searching for di hardware sub channel")

            di_chan = (
                currAxis + ":SelG:DI:" + ("0" + str(int(axis_di_idx) + 1)) + ":SUB_RBV"
            )
            DI_hardware_Channel = epics.caget(di_chan, as_string=True)
            self.logger.debug(f"DI_hardware_Channel: {int(DI_hardware_Channel)}")

            for i in range(0, self.digital_input_sub_channels.count()):
                if (
                    DI_hardware_Channel
                    == self.digital_input_sub_channels.item(i).text()
                ):
                    self.logger.debug(
                        f"found channel: {self.digital_input_sub_channels.item(i).text()}"
                    )
                    self.digital_input_sub_channels.setCurrentRow(i)
                elif DI_hardware_Channel == "0":
                    self.logger.debug("something went wrong, should not be possible")
                    self.digital_input_sub_channels.setSelectionMode(
                        QAbstractItemView.NoSelection
                    )

    def load_di_channel(self):
        """
        Populate available digital input hardware channels for the selected DI module.

        Clears the DI channel lists and adds available main and sub-channels based on
        the currently selected digital input hardware module type (EL7062, EL1429, etc).
        """
        self.logger.debug("load di_channel")
        self.digital_input_main_channels.clear()
        self.digital_input_sub_channels.clear()
        current_item = self.digital_input_hardware.currentItem()
        if current_item is None:
            self.logger.warning("No digital input hardware item selected")
            return

        currDI = current_item.text()
        if currDI == "None":
            self.logger.debug(
                "Selected digital input hardware is None, no hardware selected"
            )
            return

        currDI = currDI.split("_")[0]
        self.logger.debug(f"DI Slice: {currDI}")
        currAxisIdx = self.axis_list_linker.currentRow()
        axis_di_idx = self.digital_input_axis.currentRow()
        currAxis = self.prefixName + ":AXIS:0" + str(currAxisIdx + 1)

        if currDI.startswith("EL7062"):
            for i in range(0, int(2)):
                self.digital_input_main_channels.addItem(str(i + 1))
            for i in range(0, int(2)):
                self.digital_input_sub_channels.addItem(str(i + 1))
        elif currDI.startswith("EL1429"):
            di_chan = (
                currAxis + ":SelG:DI:" + ("0" + str(int(axis_di_idx) + 1)) + ":SUB_RBV"
            )
            self.di_size = epics.caget(di_chan)
            for i in range(0, int(16)):
                self.digital_input_main_channels.addItem(str(i + 1))
            for i in range(0, int(1)):
                self.digital_input_sub_channels.addItem(str(i + 1))
        else:
            self.logger.debug("Slice Unknown")

    def load_drives(self):
        """
        Populate available drive hardware options for the selected axis.

        Clears the drives list and queries EPICS PVs to get all available
        drive hardware identifiers, then displays them in the UI.
        """
        # update enum with drives pulled from .db file
        self.logger.info(f"in load drives")
        self.drives_list.clear()

        replaced_items = []
        for item in self.drives_linker[1:]:
            self.logger.debug(f"drives: {item}")
            replaced_items.append(item.replace("WCIB_RBV", "Id_RBV"))

        val = epics.caget_many(replaced_items, as_string=True)
        self.drives_linker[1:] = val[0:]
        self.drives_list.addItems(self.drives_linker)

        if not self.drives_list.isEnabled():
            self.drives_list.setEnabled(True)

    def load_encoders(self):
        """
        Populate available encoder hardware options for the selected axis.

        Clears the encoders list and queries EPICS PVs to get all available
        encoder hardware identifiers, then displays them in the UI.
        """
        # update enum with drives pulled from .db file
        self.logger.info(f"in load enc")
        self.encoders_list.clear()
        replaced_items = []
        self.logger.debug(f"encoder list size: {len(self.encoders_list)}")
        for item in self.encoders_linker[1:]:
            replaced_items.append(item.replace("WCIB_RBV", "Id_RBV"))
        self.logger.debug(f"len replaced_items: {len(replaced_items)}")

        val = epics.caget_many(replaced_items, as_string=True)
        self.encoders_linker[1:] = val[0:]
        self.encoders_list.addItems(self.encoders_linker)

        if not self.encoders_list.isEnabled():
            self.encoders_list.setEnabled(True)

    def caput_async(self, pv_name, value):
        """
        Perform an asynchronous EPICS PV write operation.

        Starts a worker thread to write the given value to the specified PV
        without blocking the UI. Results are emitted via signals.

        Parameters:
            pv_name (str): Name of the process variable to write to
            value: Value to write to the PV
        """
        worker = CaputWorker(pv_name, value)
        thread = QThread()
        worker.moveToThread(thread)
        thread.started.connect(worker.do_caput)
        worker.result.connect(self.handle_caput_result)
        worker.finished.connect(thread.quit)
        thread.finished.connect(thread.deleteLater)
        worker.finished.connect(worker.deleteLater)
        thread.start()

    # Standard handler for all async results
    def handle_caput_result(self, pv_name, success, new_value):
        """
        Handle the result of an asynchronous caput operation.

        Logs the success or failure of the PV write and can update the UI
        with the result status.

        Parameters:
            pv_name (str): Name of the process variable that was written to
            success (bool): Whether the caput operation succeeded
            new_value: The value that was successfully written
        """
        if success:
            self.logger.info(f"caput {pv_name} succeeded, new value: {new_value}")
        else:
            self.logger.warning(f"caput {pv_name} failed, attempted set to {new_value}")

    def update_links(self):
        """
        # Construct the Process Variable (PV) strings
        # TST:UM:LinkSel:SelG:DI:01:Id
        # TST:UM:LinkSel:SelG:AXIS:Id
        # TST:UM:LinkSel:SelG:DRV:Id
        # TST:UM:LinkSel:SelG:ENC:Id

        # Update link process
        # 1. in link selector caput axis id of interest
        #     - TST:UM:LinkSel:SelG:AXIS:Id
        # 2. view set to true -> gives you all of current config
        #     - TST:UM:LinkSel:View
        # 3. change all link defs you want to change
        #     - TST:UM:LinkSel:DI:[number]:[channel]
        # 4. link selector update command
        #     - TST:UM:LinkSel:Update

        """
        self.logger.info(f"in update links")
        # caput staged changes to axis
        for axis_i, axis in enumerate(self.staged_mapping):
            if len(axis) > 0:
                self.logger.debug(f"found changes in axis: {axis}")

                # step 1
                select_axis_string = f"{self.prefixName}:LinkSel:SelG:AXIS:Id"
                self.logger.debug(f"select_axis_string: {select_axis_string}")
                caReadBackSelectedAxis = epics.caput(
                    select_axis_string,
                    self.axis_list_linker.currentItem().text(),
                    wait=True,
                )
                time.sleep(0.5)
                self.logger.debug(f"caReadBackSelectedAxis: {caReadBackSelectedAxis}")

                # step 2
                view_to_true = f"{self.prefixName}:LinkSel:View"
                caReadBack_view_to_true = epics.caput(view_to_true, True, wait=True)
                time.sleep(0.5)
                self.logger.debug(f"caReadBack_view_to_true: {caReadBack_view_to_true}")

                # step 3
                # digital inputs
                self.logger.debug(f"di_1: {axis[0]}")
                if len(axis[0]) > 2:
                    di_1_id = f"{self.prefixName}:LinkSel:SelG:DI:01:Id"
                    self.logger.debug(f"di_1_id: {di_1_id}")
                    di_1_id_new_value = axis[0][1]
                    self.logger.debug(f"di_1_id_new_value: {di_1_id_new_value}")
                    caReadBack_di_1_id = epics.caput(
                        di_1_id, di_1_id_new_value, wait=True
                    )
                    # time.sleep(2)
                    self.logger.debug(f"caReadBack_di_1_id: {caReadBack_di_1_id}")

                    di_1_main = f"{self.prefixName}:LinkSel:SelG:DI:01:MAIN"
                    self.logger.debug(f"di_1_main: {di_1_main}")
                    di_1_main_new_value = axis[0][2]
                    self.logger.debug(f"di_1_main_new_value: {di_1_main_new_value}")
                    caReadBack_di_1_main = epics.caput(
                        di_1_main, int(di_1_main_new_value), wait=True
                    )
                    # time.sleep(2)
                    self.logger.debug(f"caReadBack_di_1_main: {caReadBack_di_1_main}")

                    di_1_sub = f"{self.prefixName}:LinkSel:SelG:DI:01:SUB"
                    self.logger.debug(f"di_1_sub: {di_1_sub}")
                    di_1_sub_new_value = axis[0][3]
                    self.logger.debug(f"di_1_sub_new_value: {di_1_sub_new_value}")
                    caReadBack_di_1_sub = epics.caput(
                        di_1_sub, int(di_1_sub_new_value), wait=True
                    )
                    # time.sleep(2)
                    self.logger.debug(f"caReadBack_di_1_sub: {caReadBack_di_1_sub}")
                else:
                    self.logger.debug(f"nothing in di1")
                self.logger.debug(f"di_2: {axis[1]}")
                if len(axis[1]) > 2:
                    di_2_id = f"{self.prefixName}:LinkSel:SelG:DI:02:Id"
                    self.logger.debug(f"di_2_id: {di_2_id}")
                    di_2_id_new_value = axis[1][1]
                    self.logger.debug(f"di_2_id_new_value: {di_2_id_new_value}")
                    caReadBack_di_2_id = epics.caput(
                        di_2_id, di_2_id_new_value, wait=True
                    )
                    # time.sleep(2)
                    self.logger.debug(f"caReadBack_di_2_id: {caReadBack_di_2_id}")

                    di_2_main = f"{self.prefixName}:LinkSel:SelG:DI:02:MAIN"
                    self.logger.debug(f"di_2_main: {di_2_main}")
                    di_2_main_new_value = axis[1][2]
                    self.logger.debug(f"di_2_main_new_value: {di_2_main_new_value}")
                    caReadBack_di_2_main = epics.caput(
                        di_2_main, int(di_2_main_new_value), wait=True
                    )
                    # time.sleep(2)
                    self.logger.debug(f"caReadBack_di_2_main: {caReadBack_di_2_main}")

                    di_2_sub = f"{self.prefixName}:LinkSel:SelG:DI:02:SUB"
                    self.logger.debug(f"di_1_sub: {di_2_sub}")
                    di_2_sub_new_value = axis[1][3]
                    self.logger.debug(f"di_1_sub_new_value: {di_2_sub_new_value}")
                    caReadBack_di_2_sub = epics.caput(
                        di_2_sub, int(di_2_sub_new_value), wait=True
                    )
                    # time.sleep(2)
                    self.logger.debug(f"caReadBack_di_2_sub: {caReadBack_di_2_sub}")
                else:
                    self.logger.debug(f"nothing in di2")
                self.logger.debug(f"di_3: {axis[2]}")
                if len(axis[2]) > 2:
                    di_3_id = f"{self.prefixName}:LinkSel:SelG:DI:03:Id"
                    self.logger.debug(f"di_1_id: {di_3_id}")
                    di_3_id_new_value = axis[2][1]
                    self.logger.debug(f"di_1_id_new_value: {di_3_id_new_value}")
                    caReadBack_di_3_id = epics.caput(
                        di_3_id, di_3_id_new_value, wait=True
                    )
                    # time.sleep(2)
                    self.logger.debug(f"caReadBack_di_3_id: {caReadBack_di_3_id}")

                    di_3_main = f"{self.prefixName}:LinkSel:SelG:DI:03:MAIN"
                    self.logger.debug(f"di_1_main: {di_3_main}")
                    di_3_main_new_value = axis[2][2]
                    self.logger.debug(f"di_1_main_new_value: {di_3_main_new_value}")
                    caReadBack_di_3_main = epics.caput(
                        di_3_main, int(di_3_main_new_value), wait=True
                    )
                    # time.sleep(2)
                    self.logger.debug(f"caReadBack_di_3_main: {caReadBack_di_3_main}")

                    di_3_sub = f"{self.prefixName}:LinkSel:SelG:DI:03:SUB"
                    self.logger.debug(f"di_3_sub: {di_3_sub}")
                    di_3_sub_new_value = axis[2][3]
                    self.logger.debug(f"di_3_sub_new_value: {di_3_sub_new_value}")
                    caReadBack_di_3_sub = epics.caput(
                        di_3_sub, int(di_3_sub_new_value), wait=True
                    )
                    # time.sleep(2)
                    self.logger.debug(f"caReadBack_di_3_sub: {caReadBack_di_3_sub}")
                else:
                    self.logger.debug(f"nothing in di3")
                time.sleep(0.5)

        #  drives and encoders
        for item in self.staged_de:
            self.logger.debug(f"item: {item}")
            if len(item[0]) > 1:
                # TST:UM:LinkSel:SelG:DRV:Id
                stringDrvHardware = f"{self.prefixName}:LinkSel:SelG:DRV:Id"
                self.logger.debug(f"stringDrvHardware: {stringDrvHardware}")
                stringNewDrvVal = item[0][0]
                self.logger.debug(f"stringNewDrvVal: {stringNewDrvVal}")
                caReadbackNewDriveVal = epics.caput(
                    stringDrvHardware, stringNewDrvVal, wait=True
                )
                time.sleep(0.5)
                self.logger.debug(f"caReadbackNewDriveVal: {caReadbackNewDriveVal}")

                stringDriveMainChan = f"{self.prefixName}:LinkSel:SelG:DRV:MAIN"
                self.logger.debug(f"stringDriveMainChan: {stringDriveMainChan}")
                stringNewMainChanVal = item[0][1]
                self.logger.debug(f"stringNewMainChanVal: {stringNewMainChanVal}")
                caReadbackNewMainChanVal = epics.caput(
                    stringDriveMainChan, stringNewMainChanVal, wait=True
                )
                time.sleep(0.5)
                self.logger.debug(
                    f"caReadbackNewMainChanVal: {caReadbackNewMainChanVal}"
                )

            if len(item[1]) > 1:
                # TST:UM:LinkSel:SelG:ENC:Id
                stringEncHardware = f"{self.prefixName}:LinkSel:SelG:ENC:Id"
                self.logger.debug(f"stringEncHardware: {stringEncHardware}")
                stringNewEncVal = item[1][0]
                self.logger.debug(f"stringNewEncVal: {stringNewEncVal}")
                caReadbackNewEncChanVal = epics.caput(
                    stringEncHardware, stringNewEncVal, wait=True
                )
                time.sleep(0.5)
                self.logger.debug(f"caReadbackNewEncChanVal: {caReadbackNewEncChanVal}")

                stringEncMainChan = f"{self.prefixName}:LinkSel:SelG:ENC:MAIN"
                self.logger.debug(f"stringEncMainChan: {stringEncMainChan}")
                stringNewMainChanVal = item[1][1]
                caReadbackNewMainChanVal = epics.caput(
                    stringEncMainChan, stringNewMainChanVal, wait=True
                )
                time.sleep(0.5)
                self.logger.debug(
                    f"caReadbackNewMainChanVal: {caReadbackNewMainChanVal}"
                )

        # step 4
        update_to_true = f"{self.prefixName}:LinkSel:Update"
        caReadBack_update_to_true = epics.caput(update_to_true, True, wait=True)
        time.sleep(0.5)
        self.logger.debug(f"caReadBack_update_to_true: {caReadBack_update_to_true}")

        # step 5 - clear staged changes
        self.clear_stage()
