"""
ui.py

User interaction helpers for StartupCAN.

This module contains small helper functions used for console interaction
during the startup workflow. It handles:

- formatting CAN IDs for display
- printing device identifiers
- warning messages
- user prompts (connect device, disconnect device, continue workflow)
- pause/confirmation steps

All functions are intentionally simple to keep the main workflow readable.
"""

from startupcan.gsv86can import GSV86CAN
from startupcan.runtime import _safe_release


def fmt_can_id(x: int) -> str:
    """
    Format a CAN ID for console output.

    Standard CAN IDs are typically 11-bit (0..0x7FF),
    but some systems may use extended 29-bit IDs.

    This function formats both cases cleanly.

    Example:
        258 -> "0x102"
    """
    if x <= 0x7FF:
        return f"0x{x:03X}"
    return f"0x{x:X}"

UNKNOWN_HINT = (
    "CAN ID is unknown. Please connect the device via USB and use GSVmulti "
    "to read or set the correct CAN IDs (CMD/ANSWER)."
)

def _fmt_dev(dev_no: int, serial: int | None) -> str:
    """
    Format a device label for log messages.

    Example:
        DEV 1 (SN=25455437)
        DEV 2 (SN=?)
    """
    if serial is None:
        return f"DEV {dev_no} (SN=?)"
    return f"DEV {dev_no} (SN={serial})"

def _warn_unknown(dev_no: int, serial: int | None, *, where: str = ""):
    """
    Print a warning that the device's CAN ID state is unknown.
    """
    tag = _fmt_dev(dev_no, serial)
    prefix = f"[{tag}]"

    if where:
        prefix += f" [{where}]"

    print(f"{prefix} ⚠️  {UNKNOWN_HINT}")

def _pause(msg: str):
    """
    Print a message and wait for the user to press ENTER.

    Used to pause the workflow when manual user interaction is required.
    """
    print(msg)
    input("➡️  Press ENTER to continue ... ")

def _ask_continue(prompt: str = "[WIZARD] Process next device? [y/N]: ") -> bool:
    """
    Ask the user whether the next device should be processed.

    Returns:
        True if the user answers yes (y/yes/j/ja),
        otherwise False.
    """
    more = input(prompt).strip().lower()
    return more in ("j", "ja", "y", "yes")

def _connect_one(dev_no: int):
    """
    Prompt the user to connect exactly one device to the CAN bus.

    This is a safety step to avoid CAN ID collisions.
    """
    print("\n" + "=" * 80)
    print(f"[WIZARD] DEV {dev_no}")
    print("⚠️  IMPORTANT: Exactly ONE device must be connected to the CAN bus.")
    print(f"➡️  Please connect device DEV {dev_no} now.")
    print("=" * 80)
    _pause("When the device is connected:")


def _finish_device_step(gsv: GSV86CAN, dev_no: int, serial: int | None, reason: str = ""):
    """
    Finalize processing of a single device.

    This performs two actions:
    - release the device session (best-effort)
    - ask the user to remove the device from the CAN bus
    """
    tag = _fmt_dev(dev_no, serial)

    if reason:
        print(f"[{tag}] {reason}")

    # Ensure the device session is released safely
    _safe_release(gsv, dev_no, where="finish_device_step")

    _pause(f"➡️  Please remove device {tag} from the bus NOW, then press ENTER ...")

def _disconnect_one(gsv: GSV86CAN, dev_no: int, serial: int | None, reason: str = ""):
    """
    Disconnect step for a processed device.

    This always instructs the user to remove the device from the bus,
    which follows the safety model of StartupCAN (only one device
    connected at a time).
    """
    _finish_device_step(gsv, dev_no, serial, reason=reason)