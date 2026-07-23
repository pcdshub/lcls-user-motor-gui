import json
import logging
import os
import re
from getpass import getuser
from pathlib import Path
from uuid import UUID

import epics
import numpy as np
from pcdsutils.qt.designer_display import DesignerDisplay
from pydm.widgets.line_edit import PyDMLineEdit
from qtpy.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QGroupBox,
    QInputDialog,
    QMessageBox,
    QPushButton,
)
from qtpy.uic import loadUi

from ..save_and_restore import ValueConfig, config_to_file, put_live_config
from .filtered_list import FilteredListWidget

# from superscore.backends.core import SearchTerm
# from superscore.client import Client
# from superscore.errors import EntryNotFoundError
# from superscore.model import Collection, Parameter, Setpoint, Snapshot


class StageSettings(DesignerDisplay, QDialog):
    filename = "config_editor.ui"
    ui_dir = Path(__file__).parent / "./../ui"

    stage_list: QGroupBox
    comboBox_config_to_axis: QComboBox
    apply_config: QPushButton
    save_to_file: QPushButton
    axis_dropdown: QComboBox

    def __init__(self, user_input_widget, parent=None, logger=None):
        """Initialize stage settings dialog widgets and signal bindings."""
        super(StageSettings, self).__init__(parent)
        self.logger = logger
        self.user_input_widget = user_input_widget
        self.stage_list_widget = FilteredListWidget(self.stage_list)
        self.stage_list.layout().addWidget(self.stage_list_widget)
        self.load_configs_from_user_input()

        self.save_to_file.clicked.connect(self.axis_to_toml)
        self.apply_config.clicked.connect(self.apply_config_to_axis)

    def apply_config_to_axis(self):
        self.logger.info("in apply_config_to_axis")
        config_name = self.stage_list_widget.currentText()
        self.logger.debug(f"selected config: {config_name}")
        if not config_name:
            self.logger.warning("apply config requested with no config selected")
            QMessageBox.warning(self, "No config selected", "Select a config first.")
            return

        target_axis_index = self.comboBox_config_to_axis.currentIndex()
        target_axis_name = self.comboBox_config_to_axis.currentText()
        self.logger.debug(
            f"target axis index: {target_axis_index}, target axis: {target_axis_name}"
        )
        if target_axis_index < 0:
            self.logger.warning("apply config requested with no target axis selected")
            QMessageBox.warning(self, "No axis selected", "Select an axis first.")
            return

        config = self.user_input_widget.loaded_configs.get(config_name)
        self.logger.debug(f"loaded config type: {type(config)}")
        if not isinstance(config, ValueConfig):
            self.logger.warning(f"selected config is not ValueConfig: {config_name}")
            QMessageBox.warning(
                self,
                "Invalid config",
                "Selected config does not include saved values.",
            )
            return

        target_axis_prefix = (
            f"{self.user_input_widget.prefixName}:MMS:{target_axis_index + 1:02}:NC:"
        )
        source_axis_prefix = self.get_config_axis_prefix(config)
        self.logger.debug(f"source axis prefix: {source_axis_prefix}")
        self.logger.debug(f"target axis prefix: {target_axis_prefix}")
        self.logger.debug(f"config data rows: {len(config.data)}")

        macros = {"axis_prefix": target_axis_prefix}
        if source_axis_prefix is not None and "axis_prefix" not in config.get_macros():
            config = config.configure_macros({"axis_prefix": source_axis_prefix})

        applied_config = config.apply_macros(macros)
        for setpoint, readback, value in applied_config.data:
            self.logger.debug(
                f"applying config pv: setpoint={setpoint}, readback={readback}, value={value}"
            )

        self.logger.info(f"applying {len(config.data)} values to {target_axis_name}")
        put_live_config(applied_config)
        self.logger.info("finished apply_config_to_axis")

    def get_config_axis_prefix(self, config: ValueConfig) -> str | None:
        source_axis_index = config.metadata.get("axis_index")
        if source_axis_index is not None:
            return f"{self.user_input_widget.prefixName}:MMS:{int(source_axis_index):02}:NC:"
        pattern = re.compile(
            rf"{re.escape(self.user_input_widget.prefixName)}:MMS:\d{{2}}:NC:"
        )
        for tup in config.data:
            for elem in tup:
                if isinstance(elem, str):
                    match = pattern.search(elem)
                    if match is not None:
                        return match.group(0)
        return None

    def axis_to_toml(self):
        axis_index = self.axis_dropdown.currentIndex()
        if axis_index < 0:
            QMessageBox.warning(self, "No axis selected", "Select an axis first.")
            return

        axis_name = self.user_input_widget.axis[axis_index]
        axis_prefix = f"{self.user_input_widget.prefixName}:MMS:{axis_index + 1:02}:NC:"
        nc_name_pvs = [
            pv.strip()
            for pv in self.user_input_widget.ncList
            if re.search(axis_prefix + "[^:]+:Name_RBV", pv)
        ]

        data = []
        for name_pv in nc_name_pvs:
            nc_pv = name_pv.removesuffix(":Name_RBV")
            if self.user_input_widget.is_fixed_readonly(f"{nc_pv}:Acc_RBV"):
                continue

            readback_pv = f"{nc_pv}:Val_RBV"
            data.append((f"{nc_pv}:Goal", readback_pv, epics.caget(readback_pv)))

        if not data:
            QMessageBox.warning(
                self,
                "No writable NC parameters",
                "No non fixed read-only NC parameters found for this axis.",
            )
            return

        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Save Axis Config",
            str(Path(self.user_input_widget.loaded_config_path) / f"{axis_name}.toml"),
            "TOML files (*.toml)",
        )
        if not filename:
            return

        config_to_file(
            filename,
            ValueConfig(
                name=axis_name,
                desc=f"Writable NC parameters for {axis_name}",
                schema_ver=0,
                metadata={"axis": axis_name, "axis_index": axis_index + 1},
                data=data,
            ).configure_macros({"axis_prefix": axis_prefix}),
        )
        self.user_input_widget.load_configs()
        self.load_configs_from_user_input()

    # def axis_to_toml(self):
    #     axis_index = self.axis_dropdown.currentIndex()
    #     if axis_index < 0:
    #         QMessageBox.warning(self, "No axis selected", "Select an axis first.")
    #         return

    #     axis_name = self.user_input_widget.axis[axis_index]
    #     axis_prefix = f"{self.user_input_widget.prefixName}:MMS:{axis_index + 1:02}:NC:"
    #     nc_name_pvs = [
    #         pv.strip()
    #         for pv in self.user_input_widget.ncList
    #         if re.search(axis_prefix + "[^:]+:Name_RBV", pv)
    #     ]

    #     data = []
    #     for name_pv in nc_name_pvs:
    #         nc_pv = name_pv.removesuffix(":Name_RBV")
    #         if self.user_input_widget.is_fixed_readonly(f"{nc_pv}:Acc_RBV"):
    #             continue

    #         readback_pv = f"{nc_pv}:Val_RBV"
    #         data.append((f"{nc_pv}:Goal", readback_pv, epics.caget(readback_pv)))

    #     if not data:
    #         QMessageBox.warning(
    #             self,
    #             "No writable NC parameters",
    #             "No non fixed read-only NC parameters found for this axis.",
    #         )
    #         return

    #     filename, _ = QFileDialog.getSaveFileName(
    #         self,
    #         "Save Axis Config",
    #         str(Path(self.user_input_widget.loaded_config_path) / f"{axis_name}.toml"),
    #         "TOML files (*.toml)",
    #     )
    #     if not filename:
    #         return

    #     config_to_file(
    #         filename,
    #         ValueConfig(
    #             name=axis_name,
    #             desc=f"Writable NC parameters for {axis_name}",
    #             schema_ver=0,
    #             metadata={"axis": axis_name, "axis_index": axis_index + 1},
    #             data=data,
    #         ).configure_macros({"axis_prefix": axis_prefix}),
    #     )
    #     self.user_input_widget.load_configs()
    #     self.load_configs_from_user_input()

    def load_configs_from_user_input(self):
        self.stage_list_widget.clear_items()
        self.stage_list_widget.add_items(
            self.user_input_widget.stage_configs_widget.all_items()
        )
        axis_items = [
            self.user_input_widget.display_axis_ui.item(index).text()
            for index in range(self.user_input_widget.display_axis_ui.count())
        ]
        self.axis_dropdown.clear()
        self.axis_dropdown.addItems(axis_items)
        # self.axis_dropdown.setCurrentIndex(self.user_input_widget.display_axis_ui.currentRow())
        self.axis_dropdown.setCurrentIndex(0)
        self.comboBox_config_to_axis.clear()
        self.comboBox_config_to_axis.addItems(axis_items)
        self.comboBox_config_to_axis.setCurrentIndex(0)

    def AUTH_FILE(self) -> str:
        """
        A template for the location of the iocmanager.auth config file.

        This file contains usernames, one per line, of users who are authorized
        to make changes to the hutch's main iocmanager.cfg configuration file
        (see attr:`CONFIG_FILE`).

        To complete this template, the %s must be replaced with the 3-letter hutch name.
        """
        return f"/cds/group/pcds/pyps/apps/user_motor_gui/config/user_motor_gui.auth"

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
