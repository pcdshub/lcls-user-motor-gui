import re
from functools import partial
from os import path
from pathlib import Path

import epics
from pcdsutils.qt.designer_display import DesignerDisplay
from pydm.widgets.display_format import DisplayFormat
from pydm.widgets.enum_combo_box import PyDMEnumComboBox
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

from .filtered_list import FilteredListWidget
from .normalize import (
    hardware_prefix_for_coe,
    normalize_hardware_channel,
    normalize_hardware_id,
    remove_name_rbv,
)


class ReadOnlyEnumLineEdit(PyDMLineEdit):
    def __init__(self, parent=None, init_channel=None):
        """Initialize a read-only enum line edit.

        Parameters
        ----------
        parent : QWidget, optional
            Parent widget.
        init_channel : str, optional
            Initial PyDM channel.
        """
        super().__init__(parent=parent, init_channel=init_channel)
        self.displayFormat = DisplayFormat.String
        self.setReadOnly(True)

    def enum_strings_changed(self, new_enum_strings):
        """Update stored enum strings and refresh the displayed value.

        Parameters
        ----------
        new_enum_strings : tuple[str, ...]
            Enum strings from the connected channel.
        """
        super().enum_strings_changed(new_enum_strings)
        self._display_enum_string()

    def value_changed(self, new_val):
        """Update the line edit when the channel value changes.

        Parameters
        ----------
        new_val : object
            New value from the connected channel.
        """
        super().value_changed(new_val)
        self._display_enum_string()

    def _display_enum_string(self):
        """Display the enum string for the current enum index value."""
        if self.value is None or not self.enum_strings:
            return

        if isinstance(self.value, int) and not isinstance(self.value, bool):
            if 0 <= self.value < len(self.enum_strings):
                value = self.enum_strings[self.value]
            else:
                return
        else:
            value = self.value

        self._has_displayed_value_yet = True
        self._display = str(value)
        self.setText(self._display)


class ExpertWindow(DesignerDisplay, QWidget):
    filename = "expert_tab.ui"
    ui_dir = Path(__file__).parent / "./../ui"

    # Expert Tab
    expert_axis: QComboBox
    param_tab: QTabWidget
    expert_nc_filter: QGroupBox
    expert_drive_filter: QGroupBox
    expert_encoder_filter: QGroupBox

    expert_nc_filter_list: QGroupBox
    expert_coe_drive_filter_list: QGroupBox
    expert_coe_encoder_filter_list: QGroupBox
    # nc_groupbox: QGroupBox

    def __init__(self, main_window, parent=None, logger=None):
        """Initialize the expert tab.

        Parameters
        ----------
        main_window : QWidget
            Main window instance.
        parent : QWidget, optional
            Parent widget.
        logger : logging.Logger, optional
            Logger instance.
        """
        super().__init__(parent)
        self.logger = logger
        self.main_window = main_window
        self.axis = []
        self.prefixName = ""
        self.nc_list = []
        self.coe_drive_list = []
        self.coe_encoder_list = []
        self.pvDict = []

        # Expert Tab -> NC Tab
        self.expert_nc_widget = FilteredListWidget(self.expert_nc_filter)
        self.expert_nc_filter.layout().addWidget(self.expert_nc_widget)

        # # Drive Tab -> NC Tab
        self.expert_drive_widget = FilteredListWidget(self.expert_drive_filter)
        self.expert_drive_filter.layout().addWidget(self.expert_drive_widget)

        # # Encoder Tab -> NC Tab
        self.expert_encoder_widget = FilteredListWidget(self.expert_encoder_filter)
        self.expert_encoder_filter.layout().addWidget(self.expert_encoder_widget)

        # Setting up widget signals
        for slot in [
            self.expert_update_nc,
            self.expert_update_drive,
            self.expert_update_encoder,
        ]:
            self.expert_axis.currentIndexChanged.connect(slot)

        self.expert_nc_widget.currentIndexChanged.connect(self.highlight_nc_param)
        self.expert_drive_widget.currentIndexChanged.connect(
            self.highlight_coe_drive_param
        )
        self.expert_encoder_widget.currentIndexChanged.connect(
            self.highlight_coe_encoder_param
        )

    def filter_expert_nc_filter(self, text):
        """Filter items in the expert NC filter list.

        This method iterates through all items in the expert_nc_filter widget and hides
        those that do not contain the filter text (case-insensitive).

        Parameters
        ----------
        text : str
            Filter text to search for in item text.
        """
        self.logger.info("in filter_expert_nc_filter")
        for i in range(self.expert_nc_filter.count()):
            item = self.expert_nc_filter.item(i)
            item.setHidden(text.lower() not in item.text().lower())

    def publish_axis_expert(self):
        """
        Populate the expert axis combo box with available axes.

        Clears the current items in the expert_axis combo box and adds all axes
        from the self.axis list.
        """
        # update enum with axis pulled from .db file
        self.logger.info(f"in populate axis_expert")
        self.expert_axis.clear()
        axis_list = self.axis
        for item in axis_list:
            self.expert_axis.addItem(item)

        if not self.expert_axis.isEnabled():
            self.expert_axis.setEnabled(True)
        self.logger.debug(f"caput to: self.axis_selection")

    def expert_update_nc(self):
        """
        Update the expert NC filter with parameters for the currently selected axis.

        Filters NC parameters from self.nc_list that match the current axis pattern,
        fetches their values with caget, and populates the NC filter widget.
        """
        self.logger.info("in expert_update_nc")

        axis_index = self.expert_axis.currentIndex()
        self.logger.debug(f"axis: {axis_index}")
        axis = f"{self.prefixName}:MMS:{(axis_index+1):02}:NC:"
        c_nc_p = axis + "[^:]+:Name_RBV"
        self.logger.debug(f"nc p regex: {c_nc_p}")
        stripped_nc = []
        for pv in self.nc_list:
            # self.logger.debug(f"nc pv: {pv}")
            if re.search(c_nc_p, pv):
                stripped_nc.append(pv.strip())

        self.logger.debug(f"len of nc param list: {len(stripped_nc)}")
        # Clear previous items
        self.expert_nc_widget.clear_items()

        self.ca_nc_list = epics.caget_many(stripped_nc, as_string=True)
        self.logger.info(f"items size after caget: {len(self.ca_nc_list)}")

        # Add items (filter out None just in case)
        items = [item for item in self.ca_nc_list if item]
        self.expert_nc_widget.add_items(items)

        # Enable widget if needed
        self.expert_nc_widget.setEnabled(True)

        # Dynamically add param widgets
        self.add_param_widgets(stripped_nc, self.expert_nc_filter_list)

    def expert_update_drive(self, axis):
        """Update the expert drive filter for the selected axis.

        Retrieves the hardware ID for the selected axis, filters COE drive parameters,
        fetches their values with caget, and populates the drive filter widget.
        Also adds parameter widgets for each drive parameter.

        Parameters
        ----------
        axis : int
            Unused signal argument. The current axis is read from ``expert_axis``.
        """
        self.logger.info(f"in expert_update_drive")

        # Get current axis
        axis_index = self.expert_axis.currentIndex()
        axis_prefix = f"{self.prefixName}:AXIS:{(axis_index+1):02}:SelG:DRV"
        drive_string = f"{axis_prefix}:Id_RBV"
        drive_channel_string = f"{axis_prefix}:MAIN_RBV"
        self.logger.debug(f"drive_string: {drive_string}")

        # Get hardware slice
        # TST:UM:01:SelG:DRV:Id_RBV
        hardwareID = epics.caget(drive_string, as_string=True)
        hardware_channel = epics.caget(drive_channel_string, as_string=True)

        self.logger.debug(f"hardwareID before split: {hardwareID}")
        hardwareID = normalize_hardware_id(hardwareID)
        hardware_channel = normalize_hardware_channel(
            hardware_channel, f"{axis_index+1:02}"
        )

        self.logger.debug(f"hardwareID after split: {hardwareID}")
        self.logger.debug(f"hardware channel: {hardware_channel}")

        formatted_drive_string = f"{hardware_prefix_for_coe(self.prefixName, hardwareID, hardware_channel)}:COE:{hardware_channel}"
        string_drive_regex = (
            f"{re.escape(formatted_drive_string)}:(?!.*:DG:)[^:]+:Name_RBV"
        )
        self.logger.debug(f"string_drive_regex: {string_drive_regex}")
        stripped_coe = []
        self.logger.debug(f"coe len: {len(self.coe_drive_list)}")
        for pv in self.coe_drive_list:
            if re.search(string_drive_regex, pv):
                stripped_coe.append(pv.strip())
        self.logger.debug(f"coe_drive_list len: {len(self.coe_drive_list)}")

        # Clear previous items
        self.expert_drive_widget.clear_items()
        # self.coe_drive_list.clear()

        self.ca_coe_drive_list = epics.caget_many(stripped_coe, as_string=True)
        self.logger.info(f"items size: {len(self.ca_coe_drive_list)}")

        # Add items (filter out None just in case)
        items = [item for item in self.ca_coe_drive_list if item]
        self.expert_drive_widget.add_items(items)

        # Enable widget if needed
        self.expert_drive_widget.setEnabled(True)

        # # Dynamically add param widgets
        self.add_param_widgets(stripped_coe, self.expert_coe_drive_filter_list)

    def expert_update_encoder(self, axis):
        """Update the expert encoder filter for the selected axis.

        1. Retrieves the hardware ID for the selected axis
        2. Filters COE encoder parameters,
        3. populates the encoder filter widget.
        Also adds parameter widgets for each encoder parameter.

        Parameters
        ----------
        axis : int
            Unused signal argument. The current axis is read from ``expert_axis``.
        """
        self.logger.info(f"in expert_update_encoder")

        # Get current axis and use it to find hardware ID
        axis_index = self.expert_axis.currentIndex()
        axis_prefix = f"{self.prefixName}:AXIS:{(axis_index+1):02}:SelG:ENC"
        encoder_string = f"{axis_prefix}:Id_RBV"
        encoder_channel_string = f"{axis_prefix}:MAIN_RBV"
        self.logger.debug(f"encoder_string: {encoder_string}")

        # Get hardware slice
        # TST:UM:01:SelG:DRV:Id_RBV
        hardwareID = epics.caget(encoder_string, as_string=True)
        hardware_channel = epics.caget(encoder_channel_string, as_string=True)

        hardwareID = normalize_hardware_id(hardwareID)
        hardware_channel = normalize_hardware_channel(
            hardware_channel, f"{axis_index+1:02}"
        )

        self.logger.debug(f"hardware ID after normalize: {hardwareID}")
        self.logger.debug(f"hardware Chan after normalize: {hardware_channel}")

        formatted_drive_string = f"{hardware_prefix_for_coe(self.prefixName, hardwareID, hardware_channel)}:COE:{hardware_channel}"
        self.logger.debug(f"formatted_drive_string: {formatted_drive_string}")
        # Filter out COE DG PVs
        string_enc_regex = (
            f"{re.escape(formatted_drive_string)}:(?!.*:DG:)[^:]+:Name_RBV"
        )
        self.logger.debug(f"string_enc_regex: {string_enc_regex}")

        stripped_coe = []
        self.logger.debug(f"coe len with DG: {len(self.coe_encoder_list)}")
        for pv in self.coe_encoder_list:
            # self.logger.debug(f"pv: {pv}")
            if re.search(string_enc_regex, pv):
                stripped_coe.append(pv.strip())

        # Clear previous items
        self.expert_encoder_widget.clear_items()

        self.ca_coe_encoder_list = epics.caget_many(stripped_coe, as_string=True)
        self.logger.info(f"coe len w/o DG: {len(self.ca_coe_encoder_list)}")

        # Add items (filter out None just in case)
        items = [item for item in self.ca_coe_encoder_list if item]
        self.expert_encoder_widget.add_items(items)

        # Enable widget if needed
        self.expert_encoder_widget.setEnabled(True)

        # # Dynamically add param widgets
        self.add_param_widgets(stripped_coe, self.expert_coe_encoder_filter_list)

    def configure_param_widgets(self, widget: QWidget, nc_pv: str):
        """Configure a parameter widget with PyDM channels for a base PV.

        Parameters
        ----------
        widget : QWidget
            Parameter UI widget loaded from ``param.ui``.
        nc_pv : str
            Base NC parameter PV without a field suffix.
        """
        pv_map = {
            "pv_name": f"{nc_pv}:Name_RBV",
            "pv_goal": f"{nc_pv}:Goal",
            "pv_rbv": f"{nc_pv}:Val_RBV",
            "pv_units": f"{nc_pv}:EU_RBV",
        }

        def configure_channel(
            pvname: str, pydm_widget, timeout: float = 1.0
        ) -> tuple[str, str]:
            """Return a PyDM CA channel and configure string display.

            Parameters
            ----------
            pvname : str
                EPICS PV name without the ``ca://`` prefix.
            pydm_widget : QWidget
                PyDM widget to configure.
            timeout : float, optional
                Maximum connection wait time in seconds.

            Returns
            -------
            channel : str
                PyDM Channel Access channel string.
            pv_type : str
                Lowercase EPICS PV type string, if available.
            """
            pvt = ""
            try:
                pv = epics.PV(pvname, auto_monitor=False)
                # self.logger.debug(f"Created PV object for {pvname}")

                if pv.wait_for_connection(timeout=timeout):
                    pvt = (pv.type or "").lower()
                    self.logger.debug(f"PV {pvname} type: {pvt}")
                    if (("enum" in pvt) or ("char" in pvt)) and hasattr(
                        pydm_widget, "displayFormat"
                    ):
                        pydm_widget.displayFormat = DisplayFormat.String
                    elif hasattr(pydm_widget, "precision") and hasattr(
                        pydm_widget, "precisionFromPV"
                    ):
                        ctrlvars = pv.get_ctrlvars() or {}
                        pv_precision = ctrlvars.get("precision")
                        if pv_precision is not None:
                            display_precision = max(0, int(pv_precision))
                            pydm_widget.precisionFromPV = False
                            pydm_widget.precision = display_precision
                            self.logger.debug(
                                "Set %s display precision to %d",
                                pvname,
                                display_precision,
                            )
                else:
                    self.logger.warning(f"PV connection timeout for {pvname}")
            except Exception as e:
                self.logger.error(f"Error configuring channel for {pvname}: {e}")

            return f"ca://{pvname}", pvt

        def replace_pydm_widget(pydm_widget, widget_cls):
            """Replace a PyDM widget with another widget class.

            Parameters
            ----------
            pydm_widget : QWidget
                Existing widget to replace in its parent layout.
            widget_cls : type[QWidget]
                Replacement widget class.

            Returns
            -------
            QWidget
                Replacement widget instance.
            """
            parent = pydm_widget.parentWidget()
            layout = parent.layout()
            replacement = widget_cls(parent)
            replacement.setObjectName(pydm_widget.objectName())
            replacement.setSizePolicy(pydm_widget.sizePolicy())
            replacement.setMinimumSize(pydm_widget.minimumSize())
            replacement.setToolTip(pydm_widget.toolTip())
            layout.replaceWidget(pydm_widget, replacement)
            pydm_widget.setParent(None)
            pydm_widget.deleteLater()
            return replacement

        def replace_with_enum_combo(pydm_widget):
            """Replace a PyDM widget with an enum combo box.

            Parameters
            ----------
            pydm_widget : QWidget
                Existing widget to replace.

            Returns
            -------
            PyDMEnumComboBox
                Replacement enum combo box.
            """
            return replace_pydm_widget(pydm_widget, PyDMEnumComboBox)

        def replace_with_read_only_line_edit(pydm_widget):
            """Replace a PyDM widget with a read-only enum line edit.

            Parameters
            ----------
            pydm_widget : QWidget
                Existing widget to replace.

            Returns
            -------
            ReadOnlyEnumLineEdit
                Replacement read-only enum line edit.
            """
            return replace_pydm_widget(pydm_widget, ReadOnlyEnumLineEdit)

        def is_fixed_readonly(pvname: str, timeout: float = 10.0) -> bool:
            """Return whether an access PV reports FIXED_READONLY.

            Parameters
            ----------
            pvname : str
                Access PV name.
            timeout : float, optional
                Maximum connection wait time in seconds.

            Returns
            -------
            bool
                True if the access PV is connected and reports FIXED_READONLY.
            """
            try:
                pv = epics.PV(pvname, auto_monitor=False)
                if pv.wait_for_connection(timeout=timeout):
                    return pv.get(as_string=True) == "FIXED_READONLY"
            except Exception as e:
                self.logger.error(f"Error checking access for {pvname}: {e}")
            # safest default: treat as not fixed-read-only
            self.logger.info(f"{pvname} did not connect")
            return False

        # Access PyDM widgets directly from ParamWidget attributes
        name = widget.pv_name
        goal = widget.pv_goal
        goal_label = widget.findChild(QLabel, "label")
        rbv = widget.pv_rbv
        units = widget.pv_units

        # Set channels using the channel property
        channel_str, _ = configure_channel(pv_map["pv_name"], name)
        name.channel = channel_str
        # self.logger.debug(f"Set pv_name channel to {channel_str}")

        fixed_readonly = is_fixed_readonly(f"{nc_pv}:Acc_RBV")

        widget.goal_visible = not fixed_readonly

        if fixed_readonly:
            if goal_label is not None:
                goal_label.setVisible(False)
            goal.setVisible(False)
        else:
            if goal_label is not None:
                goal_label.setVisible(True)
            goal.setVisible(True)
            channel_str, goal_type = configure_channel(pv_map["pv_goal"], goal)
            if "enum" in goal_type:
                goal = replace_with_enum_combo(goal)
                widget.pv_goal = goal
            goal.channel = channel_str

        channel_str, rbv_type = configure_channel(pv_map["pv_rbv"], rbv)
        if "enum" in rbv_type:
            rbv = replace_pydm_widget(rbv, ReadOnlyEnumLineEdit)
            widget.pv_rbv = rbv
        rbv.channel = channel_str
        # self.logger.debug(f"Set pv_rbv channel to {channel_str}")

        channel_str, _ = configure_channel(pv_map["pv_units"], units)
        units.channel = channel_str
        # self.logger.debug(f"Set pv_units channel to {channel_str}")

    def add_param_widgets(self, param, widget: QListWidget):
        """Add parameter widgets to a list widget.

        For each parameter in the list, loads a param.ui widget, configures it with
        EPICS channels, connects editing signals, and adds it as an item to the widget.

        Parameters
        ----------
        param : list[str]
            Parameter PV names.
        widget : QListWidget
            List widget that receives the parameter widgets.
        """
        self.logger.info("in add_param_widgets")
        widget.clear()
        self.param_widgets = []
        self.param_connections = []  # (optional, if you want a new list for every call)
        self.logger.debug(str(Path(__file__).parent))
        for i, pv in enumerate(param):
            param_widget = uic.loadUi(
                str(Path(__file__).parent / "./../ui" / "param.ui")
            )
            item = QListWidgetItem()
            pv_wo_rbv = remove_name_rbv(pv)
            self.configure_param_widgets(param_widget, pv_wo_rbv)

            # --- Find the PyDMLineEdit and connect its signals only if goal is visible ---
            pydm_line_edit = param_widget.findChild(PyDMLineEdit, "pv_goal")
            if pydm_line_edit is not None and getattr(
                param_widget, "goal_visible", False
            ):
                """to be implemented later, I want to see if the subscriptions from the widgets is sufficient"""
                # pydm_line_edit.editingFinished.connect(partial(self.check_caput, pv))
                self.param_connections.append(pydm_line_edit)

            item.setSizeHint(param_widget.sizeHint())
            widget.addItem(item)
            widget.setItemWidget(item, param_widget)

            self.param_widgets.append(param_widget)

    def highlight_nc_param(self):
        """
        Highlight the parameter widget corresponding to the currently selected NC item.

        Finds the index of the current text in the NC list, clears highlights from all
        parameter widgets, and applies a highlight style to the matching widget.
        Also scrolls the list to center the highlighted item.
        """
        self.logger.info("in highlight_nc_param")
        current_text = self.expert_nc_widget.currentText()
        # Defensive check: Make sure current_text is in the list
        if current_text in self.ca_nc_list:
            pv_index = self.ca_nc_list.index(current_text)
            self.logger.debug(f"current pv: {pv_index} ({current_text})")

            # Clear all highlights
            for i in range(self.expert_nc_filter_list.count()):
                widget = self.expert_nc_filter_list.itemWidget(
                    self.expert_nc_filter_list.item(i)
                )
                if widget is not None:
                    widget.setStyleSheet("")  # Remove highlight

            # Highlight the desired item
            item = self.expert_nc_filter_list.item(pv_index)
            widget = self.expert_nc_filter_list.itemWidget(item)
            if widget is not None:
                widget.setStyleSheet(
                    "border: 2px solid #22AAFF; border-radius: 4px; background: #e8f6fd;"
                )
            # Scroll to this item
            if item is not None:
                self.expert_nc_filter_list.scrollToItem(
                    item, QAbstractItemView.PositionAtCenter
                )
            else:
                self.logger.debug("Current filter text not found in ca_nc_list!")

    def highlight_coe_drive_param(self):
        """
        Highlight the parameter widget corresponding to the currently selected COE drive item.

        Finds the index of the current text in the COE drive list, clears highlights from all
        parameter widgets, and applies a highlight style to the matching widget.
        Also scrolls the list to center the highlighted item.
        """
        self.logger.info("in highlight_coe_drive_param")
        current_text = self.expert_drive_widget.currentText()
        # Defensive check: Make sure current_text is in the list
        if current_text in self.ca_coe_drive_list:
            pv_index = self.ca_coe_drive_list.index(current_text)
            self.logger.debug(f"current pv: {pv_index} ({current_text})")

            # Clear all highlights
            for i in range(self.expert_coe_drive_filter_list.count()):
                widget = self.expert_coe_drive_filter_list.itemWidget(
                    self.expert_coe_drive_filter_list.item(i)
                )
                if widget is not None:
                    widget.setStyleSheet("")  # Remove highlight

            # Highlight the desired item
            item = self.expert_coe_drive_filter_list.item(pv_index)
            widget = self.expert_coe_drive_filter_list.itemWidget(item)
            if widget is not None:
                widget.setStyleSheet(
                    "border: 2px solid #22AAFF; border-radius: 4px; background: #e8f6fd;"
                )
            # Scroll to this item
            if item is not None:
                self.expert_coe_drive_filter_list.scrollToItem(
                    item, QAbstractItemView.PositionAtCenter
                )

            # (Optional: also select the item in the list view)
            else:
                self.logger.debug("Current filter text not found in ca_coe_drive_list!")

    def highlight_coe_encoder_param(self):
        """
        Highlight the parameter widget corresponding to the currently selected COE encoder item.

        Finds the index of the current text in the COE encoder list, clears highlights from all
        parameter widgets, and applies a highlight style to the matching widget.
        Also scrolls the list to center the highlighted item.
        """
        self.logger.info("in highlight_coe_encoder_param")
        current_text = self.expert_encoder_widget.currentText()
        # Defensive check: Make sure current_text is in the list
        if current_text in self.ca_coe_encoder_list:
            pv_index = self.ca_coe_encoder_list.index(current_text)
            self.logger.debug(f"current pv: {pv_index} ({current_text})")

            # Clear all highlights
            for i in range(self.expert_coe_encoder_filter_list.count()):
                widget = self.expert_coe_encoder_filter_list.itemWidget(
                    self.expert_coe_encoder_filter_list.item(i)
                )
                if widget is not None:
                    widget.setStyleSheet("")  # Remove highlight

            # Highlight the desired item
            item = self.expert_coe_encoder_filter_list.item(pv_index)
            widget = self.expert_coe_encoder_filter_list.itemWidget(item)
            if widget is not None:
                widget.setStyleSheet(
                    "border: 2px solid #22AAFF; border-radius: 4px; background: #e8f6fd;"
                )
            # Scroll to this item
            if item is not None:
                self.expert_coe_encoder_filter_list.scrollToItem(
                    item, QAbstractItemView.PositionAtCenter
                )

            # (Optional: also select the item in the list view)
            else:
                self.logger.debug(
                    "Current filter text not found in ca_coe_encoder_list!"
                )
