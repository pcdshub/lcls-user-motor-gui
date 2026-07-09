"""
Shared test fixtures for the StageSettings superscore workflows.

These tests exercise the *real* ``save_to_collection`` / ``take_snapshot_now`` /
``apply_snapshot_now`` methods against a real temporary ``FilestoreBackend`` so
the persistence behaviour (linked readbacks, update-in-place, snapshot
overwrite, dangling-child detection) is verified end to end -- without needing a
running Qt event loop or live EPICS.

How the Qt/EPICS coupling is removed:

* The ``StageSettings`` instance is built with ``__new__`` so ``__init__``
  (which calls ``loadUi`` and needs a ``QApplication``) never runs. Only the
  attributes the three methods actually touch are stubbed in.
* ``Client.from_config`` is patched to return a ``Client`` bound to a temp
  ``FilestoreBackend`` plus a ``FakeControlLayer`` (no real EPICS I/O).
* The module-level ``QInputDialog`` / ``QMessageBox`` are swapped for fakes so
  the collection-name prompt and overwrite prompt return scripted answers.
* ``is_fixed_readonly`` (an EPICS caget) and ``check_auth`` (a file read) are
  replaced with simple stubs on the instance.
"""

import logging
from unittest.mock import MagicMock

import pytest
from superscore.backends.filestore import FilestoreBackend
from superscore.client import Client
from superscore.control_layers._base_shim import EpicsData

from lcls_user_motor_gui.widgets import user_input


class FakeControlLayer:
    """Stand-in for ``superscore.control_layers.core.ControlLayer``.

    ``get`` returns canned ``EpicsData`` for each requested PV; ``put`` records
    every write so ``apply`` can be asserted against.
    """

    def __init__(self):
        # pv_name -> value handed back by get()
        self.values = {}
        # list of (pv_name, value) recorded by put()
        self.puts = []

    def get(self, pvs):
        if isinstance(pvs, str):
            return EpicsData(data=self.values.get(pvs, 0.0))
        return [EpicsData(data=self.values.get(pv, 0.0)) for pv in pvs]

    def put(self, pvs, data=None):
        if isinstance(pvs, (list, tuple)):
            for pv, value in zip(pvs, data):
                self.puts.append((pv, value))
        else:
            self.puts.append((pvs, data))
        # apply() in non-sequential mode does not inspect the return value
        return MagicMock()


class FakeQInputDialog:
    """Returns a scripted ``(text, ok)`` tuple from ``getText``."""

    result = ("User Motors", True)

    @staticmethod
    def getText(*args, **kwargs):
        return FakeQInputDialog.result


class FakeQMessageBox:
    """Records info dialogs and returns a scripted answer from ``question``."""

    Warning = 1
    Information = 2
    Critical = 3
    Yes = 0x4000
    No = 0x10000

    answer = No

    @staticmethod
    def question(*args, **kwargs):
        return FakeQMessageBox.answer


@pytest.fixture
def control_layer():
    return FakeControlLayer()


@pytest.fixture
def store_path(tmp_path):
    return str(tmp_path / "filestore.json")


@pytest.fixture
def client(store_path, control_layer):
    backend = FilestoreBackend(path=store_path)
    backend.initialize()
    # _is_writable is computed at construction time, before the file exists, so
    # it comes back False; the file now exists in a writable tmp dir.
    backend._is_writable = True
    return Client(
        backend=backend,
        control_layer=control_layer,
        enable_editing_past=True,
    )


@pytest.fixture
def patch_client(monkeypatch, client):
    """Make every ``Client.from_config(...)`` call return the temp-backed client."""
    monkeypatch.setattr(user_input.Client, "from_config", lambda cfg: client)
    return client


@pytest.fixture
def patch_dialogs(monkeypatch):
    """Swap the GUI dialogs for scriptable fakes and reset their defaults."""
    FakeQInputDialog.result = ("User Motors", True)
    FakeQMessageBox.answer = FakeQMessageBox.No
    monkeypatch.setattr(user_input, "QInputDialog", FakeQInputDialog)
    monkeypatch.setattr(user_input, "QMessageBox", FakeQMessageBox)
    return FakeQInputDialog, FakeQMessageBox


@pytest.fixture
def stage_settings(patch_client, patch_dialogs):
    """A ``StageSettings`` with only the attributes the workflows touch.

    Built via ``__new__`` so ``__init__``/``loadUi`` (which need a QApplication)
    never run.
    """
    ss = user_input.StageSettings.__new__(user_input.StageSettings)
    ss.logger = logging.getLogger("test.stage_settings")
    ss.msg = MagicMock()
    ss.cfg_path = "unused.cfg"
    ss.ncList = []

    ui = MagicMock()
    ui.prefixName = "TST:UM"
    ui.display_axis_ui.currentRow.return_value = 0
    ui.stage_configs_widget.currentText.return_value = ""
    ss.user_input_widget = ui

    # EPICS / filesystem side effects -> harmless stubs
    ss.is_fixed_readonly = lambda *a, **k: False
    ss.check_auth = lambda *a, **k: True
    return ss
