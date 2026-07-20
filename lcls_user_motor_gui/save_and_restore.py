"""
Prototype module for a future as-simple-as-possible save and restore app.

Used in user motors to save motor configurations on one channel
and apply them to another.
"""

from copy import deepcopy
from dataclasses import dataclass
from string import Template
from typing import Any

from epics import caget_many, caput_many
from tomlkit import document, dump, load, nl, table

epics_type = str | int | float


@dataclass
class ConfigBase:
    """Base class for data we expect to find in all files."""

    name: str
    desc: str
    schema: str
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
    """

    data: list[tuple[str, str, epics_type]]


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
    pvnames = [tup[1] for tup in source.data]
    vals = caget_many(pvnames)
    config = ValueConfig(
        name=source.name,
        desc=source.desc,
        schema=source.schema,
        metadata=source.metadata,
        data=[
            (tup[0], tup[1], val) for tup, val in zip(source.data, vals, strict=True)
        ],
    )
    if as_template and macros is not None:
        config = config.configure_macros(macros=macros)
    return config


def put_live_config(source: ValueConfig, macros: dict[str, str] | None = None):
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
    pvnames = [tup[0] for tup in source.data]
    values = [tup[2] for tup in source.data]
    caput_many(pvnames, values)


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
    info_table.add("schema", "v0")
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
        schema=info_table["schema"],
        metadata=dict(meta_table),
        data=data_table["data"],
    )
