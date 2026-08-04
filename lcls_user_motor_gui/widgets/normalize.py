def normalize_hardware_channel(channel, fallback):
    """
    Normalize EPICS channel values to two-digit PV path components.

    Args:
        channel (Any): EPICS channel value to normalize.
        fallback (Any): Value to use when channel is empty.

    Returns:
        str: Two-digit channel string, or the stripped and zero-filled channel value.
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


def hardware_prefix_for_coe(prefix_name, hardware_id, hardware_channel, coe_list):
    """
    Return the hardware prefix that exists in the loaded COE PV list.

    Args:
        prefix_name (str): Base PV prefix.
        hardware_id (str): Normalized hardware ID.
        hardware_channel (str): Normalized hardware channel.
        coe_list (Iterable[str]): COE PV names to match against.

    Returns:
        str: Matching hardware prefix, or the full hardware prefix fallback.
    """
    hardware_ids = [hardware_id]
    if "_" in hardware_id:
        hardware_ids.append(hardware_id.split("_", 1)[0])

    for candidate in hardware_ids:
        hardware_prefix = f"{prefix_name}:{candidate}:{hardware_channel}"
        coe_prefix = f"{hardware_prefix}:COE:"
        if any(pv.startswith(coe_prefix) for pv in coe_list):
            return hardware_prefix

    return f"{prefix_name}:{hardware_id}:{hardware_channel}"


def remove_name_rbv(pv_name):
    """
    Remove the ':Name_RBV' suffix from a PV name if present.

    Args:
        pv_name (str): PV name to process.

    Returns:
        str: PV name without ':Name_RBV', or the original PV name.
    """
    suffix = ":Name_RBV"
    if pv_name.endswith(suffix):
        return pv_name[: -len(suffix)]
    return pv_name
