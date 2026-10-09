import re
from pathlib import Path

import epics
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
from .normalize import normalize_hardware_channel


class StageSettings(DesignerDisplay, QDialog):
    """Dialog for saving, templating, and applying stage configuration files."""

    filename = "config_editor.ui"
    ui_dir = Path(__file__).parent / "./../ui"
    template_prefix_macro = "PREFIX"
    template_ioc_prefix_macro = "IOC_PREFIX"
    template_axis_macro = "AXIS"
    template_drive_coe_prefix_macro = "DRIVE_COE_PREFIX"
    template_encoder_coe_prefix_macro = "ENCODER_COE_PREFIX"

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
        read_live_values = False
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

            source_axis_index = self.get_config_axis_index(config, config_name)
            if source_axis_index is not None:
                config = config.apply_macros(
                    self.get_template_macros(source_axis_index)
                )
                config = self.resolve_template_coe_pvs(config, source_axis_index)
                config = self.configure_template_macros(config, source_axis_index)
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
            read_live_values = True
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

        if read_live_values:
            template_config = get_live_config(
                config, macros=self.get_template_macros(axis_index)
            )
            template_config = self.drop_unreadable_config_values(template_config)
            if template_config is None:
                return
        else:
            template_config = config
        template_config = self.configure_template_macros(template_config, axis_index)
        template_config.name = dialog.lineEdit_template_name.text().strip()
        template_config.desc = dialog.lineEdit_template_desc.text().strip()
        template_config.metadata = {
            **{
                key: value
                for key, value in template_config.metadata.items()
                if key
                not in {
                    "drive_coe_prefix",
                    "encoder_coe_prefix",
                    "is_drive_and_encoder_same",
                }
            },
            "axis": axis_name,
            "axis_index": axis_index + 1,
        }

        config_to_file(filename, template_config)
        self.user_input_widget.load_configs()
        self.load_configs_from_user_input()

    def get_axis_pv_config(
        self, axis_index: int, axis_name: str, axis_prefix: str
    ) -> PVConfig | None:
        """Build a PV-only config for writable NC and linked COE parameters."""
        nc_name_pvs = [
            pv.strip()
            for pv in self.user_input_widget.ncList
            if re.search(axis_prefix + "[^:]+:Name_RBV", pv)
        ]
        coe_name_pvs, coe_prefixes = self.get_axis_coe_name_pvs(axis_index)

        data = []
        seen_pvs = set()
        for name_pv in [*nc_name_pvs, *coe_name_pvs]:
            param_pv = name_pv.removesuffix(":Name_RBV")
            if self.is_fixed_readonly(f"{param_pv}:Acc_RBV"):
                continue

            row = (f"{param_pv}:Goal", f"{param_pv}:Val_RBV")
            if row in seen_pvs:
                continue
            seen_pvs.add(row)
            data.append(row)

        if not data:
            QMessageBox.warning(
                self,
                "No writable parameters",
                "No non fixed read-only NC or COE parameters found for this axis.",
            )
            return None

        return PVConfig(
            name=axis_name,
            desc=f"Writable NC and linked COE parameters for {axis_name}",
            schema_ver=0,
            metadata={
                "axis": axis_name,
                "axis_index": axis_index + 1,
            },
            data=data,
        )

    def get_ioc_template_prefix(self) -> str:
        """Return the IOC root prefix, without any axis or hardware suffix."""
        return self.user_input_widget.prefixName.removesuffix(":")

    def get_template_prefix(self, axis_index: int) -> str:
        """Return the axis-specific prefix used to expand ${PREFIX}."""
        return f"{self.get_ioc_template_prefix()}:MMS:{axis_index + 1:02}"

    def get_template_macros(self, axis_index: int) -> dict[str, str]:
        """Return macro values for applying a template to one target axis."""
        axis_prefix = f"{self.get_template_prefix(axis_index)}:NC:"
        macros = {
            self.template_prefix_macro: self.get_template_prefix(axis_index),
            "NC_PREFIX": self.get_template_prefix(axis_index),
            self.template_ioc_prefix_macro: self.get_ioc_template_prefix(),
            self.template_axis_macro: f"{axis_index + 1:02}",
            "axis_prefix": axis_prefix,
        }

        drive_coe_prefix = self.get_axis_coe_channel_prefix(axis_index, "DRV")
        if drive_coe_prefix is not None:
            macros[self.template_drive_coe_prefix_macro] = drive_coe_prefix.rstrip(":")

        encoder_coe_prefix = self.get_axis_coe_channel_prefix(axis_index, "ENC")
        if encoder_coe_prefix is not None:
            macros[self.template_encoder_coe_prefix_macro] = encoder_coe_prefix.rstrip(
                ":"
            )

        return macros

    def configure_template_macros(
        self, config: ValueConfig, axis_index: int
    ) -> ValueConfig:
        """Replace concrete NC/COE PV prefixes with reusable template macros."""
        macros = config.get_macros()
        if (
            self.template_prefix_macro in macros
            or self.template_ioc_prefix_macro in macros
            or self.template_axis_macro in macros
            or self.template_drive_coe_prefix_macro in macros
            or self.template_encoder_coe_prefix_macro in macros
        ):
            return config
        if "axis_prefix" in macros:
            config = config.apply_macros(self.get_template_macros(axis_index))

        drive_coe_prefix = config.metadata.get("drive_coe_prefix")
        encoder_coe_prefix = config.metadata.get("encoder_coe_prefix")

        new_data = []
        for row in config.data:
            new_row = []
            for elem in row:
                if isinstance(elem, str):
                    new_elem = elem.replace(
                        self.get_template_prefix(axis_index),
                        f"${{{self.template_prefix_macro}}}",
                    )
                    if ":NC:" in new_elem:
                        new_elem = new_elem.replace(
                            f"${{{self.template_prefix_macro}}}:NC:",
                            f"${{NC_PREFIX}}:NC:",
                        )
                    if ":COE:" in new_elem:
                        if drive_coe_prefix and new_elem.startswith(drive_coe_prefix):
                            suffix = new_elem.removeprefix(drive_coe_prefix)
                            new_elem = (
                                f"${{{self.template_drive_coe_prefix_macro}}}:{suffix}"
                            )
                        elif encoder_coe_prefix and new_elem.startswith(
                            encoder_coe_prefix
                        ):
                            suffix = new_elem.removeprefix(encoder_coe_prefix)
                            new_elem = f"${{{self.template_encoder_coe_prefix_macro}}}:{suffix}"
                        else:
                            _, suffix = new_elem.split(":COE:", 1)
                            suffix = self.normalize_template_coe_suffix(suffix)
                            new_elem = f"${{{self.template_prefix_macro}}}:COE:{suffix}"
                    new_row.append(new_elem)
                else:
                    new_row.append(elem)
            new_data.append(tuple(new_row))

        new_config = ValueConfig(
            name=config.name,
            desc=config.desc,
            schema_ver=config.schema_ver,
            metadata=config.metadata,
            data=new_data,
        )
        return new_config

    def normalize_template_coe_suffix(self, suffix: str) -> str:
        """Convert legacy COE ParamChN suffixes to NN:Param canonical form."""
        match = re.match(r"^(?P<param>.+)Ch(?P<channel>\d+):(?P<tail>.+)$", suffix)
        if match is None:
            return suffix
        channel = f"{int(match.group('channel')):02}"
        return f"{channel}:{match.group('param')}:{match.group('tail')}"

    def drop_unreadable_config_values(self, config: ValueConfig) -> ValueConfig | None:
        """Drop rows with unreadable live values before writing TOML."""
        readable_data = [
            row for row in config.data if len(row) < 3 or row[2] is not None
        ]
        skipped = len(config.data) - len(readable_data)
        if skipped == 0:
            return config

        if not readable_data:
            QMessageBox.warning(
                self,
                "No readable values",
                "No selected PV readbacks returned values. Config was not saved.",
            )
            return None

        QMessageBox.warning(
            self,
            "Skipped unreadable PVs",
            f"Skipped {skipped} PVs whose readbacks did not return values.",
        )
        return ValueConfig(
            name=config.name,
            desc=config.desc,
            schema_ver=config.schema_ver,
            metadata=config.metadata,
            data=readable_data,
        )

    def get_axis_index_from_prefix(self, axis_prefix: str) -> int | None:
        """Return a zero-based axis index from an NC axis prefix."""
        match = re.search(r":MMS:(\d{2}):NC:?$", axis_prefix)
        if match is None:
            return None
        return int(match.group(1)) - 1

    def get_config_axis_index(
        self, config: ValueConfig, config_name: str | None = None
    ) -> int | None:
        """Infer a config's source axis from metadata, PV text, or file label."""
        source_axis_index = config.metadata.get("axis_index")
        if source_axis_index is not None:
            return int(source_axis_index) - 1

        source_axis_prefix = self.get_config_axis_prefix(config)
        if source_axis_prefix is not None:
            return self.get_axis_index_from_prefix(source_axis_prefix)

        for name in (config.name, config_name):
            if not name:
                continue
            match = re.search(r"Axis[_ ]?(\d{1,2})", name)
            if match is not None:
                return int(match.group(1)) - 1
        return None

    def get_axis_coe_name_pvs(
        self, axis_index: int
    ) -> tuple[list[str], dict[str, str]]:
        """Return linked drive and encoder COE Name_RBV PVs for an axis."""
        coe_name_pvs = []
        coe_prefixes = {}
        for key, selector in (
            ("drive_coe_prefix", "DRV"),
            ("encoder_coe_prefix", "ENC"),
        ):
            coe_channel_prefix = self.get_axis_coe_channel_prefix(axis_index, selector)
            if coe_channel_prefix is None:
                continue
            coe_prefixes[key] = coe_channel_prefix
            coe_name_pvs.extend(self.get_coe_name_pvs(coe_channel_prefix))
        return list(dict.fromkeys(coe_name_pvs)), coe_prefixes

    def get_axis_coe_channel_prefix(self, axis_index: int, selector: str) -> str | None:
        """Return the linked COE prefix including the selected hardware channel."""
        coe_prefix = self.get_axis_coe_prefix(axis_index, selector)
        if coe_prefix is None:
            return None

        axis_number = axis_index + 1
        selector_prefix = (
            f"{self.user_input_widget.prefixName}:AXIS:{axis_number:02}:SelG:{selector}"
        )
        hardware_channel = epics.caget(f"{selector_prefix}:MAIN_RBV", as_string=True)
        hardware_channel = normalize_hardware_channel(
            hardware_channel, f"{axis_number:02}"
        )
        return f"{coe_prefix}{hardware_channel}:"

    def get_axis_coe_prefix(self, axis_index: int, selector: str) -> str | None:
        """Find the concrete COE prefix for an axis's linked drive or encoder."""
        axis_number = axis_index + 1
        selector_prefix = (
            f"{self.user_input_widget.prefixName}:AXIS:{axis_number:02}:SelG:{selector}"
        )
        hardware_id = epics.caget(f"{selector_prefix}:Id_RBV", as_string=True)
        hardware_channel = epics.caget(f"{selector_prefix}:MAIN_RBV", as_string=True)
        if not hardware_id or not hardware_channel or hardware_id == "None":
            self.logger.debug(
                f"no linked {selector} COE hardware for axis {axis_number}"
            )
            return None

        hardware_id = str(hardware_id).strip()
        candidates = [hardware_id]
        base_hardware_id = hardware_id.split("_", 1)[0]
        if base_hardware_id != hardware_id:
            candidates.append(base_hardware_id)

        hardware_channels = [f"{axis_number:02}"]
        if "_" in hardware_id:
            id_channel = hardware_id.rsplit("_", 1)[1]
            if id_channel.isdigit():
                id_channel = f"{int(id_channel):02}"
                if id_channel not in hardware_channels:
                    hardware_channels.append(id_channel)

        hardware_channel = str(hardware_channel).strip()
        if hardware_channel.isdigit():
            hardware_channel = f"{int(hardware_channel):02}"
        if hardware_channel and hardware_channel not in hardware_channels:
            hardware_channels.append(hardware_channel)

        coe_list = self.get_coe_list()
        for candidate in candidates:
            for channel in hardware_channels:
                coe_prefix = (
                    f"{self.user_input_widget.prefixName}:{candidate}:{channel}:COE:"
                )
                if any(pv.startswith(coe_prefix) for pv in coe_list):
                    return coe_prefix

        self.logger.warning(
            f"no COE PVs found for {selector} hardware {hardware_id} "
            f"channel {hardware_channel}"
        )
        return None

    def get_coe_name_pvs(self, coe_prefix: str) -> list[str]:
        """Return non-diagnostic COE Name_RBV PVs under an exact COE channel."""
        pattern = re.compile(rf"^{re.escape(coe_prefix)}(?!DG:)[^:]+:Name_RBV$")
        return [pv.strip() for pv in self.get_coe_list() if pattern.search(pv)]

    def get_coe_list(self) -> list[str]:
        """Return the loaded COE PV list from the owning widgets."""
        main_window = getattr(self.user_input_widget, "main_window", None)
        if main_window is not None and getattr(main_window, "coeList", None):
            return main_window.coeList
        expert_widget = getattr(main_window, "expert_widget", None)
        if expert_widget is not None and getattr(expert_widget, "coe_drive_list", None):
            return expert_widget.coe_drive_list
        return getattr(self.user_input_widget, "coeList", [])

    def is_fixed_readonly(self, pvname: str, timeout: float = 10.0) -> bool:
        """Return True when an access PV reports FIXED_READONLY."""
        try:
            self.logger.debug(f"checking access of the pv: {pvname}")
            pv = epics.PV(pvname, auto_monitor=False)
            if pv.wait_for_connection(timeout=timeout):
                value = pv.get(as_string=True)
                self.logger.debug(f"connected to pv, {value}{pvname}")
                return value == "FIXED_READONLY"
        except Exception as ex:
            self.logger.error(f"Error checking access for {pvname}: {ex}")
        self.logger.info(f"{pvname} did not connect")
        return False

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

        macros = {
            **self.get_template_macros(target_axis_index),
            "axis_prefix": target_axis_prefix,
        }
        if source_axis_prefix is not None:
            source_axis_index = self.get_axis_index_from_prefix(source_axis_prefix)
            if source_axis_index is not None:
                config = self.configure_template_macros(config, source_axis_index)

        applied_config = config.apply_macros(macros)
        applied_config = self.resolve_template_coe_pvs(
            applied_config, target_axis_index
        )
        for setpoint, readback, value in applied_config.data:
            self.logger.debug(
                f"applying config pv: setpoint={setpoint}, readback={readback}, value={value}"
            )

        self.logger.info(f"applying {len(config.data)} values to {target_axis_name}")
        put_live_config(applied_config)
        self.logger.info("finished apply_config_to_axis")

    def resolve_template_coe_pvs(
        self, config: ValueConfig, axis_index: int
    ) -> ValueConfig:
        """Rewrite expanded template COE rows to linked hardware COE PVs."""
        coe_prefixes = [
            prefix
            for prefix in (
                self.get_axis_coe_channel_prefix(axis_index, "DRV"),
                self.get_axis_coe_channel_prefix(axis_index, "ENC"),
            )
            if prefix is not None
        ]
        if not coe_prefixes:
            return config

        coe_prefixes = list(dict.fromkeys(coe_prefixes))
        coe_list = set(self.get_coe_list())
        template_prefix = self.get_template_prefix(axis_index)
        template_coe_prefix = f"{template_prefix}:COE:"

        new_data = []
        for row in config.data:
            new_row = []
            for elem in row:
                if isinstance(elem, str) and elem.startswith(template_coe_prefix):
                    suffix = elem.removeprefix(template_coe_prefix)
                    elem = self.resolve_template_coe_pv(suffix, coe_prefixes, coe_list)
                new_row.append(elem)
            new_data.append(tuple(new_row))

        return ValueConfig(
            name=config.name,
            desc=config.desc,
            schema_ver=config.schema_ver,
            metadata=config.metadata,
            data=new_data,
        )

    def resolve_template_coe_pv(
        self, suffix: str, coe_prefixes: list[str], coe_list: set[str]
    ) -> str:
        """Choose the concrete COE PV matching a template suffix."""
        suffix = self.normalize_template_coe_suffix(suffix)
        suffixes_to_try = [suffix]
        channel, separator, remainder = suffix.partition(":")
        if separator and channel.isdigit() and remainder:
            suffixes_to_try.append(remainder)

        for coe_prefix in coe_prefixes:
            for suffix_to_try in suffixes_to_try:
                pv = f"{coe_prefix}{suffix_to_try}"
                name_pv = re.sub(r":(Goal|Val_RBV)$", ":Name_RBV", pv)
                if pv in coe_list or name_pv in coe_list:
                    return pv

        fallback_suffix = suffixes_to_try[-1]
        return f"{coe_prefixes[0]}{fallback_suffix}"

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
        """Save live writable NC and linked COE values to a TOML config."""
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

        config = get_live_config(pv_config)
        config = self.drop_unreadable_config_values(config)
        if config is None:
            return
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
