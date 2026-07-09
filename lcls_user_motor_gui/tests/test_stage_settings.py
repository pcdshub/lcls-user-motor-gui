"""
End-to-end tests for the three StageSettings superscore workflows:
``save_to_collection``, ``take_snapshot_now`` and ``apply_snapshot_now``.

Each test drives the *real* method against a temporary FilestoreBackend (see
``conftest.py``) and asserts on what actually persisted, so the bugs fixed in
this area stay fixed:

* Goal setpoints are linked to their ``Goal_RBV`` readbacks.
* Saving the same collection twice updates in place (no duplicate).
* A collection holds a single snapshot; re-snapshotting prompts to overwrite.
* Snapshot values are applied back to the writable setpoints only.
* Dangling-child corruption is detected by ``_verify_entry_persisted``.
"""

import json

import numpy as np
import pytest
from superscore.backends.core import SearchTerm
from superscore.model import Collection, Parameter, Snapshot

from lcls_user_motor_gui.widgets.user_input import StageSettings


def _collections(client, title):
    return list(
        client.search(
            SearchTerm("title", "eq", title),
            SearchTerm("entry_type", "eq", Collection),
        )
    )


def _snapshots(client):
    return list(client.search(SearchTerm("entry_type", "eq", Snapshot)))


# --------------------------------------------------------------------------- #
# save_to_collection
# --------------------------------------------------------------------------- #
def test_save_builds_collection_with_linked_readbacks(
    stage_settings, patch_client, patch_dialogs
):
    client = patch_client
    input_dialog, _ = patch_dialogs
    input_dialog.result = ("MyStage", True)

    stage_settings.ncList = [
        "TST:UM:MMS:01:NC:m1:Goal_RBV",
        "TST:UM:MMS:01:NC:m2:Goal_RBV",
        # different axis -> must be excluded
        "TST:UM:MMS:02:NC:m3:Goal_RBV",
        # not a Goal_RBV -> must be excluded
        "TST:UM:MMS:01:NC:m1:Pos_RBV",
    ]

    stage_settings.save_to_collection()

    colls = _collections(client, "MyStage")
    assert len(colls) == 1
    coll = colls[0]
    assert len(coll.children) == 2

    children = sorted(coll.children, key=lambda c: c.pv_name)
    for child in children:
        assert isinstance(child, Parameter)
        # setpoint PV is the Goal (writable), not Goal_RBV
        assert child.pv_name.endswith(":Goal")
        assert child.read_only is False
        # readback is linked and read-only
        assert child.readback is not None
        assert child.readback.pv_name.endswith(":Goal_RBV")
        assert child.readback.read_only is True

    # saved cleanly -> no warning dialog
    stage_settings.msg.exec_.assert_not_called()


def test_save_excludes_fixed_readonly_axes(stage_settings, patch_client, patch_dialogs):
    client = patch_client
    input_dialog, _ = patch_dialogs
    input_dialog.result = ("FROStage", True)

    stage_settings.ncList = [
        "TST:UM:MMS:01:NC:m1:Goal_RBV",
        "TST:UM:MMS:01:NC:m2:Goal_RBV",
    ]
    # m2's acceleration record is FIXED_READONLY -> skip it
    stage_settings.is_fixed_readonly = lambda pv, *a, **k: "m2" in pv

    stage_settings.save_to_collection()

    coll = _collections(client, "FROStage")[0]
    assert len(coll.children) == 1
    assert "m1" in coll.children[0].pv_name


def test_save_twice_updates_in_place(stage_settings, patch_client, patch_dialogs):
    client = patch_client
    input_dialog, _ = patch_dialogs
    input_dialog.result = ("DupStage", True)
    stage_settings.ncList = ["TST:UM:MMS:01:NC:m1:Goal_RBV"]

    stage_settings.save_to_collection()
    first_uuid = _collections(client, "DupStage")[0].uuid

    # second save with same title must update, not duplicate
    stage_settings.save_to_collection()
    colls = _collections(client, "DupStage")
    assert len(colls) == 1
    assert colls[0].uuid == first_uuid


def test_save_cancelled_does_nothing(stage_settings, patch_client, patch_dialogs):
    client = patch_client
    input_dialog, _ = patch_dialogs
    input_dialog.result = ("", False)
    stage_settings.ncList = ["TST:UM:MMS:01:NC:m1:Goal_RBV"]

    stage_settings.save_to_collection()

    assert _collections(client, "User Motors") == []


# --------------------------------------------------------------------------- #
# take_snapshot_now
# --------------------------------------------------------------------------- #
def _save_collection(stage_settings, input_dialog, title, ncList):
    input_dialog.result = (title, True)
    stage_settings.ncList = ncList
    stage_settings.save_to_collection()


def test_take_snapshot_creates_one_snapshot(
    stage_settings, patch_client, patch_dialogs, control_layer
):
    client = patch_client
    input_dialog, _ = patch_dialogs
    _save_collection(
        stage_settings,
        input_dialog,
        "SnapStage",
        ["TST:UM:MMS:01:NC:m1:Goal_RBV"],
    )
    control_layer.values["TST:UM:MMS:01:NC:m1:Goal"] = 1.23
    control_layer.values["TST:UM:MMS:01:NC:m1:Goal_RBV"] = 1.23

    stage_settings.user_input_widget.stage_configs_widget.currentText.return_value = (
        "SnapStage"
    )
    stage_settings.take_snapshot_now()

    snaps = _snapshots(client)
    assert len(snaps) == 1
    origin = client.find_origin_collection(snaps[0])
    assert origin.uuid == _collections(client, "SnapStage")[0].uuid


def test_take_snapshot_overwrite_yes_replaces(
    stage_settings, patch_client, patch_dialogs, control_layer
):
    client = patch_client
    input_dialog, msg_box = patch_dialogs
    _save_collection(
        stage_settings,
        input_dialog,
        "OverStage",
        ["TST:UM:MMS:01:NC:m1:Goal_RBV"],
    )
    stage_settings.user_input_widget.stage_configs_widget.currentText.return_value = (
        "OverStage"
    )

    stage_settings.take_snapshot_now()
    first_uuid = _snapshots(client)[0].uuid

    msg_box.answer = msg_box.Yes
    stage_settings.take_snapshot_now()

    snaps = _snapshots(client)
    assert len(snaps) == 1
    assert snaps[0].uuid != first_uuid


def test_take_snapshot_overwrite_no_keeps_original(
    stage_settings, patch_client, patch_dialogs, control_layer
):
    client = patch_client
    input_dialog, msg_box = patch_dialogs
    _save_collection(
        stage_settings,
        input_dialog,
        "KeepStage",
        ["TST:UM:MMS:01:NC:m1:Goal_RBV"],
    )
    stage_settings.user_input_widget.stage_configs_widget.currentText.return_value = (
        "KeepStage"
    )

    stage_settings.take_snapshot_now()
    first_uuid = _snapshots(client)[0].uuid

    msg_box.answer = msg_box.No
    stage_settings.take_snapshot_now()

    snaps = _snapshots(client)
    assert len(snaps) == 1
    assert snaps[0].uuid == first_uuid


def test_take_snapshot_no_collection_selected_warns(
    stage_settings, patch_client, patch_dialogs
):
    stage_settings.user_input_widget.stage_configs_widget.currentText.return_value = ""
    stage_settings.take_snapshot_now()
    stage_settings.msg.exec_.assert_called()


# --------------------------------------------------------------------------- #
# apply_snapshot_now
# --------------------------------------------------------------------------- #
def test_apply_writes_setpoints_only(
    stage_settings, patch_client, patch_dialogs, control_layer
):
    client = patch_client
    input_dialog, _ = patch_dialogs
    _save_collection(
        stage_settings,
        input_dialog,
        "ApplyStage",
        ["TST:UM:MMS:01:NC:m1:Goal_RBV"],
    )
    control_layer.values["TST:UM:MMS:01:NC:m1:Goal"] = 4.56
    control_layer.values["TST:UM:MMS:01:NC:m1:Goal_RBV"] = 4.56

    stage_settings.user_input_widget.stage_configs_widget.currentText.return_value = (
        "ApplyStage"
    )
    stage_settings.take_snapshot_now()

    control_layer.puts.clear()
    stage_settings.apply_snapshot_now()

    written = dict(control_layer.puts)
    assert "TST:UM:MMS:01:NC:m1:Goal" in written
    # readback PV is never written back
    assert "TST:UM:MMS:01:NC:m1:Goal_RBV" not in written
    assert written["TST:UM:MMS:01:NC:m1:Goal"] == 4.56


def test_apply_no_collection_selected_warns(
    stage_settings, patch_client, patch_dialogs
):
    stage_settings.user_input_widget.stage_configs_widget.currentText.return_value = ""
    stage_settings.apply_snapshot_now()
    stage_settings.msg.exec_.assert_called()


def test_apply_without_snapshot_warns(
    stage_settings, patch_client, patch_dialogs, control_layer
):
    input_dialog, _ = patch_dialogs
    _save_collection(
        stage_settings,
        input_dialog,
        "NoSnapStage",
        ["TST:UM:MMS:01:NC:m1:Goal_RBV"],
    )
    stage_settings.user_input_widget.stage_configs_widget.currentText.return_value = (
        "NoSnapStage"
    )
    stage_settings.apply_snapshot_now()
    stage_settings.msg.exec_.assert_called()


# --------------------------------------------------------------------------- #
# helpers: _verify_entry_persisted / _sanitize_snapshot_values
# --------------------------------------------------------------------------- #
def test_verify_detects_dangling_children(
    stage_settings, patch_client, patch_dialogs, store_path
):
    client = patch_client
    input_dialog, _ = patch_dialogs
    _save_collection(
        stage_settings,
        input_dialog,
        "HealthyStage",
        ["TST:UM:MMS:01:NC:m1:Goal_RBV"],
    )
    coll = _collections(client, "HealthyStage")[0]

    # healthy collection -> ok
    ok, _ = stage_settings._verify_entry_persisted(client, coll.uuid)
    assert ok is True

    # corrupt the store: replace the collection's children with a bare,
    # unbacked UUID string (the "dangling children" failure mode)
    with open(store_path) as fd:
        data = json.load(fd)
    for entry in data["entries"]:
        for body in entry.values():
            if isinstance(body, dict) and body.get("uuid") == str(coll.uuid):
                body["children"] = ["00000000-0000-0000-0000-000000000000"]
    with open(store_path, "w") as fd:
        json.dump(data, fd)

    ok, detail = stage_settings._verify_entry_persisted(client, coll.uuid)
    assert ok is False
    assert "missing backing" in detail


def test_sanitize_snapshot_values_coerces_numpy():
    class _Child:
        def __init__(self, data, pv_name):
            self.data = data
            self.pv_name = pv_name

    class _Snap:
        pass

    snap = _Snap()
    snap.children = [
        _Child(np.int64(7), "a"),
        _Child(np.float64(1.5), "b"),
        _Child(np.bool_(True), "c"),
        # char waveform (DBR_CHAR) "um\0" -> decoded string
        _Child(np.array([117, 109, 0], dtype=np.uint8), "egu"),
        # unrepresentable 2-D array -> dropped to None
        _Child(np.zeros((2, 2)), "bad"),
    ]

    StageSettings._sanitize_snapshot_values(snap)

    assert snap.children[0].data == 7
    assert isinstance(snap.children[0].data, int)
    assert snap.children[1].data == 1.5
    assert isinstance(snap.children[1].data, float)
    assert snap.children[2].data is True
    assert snap.children[3].data == "um"
    assert snap.children[4].data is None
