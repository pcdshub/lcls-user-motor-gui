import re
from pathlib import Path

from pcdsutils.qt.designer_display import DesignerDisplay
from qtpy.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QGroupBox,
    QMessageBox,
    QPushButton,
)
from qtpy.uic import loadUi

from ..save_and_restore import (
    PVConfig,
    ValueConfig,
    config_to_file,
    get_live_config,
    put_live_config,
)
from .filtered_list import FilteredListWidget


class StageSettings(DesignerDisplay, QDialog):
    filename = "config_editor.ui"
    ui_dir = Path(__file__).parent / "./../ui"

    stage_list: QGroupBox
    comboBox_config_to_axis: QComboBox
    apply_config: QPushButton
    save_to_file: QPushButton
    axis_dropdown: QComboBox
    comboBox_convert_to_template: QComboBox
    pushButton_convert_to_template: QPushButton
    comboBox_existing_configs: QComboBox
    checkBox_use_existing_config: QCheckBox

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
        self.pushButton_convert_to_template.clicked.connect(
            self.convert_axis_to_template
        )
        self.checkBox_use_existing_config.toggled.connect(
            self.update_existing_config_selector
        )
        self.update_existing_config_selector(
            self.checkBox_use_existing_config.isChecked()
        )

    def convert_axis_to_template(self):
        """Convert an axis or loaded value config into a reusable template file."""
        self.logger.debug("in convert_axis_to_template")
        axis_index = self.comboBox_convert_to_template.currentIndex()
        if axis_index < 0:
            QMessageBox.warning(self, "No axis selected", "Select an axis first.")
            return

        axis_name = self.comboBox_convert_to_template.currentText()
        axis_prefix = f"{self.user_input_widget.prefixName}:MMS:{axis_index + 1:02}:NC:"
        if self.checkBox_use_existing_config.isChecked():
            config_name = self.comboBox_existing_configs.currentText()
            if not config_name:
                QMessageBox.warning(
                    self, "No config selected", "Select an existing config first."
                )
                return

            config = self.user_input_widget.loaded_configs.get(config_name)
            if not isinstance(config, ValueConfig):
                QMessageBox.warning(
                    self,
                    "Invalid config",
                    "Selected config does not include saved values.",
                )
                return

            source_axis_prefix = self.get_config_axis_prefix(config)
            if (
                source_axis_prefix is not None
                and "axis_prefix" not in config.get_macros()
            ):
                config = config.configure_macros({"axis_prefix": source_axis_prefix})
            template_name = f"{config.name} Template"
            template_desc = config.desc
            default_filename = (
                Path(self.user_input_widget.loaded_config_path)
                / f"{Path(config_name).stem}_template.toml"
            )
        else:
            config = self.get_axis_pv_config(axis_index, axis_name, axis_prefix)
            if config is None:
                return
            template_name = f"{axis_name} Template"
            template_desc = config.desc
            default_filename = (
                Path(self.user_input_widget.loaded_config_path)
                / f"{axis_name}_template.toml"
            )

        dialog = QDialog(self)
        loadUi(str(self.ui_dir / "template_config.ui"), dialog)
        dialog.label_axis_value.setText(axis_name)
        dialog.lineEdit_template_name.setText(template_name)
        dialog.lineEdit_template_desc.setText(template_desc)
        dialog.lineEdit_template_file.setText(str(default_filename))

        def select_template_file():
            filename, _ = QFileDialog.getSaveFileName(
                dialog,
                "Save Template Config",
                dialog.lineEdit_template_file.text(),
                "TOML files (*.toml)",
            )
            if filename:
                dialog.lineEdit_template_file.setText(filename)

        dialog.pushButton_browse_template_file.clicked.connect(select_template_file)
        if dialog.exec_() != QDialog.Accepted:
            return

        filename = dialog.lineEdit_template_file.text().strip()
        if not filename:
            QMessageBox.warning(self, "No file selected", "Select a template file.")
            return

        template_config = get_live_config(
            config,
            macros={"axis_prefix": axis_prefix},
            as_template=True,
        )
        template_config.name = dialog.lineEdit_template_name.text().strip()
        template_config.desc = dialog.lineEdit_template_desc.text().strip()
        template_config.metadata = {
            **template_config.metadata,
            "axis": axis_name,
            "axis_index": axis_index + 1,
        }

        config_to_file(filename, template_config)
        self.user_input_widget.load_configs()
        self.load_configs_from_user_input()

    def get_axis_pv_config(
        self, axis_index: int, axis_name: str, axis_prefix: str
    ) -> PVConfig | None:
        """Build a PV-only config for writable NC parameters on one axis."""
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

            data.append((f"{nc_pv}:Goal", f"{nc_pv}:Val_RBV"))

        if not data:
            QMessageBox.warning(
                self,
                "No writable NC parameters",
                "No non fixed read-only NC parameters found for this axis.",
            )
            return None

        return PVConfig(
            name=axis_name,
            desc=f"Writable NC parameters for {axis_name}",
            schema_ver=0,
            metadata={"axis": axis_name, "axis_index": axis_index + 1},
            data=data,
        )

    def update_existing_config_selector(self, checked: bool):
        """Enable and populate the existing config selector when requested."""
        self.comboBox_existing_configs.clear()
        if checked:
            self.comboBox_existing_configs.addItems(
                self.user_input_widget.stage_configs_widget.all_items()
            )
        self.comboBox_existing_configs.setEnabled(checked)

    def apply_config_to_axis(self):
        """Apply the selected value config to the selected target axis."""
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
        """Return the source axis NC PV prefix stored in or inferred from a config."""
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
        """Save live writable NC values for the selected axis to a TOML config."""
        axis_index = self.axis_dropdown.currentIndex()
        if axis_index < 0:
            QMessageBox.warning(self, "No axis selected", "Select an axis first.")
            return

        axis_name = self.user_input_widget.axis[axis_index]
        axis_prefix = f"{self.user_input_widget.prefixName}:MMS:{axis_index + 1:02}:NC:"
        pv_config = self.get_axis_pv_config(axis_index, axis_name, axis_prefix)
        if pv_config is None:
            return

        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Save Axis Config",
            str(Path(self.user_input_widget.loaded_config_path) / f"{axis_name}.toml"),
            "TOML files (*.toml)",
        )
        if not filename:
            return

        config = get_live_config(
            pv_config,
            macros={"axis_prefix": axis_prefix},
            as_template=True,
        )
        config_to_file(filename, config)
        self.user_input_widget.load_configs()
        self.load_configs_from_user_input()

    def load_configs_from_user_input(self):
        """Refresh config and axis selectors from the owning user input widget."""
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
        self.comboBox_convert_to_template.clear()
        self.comboBox_convert_to_template.addItems(axis_items)
        self.comboBox_convert_to_template.setCurrentIndex(0)
        self.update_existing_config_selector(
            self.checkBox_use_existing_config.isChecked()
        )

    def auth_file(self) -> str:
        """
        A template for the location of the iocmanager.auth config file.

        This file contains usernames, one per line, of users who are authorized
        to make changes to the hutch's main iocmanager.cfg configuration file
        (see attr:`CONFIG_FILE`).

        To complete this template, the %s must be replaced with the 3-letter hutch name.
        """
        return "/cds/group/pcds/pyps/apps/user_motor_gui/config/user_motor_gui.auth"

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
        with open(self.auth_file()) as fd:
            lines = fd.readlines()
        lines = [ln.strip() for ln in lines]
        for ln in lines:
            if ln == user:
                return True
        return False
