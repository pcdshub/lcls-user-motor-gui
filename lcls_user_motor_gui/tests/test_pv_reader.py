from copy import deepcopy
from threading import Thread
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from qtpy.QtCore import QObject, Qt, QThread, QTimer, Signal
from qtpy.QtWidgets import QApplication, QListWidget

from lcls_user_motor_gui.save_and_restore import ValueConfig
from lcls_user_motor_gui.widgets import pv_reader, stage_settings


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def channels(monkeypatch):
    created = {}
    values = {}

    class Channel:
        def __init__(self, address, connection_slot, value_slot, enum_strings_slot):
            self.name = address.removeprefix("ca://")
            self.connection_slot = connection_slot
            self.value_slot = value_slot
            self.enum_strings_slot = enum_strings_slot
            self.closed = False
            created[self.name] = self

        def connect(self):
            def deliver():
                if not self.closed and self.name in values:
                    value, enums = values[self.name]
                    self.connection_slot(True)
                    self.enum_strings_slot(enums)
                    self.value_slot(value)

            QTimer.singleShot(0, deliver)

        def disconnect(self):
            self.closed = True

    monkeypatch.setattr(pv_reader, "PyDMChannel", Channel)
    return created, values


def drain(app):
    for _ in range(30):
        app.processEvents()


def test_reads_are_nonblocking_and_enum_labels_are_available(app, channels):
    created, values = channels
    values["PV"] = (1, ("FIXED_READONLY", "READWRITE"))
    reader = pv_reader.PVReader(None)
    result = MagicMock()
    heartbeat = MagicMock()
    QTimer.singleShot(0, heartbeat)

    reader.read(["PV", "PV"], result)
    result.assert_not_called()
    drain(app)

    heartbeat.assert_called_once()
    result.assert_called_once_with({"PV": 1})
    assert reader.value("PV", as_string=True) == "READWRITE"
    assert len(created) == 1
    reader.reset()


def test_disconnect_invalidates_cached_values(app, channels):
    created, values = channels
    values["PV"] = (0, ())
    reader = pv_reader.PVReader(None)
    reader.read(["PV"], MagicMock())
    drain(app)
    created["PV"].connection_slot(False)
    created["PV"].value_slot(99)
    with pytest.raises(pv_reader.PVUnavailableError):
        reader.value("PV")
    created["PV"].connection_slot(True)
    created["PV"].value_slot(2)
    assert reader.value("PV") == 2
    reader.reset()


def test_timeout_returns_only_connected_readbacks(app, channels):
    _, values = channels
    values["good"] = (0, ())
    reader = pv_reader.PVReader(None)
    result = MagicMock()
    reader.read(["good", "missing"], result)
    assert reader.timer.interval() == 10_000
    drain(app)
    result.assert_not_called()
    reader.complete()
    result.assert_called_once_with({"good": 0})
    reader.reset()


def test_cancel_ignores_queued_callbacks(app, channels):
    created, values = channels
    values["PV"] = (1, ())
    reader = pv_reader.PVReader(None)
    result = MagicMock()
    reader.read(["PV"], result)
    reader.reset()
    drain(app)
    result.assert_not_called()
    assert created["PV"].closed
    assert reader.subscriptions == {}


def test_worker_values_are_dispatched_on_gui_thread(app, channels):
    created, _ = channels

    class Emitter(QObject):
        value = Signal(int)

    reader = pv_reader.PVReader(None)
    result = MagicMock()
    reader.read(["PV"], result)
    subscription = reader.subscriptions["PV"]
    created["PV"].connection_slot(True)
    threads = []
    subscription.changed.connect(lambda: threads.append(QThread.currentThread()))
    emitter = Emitter()
    emitter.value.connect(subscription.value_changed, Qt.QueuedConnection)
    worker = Thread(target=lambda: emitter.value.emit(7))
    worker.start()
    worker.join()
    result.assert_not_called()
    drain(app)
    result.assert_called_once_with({"PV": 7})
    assert threads == [app.thread()]
    reader.reset()


@pytest.fixture
def dialog(app, channels, monkeypatch):
    display = QListWidget()
    display.addItems(["Axis 1", "Axis 2"])
    inputs = SimpleNamespace(
        prefixName="TST:UM",
        ncList=["TST:UM:MMS:01:NC:Vel:Name_RBV"],
        axis=["Axis 1", "Axis 2"],
        display_axis_ui=display,
        stage_configs_widget=SimpleNamespace(all_items=lambda: ["template"]),
        loaded_configs={},
        loaded_config_path="/tmp",
        load_configs=MagicMock(),
        main_window=SimpleNamespace(coeList=[]),
    )
    widget = stage_settings.StageSettings(inputs, logger=MagicMock())
    widget.stage_list_widget.list_widget.setCurrentRow(0)
    monkeypatch.setattr(stage_settings.QMessageBox, "warning", MagicMock())
    monkeypatch.setattr(stage_settings, "put_live_config", MagicMock())
    monkeypatch.setattr(stage_settings, "config_to_file", MagicMock())
    monkeypatch.setattr(
        stage_settings.QFileDialog,
        "getSaveFileName",
        lambda *args: ("/tmp/async-config.toml", ""),
    )
    yield widget
    widget.cancel_pv_reads()
    widget.close()
    widget.deleteLater()
    drain(app)


def add_unlinked_selectors(values):
    for selector in ("DRV", "ENC"):
        base = f"TST:UM:AXIS:01:SelG:{selector}"
        values[f"{base}:Id_RBV"] = ("None", ())
        values[f"{base}:MAIN_RBV"] = (0, ())


def test_axis_save_uses_async_access_and_snapshot(app, channels, dialog):
    _, values = channels
    add_unlinked_selectors(values)
    values["TST:UM:MMS:01:NC:Vel:Acc_RBV"] = (
        1,
        ("FIXED_READONLY", "READWRITE"),
    )
    values["TST:UM:MMS:01:NC:Vel:Val_RBV"] = (2.5, ())

    dialog.axis_to_toml()
    stage_settings.config_to_file.assert_not_called()
    drain(app)

    stage_settings.config_to_file.assert_called_once()
    filename, config = stage_settings.config_to_file.call_args.args
    assert filename == "/tmp/async-config.toml"
    assert config.data == [
        ("${NC_PREFIX}:NC:Vel:Goal", "${NC_PREFIX}:NC:Vel:Val_RBV", 2.5)
    ]
    assert not dialog.reading_pvs


def test_unavailable_access_never_defaults_to_writable(app, channels, dialog):
    _, values = channels
    add_unlinked_selectors(values)
    dialog.axis_to_toml()
    drain(app)
    dialog.pv_reader.complete()
    stage_settings.config_to_file.assert_not_called()
    stage_settings.QMessageBox.warning.assert_called_once()
    assert not dialog.reading_pvs


def test_missing_selector_is_not_treated_as_unlinked(app, channels, dialog):
    dialog.axis_to_toml()
    drain(app)
    dialog.pv_reader.complete()
    stage_settings.config_to_file.assert_not_called()
    stage_settings.QMessageBox.warning.assert_called_once()


def test_nc_only_apply_does_not_require_coe_selectors(app, channels, dialog):
    created, _ = channels
    dialog.user_input_widget.loaded_configs["template"] = ValueConfig(
        name="NC only",
        desc="",
        schema_ver=0,
        metadata={},
        data=[("${NC_PREFIX}:NC:Vel:Goal", "${NC_PREFIX}:NC:Vel:Val_RBV", 2)],
    )
    dialog.apply_config_to_axis()
    stage_settings.put_live_config.assert_not_called()
    drain(app)
    stage_settings.put_live_config.assert_called_once()
    assert not created
    assert stage_settings.put_live_config.call_args.args[0].data == [
        ("TST:UM:MMS:01:NC:Vel:Goal", "TST:UM:MMS:01:NC:Vel:Val_RBV", 2)
    ]


@pytest.mark.parametrize("change", ["cancel", "ioc", "axis"])
def test_pending_operations_are_invalidated(app, channels, dialog, change):
    dialog.axis_to_toml()
    if change == "cancel":
        dialog.cancel_pv_reads()
    elif change == "ioc":
        dialog.user_input_widget.prefixName = "OTHER:UM"
    else:
        dialog.axis_dropdown.setCurrentIndex(1)
    drain(app)
    dialog.pv_reader.complete()
    stage_settings.config_to_file.assert_not_called()
    stage_settings.put_live_config.assert_not_called()


def test_drive_only_template_requires_only_target_drive(app, channels, dialog):
    created, values = channels
    dialog.comboBox_config_to_axis.setCurrentIndex(1)
    dialog.user_input_widget.main_window.coeList = [
        "TST:UM:EL7062:01:COE:02:RateCur:Name_RBV"
    ]
    values["TST:UM:AXIS:02:SelG:DRV:Id_RBV"] = ("EL7062_1", ())
    values["TST:UM:AXIS:02:SelG:DRV:MAIN_RBV"] = (2, ())
    dialog.user_input_widget.loaded_configs["template"] = ValueConfig(
        name="Drive only",
        desc="",
        schema_ver=0,
        metadata={"axis_index": 1},
        data=[
            (
                "${DRIVE_COE_PREFIX}:RateCur:Goal",
                "${DRIVE_COE_PREFIX}:RateCur:Val_RBV",
                1000,
            )
        ],
    )
    dialog.apply_config_to_axis()
    drain(app)
    stage_settings.put_live_config.assert_called_once()
    assert set(created) == {
        "TST:UM:AXIS:02:SelG:DRV:Id_RBV",
        "TST:UM:AXIS:02:SelG:DRV:MAIN_RBV",
    }
    assert stage_settings.put_live_config.call_args.args[0].data == [
        (
            "TST:UM:EL7062:01:COE:02:RateCur:Goal",
            "TST:UM:EL7062:01:COE:02:RateCur:Val_RBV",
            1000,
        )
    ]


def test_selector_disconnect_during_snapshot_stops_save(app, channels, dialog):
    created, values = channels
    add_unlinked_selectors(values)
    access = "TST:UM:MMS:01:NC:Vel:Acc_RBV"
    values[access] = (1, ("FIXED_READONLY", "READWRITE"))
    dialog.axis_to_toml()
    drain(app)
    assert dialog.pv_reader.pending == ("TST:UM:MMS:01:NC:Vel:Val_RBV",)
    created["TST:UM:AXIS:01:SelG:DRV:Id_RBV"].connection_slot(False)
    readback = created["TST:UM:MMS:01:NC:Vel:Val_RBV"]
    readback.connection_slot(True)
    readback.value_slot(1.0)
    drain(app)
    stage_settings.config_to_file.assert_not_called()
    stage_settings.QMessageBox.warning.assert_called_once()


@pytest.mark.parametrize("save", [False, True])
def test_partial_snapshot_requires_consent(app, channels, dialog, save):
    _, values = channels
    add_unlinked_selectors(values)
    dialog.user_input_widget.ncList.append("TST:UM:MMS:01:NC:Acc:Name_RBV")
    for parameter in ("Vel", "Acc"):
        values[f"TST:UM:MMS:01:NC:{parameter}:Acc_RBV"] = (
            1,
            ("FIXED_READONLY", "READWRITE"),
        )
    values["TST:UM:MMS:01:NC:Vel:Val_RBV"] = (2.5, ())
    confirmed = MagicMock(
        side_effect=lambda config: (
            ValueConfig(
                name=config.name,
                desc=config.desc,
                schema_ver=config.schema_ver,
                metadata=config.metadata,
                data=[row for row in config.data if row[2] is not None],
            )
            if save
            else None
        )
    )
    dialog.drop_unreadable_config_values = confirmed
    dialog.axis_to_toml()
    drain(app)
    dialog.pv_reader.complete()
    confirmed.assert_called_once()
    assert confirmed.call_args.args[0].data[-1][2] is None
    assert stage_settings.config_to_file.call_count == int(save)


def test_progress_cancel_releases_channels(app, channels, dialog):
    created, _ = channels
    dialog.axis_to_toml()
    dialog.read_progress.canceled.emit()
    drain(app)
    assert all(channel.closed for channel in created.values())
    assert dialog.pv_reader.subscriptions == {}
    assert not dialog.reading_pvs
    stage_settings.config_to_file.assert_not_called()


def test_template_conversion_waits_for_snapshot(app, channels, dialog, monkeypatch):
    _, values = channels
    add_unlinked_selectors(values)
    values["TST:UM:MMS:01:NC:Vel:Acc_RBV"] = (
        1,
        ("FIXED_READONLY", "READWRITE"),
    )
    values["TST:UM:MMS:01:NC:Vel:Val_RBV"] = (3.0, ())

    class Edit:
        def __init__(self):
            self.value = ""

        def setText(self, value):
            self.value = value

        def text(self):
            return self.value

    class TemplateDialog:
        Accepted = 1

        def __init__(self, parent):
            pass

        def exec_(self):
            return self.Accepted

    def load_ui(filename, window):
        for field in (
            "label_axis_value",
            "lineEdit_template_name",
            "lineEdit_template_desc",
            "lineEdit_template_file",
        ):
            setattr(window, field, Edit())
        window.pushButton_browse_template_file = MagicMock()

    monkeypatch.setattr(stage_settings, "QDialog", TemplateDialog)
    monkeypatch.setattr(stage_settings, "loadUi", load_ui)
    dialog.convert_axis_to_template()
    stage_settings.config_to_file.assert_not_called()
    drain(app)
    stage_settings.config_to_file.assert_called_once()
    config = stage_settings.config_to_file.call_args.args[1]
    assert config.name == "Axis 1 Template"
    assert config.metadata["axis_index"] == 1
    assert config.data == [
        ("${NC_PREFIX}:NC:Vel:Goal", "${NC_PREFIX}:NC:Vel:Val_RBV", 3.0)
    ]


def test_unlinked_hardware_does_not_require_main_channel(app, channels, dialog):
    created, values = channels
    for selector in ("DRV", "ENC"):
        values[f"TST:UM:AXIS:01:SelG:{selector}:Id_RBV"] = ("None", ())
    values["TST:UM:MMS:01:NC:Vel:Acc_RBV"] = ("READWRITE", ())
    values["TST:UM:MMS:01:NC:Vel:Val_RBV"] = (1.0, ())
    dialog.axis_to_toml()
    drain(app)
    stage_settings.config_to_file.assert_called_once()
    assert not any(name.endswith(":MAIN_RBV") for name in created)


def test_legacy_coe_parameter_not_found_aborts_apply(app, channels, dialog):
    _, values = channels
    dialog.user_input_widget.main_window.coeList = [
        "TST:UM:EL7062:01:COE:01:RateCur:Name_RBV"
    ]
    values["TST:UM:AXIS:01:SelG:DRV:Id_RBV"] = ("EL7062_1", ())
    values["TST:UM:AXIS:01:SelG:DRV:MAIN_RBV"] = (1, ())
    values["TST:UM:AXIS:01:SelG:ENC:Id_RBV"] = ("None", ())
    dialog.user_input_widget.loaded_configs["template"] = ValueConfig(
        name="Legacy",
        desc="",
        schema_ver=0,
        metadata={},
        data=[("${PREFIX}:COE:01:Missing:Goal", "${PREFIX}:COE:01:Missing:Val_RBV", 1)],
    )
    dialog.apply_config_to_axis()
    drain(app)
    stage_settings.put_live_config.assert_not_called()
    stage_settings.QMessageBox.warning.assert_called_once()


def test_unresolved_macro_is_reported_without_writing(app, channels, dialog):
    dialog.user_input_widget.loaded_configs["template"] = ValueConfig(
        name="Unknown macro",
        desc="",
        schema_ver=0,
        metadata={},
        data=[("${UNKNOWN}:NC:Vel:Goal", "${UNKNOWN}:NC:Vel:Val_RBV", 1)],
    )
    dialog.apply_config_to_axis()
    drain(app)
    stage_settings.put_live_config.assert_not_called()
    stage_settings.QMessageBox.warning.assert_called_once()


def test_access_without_enum_labels_is_not_writable(app, channels, dialog):
    _, values = channels
    add_unlinked_selectors(values)
    values["TST:UM:MMS:01:NC:Vel:Acc_RBV"] = (1, ())
    dialog.axis_to_toml()
    drain(app)
    stage_settings.config_to_file.assert_not_called()
    stage_settings.QMessageBox.warning.assert_called_once()


def test_all_unreadable_snapshot_does_not_write(app, channels, dialog):
    _, values = channels
    add_unlinked_selectors(values)
    values["TST:UM:MMS:01:NC:Vel:Acc_RBV"] = ("READWRITE", ())
    dialog.axis_to_toml()
    drain(app)
    dialog.pv_reader.complete()
    stage_settings.config_to_file.assert_not_called()
    stage_settings.QMessageBox.warning.assert_called_once()


def test_fixed_readonly_parameters_are_not_snapshotted(app, channels, dialog):
    created, values = channels
    add_unlinked_selectors(values)
    values["TST:UM:MMS:01:NC:Vel:Acc_RBV"] = (
        0,
        ("FIXED_READONLY", "READWRITE"),
    )
    dialog.axis_to_toml()
    drain(app)
    assert "TST:UM:MMS:01:NC:Vel:Val_RBV" not in created
    stage_settings.config_to_file.assert_not_called()


def template_config(data, metadata=None):
    return ValueConfig(
        name="Motor template",
        desc="Template test",
        schema_ver=0,
        metadata={} if metadata is None else metadata,
        data=data,
    )


def test_concrete_nc_drive_encoder_prefixes_become_macros(dialog):
    source = template_config(
        [
            ("TST:UM:MMS:01:NC:Vel:Goal", "TST:UM:MMS:01:NC:Vel:Val_RBV", 0.0),
            (
                "TST:UM:EL7062:01:COE:01:RateCur:Goal",
                "TST:UM:EL7062:01:COE:01:RateCur:Val_RBV",
                1000,
            ),
            (
                "TST:UM:EL5042:01:COE:01:SBits:Goal",
                "TST:UM:EL5042:01:COE:01:SBits:Val_RBV",
                26,
            ),
        ],
        {
            "axis_index": 1,
            "drive_coe_prefix": "TST:UM:EL7062:01:COE:01",
            "encoder_coe_prefix": "TST:UM:EL5042:01:COE:01:",
        },
    )
    original = deepcopy(source)

    result = dialog.configure_template_macros(source, axis_index=0)

    assert result.data == [
        ("${NC_PREFIX}:NC:Vel:Goal", "${NC_PREFIX}:NC:Vel:Val_RBV", 0.0),
        (
            "${DRIVE_COE_PREFIX}:RateCur:Goal",
            "${DRIVE_COE_PREFIX}:RateCur:Val_RBV",
            1000,
        ),
        ("${ENCODER_COE_PREFIX}:SBits:Goal", "${ENCODER_COE_PREFIX}:SBits:Val_RBV", 26),
    ]
    assert source == original
    assert result.name == source.name
    assert result.desc == source.desc
    assert result.schema_ver == source.schema_ver
    assert result.metadata == source.metadata


@pytest.mark.parametrize(
    ("suffix", "expected"),
    [
        ("RateCurCh1:Goal", "01:RateCur:Goal"),
        ("ScaleCh12:Val_RBV", "12:Scale:Val_RBV"),
        ("01:RateCur:Goal", "01:RateCur:Goal"),
        ("RateCur:Goal", "RateCur:Goal"),
    ],
)
def test_legacy_coe_suffix_normalization(dialog, suffix, expected):
    assert dialog.normalize_template_coe_suffix(suffix) == expected


def test_legacy_concrete_coe_paths_become_canonical_template(dialog):
    source = template_config(
        [
            (
                "TST:UM:EL7062:01:COE:RateCurCh1:Goal",
                "TST:UM:EL7062:01:COE:RateCurCh1:Val_RBV",
                1000,
            )
        ]
    )
    result = dialog.configure_template_macros(source, axis_index=0)
    assert result.data == [
        ("${PREFIX}:COE:01:RateCur:Goal", "${PREFIX}:COE:01:RateCur:Val_RBV", 1000)
    ]


@pytest.mark.parametrize(
    "macro", ["PREFIX", "IOC_PREFIX", "AXIS", "DRIVE_COE_PREFIX", "ENCODER_COE_PREFIX"]
)
def test_existing_template_is_not_rewritten(dialog, macro):
    source = template_config([(f"${{{macro}}}:Param:Goal", "readback", "enum label")])
    original = deepcopy(source)
    assert dialog.configure_template_macros(source, axis_index=1) is source
    assert source == original


def test_legacy_axis_prefix_is_converted_to_nc_macro(dialog):
    source = template_config(
        [("${axis_prefix}Vel:Goal", "${axis_prefix}Vel:Val_RBV", 1.25)]
    )
    result = dialog.configure_template_macros(source, axis_index=0)
    assert result.data == [
        ("${NC_PREFIX}:NC:Vel:Goal", "${NC_PREFIX}:NC:Vel:Val_RBV", 1.25)
    ]
    assert source.data[0][0] == "${axis_prefix}Vel:Goal"


def test_legacy_template_resolves_to_target_hardware(app, channels, dialog):
    _, values = channels
    dialog.comboBox_config_to_axis.setCurrentIndex(1)
    dialog.user_input_widget.main_window.coeList = [
        "TST:UM:EL7062:01:COE:02:RateCur:Name_RBV"
    ]
    values["TST:UM:AXIS:02:SelG:DRV:Id_RBV"] = ("EL7062_1", ())
    values["TST:UM:AXIS:02:SelG:DRV:MAIN_RBV"] = (2, ())
    values["TST:UM:AXIS:02:SelG:ENC:Id_RBV"] = ("None", ())
    source = template_config(
        [
            ("${NC_PREFIX}:NC:Vel:Goal", "${NC_PREFIX}:NC:Vel:Val_RBV", 1.0),
            ("${PREFIX}:COE:01:RateCur:Goal", "${PREFIX}:COE:01:RateCur:Val_RBV", 1000),
        ],
        {"axis_index": 1},
    )
    original = deepcopy(source)
    dialog.user_input_widget.loaded_configs["template"] = source

    dialog.apply_config_to_axis()
    drain(app)

    stage_settings.put_live_config.assert_called_once()
    applied = stage_settings.put_live_config.call_args.args[0]
    assert applied.data == [
        ("TST:UM:MMS:02:NC:Vel:Goal", "TST:UM:MMS:02:NC:Vel:Val_RBV", 1.0),
        (
            "TST:UM:EL7062:01:COE:02:RateCur:Goal",
            "TST:UM:EL7062:01:COE:02:RateCur:Val_RBV",
            1000,
        ),
    ]
    assert applied.get_macros() == []
    assert source == original


def test_template_round_trip_through_toml(tmp_path, dialog):
    from lcls_user_motor_gui.save_and_restore import config_from_file, config_to_file

    source = template_config(
        [("TST:UM:MMS:01:NC:Vel:Goal", "TST:UM:MMS:01:NC:Vel:Val_RBV", 1.5)],
        {"axis_index": 1},
    )
    template = dialog.configure_template_macros(source, axis_index=0)
    path = tmp_path / "template.toml"
    config_to_file(str(path), template)
    loaded = config_from_file(str(path))

    assert isinstance(loaded, ValueConfig)
    assert loaded.name == template.name
    assert loaded.metadata == template.metadata
    assert [tuple(row) for row in loaded.data] == template.data
    applied = loaded.apply_macros({"NC_PREFIX": "TST:UM:MMS:02"})
    assert [tuple(row) for row in applied.data] == [
        ("TST:UM:MMS:02:NC:Vel:Goal", "TST:UM:MMS:02:NC:Vel:Val_RBV", 1.5)
    ]
