import logging
import os
import re
from getpass import getuser
from pathlib import Path

import epics
from pcdsutils.qt.designer_display import DesignerDisplay
from pydm.widgets.label import PyDMLabel
from pydm.widgets.line_edit import PyDMLineEdit
from PyQt5 import QtCore
from PyQt5.QtGui import QColor
from qtpy.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QCompleter,
    QDialog,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
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
from qtpy.uic import loadUi
from superscore.backends.core import SearchTerm
from superscore.client import Client
from superscore.model import Collection, Parameter, Snapshot

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
    keep_prefix,
    strip_axis_id,
    val_to_key,
)
from .filtered_list import FilteredListWidget


class StageSettings(QDialog):
    def __init__(self, user_input_widget, parent=None, logger=None):
        super(StageSettings, self).__init__(parent)
        self.logger = logger
        ui_path = Path(__file__).resolve().parent / "./../ui" / "stage-config.ui"
        # ui_path = Path(__file__).resolve().parent
        print(ui_path)
        loadUi(str(ui_path), self)  # Load the UI from the .ui file
        self.egu_rev = self.findChild(PyDMLineEdit, "egu_rev")
        self.step_rev = self.findChild(PyDMLineEdit, "step_rev")
        self.run_current = self.findChild(PyDMLineEdit, "run_current")
        self.encoder_scaling = self.findChild(PyDMLineEdit, "encoder_scaling")
        self.backlash = self.findChild(PyDMLineEdit, "backlash")
        self.generate_params = self.findChild(QPushButton, "generate_params")
        self.save_collection = self.findChild(QPushButton, "save_collection")
        self.take_snapshot_button = self.findChild(QPushButton, "take_snapshot")
        self.apply_snapshot_button = self.findChild(QPushButton, "apply_snapshot")
        self.current_axis_line_edit = self.findChild(QLineEdit, "current_axis")
        self.current_collection = self.findChild(QLineEdit, "current_collection")
        # self.user_input_widget = user_input_widget
        self.user_input_widget = user_input_widget
        self.ncList = user_input_widget.ncList
        self.cfg_path = user_input_widget.cfg_path
        self.save_collection.clicked.connect(self.save_to_collection)
        self.take_snapshot_button.clicked.connect(self.take_snapshot_now)
        self.generate_params.clicked.connect(self.calculate_params)
        self.apply_snapshot_button.clicked.connect(self.apply_snapshot_now)
        self.msg = QMessageBox()
        self.currAxis = ""
        self.currConfig = ""
        # self.user_input_widget.populate_collections()
        # self.user_input_widget.stage_load.clicked.connect(self.user_input_widget.load_stage_settings)

    def apply_snapshot_now(self):
        self.logger.info(f"in apply_snapshot")
        self.currAxis = self.user_input_widget.display_axis_ui.currentRow()
        currAxisFormatted = (
            f"{self.user_input_widget.prefixName}:MMS:{self.currAxis+1:02}"
        )
        print("Saving settings for axis: %s", currAxisFormatted)
        self.currConfig = self.user_input_widget.stage_configs_widget.currentText()
        print(f"Current Config: {self.currConfig}")
        superscore_client = Client.from_config(self.cfg_path)
        results = list(
            superscore_client.search(SearchTerm("title", "eq", self.currConfig))
        )
        if not results:
            self.logger.warning(f"something went wrong when trying to snapshot")
            self.msg.setIcon(QMessageBox.Warning)
            self.msg.setText(f"Collection '{self.currConfig}' not found!")
            self.msg.setWindowTitle("Error")
            self.msg.exec_()
            return

        collection = results[0]

        # Find the snapshot(s) taken from this collection and restore the most
        # recent one. apply() needs a data-filled Snapshot, not the Collection.
        snapshots = list(
            superscore_client.search(
                SearchTerm("origin_collection", "eq", collection.uuid)
            )
        )
        if not snapshots:
            self.logger.warning(f"no snapshot found for collection '{self.currConfig}'")
            self.msg.setIcon(QMessageBox.Warning)
            self.msg.setText(f"No snapshot found for collection '{self.currConfig}'!")
            self.msg.setWindowTitle("Error")
            self.msg.exec_()
            return

        latest_snapshot = max(snapshots, key=lambda s: s.creation_time)
        self.logger.info(
            f"applying snapshot {latest_snapshot.uuid} "
            f"({latest_snapshot.creation_time}) for collection '{self.currConfig}'"
        )
        superscore_client.apply(latest_snapshot)
        print(f"Applied snapshot UUID: {latest_snapshot.uuid}")

    def save_to_collection(self):
        print(f"in save_to_collection")
        coll_name, ok = QInputDialog.getText(
            self,
            "Save Collection",
            "Enter a name for the collection:",
            text="User Motors",
        )
        if not ok or not coll_name.strip():
            print("Save to collection cancelled")
            return
        coll_name = coll_name.strip()
        self.currAxis = self.user_input_widget.display_axis_ui.currentRow()
        currAxisFormatted = (
            f"{self.user_input_widget.prefixName}:MMS:{self.currAxis+1:02}"
        )
        print("Saving settings for axis: %s", currAxisFormatted)
        # self.currConfig = self.user_input_widget.stage_configs_widget.currentText()
        # print(f"Current Config: {self.currConfig}")
        # cfg_path = Path(__file__).resolve().parent / "./../.." / "superscore.cfg"
        print(f"cfg path: {self.cfg_path}")
        superscore_client = Client.from_config(self.cfg_path)
        print(superscore_client)

        # Let the user choose/edit the file the collection is saved to.
        # Default to the path configured in the backend (superscore.cfg).
        default_path = getattr(superscore_client.backend, "path", "")
        save_path, ok = QFileDialog.getSaveFileName(
            self,
            "Save Collection To File",
            str(default_path),
            "JSON files (*.json);;All files (*)",
        )
        if not ok or not save_path:
            print("Save to collection cancelled")
            return
        superscore_client.backend.path = save_path
        print(f"saving collection to file: {save_path}")

        # A freshly chosen file may not exist yet or be an empty 0-byte file.
        # The filestore backend's load() only handles a missing file, so an
        # empty/invalid file raises JSONDecodeError. Initialize a valid, empty
        # JSON database in that case before saving.
        if not os.path.exists(save_path) or os.stat(save_path).st_size == 0:
            superscore_client.backend.initialize()

        str_currAxis = f"^{currAxisFormatted}:NC:[^:]+:Goal_RBV$"
        list_ncRBV = []
        for pv in self.ncList:
            if re.search(str_currAxis, pv):
                list_ncRBV.append(pv)
        print(f"len list_ncRbv: {len(list_ncRBV)}")

        coll = Collection(
            title=coll_name,
            tags=["demo"],
        )

        coll.children = [
            Parameter(
                pv_name=pv,
                description="",
                read_only=False,  # these are Goal setpoints; keep writable
            )
            for pv in list_ncRBV
        ]

        superscore_client.save(coll)
        print("Saved collection UUID:", coll.uuid)
        # self.populate_collections()

    # def populate_collections(self):
    #     # search by title (or tags/uuid/etc.)
    #     self.user_input_widget.stage_configs_widget.clear_items()
    #     client = Client.from_config(self.cfg_path)
    #     coll = next(client.search(SearchTerm("title", "eq", "User Motors")))
    #     self.user_input_widget.stage_configs_widget.add_item(coll.title)
    #     self.user_input_widget.stage_configs_widget.setEnabled(True)
    #     print(coll.uuid, coll.title)

    def take_snapshot_now(self):
        print(f"in take_snapshot")
        # check to see if user is authorized
        user = getuser()

        b_isUserAuth = self.check_auth(user)
        print(f"current user: {user}, is user auth: {b_isUserAuth}")
        # if not b_isUserAuth:
        #     self.msg.setIcon(QMessageBox.Warning)
        #     self.msg.setText("You are not authorized to save the collection, please ask the CDSO.")
        #     self.msg.setWindowTitle("Error")
        #     self.msg.exec_()
        # else:

        # Find the collection currently selected in the stage configs widget.
        coll_title = self.user_input_widget.stage_configs_widget.currentText()
        if not coll_title:
            self.msg.setIcon(QMessageBox.Warning)
            self.msg.setText("Please select a collection to snapshot!")
            self.msg.setWindowTitle("Warning")
            self.msg.exec_()
            return

        print(f"taking snapshot for collection: {coll_title}")
        superscore_client = Client.from_config(self.cfg_path)
        results = list(superscore_client.search(SearchTerm("title", "eq", coll_title)))
        if not results:
            self.msg.setIcon(QMessageBox.Warning)
            self.msg.setText(f"Collection '{coll_title}' not found!")
            self.msg.setWindowTitle("Error")
            self.msg.exec_()
            return

        collection = results[0]
        snapshot = superscore_client.snap(collection)
        superscore_client.save(snapshot)
        print(f"Saved snapshot UUID: {snapshot.uuid} for collection {coll_title}")

    def calculate_params(self):
        egu_rev = self.egu_rev.text()
        step_rev = self.step_rev.text()
        run_current = self.run_current.text()
        encoder_scaling = self.encoder_scaling.text()
        backlash = self.backlash.text()
        generate_params = self.generate_params.text()

        self.logger.debug(
            "calculate_params inputs: egu_rev=%s, step_rev=%s, run_current=%s, "
            "encoder_scaling=%s, backlash=%s, generate_params=%s",
            egu_rev,
            step_rev,
            run_current,
            encoder_scaling,
            backlash,
            generate_params,
        )

    def AUTH_FILE(self) -> str:
        """
        A template for the location of the iocmanager.auth config file.

        This file contains usernames, one per line, of users who are authorized
        to make changes to the hutch's main iocmanager.cfg configuration file
        (see attr:`CONFIG_FILE`).

        To complete this template, the %s must be replaced with the 3-letter hutch name.
        """
        return f"/cds/group/pcds/pyps/apps/user_motor_gui/user_motor_gui.auth"

    def check_auth(self, user: str) -> bool:
        """
        Check if a user is authorized to apply changes.

        Parameters
        ----------
        user : str
            Username to check
        hutch : str
            Hutch to check for, such as xpp or tmo

        Returns
        -------
        auth_ok : bool
            True if the user is authorized, False otherwise.
        """
        with open(self.AUTH_FILE()) as fd:
            lines = fd.readlines()
        lines = [ln.strip() for ln in lines]
        for ln in lines:
            if ln == user:
                return True
        return False


class UserInputWindow(DesignerDisplay, QWidget):
    filename = "user_input_tab.ui"
    ui_dir = Path(__file__).parent / "./../ui"

    # User Input Tab
    display_axis_ui: QListWidget
    display_drives_ui: QListWidget
    display_drives_channel_ui: QListWidget
    display_encoders_ui: QListWidget
    display_encoders_channel_ui: QListWidget
    digital_input_axis_ui: QListWidget
    digital_input_hardware_ui: QListWidget
    digital_input_channels_ui: QListWidget
    digital_input_channel_slot_ui: QListWidget
    stage_settings: QPushButton
    refresh_list: QPushButton
    stage_configs: QGroupBox

    def __init__(self, main_window, parent=None, logger=None):
        """
        Initialize the UserInputWindow.

        Args:
            main_window: The main window instance.
            parent: The parent widget.
            logger: Logger instance for logging.
        """
        # Properly call the superclass __init__!
        super().__init__(parent)
        self.logger = logger
        self.main_window = main_window
        self.prefixName = ""
        self.axis = []
        # self.drives = []
        # self.encoders = []
        self.pvDict = {}
        self.store_di_selection = [[-1, -1], [-1, -1], [-1, -1]]
        self.loaded_unique_di = []
        self.drives_ui = ["None"]
        self.encoders_ui = ["None"]
        self.di_size = 0
        self.digital_inputs_ui = ["None"]
        self.digital_inputs_hardware_ui = ["None"]
        self.loaded_di_channels_ui = []
        self.msg = QMessageBox()
        self.cfg_path = Path(__file__).resolve().parent / "./../.." / "superscore.cfg"
        self.ncList = []
        self.stage_configs_widget = FilteredListWidget(self.stage_configs)
        self.stage_configs.layout().addWidget(self.stage_configs_widget)

        # Setting up widget signals
        self.display_axis_ui.currentRowChanged.connect(self.select_axis_ui)
        self.digital_input_axis_ui.currentRowChanged.connect(self.select_di_channel_ui)
        self.digital_input_hardware_ui.currentRowChanged.connect(
            self.load_di_channel_ui
        )
        self.display_drives_ui.currentRowChanged.connect(self.load_drives_channel_ui)
        self.display_encoders_ui.currentRowChanged.connect(
            self.load_encoders_channel_ui
        )

        self.stage_settings.clicked.connect(self.open_stage_settings)
        self.refresh_list.clicked.connect(self.refresh_collections)

    def populate_collections(self, search_term="", attr="title"):
        """
        Populate the stage configs widget with collections matching ``search_term``.

        Parameters
        ----------
        search_term : str, optional
            Value to fuzzy-match against ``attr``. An empty string returns all
            collections. Defaults to "".
        attr : str, optional
            The collection attribute to search against (e.g. "title", "tags").
            Defaults to "title".
        """
        self.stage_configs_widget.clear_items()
        self.stage_configs_widget.add_item("None")
        client = Client.from_config(self.cfg_path)
        results = list(client.search(SearchTerm(attr, "like", search_term)))
        if results:
            for coll in results:
                self.stage_configs_widget.add_item(coll.title)
                print(coll.uuid, coll.title)
            self.stage_configs_widget.setEnabled(True)
        else:
            print(f"No collection matching {attr} ~ '{search_term}' found.")
            self.stage_configs_widget.setEnabled(False)

    def select_axis_ui(self):
        """
        Publish axis selection and detect linked encoders and drives.
        """
        self.logger.info(f"in select_axis_ui")
        # self.populate_di()
        self.detect_linked_enc_ui()
        self.detect_linked_drv_ui()
        self.publish_axis_di_ui()

    def detect_linked_enc_ui(self):
        """
        Detect and select the linked encoder for the current axis.
        """
        self.logger.info(f"in detect_linked_enc_ui")
        currAxis = self.display_axis_ui.currentRow()
        self.logger.debug(f"currAxis: {currAxis}")
        detectableENC = (
            self.prefixName + ":AXIS:0" + str(currAxis + 1) + ":SelG:ENC:Id_RBV"
        )
        encValue = epics.caget(detectableENC, as_string=True)
        self.logger.debug(f"detectableENC: {detectableENC}")
        self.logger.debug(f"encValue: {encValue}")

        found_enc = False
        for i in range(0, self.display_encoders_ui.count()):
            if encValue == self.display_encoders_ui.item(i).text():
                self.logger.debug(
                    f"found enc: {self.display_encoders_ui.item(i).text()}"
                )
                self.display_encoders_ui.setCurrentRow(i)
                found_enc = True
                break

        # load encoder channels
        encoder_channel = (
            self.prefixName
            + ":AXIS:0"
            + str(self.display_axis_ui.currentRow() + 1)
            + ":SelG:ENC:MAIN_RBV"
        )
        self.logger.debug(f"encoder_channel: {encoder_channel}")
        encoder_channel_val = epics.caget(encoder_channel, as_string=True)
        self.logger.debug(f"encoder_channel_val: {encoder_channel_val}")
        for i in range(0, self.display_encoders_channel_ui.count()):
            item = self.display_encoders_channel_ui.item(i)
            if item is not None and encoder_channel_val == item.text():
                self.logger.debug(f"found enc chan: {item.text()}")
                self.display_encoders_channel_ui.setCurrentRow(i)
                break
            else:
                self.logger.debug(f"channel is none, something went wrong")

        if not found_enc:
            self.logger.debug("No link found, defaulting to None")
            self.display_drives_ui.setCurrentRow(0)

    def detect_linked_drv_ui(self):
        """
        Detect and select the linked drive for the current axis.
        """
        self.logger.info(f"in detect_linked_drv_ui")
        currAxis = self.display_axis_ui.currentRow()
        # currAxis = val_to_key(self.axis[currAxisIdx], self.pvDict)
        self.logger.debug(f"currAxis: {currAxis}")
        # currAxis = strip_axis_id(currAxis)
        detectableDRV = (
            self.prefixName + ":AXIS:0" + str(currAxis + 1) + ":SelG:DRV:Id_RBV"
        )

        drvValue = epics.caget(detectableDRV, as_string=True)
        self.logger.debug(f"detDRV: {detectableDRV}")
        self.logger.debug(f"drvValue: {drvValue}")

        found_drv = False
        for i in range(0, self.display_drives_ui.count()):
            if drvValue == self.display_drives_ui.item(i).text():
                self.logger.debug(f"found drv: {self.display_drives_ui.item(i).text()}")
                self.display_drives_ui.setCurrentRow(i)
                found_drv = True
                break

        # load drive channels
        drive_channel = (
            self.prefixName
            + ":AXIS:0"
            + str(self.display_axis_ui.currentRow() + 1)
            + ":SelG:DRV:MAIN_RBV"
        )
        self.logger.debug(f"drive_channel: {drive_channel}")
        drive_channel_val = epics.caget(drive_channel, as_string=True)
        self.logger.debug(f"drive_channel_val: {drive_channel_val}")
        for i in range(0, self.display_drives_channel_ui.count()):
            item = self.display_drives_channel_ui.item(i)
            if item is not None and drive_channel_val == item.text():
                self.logger.debug(f"found drv chan: {item.text()}")
                self.display_drives_channel_ui.setCurrentRow(i)
                break
            else:
                self.logger.debug(f"channel is none, something went wrong")

        if not found_drv:
            self.logger.debug("No link found, defaulting to None")
            self.display_drives_ui.setCurrentRow(0)

    def publish_axis_di_ui(self):
        """
        Publish digital input axis UI for the current axis.
        """
        self.logger.info(f"in publish_axis_di_ui")
        self.digital_input_axis_ui.clear()
        currDisplayAxis = self.display_axis_ui.currentRow()
        numDI = f"{self.prefixName}:AXIS:{(currDisplayAxis+1):02}:NUMDI_RBV"
        self.logger.debug(f"numDI: {numDI}")
        ca_numDI = epics.caget(numDI, as_string=True)
        for i in range(0, int(ca_numDI)):
            self.logger.debug("adding di item")
            self.digital_input_axis_ui.addItem("0" + str(1 + i))

        self.select_di_channel_ui()

    def select_di_channel_ui(self):
        """
        Select the digital input channel UI based on the current axis and DI index.
        """
        self.logger.info(f" select_di_channel_ui:")
        DI_hardware_Channel = 0
        DI_hardware_Channel_Slots = 0
        axis_di_idx = self.digital_input_axis_ui.currentRow()
        self.logger.debug(f"axis_di_idx: {axis_di_idx}")
        if axis_di_idx < 0:
            self.logger.debug("please select a di")
        else:
            currAxisIdx = self.display_axis_ui.currentRow()
            self.logger.debug(f"currAxisIdx: {currAxisIdx}")
            self.logger.debug(f"axis: {self.axis[currAxisIdx]}")
            currAxis = self.prefixName + ":AXIS:0" + str(currAxisIdx + 1)
            detectableDi = (
                currAxis + ":SelG:DI:" + ("0" + str(int(axis_di_idx) + 1)) + ":Id_RBV"
            )
            self.logger.debug(f"link to check: {detectableDi}")
            DI_hardware = epics.caget(detectableDi, as_string=True)
            if DI_hardware == 0:
                DI_hardware = None
            self.logger.debug(f"DI_hardware: {DI_hardware}")
            self.logger.debug("searching for DI hardware channel")
            """
            detect DI hardware, here this is any slice
            the next thing that needs to happen is parse by slice type and check mains and sub-mains
            ie.
            ID = 1429 -> 16 main -> 1 submain
            ID = 7062 -> 2 mains -> 2 submains per main
            ID = 7047 -> 1 main -> 1 submain
            """
            for i in range(0, self.digital_input_hardware_ui.count()):
                if DI_hardware == self.digital_input_hardware_ui.item(i).text():
                    # self.logger.debug(f"currItem: {self.digital_input_hardware.item(i).text()}")
                    self.logger.debug(
                        f"found hardware: {self.digital_input_hardware_ui.item(i).text()}"
                    )
                    self.digital_input_hardware_ui.setCurrentRow(i)
                    break
                elif DI_hardware == None:
                    self.logger.debug("no hardware detected")
                    self.digital_input_hardware_ui.setCurrentRow(0)
                else:
                    self.logger.debug("something went wrong/thinking")

            self.logger.debug("searching for DI hardware channel slot")
            di_chan_slot = (
                currAxis + ":SelG:DI:" + ("0" + str(int(axis_di_idx) + 1)) + ":MAIN_RBV"
            )
            DI_hardware_Channel_Slots = epics.caget(di_chan_slot, as_string=True)
            self.logger.debug(
                f"DI_hardware_Channel Slot: {int(DI_hardware_Channel_Slots)}"
            )

            for i in range(0, self.digital_input_channel_slot_ui.count()):
                if (
                    DI_hardware_Channel_Slots
                    == self.digital_input_channel_slot_ui.item(i).text()
                ):
                    self.logger.debug(
                        f"found channel main: {self.digital_input_channel_slot_ui.item(i).text()}"
                    )
                    self.digital_input_channel_slot_ui.setCurrentRow(i)
                elif DI_hardware_Channel_Slots == "0":
                    self.logger.debug("something went wrong, should not be possible")
                    self.digital_input_channel_slot_ui.selectionMode(
                        QAbstractItemView.NoSelection
                    )

            self.logger.debug("searching for DI hardware channel")
            di_chan = (
                currAxis + ":SelG:DI:" + ("0" + str(int(axis_di_idx) + 1)) + ":SUB_RBV"
            )
            DI_hardware_Channel = epics.caget(di_chan, as_string=True)
            self.logger.debug(f"DI_hardware_Channel: {int(DI_hardware_Channel)}")

            for i in range(0, self.digital_input_channels_ui.count()):
                if DI_hardware_Channel == self.digital_input_channels_ui.item(i).text():
                    self.logger.debug(
                        f"found channel sub: {self.digital_input_channels_ui.item(i).text()}"
                    )
                    self.digital_input_channels_ui.setCurrentRow(i)
                elif DI_hardware_Channel == "0":
                    self.logger.debug("something went wrong, should not be possible")
                    self.digital_input_channels_ui.selectionMode(
                        QAbstractItemView.NoSelection
                    )

    def load_di_ui(self):
        """
        comes from WCIB
        needs to publish, and call discover_di_channel
        """
        self.logger.info(f"in load_di_ui")
        self.digital_input_hardware_ui.clear()
        self.digital_input_hardware_ui.addItem("None")
        # self.digital_inputs = identify_inputs(
        #     self.pvList, self.axis_list.currentItem().text()
        # )

        replaced_items = []
        for item in self.digital_inputs_ui:
            self.logger.debug(f"item: {item}")
            replaced_items.append(item.replace("WCIB_RBV", "Id_RBV"))

        val = epics.caget_many(replaced_items, as_string=True)
        self.digital_inputs_ui[:] = val[0:]
        self.digital_input_hardware_ui.addItems(self.digital_inputs_ui)
        if self.digital_input_hardware_ui.isEnabled():
            self.digital_input_hardware_ui.setEnabled(False)

    def load_di_channel_ui(self):
        """
        Load digital input channel UI based on the selected hardware.
        """
        self.logger.info(f"in load di_channel_ui")
        self.digital_input_channels_ui.clear()
        self.digital_input_channel_slot_ui.clear()
        current_item = self.digital_input_hardware_ui.currentItem()
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
        currAxisIdx = self.display_axis_ui.currentRow()
        axis_di_idx = self.digital_input_axis_ui.currentRow()
        currAxis = self.prefixName + ":AXIS:0" + str(currAxisIdx + 1)
        # will change this to use the number of channels pv
        if currDI.startswith("EL7062"):
            for i in range(0, int(2)):
                self.digital_input_channel_slot_ui.addItem(str(i + 1))
            for i in range(0, int(2)):
                self.digital_input_channels_ui.addItem(str(i + 1))
        elif currDI.startswith("EL1429"):
            di_chan = (
                currAxis + ":SelG:DI:" + ("0" + str(int(axis_di_idx) + 1)) + ":SUB_RBV"
            )
            self.di_size = epics.caget(di_chan)
            for i in range(0, int(16)):
                self.digital_input_channel_slot_ui.addItem(str(i + 1))
            for i in range(0, int(1)):
                self.digital_input_channels_ui.addItem(str(i + 1))
        else:
            self.logger.debug("Slice Unknown")

        if self.digital_input_channels_ui.isEnabled():
            self.digital_input_channels_ui.setEnabled(False)

        if self.digital_input_channel_slot_ui.isEnabled():
            self.digital_input_channel_slot_ui.setEnabled(False)

    def load_drives_ui(self):
        """
        Load drives UI elements from the PV data.
        """
        # update enum with drives pulled from .db file
        self.logger.info(f"in populate drives_ui")
        self.display_drives_ui.clear()
        replaced_items = []
        for item in self.drives_ui[1:]:
            self.logger.debug(f"drives: {item}")
            replaced_items.append(item.replace("WCIB_RBV", "Id_RBV"))

        val = epics.caget_many(replaced_items, as_string=True)
        self.drives_ui[1:] = val[0:]
        self.display_drives_ui.addItems(self.drives_ui)

        if self.display_drives_ui.isEnabled():
            self.display_drives_ui.setEnabled(False)

    def load_drives_channel_ui(self):
        """
        Load drives channel UI based on the selected drive.
        """
        self.logger.info(f"in load_drives_channel_ui")
        self.display_drives_channel_ui.clear()
        if "7062" in self.display_drives_ui.currentItem().text():
            for i in range(0, 2):
                self.display_drives_channel_ui.addItem(str(i + 1))
        if self.display_drives_channel_ui.isEnabled():
            self.display_drives_channel_ui.setEnabled(False)

    def load_encoders_channel_ui(self):
        """
        Load encoders channel UI based on the selected encoder.
        """
        self.logger.info(f"in load_encoders_channel_ui")

        # This needs to be replaced with with the pv for the selected encoder
        self.display_encoders_channel_ui.clear()
        if "7062" in self.display_encoders_ui.currentItem().text():
            for i in range(0, 2):
                self.display_encoders_channel_ui.addItem(str(i + 1))
        elif "5042" in self.display_encoders_ui.currentItem().text():
            for i in range(0, 2):
                self.display_encoders_channel_ui.addItem(str(i + 1))

        if self.display_encoders_channel_ui.isEnabled():
            self.display_encoders_channel_ui.setEnabled(False)

    def discover_di_channel_ui(self):
        """
        comes from load_di
        ---
        find out number of DIs
        """
        self.logger.info(f"in load_di channel_ui")

        for pv in self.pvDict:
            if pv.endswith("NUMDI_RBV"):
                # self.logger.debug(f"pv: {pv}")
                self.loaded_di_channels_ui.append(pv)

    def publish_axis_ui(self):
        """
        Publish axis UI elements from the PV data.
        """
        # update enum with axis pulled from .db file
        self.logger.info(f"in populate axis_ui")
        self.display_axis_ui.clear()
        for item in self.axis:
            self.logger.debug(f"axis: {item}")
        self.display_axis_ui.addItems(self.axis)
        if not self.display_axis_ui.isEnabled():
            self.display_axis_ui.setEnabled(True)

    def load_encoders_ui(self):
        """
        Load encoders UI elements from the PV data, replave the WCIB with Id_RBV
        """
        # update enum with drives pulled from .db file
        self.logger.info(f"in populate enc_ui")
        self.display_encoders_ui.clear()
        replaced_items = []
        for item in self.encoders_ui[1:]:
            self.logger.debug(f"drives: {item}")
            replaced_items.append(item.replace("WCIB_RBV", "Id_RBV"))
        val = epics.caget_many(replaced_items, as_string=True)
        self.encoders_ui[1:] = val[0:]
        self.display_encoders_ui.addItems(self.encoders_ui)
        if self.display_encoders_ui.isEnabled():
            self.display_encoders_ui.setEnabled(False)

    def open_stage_settings(self):
        axis_item = self.display_axis_ui.currentRow()
        print(f"axis item: {axis_item}, {type(axis_item)}")
        if axis_item == -1:
            self.msg.setIcon(QMessageBox.Warning)
            self.msg.setText("Please select an axis!")
            self.msg.setWindowTitle("Warning")
            self.msg.exec_()
        else:
            currAxisFormatted = f"{self.prefixName}:MMS:{axis_item+1:02}"
            print("Saving settings for axis: %s", currAxisFormatted)
            currConfig = self.stage_configs_widget.currentText()
            print(f"Current Config: {currConfig}")

            stageSettings = StageSettings(
                user_input_widget=self, parent=self, logger=self.logger
            )
            stageSettings.current_axis_line_edit.setText(currAxisFormatted)
            stageSettings.current_collection.setText(currConfig or "")
            stageSettings.exec_()

    def refresh_collections(self):
        self.logger.info(f"in refresh_collections")
        self.populate_collections()
