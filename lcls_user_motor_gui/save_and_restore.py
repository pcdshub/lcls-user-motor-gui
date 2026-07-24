"""
Prototype module for a future as-simple-as-possible save and restore app.

Used in user motors to save motor configurations on one channel
and apply them to another.
"""

import logging
import time
from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum, auto
from string import Template
from typing import Any

from epics import PV, caget_many, get_pv
from tomlkit import document, dump, load, nl, table

epics_type = str | int | float

logger = logging.getLogger(__name__)


@dataclass
class ConfigBase:
    """
    Base class for data we expect to find in all files.

    Parameters
    ----------
    name : str
        A name for the config. This should usually match the filename,
        but is not required to.
    desc : str
        A longer description that explains what the config is for.
    schema_ver : int
        The version of dataclass schema that had been used to create
        this object. This helps us support backwards compatibility
        when we load from file configs.
    metadata : dict[str, Any]
        A place for the user to include any additional data helpful
        for their use case without needing to expand the schema.
    data : list[tuple]
        The pv or pv value data included in the object.
        See subclasses for implementations.
    """

    name: str
    desc: str
    schema_ver: int
    metadata: dict[str, Any]
    data: list[tuple]

    def apply_macros[T: ConfigBase](self: T, macros: dict[str, str]) -> T:
        """
        Replaces macro placeholders with real strings.

        Parameters
        ----------
        macros : dict[str, str]
            Key-value pairs: each key is a macro (e.g. prefix)
            and each value is what to replace it with (e.g. SOME:PVNAME).
            Macros are case-sensitive.
            Note that the same dictionary input is used in both configure_macros
            and apply_macros regardless of the translation direction.
        """
        new_data = []
        for tup in self.data:
            new_elems = []
            for elem in tup:
                if isinstance(elem, str):
                    templ = Template(elem)
                    new_elems.append(templ.safe_substitute(macros))
                else:
                    new_elems.append(elem)
            new_data.append(tuple(new_elems))
        new_config = deepcopy(self)
        new_config.data = new_data
        return new_config

    def configure_macros[T: ConfigBase](self: T, macros: dict[str, str]) -> T:
        """
        Replaces real strings with macro placeholders.

        Parameters
        ----------
        macros : dict[str, str]
            Key-value pairs: each key is a macro (e.g. prefix)
            and each value is what to replace it with (e.g. SOME:PVNAME).
            Macros are case-sensitive.
            Note that the same dictionary input is used in both configure_macros
            and apply_macros regardless of the translation direction.
        """
        new_data = []
        for tup in self.data:
            new_elems = []
            for elem in tup:
                if isinstance(elem, str):
                    new_elem = elem
                    for macro_name, value in macros.items():
                        new_elem = elem.replace(value, f"${{{macro_name}}}")
                    new_elems.append(new_elem)
                else:
                    new_elems.append(elem)
            new_data.append(tuple(new_elems))
        new_config = deepcopy(self)
        new_config.data = new_data
        return new_config

    def get_macros(self) -> list[str]:
        """
        Returns a list of unfilled configured macros.

        You can call this before or after apply_macros or configure_macros
        as a validation step.

        After calling apply_macros with all the configured macros as keys,
        this will return an empty list if everything went well.
        After calling configure_macros, there should be new elements in the list
        that correspond to the keys that were added.
        """
        macros: set[str] = set()
        for tup in self.data:
            for elem in tup:
                if isinstance(elem, str):
                    for ident in Template(elem).get_identifiers():
                        macros.add(ident)
        return sorted(macros)

    def raise_if_not_filled(self):
        """
        Raises a ValueError if there are any unfilled macros.

        This is a built-in validation step using get_macros.
        We will call this before trying to get or put PVs to get a better error message.
        """
        macros = self.get_macros()
        if macros:
            raise ValueError(f"Configuration has unfilled macros {macros}.")


@dataclass
class PVConfig(ConfigBase):
    """
    PVConfig files include only PV names without values.

    The data here is (setpoint, readback)
    """

    data: list[tuple[str, str]]


@dataclass
class ValueConfig(ConfigBase):
    """
    ValueConfig files include PV names and PV values.

    The data here is (setpoint, readback, value)

    Parameters
    ----------
    error_pvs : list[str]
        A list of PVs that we were unable to get the value of.
        They have "None" values in the data and cannot
        be "put" to when we restore.
        This attribute will not be included in the
        file, but it is included as a convenience for calling code to check
        and optionally raise.
        We do not raise by default because this makes it hard
        for the user to get a "partial" save if they'd like one.
    """

    data: list[tuple[str, str, epics_type]]
    error_pvs: list[str] = field(default_factory=list)


def get_live_config(
    source: PVConfig | ValueConfig,
    macros: dict[str, str] | None = None,
    as_template: bool = False,
) -> ValueConfig:
    """
    Returns a config object that reflects live values of the source config.

    Parameters
    ----------
    source : PVConfig or ValueConfig
        Either a PVConfig with no values or a ValueConfig that already has values.
    macros : dict, optional
        Macro subsitutions to make into the source file before reading PVs.
    as_template : bool, optional
        If False (the default), the returned config will have the literal PV values.
        If True, the returned config will have the original macro values, so that
        they could be subbed into again.
    """
    if macros is not None:
        source = source.apply_macros(macros=macros)
    source.raise_if_not_filled()
    pvnames = [tup[1] for tup in source.data]
    vals = caget_many(pvnames)
    error_pvs = []
    for pv, value in zip(pvnames, vals, strict=True):
        if value is None:
            logger.error(
                f"Unable to get data from {pv}, likely disconnected or timed out."
            )
        error_pvs.append(pv)
    config = ValueConfig(
        name=source.name,
        desc=source.desc,
        schema_ver=source.schema_ver,
        metadata=source.metadata,
        data=[
            (tup[0], tup[1], val) for tup, val in zip(source.data, vals, strict=True)
        ],
        error_pvs=error_pvs,
    )
    if as_template and macros is not None:
        config = config.configure_macros(macros=macros)
    return config


class PutResult(Enum):
    """
    The error response from doing a caput.

    "success" is a put that went well, all other results are different failure states:

    - unset: the result has not been decided yet
    - no_value: we tried to put "None"
    - disconnect: the PV did not connect
    - timeout: the PV connected, but then didn't complete the put
    - write_access: we did not have write access
    - type_mitmatch: the value we put did not match the PV's type
    - enum_mismatch: the value we put was not an available enum value or string
    """

    unset = auto()
    success = auto()
    no_value = auto()
    disconnect = auto()
    timeout = auto()
    write_access = auto()
    type_mismatch = auto()
    enum_mismatch = auto()


@dataclass
class PutInfo:
    """
    Information about how the put went.

    Parameters
    ----------
    pvname : str
        The name of the PV we were putting to.
    value : Any
        The value we tried to put
    result : PutResult
        How it went
    """

    pvname: str
    value: Any
    result: PutResult

    def set_result(self, result: PutResult):
        """Update the result attribute and log it."""
        self.result = result
        self.log_result()

    def get_message(self) -> str:
        """Return a message explaining the success or failure result."""
        if self.result == PutResult.success:
            return f"Successfully put {self.value} to {self.pvname}"
        return (
            f"Failed to put {self.value} to {self.pvname} with error {self.result.name}"
        )

    def log_result(self):
        """Log an error if the put didn't go well, or debug if it did go well."""
        if self.result == PutResult.success:
            logger.debug(self.get_message())
        else:
            logger.error(self.get_message())

    def raise_if_not_success(self):
        """Raise an exception if the put didn't go well."""
        if self.result == PutResult.success:
            return
        raise RuntimeError(self.get_message())


def caput_many_with_info(
    pvnames: list[str],
    values: list[Any],
    timeout: float = 5.0,
) -> dict[str, PutInfo]:
    """Put to many PVs and get detailed results."""
    # Start the timer for timeout
    start_time = time.monotonic()
    # Initialize the putinfo in the pvnames order
    put_infos: dict[str, PutInfo] = {
        pvn: PutInfo(pvname=pvn, value=val, result=PutResult.unset)
        for pvn, val in zip(pvnames, values, strict=True)
    }
    # Loop through, we'll only connect if the value to put is non-None
    pvs_to_put: dict[str, tuple[PV, Any]] = {}
    for pv, value in zip(pvnames, values, strict=True):
        if value is None:
            put_infos[pv].set_result(PutResult.no_value)
            continue
        this_pv = get_pv(pv, form="ctrl")
        this_pv.connect()
        pvs_to_put[pv] = (this_pv, value)

    # Keep track of which PVs have been processed already
    put_started: set[PV] = set()

    def put_cb(pvname: str, **_):
        """Record success when the put completes."""
        put_infos[pvname].set_result(PutResult.success)

    # Until we time out, wait for connections and start the puts
    while time.monotonic() - start_time < timeout:
        for pvname, (pvo, value) in pvs_to_put.items():
            if pvo.connected and pvo not in put_started:
                put_started.add(pvo)
                md = pvo.get_with_metadata()
                if not md["write_access"]:
                    put_infos[pvname].set_result(PutResult.write_access)
                    continue
                elif md["enum_strs"] is not None:
                    if isinstance(value, int):
                        try:
                            md["enum_strs"][value]
                        except IndexError:
                            put_infos[pvname].set_result(PutResult.enum_mismatch)
                            continue
                    elif isinstance(value, str):
                        if value not in md["enum_strs"]:
                            put_infos[pvname].set_result(PutResult.enum_mismatch)
                            continue
                elif not isinstance(value, type(md["value"])):
                    put_infos[pvname].set_result(PutResult.type_mismatch)
                    continue
                pvo.put(value, callback=put_cb)
        if len(put_started) >= len(pvs_to_put):
            break
        time.sleep(0.1)

    # Finish waiting until timeout if needed
    while time.monotonic() - start_time < timeout:
        all_finished = True
        for pinfo in put_infos.values():
            if pinfo.result == PutResult.unset:
                all_finished = False
                break
        if all_finished:
            break
        time.sleep(0.1)

    # Loop through one last time, the remaining unsets are either disconnect or timeout
    for pvname, pinfo in put_infos.items():
        if pinfo.result != PutResult.unset:
            continue
        if pvs_to_put[pvname][0].connected:
            pinfo.set_result(PutResult.timeout)
        else:
            pinfo.set_result(PutResult.disconnect)

    return put_infos


def put_live_config(
    source: ValueConfig, macros: dict[str, str] | None = None
) -> dict[str, PutInfo]:
    """
    Puts a config object's values back to the configured setpoints.

    Parameters
    ----------
    source : Template or Config
        Either a Template with no values or a config that already has values.
    macros : dict, optional
        Macro subsitutions to make into the source before writing to PVs.
    """
    if macros is not None:
        source.apply_macros(macros=macros)
    source.raise_if_not_filled()
    pvnames = [tup[0] for tup in source.data]
    values = [tup[2] for tup in source.data]
    return caput_many_with_info(pvnames, values)


def config_to_file(filename: str, config: PVConfig | ValueConfig):
    """Save a config object as a toml file."""
    doc = document()

    info_table = table()
    info_table.add("name", config.name)
    info_table.add("desc", config.desc)
    if isinstance(config, PVConfig):
        info_table.add("type", "PVConfig")
    elif isinstance(config, ValueConfig):
        info_table.add("type", "ValueConfig")
    else:
        raise TypeError(f"Unknown config type {type(config)}")
    info_table.add("schema_ver", 0)
    doc.add("info", info_table)
    doc.add(nl())

    meta_table = table()
    for key, value in config.metadata.items():
        meta_table.add(key, value)
    doc.add("metadata", meta_table)
    doc.add(nl())

    data_table = table()
    data_table.add("data", config.data)
    doc.add("data", data_table)
    doc.add(nl())

    with open(filename, "w") as fd:
        dump(doc, fd)


def config_from_file(filename: str) -> PVConfig | ValueConfig:
    """Load a toml file into a config object."""
    with open(filename, "r") as fd:
        doc = load(fd)

    info_table = doc["info"]
    meta_table = doc["metadata"]
    data_table = doc["data"]

    config_type = info_table["type"]

    if config_type == "PVConfig":
        cls = PVConfig
    elif config_type == "ValueConfig":
        cls = ValueConfig
    else:
        raise ValueError(f"Invalid config type {config_type}")

    return cls(
        name=info_table["name"],
        desc=info_table["desc"],
        schema_ver=info_table["schema_ver"],
        metadata=dict(meta_table),
        data=data_table["data"],
    )
