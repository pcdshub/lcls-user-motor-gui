import re
import time
from pathlib import Path

import epics
from pcdsutils.qt.designer_display import DesignerDisplay
from pydm.widgets.label import PyDMLabel
from PyQt5 import QtCore
from PyQt5.QtGui import QColor
from qtpy.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QCompleter,
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
from ..save_and_restore import config_from_file
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
from .stage_settings import StageSettings


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
        self.loaded_config_path = "/reg/g/pcds/pyps/apps/user_motor_gui/stage_configs"
        self.loaded_configs = {}
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
        # self.refresh_list.clicked.connect(self.refresh_collections)

    def load_configs(self):
        self.logger.info(f"in load_configs")
        self.stage_configs_widget.clear_items()
        self.loaded_configs.clear()

        config_dir = Path(self.loaded_config_path)
        if not config_dir.is_dir():
            self.logger.warning(f"config directory does not exist: {config_dir}")
            self.stage_configs_widget.setEnabled(False)
            return

        for config_path in sorted(config_dir.glob("*.toml")):
            try:
                config = config_from_file(str(config_path))
            except Exception as ex:
                self.logger.error(f"failed to load config {config_path}: {ex}")
                continue

            self.loaded_configs[config.name] = config
            self.stage_configs_widget.add_item(config.name, allow_duplicates=False)

        self.stage_configs_widget.setEnabled(bool(self.loaded_configs))

    def is_fixed_readonly(self, pvname: str, timeout: float = 10.0) -> bool:
        try:
            self.logger.debug(f"checking access of the pv: {pvname}")
            pv = epics.PV(pvname, auto_monitor=False)
            if pv.wait_for_connection(timeout=timeout):
                self.logger.debug(f"connected to pv, {pv.get(as_string=True)}{pvname}")
                return pv.get(as_string=True) == "FIXED_READONLY"
        except Exception as e:
            self.logger.error(f"Error checking access for {pvname}: {e}")

        self.logger.info(f"{pvname} did not connect")
        return False

    # def populate_collections(self, search_term="", attr="title"):
    #     """
    #     Populate the stage configs widget with collections matching ``search_term``.

    #     Parameters
    #     ----------
    #     search_term : str, optional
    #         Value to fuzzy-match against ``attr``. An empty string returns all
    #         collections. Defaults to "".
    #     attr : str, optional
    #         The collection attribute to search against (e.g. "title", "tags").
    #         Defaults to "title".
    #     """
    #     self.stage_configs_widget.clear_items()
    #     # self.stage_configs_widget.add_item("None")
    #     client = Client.from_config(self.cfg_path)
    #     results = list(
    #         client.search(
    #             SearchTerm(attr, "like", search_term),
    #             SearchTerm("entry_type", "eq", Collection),
    #         )
    #     )
    #     if results:
    #         for coll in results:
    #             self.stage_configs_widget.add_item(coll.title)
    #             print(coll.uuid, coll.title)
    #         self.stage_configs_widget.setEnabled(True)
    #     else:
    #         print(f"No collection matching {attr} ~ '{search_term}' found.")
    #         self.stage_configs_widget.setEnabled(False)

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
        """Open and prepopulate the stage settings dialog from current UI state."""
        stageSettings = StageSettings(
            user_input_widget=self, parent=self, logger=self.logger
        )

        stageSettings.exec_()

    # def refresh_collections(self):
    #     """Refresh collection list in the main stage configuration widget."""
    #     self.logger.info(f"in refresh_collections")
    #     self.populate_collections()
