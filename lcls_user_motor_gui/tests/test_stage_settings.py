from unittest.mock import MagicMock

from lcls_user_motor_gui.save_and_restore import PVConfig, ValueConfig
from lcls_user_motor_gui.widgets.stage_settings import StageSettings


class FakeComboBox:
    def __init__(self, index, text):
        self.index = index
        self.text = text

    def currentIndex(self):
        return self.index

    def currentText(self):
        return self.text


class FakeLineEdit:
    def __init__(self):
        self.value = ""

    def setText(self, value):
        self.value = value

    def text(self):
        return self.value


class FakeDialog:
    Accepted = 1

    def __init__(self, parent=None):
        self.parent = parent

    def exec_(self):
        self.lineEdit_template_name.setText("My Template")
        self.lineEdit_template_desc.setText("Template description")
        self.lineEdit_template_file.setText("/tmp/my_template.toml")
        return self.Accepted


def fake_load_ui(filename, dialog):
    dialog.label_axis_value = FakeLineEdit()
    dialog.lineEdit_template_name = FakeLineEdit()
    dialog.lineEdit_template_desc = FakeLineEdit()
    dialog.lineEdit_template_file = FakeLineEdit()
    dialog.pushButton_browse_template_file = MagicMock()


def test_convert_axis_to_template(monkeypatch):
    pv_config = PVConfig(
        name="Axis 1",
        desc="Writable NC parameters for Axis 1",
        schema_ver=0,
        metadata={"axis": "Axis 1", "axis_index": 1},
        data=[("${axis_prefix}:Param:Goal", "${axis_prefix}:Param:Val_RBV")],
    )
    template_config = ValueConfig(
        name="Axis 1",
        desc="Writable NC parameters for Axis 1",
        schema_ver=0,
        metadata={"existing": "metadata"},
        data=[("${axis_prefix}:Param:Goal", "${axis_prefix}:Param:Val_RBV", 5)],
    )

    mock_get_live_config = MagicMock(return_value=template_config)
    mock_config_to_file = MagicMock()
    monkeypatch.setattr(
        "lcls_user_motor_gui.widgets.stage_settings.QDialog", FakeDialog
    )
    monkeypatch.setattr(
        "lcls_user_motor_gui.widgets.stage_settings.loadUi", fake_load_ui
    )
    monkeypatch.setattr(
        "lcls_user_motor_gui.widgets.stage_settings.get_live_config",
        mock_get_live_config,
    )
    monkeypatch.setattr(
        "lcls_user_motor_gui.widgets.stage_settings.config_to_file",
        mock_config_to_file,
    )

    user_input_widget = MagicMock()
    user_input_widget.prefixName = "TST:UM"
    user_input_widget.loaded_config_path = "/tmp"

    stage_settings = StageSettings.__new__(StageSettings)
    stage_settings.logger = MagicMock()
    stage_settings.user_input_widget = user_input_widget
    stage_settings.comboBox_convert_to_template = FakeComboBox(0, "Axis 1")
    stage_settings.checkBox_use_existing_config = MagicMock()
    stage_settings.checkBox_use_existing_config.isChecked.return_value = False
    stage_settings.get_axis_pv_config = MagicMock(return_value=pv_config)
    stage_settings.load_configs_from_user_input = MagicMock()

    stage_settings.convert_axis_to_template()

    stage_settings.get_axis_pv_config.assert_called_once_with(
        0, "Axis 1", "TST:UM:MMS:01:NC:"
    )
    mock_get_live_config.assert_called_once_with(
        pv_config,
        macros={"axis_prefix": "TST:UM:MMS:01:NC:"},
        as_template=True,
    )
    mock_config_to_file.assert_called_once_with(
        "/tmp/my_template.toml", template_config
    )
    user_input_widget.load_configs.assert_called_once_with()
    stage_settings.load_configs_from_user_input.assert_called_once_with()

    assert template_config.name == "My Template"
    assert template_config.desc == "Template description"
    assert template_config.metadata == {
        "existing": "metadata",
        "axis": "Axis 1",
        "axis_index": 1,
    }


def test_axis_prefix_macro_round_trip():
    config = PVConfig(
        name="Axis 1",
        desc="Writable NC parameters for Axis 1",
        schema_ver=0,
        metadata={},
        data=[
            (
                "TST:UM:MMS:01:NC:Param:Goal",
                "TST:UM:MMS:01:NC:Param:Val_RBV",
            )
        ],
    )

    template = config.configure_macros({"axis_prefix": "TST:UM:MMS:01:NC:"})
    assert template.data == [
        (
            "${axis_prefix}:Param:Goal",
            "${axis_prefix}:Param:Val_RBV",
        )
    ]

    applied = template.apply_macros({"axis_prefix": "TST:UM:MMS:02:NC:"})
    assert applied.data == [
        (
            "TST:UM:MMS:02:NC:Param:Goal",
            "TST:UM:MMS:02:NC:Param:Val_RBV",
        )
    ]
