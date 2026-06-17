import json
import logging
import os
import re
from getpass import getuser
from pathlib import Path

import epics
import numpy as np
from pcdsutils.qt.designer_display import DesignerDisplay
from pydm.widgets.label import PyDMLabel
from pydm.widgets.line_edit import PyDMLineEdit
from PyQt5 import QtCore
from PyQt5.QtGui import QColor
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
    QInputDialog,
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
from qtpy.uic import loadUi
from superscore.backends.core import SearchTerm
from superscore.client import Client
from superscore.errors import EntryNotFoundError
from superscore.model import Collection, Parameter, Setpoint, Snapshot

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


class StageSettings(QDialog):
    def __init__(self, user_input_widget, parent=None, logger=None):
        super(StageSettings, self).__init__(parent)
        self.logger = logger
        ui_path = Path(__file__).resolve().parent / "./../ui" / "stage-config.ui"
        # ui_path = Path(__file__).resolve().parent
        print(ui_path)
        loadUi(str(ui_path), self)  # Load the UI from the .ui file
        self.egu_rev = self.findChild(PyDMLineEdit, "egu_rev")
        self.step_rev = self.findChild(PyDMLineEdit, "step_rev")
        self.run_current = self.findChild(PyDMLineEdit, "run_current")
        self.encoder_scaling = self.findChild(PyDMLineEdit, "encoder_scaling")
        self.backlash = self.findChild(PyDMLineEdit, "backlash")
        self.generate_params = self.findChild(QPushButton, "generate_params")
        self.save_collection = self.findChild(QPushButton, "save_collection")
        self.take_snapshot_button = self.findChild(QPushButton, "take_snapshot")
        self.apply_snapshot_button = self.findChild(QPushButton, "apply_snapshot")
        self.current_axis_combo_box = self.findChild(QComboBox, "current_axis")
        self.current_collection = self.findChild(QComboBox, "current_collection")
        # self.user_input_widget = user_input_widget
        self.user_input_widget = user_input_widget
        self.ncList = user_input_widget.ncList
        self.cfg_path = user_input_widget.cfg_path
        self.save_collection.clicked.connect(self.save_to_collection)
        self.take_snapshot_button.clicked.connect(self.take_snapshot_now)
        self.generate_params.clicked.connect(self.calculate_params)
        self.apply_snapshot_button.clicked.connect(self.apply_snapshot_now)
        self.msg = QMessageBox()
        self.currAxis = ""
        self.currConfig = ""
        # self.user_input_widget.populate_collections()
        # self.user_input_widget.stage_load.clicked.connect(self.user_input_widget.load_stage_settings)

    # def apply_snapshot_now(self):
    #     self.logger.info(f"in apply_snapshot")
    #     # The snapshot to apply is identified solely by the selected collection
    #     # name, so the current axis isn't needed here.
    #     self.currConfig = self.user_input_widget.stage_configs_widget.currentText()
    #     print(f"Current Config: {self.currConfig}")
    #     if not self.currConfig:
    #         self.msg.setIcon(QMessageBox.Warning)
    #         self.msg.setText("Please select a collection to apply a snapshot from!")
    #         self.msg.setWindowTitle("Warning")
    #         self.msg.exec_()
    #         return
    #     superscore_client = Client.from_config(self.cfg_path)
    #     results = list(
    #         superscore_client.search(
    #             SearchTerm("title", "eq", self.currConfig),
    #             SearchTerm("entry_type", "eq", Collection),
    #         )
    #     )
    #     if not results:
    #         self.logger.warning(
    #             f"collection '{self.currConfig}' not found, cannot apply snapshot"
    #         )
    #         self.msg.setIcon(QMessageBox.Warning)
    #         self.msg.setText(f"Collection '{self.currConfig}' not found!")
    #         self.msg.setWindowTitle("Error")
    #         self.msg.exec_()
    #         return

    #     collection = results[0]

    #     # Find the snapshot(s) taken from this collection and restore the most
    #     # recent one. apply() needs a data-filled Snapshot, not the Collection.
    #     snapshots = self._find_snapshots_for_collection(superscore_client, collection)
    #     if not snapshots:
    #         self.logger.warning(f"no snapshot found for collection '{self.currConfig}'")
    #         self.msg.setIcon(QMessageBox.Warning)
    #         self.msg.setText(f"No snapshot found for collection '{self.currConfig}'!")
    #         self.msg.setWindowTitle("Error")
    #         self.msg.exec_()
    #         return

    #     # Apply the most recent snapshot. Some snapshots may be incomplete
    #     # (their stored PV values are missing from the backend), which makes
    #     # them un-appliable; skip those and fall back to the next newest one.
    #     for snapshot in sorted(snapshots, key=lambda s: s.creation_time, reverse=True):
    #         self.logger.info(
    #             f"applying snapshot {snapshot.uuid} "
    #             f"({snapshot.creation_time}) for collection '{self.currConfig}'"
    #         )
    #         try:
    #             # Fill the snapshot to resolve all UUID children into full Entry
    #             # objects before applying. Without this, apply() cannot resolve
    #             # the PVs and raises EntryNotFoundError.
    #             superscore_client.fill(snapshot)
    #             superscore_client.apply(snapshot)
    #         except (EntryNotFoundError, IndexError) as exc:
    #             self.logger.warning(
    #                 f"snapshot {snapshot.uuid} is incomplete (missing stored "
    #                 f"values); skipping it. Reason: {type(exc).__name__}: {exc}"
    #             )
    #             continue
    #         print(f"Applied snapshot UUID: {snapshot.uuid}")
    #         return

    #     self.logger.warning(
    #         f"no applicable snapshot found for collection '{self.currConfig}'"
    #     )
    #     self.msg.setIcon(QMessageBox.Warning)
    #     self.msg.setText(
    #         f"No applicable snapshot for collection '{self.currConfig}'. "
    #         f"The stored snapshot(s) are incomplete \u2014 please take a new "
    #         f"snapshot and try again."
    #     )
    #     self.msg.setWindowTitle("Error")
    #     self.msg.exec_()

    def apply_snapshot_now(self):
        self.logger.info(f"in apply_snapshot")
        # The snapshot to apply is identified solely by the selected collection
        # name, so the current axis isn't needed here.
        self.currConfig = self.user_input_widget.stage_configs_widget.currentText()
        print(f"Current Config: {self.currConfig}")
        if not self.currConfig:
            self.msg.setIcon(QMessageBox.Warning)
            self.msg.setText("Please select a collection to apply a snapshot from!")
            self.msg.setWindowTitle("Warning")
            self.msg.exec_()
            return
        superscore_client = Client.from_config(self.cfg_path)
        results = list(
            superscore_client.search(
                SearchTerm("title", "eq", self.currConfig),
                SearchTerm("entry_type", "eq", Collection),
            )
        )
        if not results:
            self.logger.warning(
                f"collection '{self.currConfig}' not found, cannot apply snapshot"
            )
            self.msg.setIcon(QMessageBox.Warning)
            self.msg.setText(f"Collection '{self.currConfig}' not found!")
            self.msg.setWindowTitle("Error")
            self.msg.exec_()
            return

        collection = results[0]

        # Find the snapshot(s) taken from this collection and restore the most
        # recent one. apply() needs a data-filled Snapshot, not the Collection.
        snapshots = self._find_snapshots_for_collection(superscore_client, collection)
        if not snapshots:
            self.logger.warning(f"no snapshot found for collection '{self.currConfig}'")
            self.msg.setIcon(QMessageBox.Warning)
            self.msg.setText(f"No snapshot found for collection '{self.currConfig}'!")
            self.msg.setWindowTitle("Error")
            self.msg.exec_()
            return

        # Restore the most recent snapshot. Some snapshots may be incomplete
        # (their stored PV values are missing from the backend), which makes
        # them un-appliable; skip those and fall back to the next newest one.
        for snapshot in sorted(snapshots, key=lambda s: s.creation_time, reverse=True):
            self.logger.info(
                f"applying snapshot {snapshot.uuid} "
                f"({snapshot.creation_time}) for collection '{self.currConfig}'"
            )
            try:
                superscore_client.fill(snapshot)
                self._restore_snapshot(superscore_client, snapshot)
            except (EntryNotFoundError, IndexError) as exc:
                self.logger.warning(
                    f"snapshot {snapshot.uuid} is incomplete (missing stored "
                    f"values); skipping it. Reason: {type(exc).__name__}: {exc}"
                )
                continue
            print(f"Applied snapshot UUID: {snapshot.uuid}")
            return

        self.logger.warning(
            f"no applicable snapshot found for collection '{self.currConfig}'"
        )
        self.msg.setIcon(QMessageBox.Warning)
        self.msg.setText(
            f"No applicable snapshot for collection '{self.currConfig}'. "
            f"The stored snapshot(s) are incomplete \u2014 please take a new "
            f"snapshot and try again."
        )
        self.msg.setWindowTitle("Error")
        self.msg.exec_()

    def _restore_snapshot(self, client, snapshot):
        setpoints = [
            entry
            for entry in client._gather_leaves(snapshot)
            if isinstance(entry, Setpoint)
        ]
        client.apply(Snapshot(children=setpoints))

    def save_to_collection(self):
        print(f"in save_to_collection")
        coll_name, ok = QInputDialog.getText(
            self,
            "Save Collection",
            "Enter a name for the collection:",
            text="User Motors",
        )
        if not ok or not coll_name.strip():
            print("Save to collection cancelled")
            return
        coll_name = coll_name.strip()
        self.currAxis = self.user_input_widget.display_axis_ui.currentRow()
        currAxisFormatted = (
            f"{self.user_input_widget.prefixName}:MMS:{self.currAxis+1:02}"
        )
        print("Saving settings for axis: %s", currAxisFormatted)
        # self.currConfig = self.user_input_widget.stage_configs_widget.currentText()
        # print(f"Current Config: {self.currConfig}")
        # cfg_path = Path(__file__).resolve().parent / "./../.." / "superscore.cfg"
        print(f"cfg path: {self.cfg_path}")
        superscore_client = Client.from_config(self.cfg_path)
        print(superscore_client)

        # Always save into the filestore configured in superscore.cfg so that
        # Take Snapshot and Apply Snapshot (which also use Client.from_config)
        # operate on the same store. The backend path may not exist yet or be an
        # empty 0-byte file; the filestore backend's load() only handles a
        # missing file, so an empty/invalid file raises JSONDecodeError.
        # Initialize a valid, empty JSON database in that case before saving.
        save_path = getattr(superscore_client.backend, "path", "")
        print(f"saving collection to file: {save_path}")
        if not os.path.exists(save_path) or os.stat(save_path).st_size == 0:
            superscore_client.backend.initialize()

        str_currAxis = f"^{currAxisFormatted}:NC:[^:]+:Goal_RBV$"
        list_ncRBV = []
        counter = 0
        for pv in self.ncList:
            if re.search(str_currAxis, pv):
                counter = counter + 1
                # A motor whose acceleration record is FIXED_READONLY has a
                # fixed (non-writable) Goal, so skip it. The read-only state
                # lives on the ``:Acc_RBV`` PV, not on the Goal_RBV value.
                acc_pv = pv.replace(":Goal_RBV", ":Acc_RBV")
                if not self.is_fixed_readonly(acc_pv):
                    list_ncRBV.append(pv)
        print(f"len of nv pvs: {counter}")
        print(f"len of writable (non-FRO) pvs: {len(list_ncRBV)}")

        coll = Collection(
            title=coll_name,
            tags=["demo"],
        )

        coll.children = [
            Parameter(
                pv_name=pv.replace("Goal_RBV", "Goal"),
                description="",
                readback=Parameter(
                    pv_name=pv,
                    description="",
                    read_only=True,
                ),
                read_only=False,  # these are Goal setpoints; keep writable
            )
            for pv in list_ncRBV
        ]

        # Saving always builds a Collection with a fresh UUID, so a second save
        # of the same title would create a duplicate Collection (and make the
        # title lookups in take/apply snapshot ambiguous). If a Collection with
        # this title already exists in the target store, reuse its UUID so the
        # save updates it in place instead of duplicating it. ``enable_editing_past``
        # is set in superscore.cfg, so the existing entry remains editable.
        existing = list(
            superscore_client.search(
                SearchTerm("title", "eq", coll_name),
                SearchTerm("entry_type", "eq", Collection),
            )
        )
        if existing:
            coll.uuid = existing[0].uuid
            print(f"updating existing collection '{coll_name}' ({coll.uuid})")

        self._save_entry_children(superscore_client, coll)
        superscore_client.save(coll)
        print("Saved collection UUID:", coll.uuid)
        # Guard against the "dangling children" corruption (children written as
        # bare UUIDs with no backing entry), which silently makes a collection
        # un-snapshottable / un-appliable later. Verify what actually landed on
        # disk and warn the user immediately if the save didn't persist cleanly.
        ok, detail = self._verify_entry_persisted(superscore_client, coll.uuid)
        if not ok:
            self.logger.error(
                f"collection '{coll_name}' did not persist cleanly: {detail}"
            )
            self.msg.setIcon(QMessageBox.Warning)
            self.msg.setText(
                f"Collection '{coll_name}' was saved but is incomplete "
                f"({detail}). Its PVs are not properly stored, so snapshots "
                f"taken from it will fail. Please try saving again."
            )
            self.msg.setWindowTitle("Warning")
            self.msg.exec_()
        # self.populate_collections()

    # def populate_collections(self):
    #     # search by title (or tags/uuid/etc.)
    #     self.user_input_widget.stage_configs_widget.clear_items()
    #     client = Client.from_config(self.cfg_path)
    #     coll = next(client.search(SearchTerm("title", "eq", "User Motors")))
    #     self.user_input_widget.stage_configs_widget.add_item(coll.title)
    #     self.user_input_widget.stage_configs_widget.setEnabled(True)
    #     print(coll.uuid, coll.title)

    def is_fixed_readonly(self, pvname: str, timeout: float = 10.0) -> bool:
        try:
            self.logger.debug(f"checking access of the pv: {pvname}")
            pv = epics.PV(pvname, auto_monitor=False)
            if pv.wait_for_connection(timeout=timeout):
                self.logger.debug(f"connected to pv, {pv.get(as_string=True)}{pvname}")
                return pv.get(as_string=True) == "FIXED_READONLY"
        except Exception as e:
            self.logger.error(f"Error checking access for {pvname}: {e}")
        # safest default: treat as not fixed-read-only
        self.logger.info(f"{pvname} did not connect")
        return False

    def take_snapshot_now(self):
        self.logger.info(f"in take_snapshot")
        # check to see if user is authorized
        user = getuser()

        b_isUserAuth = self.check_auth(user)
        print(f"current user: {user}, is user auth: {b_isUserAuth}")
        # if not b_isUserAuth:
        #     self.msg.setIcon(QMessageBox.Warning)
        #     self.msg.setText("You are not authorized to save the collection, please ask the CDSO.")
        #     self.msg.setWindowTitle("Error")
        #     self.msg.exec_()
        # else:

        # Find the collection currently selected in the stage configs widget.
        coll_title = self.user_input_widget.stage_configs_widget.currentText()
        if not coll_title:
            self.msg.setIcon(QMessageBox.Warning)
            self.msg.setText("Please select a collection to snapshot!")
            self.msg.setWindowTitle("Warning")
            self.msg.exec_()
            return

        self.logger.info(f"taking snapshot for collection: {coll_title}")
        superscore_client = Client.from_config(self.cfg_path)
        results = list(
            superscore_client.search(
                SearchTerm("title", "eq", coll_title),
                SearchTerm("entry_type", "eq", Collection),
            )
        )
        if not results:
            self.msg.setIcon(QMessageBox.Warning)
            self.msg.setText(f"Collection '{coll_title}' not found!")
            self.msg.setWindowTitle("Error")
            self.msg.exec_()
            return

        collection = results[0]
        dest_snapshot = Snapshot(
            title=collection.title,
            tags=collection.tags.copy(),
            origin_collection=collection,
        )
        # A Collection stores its PVs as references to child entries in the
        # backing store. If any referenced child is missing (e.g. the store was
        # only partially written or got corrupted), ``snap`` raises
        # EntryNotFoundError while gathering the PVs. Surface a clear message and
        # tell the user how to recover instead of crashing the GUI.
        try:
            snapshot = superscore_client.snap(collection, dest=dest_snapshot)
        except EntryNotFoundError as exc:
            self.logger.error(
                f"collection '{coll_title}' is incomplete, cannot snapshot: {exc}"
            )
            self.msg.setIcon(QMessageBox.Warning)
            self.msg.setText(
                f"Collection '{coll_title}' is incomplete \u2014 some of its PVs "
                f"are missing from the backing store, so a snapshot can't be "
                f"taken. Please re-save the collection and try again."
            )
            self.msg.setWindowTitle("Error")
            self.msg.exec_()
            return

        snapshot.origin_collection = collection.uuid

        # A collection should hold a single snapshot. If one already exists for
        # this collection, ask the user whether to overwrite it. On "yes" the old
        # snapshot(s) are deleted and replaced; on "no" the freshly-taken snapshot
        # is discarded (never saved) and we bail out.
        existing_snapshots = self._find_snapshots_for_collection(
            superscore_client, collection
        )
        stale_same_title = self._find_stale_snapshots_for_collection_title(
            superscore_client, collection
        )
        snapshots_to_overwrite = {
            snap.uuid: snap for snap in (existing_snapshots + stale_same_title)
        }
        if snapshots_to_overwrite:
            answer = QMessageBox.question(
                self,
                "Overwrite snapshot?",
                f"A snapshot already exists for collection '{coll_title}'. "
                f"Do you want to overwrite it?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                self.logger.info(
                    f"user declined to overwrite snapshot for '{coll_title}'; "
                    f"discarding new snapshot"
                )
                return
            for old in snapshots_to_overwrite.values():
                self.logger.info(f"deleting old snapshot {old.uuid} for '{coll_title}'")
                superscore_client.delete(old)

        # superscore can only serialize scalar values (int/str/float/bool).
        # Some PVs (e.g. the engineering-units ":Eu:Goal_RBV") are char waveforms
        # that the control layer reads back as numpy arrays, which break the JSON
        # backend on save and leave the snapshot incomplete. Coerce any numpy
        # values to serializable scalars before saving.
        self._sanitize_snapshot_values(snapshot)

        self._save_entry_children(superscore_client, snapshot)
        superscore_client.save(snapshot)
        print(f"Saved snapshot UUID: {snapshot.uuid} for collection {coll_title}")
        ok, detail = self._verify_entry_persisted(superscore_client, snapshot.uuid)
        if not ok:
            self.logger.error(
                f"snapshot '{snapshot.uuid}' did not persist cleanly: {detail}"
            )
            self.msg.setIcon(QMessageBox.Warning)
            self.msg.setText(
                f"Snapshot for '{coll_title}' was saved but is incomplete "
                f"({detail}). Please take a new snapshot and try again."
            )
            self.msg.setWindowTitle("Warning")
            self.msg.exec_()

    def _save_entry_children(self, client, entry):
        for child in getattr(entry, "children", []) or []:
            self._save_entry_children(client, child)
            readback = getattr(child, "readback", None)
            if readback is not None:
                client.save(readback)
            client.save(child)

    def _find_snapshots_for_collection(self, client, collection):
        """
        Return all Snapshots in the backing store whose origin is ``collection``.

        Snapshots are matched on their origin collection's UUID rather than by
        title (a snapshot copies its collection's title, so a title search is
        ambiguous). ``find_origin_collection`` raises (ValueError when a snapshot
        records no origin, EntryNotFoundError when the origin entry is missing),
        so each snapshot is guarded individually: one orphaned/corrupt snapshot
        must not abort the search.
        """
        snapshots = []
        for snap in client.search(SearchTerm("entry_type", "eq", Snapshot)):
            try:
                origin = client.find_origin_collection(snap)
            except (ValueError, EntryNotFoundError) as exc:
                self.logger.debug(
                    f"skipping snapshot {snap.uuid}: cannot resolve origin "
                    f"collection ({exc})"
                )
                continue
            if origin.uuid == collection.uuid:
                snapshots.append(snap)
        return snapshots

    def _find_stale_snapshots_for_collection_title(self, client, collection):
        """
        Return stale snapshots for ``collection`` that match on title but whose
        origin cannot be resolved.

        Corrupt snapshots can lose resolvable origin links. Those are skipped by
        ``_find_snapshots_for_collection`` and therefore never overwritten,
        making it look like each new take creates a duplicate.
        """
        stale = []
        for snap in client.search(
            SearchTerm("entry_type", "eq", Snapshot),
            SearchTerm("title", "eq", collection.title),
        ):
            try:
                origin = client.find_origin_collection(snap)
            except (ValueError, EntryNotFoundError) as exc:
                self.logger.debug(
                    f"snapshot {snap.uuid} matches title '{collection.title}' "
                    f"but origin is unresolved ({exc}); treating as stale"
                )
                stale.append(snap)
                continue

            if origin.uuid != collection.uuid:
                self.logger.debug(
                    f"snapshot {snap.uuid} shares title '{collection.title}' "
                    f"but belongs to {origin.uuid}; leaving it untouched"
                )
        return stale

    def _verify_entry_persisted(self, client, entry_uuid):
        """
        Confirm an entry just saved to the backend persisted with resolvable
        children, not dangling bare-UUID references.

        Reads the backend's JSON file directly and checks that every child of
        ``entry_uuid`` is either stored inline (a nested object) or a UUID that
        has a backing top-level entry. Children that are bare UUIDs with no
        backing entry are the corruption mode that makes snapshots/collections
        "incomplete (missing stored values)".

        Returns ``(ok, detail)`` where ``ok`` is False on detected corruption.
        """
        path = getattr(client.backend, "path", None)
        if not path or not os.path.exists(path):
            return True, "no backing file to verify"
        try:
            with open(path) as fd:
                data = json.load(fd)
        except (OSError, ValueError) as exc:
            return False, f"could not read store: {exc}"

        entries = data.get("entries", [])
        backing = set()
        target = None
        for entry in entries:
            for _cls, body in entry.items():
                if not isinstance(body, dict):
                    continue
                uuid = body.get("uuid")
                if uuid is not None:
                    backing.add(uuid)
                if uuid == str(entry_uuid):
                    target = body
        if target is None:
            return False, "saved entry not found in store"

        children = target.get("children") or []
        if not children:
            return True, "ok"
        dangling = [c for c in children if isinstance(c, str) and c not in backing]
        if dangling:
            return False, (
                f"{len(dangling)} of {len(children)} children missing backing "
                f"entries"
            )
        return True, "ok"

    @staticmethod
    def _sanitize_snapshot_values(snapshot):
        """
        Convert non-serializable PV values stored on a snapshot's children into
        scalars that superscore's JSON backend can persist.

        numpy scalars become their Python equivalents. 1-D integer/char arrays
        (e.g. ``DBR_CHAR`` strings such as engineering units) are decoded to a
        string. Any other numpy array can't be represented as a scalar value, so
        it is dropped (set to ``None``) and a warning is logged.
        """

        def coerce(value, pv_name):
            if isinstance(value, np.ndarray):
                if value.ndim == 1 and value.dtype.kind in ("u", "i"):
                    try:
                        raw = bytes(int(x) & 0xFF for x in value)
                        return raw.split(b"\x00", 1)[0].decode("latin-1")
                    except Exception:
                        pass
                logging.getLogger(__name__).warning(
                    "dropping unserializable array value for %s", pv_name
                )
                return None
            if isinstance(value, np.integer):
                return int(value)
            if isinstance(value, np.floating):
                return float(value)
            if isinstance(value, np.bool_):
                return bool(value)
            return value

        for child in getattr(snapshot, "children", []) or []:
            if hasattr(child, "data"):
                child.data = coerce(child.data, getattr(child, "pv_name", "?"))

    def calculate_params(self):
        egu_rev = self.egu_rev.text()
        step_rev = self.step_rev.text()
        run_current = self.run_current.text()
        encoder_scaling = self.encoder_scaling.text()
        backlash = self.backlash.text()
        generate_params = self.generate_params.text()

        self.logger.debug(
            "calculate_params inputs: egu_rev=%s, step_rev=%s, run_current=%s, "
            "encoder_scaling=%s, backlash=%s, generate_params=%s",
            egu_rev,
            step_rev,
            run_current,
            encoder_scaling,
            backlash,
            generate_params,
        )

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
        self.refresh_list.clicked.connect(self.refresh_collections)

    def populate_collections(self, search_term="", attr="title"):
        """
        Populate the stage configs widget with collections matching ``search_term``.

        Parameters
        ----------
        search_term : str, optional
            Value to fuzzy-match against ``attr``. An empty string returns all
            collections. Defaults to "".
        attr : str, optional
            The collection attribute to search against (e.g. "title", "tags").
            Defaults to "title".
        """
        self.stage_configs_widget.clear_items()
        # self.stage_configs_widget.add_item("None")
        client = Client.from_config(self.cfg_path)
        results = list(
            client.search(
                SearchTerm(attr, "like", search_term),
                SearchTerm("entry_type", "eq", Collection),
            )
        )
        if results:
            for coll in results:
                self.stage_configs_widget.add_item(coll.title)
                print(coll.uuid, coll.title)
            self.stage_configs_widget.setEnabled(True)
        else:
            print(f"No collection matching {attr} ~ '{search_term}' found.")
            self.stage_configs_widget.setEnabled(False)

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
        axis_item = self.display_axis_ui.currentRow()
        print(f"axis item: {axis_item}, {type(axis_item)}")
        if axis_item == -1:
            self.msg.setIcon(QMessageBox.Warning)
            self.msg.setText("Please select an axis!")
            self.msg.setWindowTitle("Warning")
            self.msg.exec_()
        else:
            currAxisFormatted = f"{self.prefixName}:MMS:{axis_item+1:02}"
            print("Saving settings for axis: %s", currAxisFormatted)
            currConfig = self.stage_configs_widget.currentText()
            print(f"Current Config: {currConfig}")

            stageSettings = StageSettings(
                user_input_widget=self, parent=self, logger=self.logger
            )
            # START HERE I changed these to combo boxes
            stageSettings.current_axis_combo_box.clear()
            for items in range(self.display_axis_ui.count()):
                stageSettings.current_axis_combo_box.addItem(
                    self.display_axis_ui.item(items).text()
                )

            stageSettings.current_collection.clear()
            stageSettings.current_collection.addItems(
                self.stage_configs_widget.all_items()
            )

            # stageSettings.current_axis_combo_box.setText(currAxisFormatted)
            # stageSettings.current_collection.setText(currConfig or "")
            stageSettings.exec_()

    def refresh_collections(self):
        self.logger.info(f"in refresh_collections")
        self.populate_collections()
