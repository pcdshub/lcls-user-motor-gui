from unittest.mock import MagicMock

import pytest
from qtpy.QtCore import QTimer
from qtpy.QtWidgets import QApplication, QMessageBox

from lcls_user_motor_gui.save_and_restore import ValueConfig
from lcls_user_motor_gui.widgets import stage_settings

from .test_pv_reader import add_unlinked_selectors, drain
from .test_pv_reader import app as app
from .test_pv_reader import channels as channels
from .test_pv_reader import dialog as dialog


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


def test_convert_axis_to_template(app, channels, dialog, monkeypatch):
    _, values = channels
    add_unlinked_selectors(values)
    values["TST:UM:MMS:01:NC:Vel:Acc_RBV"] = ("READWRITE", ())
    values["TST:UM:MMS:01:NC:Vel:Val_RBV"] = (5, ())
    monkeypatch.setattr(
        "lcls_user_motor_gui.widgets.stage_settings.QDialog", FakeDialog
    )
    monkeypatch.setattr(
        "lcls_user_motor_gui.widgets.stage_settings.loadUi", fake_load_ui
    )
    dialog.convert_axis_to_template()
    stage_settings.config_to_file.assert_not_called()
    drain(app)
    stage_settings.config_to_file.assert_called_once()
    filename, config = stage_settings.config_to_file.call_args.args
    assert filename == "/tmp/my_template.toml"
    assert config.name == "My Template"
    assert config.desc == "Template description"
    assert config.metadata["axis"] == "Axis 1"
    assert config.metadata["axis_index"] == 1
    assert config.data == [
        ("${NC_PREFIX}:NC:Vel:Goal", "${NC_PREFIX}:NC:Vel:Val_RBV", 5)
    ]
    dialog.user_input_widget.load_configs.assert_called_once()
    assert not dialog.reading_pvs


def make_config(rows):
    return ValueConfig(
        name="Test",
        desc="Test description",
        schema_ver=0,
        metadata={
            "axis": "Axis 1",
            "axis_index": 1,
        },
        data=rows,
    )


@pytest.mark.parametrize("operation", ["save", "convert", "apply"])
def test_no_axis_selected_stops_operation(dialog, channels, operation):
    created, _ = channels
    if operation == "save":
        dialog.axis_dropdown.setCurrentIndex(-1)
        dialog.axis_to_toml()
    elif operation == "convert":
        dialog.comboBox_convert_to_template.setCurrentIndex(-1)
        dialog.convert_axis_to_template()
    else:
        dialog.user_input_widget.loaded_configs["template"] = make_config(
            [("goal", "rbv", 1)]
        )
        dialog.comboBox_config_to_axis.setCurrentIndex(-1)
        dialog.apply_config_to_axis()
    assert not created
    stage_settings.QMessageBox.warning.assert_called_once()
    stage_settings.config_to_file.assert_not_called()
    stage_settings.put_live_config.assert_not_called()


def test_no_config_selected_stops_apply(dialog, channels):
    dialog.stage_list_widget.list_widget.setCurrentRow(-1)
    dialog.apply_config_to_axis()
    assert not channels[0]
    stage_settings.QMessageBox.warning.assert_called_once()
    stage_settings.put_live_config.assert_not_called()


def test_readable_config_is_returned_unchanged(dialog):
    config = make_config([("goal", "rbv", 0), ("goal2", "rbv2", "")])
    assert dialog.drop_unreadable_config_values(config) is config
    stage_settings.QMessageBox.warning.assert_not_called()


@pytest.mark.parametrize("save", [False, True])
def test_partial_config_confirmation_defaults_to_cancel(app, dialog, save):
    config = make_config(
        [
            ("goal", "rbv", 0),
            ("missing", "rbv", None),
            ("short", "row"),
        ]
    )
    observed = []

    def respond():
        message = QApplication.activeModalWidget()
        observed.append(
            (
                message.defaultButton() == message.button(QMessageBox.Cancel),
                message.escapeButton() == message.button(QMessageBox.Cancel),
                message.text(),
            )
        )
        if save:
            next(
                button
                for button in message.buttons()
                if button.text() == "Save readable values"
            ).click()
        else:
            message.button(QMessageBox.Cancel).click()

    QTimer.singleShot(0, respond)
    result = dialog.drop_unreadable_config_values(config)
    assert observed[0][:2] == (True, True)
    assert "2 of 3" in observed[0][2]
    if save:
        assert result.data == [("goal", "rbv", 0)]
        assert result.metadata == config.metadata
        assert result.name == config.name
        assert result.desc == config.desc
    else:
        assert result is None
    assert len(config.data) == 3


def test_all_unreadable_config_is_rejected(dialog):
    config = make_config([("goal", "rbv", None)])
    assert dialog.drop_unreadable_config_values(config) is None
    stage_settings.QMessageBox.warning.assert_called_once()


def test_cancelled_file_selection_does_not_request_readbacks(
    app,
    channels,
    dialog,
    monkeypatch,
):
    created, values = channels
    add_unlinked_selectors(values)
    values["TST:UM:MMS:01:NC:Vel:Acc_RBV"] = ("READWRITE", ())
    monkeypatch.setattr(
        stage_settings.QFileDialog, "getSaveFileName", lambda *a: ("", "")
    )
    dialog.axis_to_toml()
    drain(app)
    assert "TST:UM:MMS:01:NC:Vel:Val_RBV" not in created
    stage_settings.config_to_file.assert_not_called()
    assert not dialog.reading_pvs


def test_refresh_cancels_pending_reads_and_repopulates_axes(dialog, channels):
    dialog.axis_to_toml()
    created, _ = channels
    dialog.load_configs_from_user_input()
    assert all(channel.closed for channel in created.values())
    assert not dialog.reading_pvs
    for selector in (
        dialog.axis_dropdown,
        dialog.comboBox_config_to_axis,
        dialog.comboBox_convert_to_template,
    ):
        assert [selector.itemText(index) for index in range(selector.count())] == [
            "Axis 1",
            "Axis 2",
        ]
        assert selector.currentIndex() == 0
