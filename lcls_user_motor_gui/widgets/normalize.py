def normalize_hardware_channel(channel: str | None, fallback: str) -> str:
    """
    Normalize EPICS drive hardware channel values to two-digit PV path components.
    E.g. Axis_01 has 1 for the passed-in index, after formatting it -> TST:UM:MMS:01.
    Where 01 is the padded object.

    Parameters
    ----------
    channel : str | None
        EPICS channel value to normalize.
    fallback : str
        Value to use when channel is empty.

    Returns
    -------
    str
        Two-digit channel string, or the stripped and zero-filled channel value.
    """
    if not channel:
        channel = fallback
    channel = str(channel).strip()
    try:
        return f"{int(float(channel)):02}"
    except ValueError:
        return channel.zfill(2)


def normalize_hardware_id(hardware_id):
    """
    Normalize EPICS hardware IDs for PV prefix matching.

    Args:
        hardware_id (Any): EPICS hardware ID value to normalize.

    Returns:
        str: Stripped hardware ID, or "None" when missing.
    """
    if not hardware_id:
        return "None"
    return str(hardware_id).strip()


def hardware_prefix_for_coe(prefix_name, hardware_id, hardware_channel):
    """
    Return the hardware prefix for COE PV matching.

    Args:
        prefix_name (str): Base PV prefix.
        hardware_id (str): Normalized hardware ID.
        hardware_channel (str): Normalized hardware channel fallback.

    Returns:
        str: Matching hardware prefix, or the full hardware prefix fallback.
    """
    hardware_type, separator, hardware_instance_num = hardware_id.partition("_")
    if separator:
        hardware_instance_num = normalize_hardware_channel(
            hardware_instance_num, hardware_channel
        )
        return f"{prefix_name}:{hardware_type}:{hardware_instance_num}"

    return f"{prefix_name}:{hardware_id}:{hardware_channel}"


def remove_name_rbv(pv_name):
    """
    Remove the ':Name_RBV' suffix from a PV name if present.

    Args:
        pv_name (str): PV name to process.

    Returns:
        str: PV name without ':Name_RBV', or the original PV name.
    """
    return pv_name.removesuffix(":Name_RBV")
