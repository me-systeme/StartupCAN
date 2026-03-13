"""
runtime.py

Runtime helpers for StartupCAN.

This module contains small utilities used to track and safely manage
device handles during a run. The GSV DLL can crash if a device handle
is released twice or if an invalid handle is released.

To reduce the risk of such errors, this module keeps a best-effort
record of which device handles are currently active.
"""
import time

from startupcan.gsv86can import GSV86CAN


# Tracks whether a DLL handle is currently active for each device.
# This helps avoid double-release issues with the underlying DLL.
HANDLE_ACTIVE: dict[int, bool] = {}


def _set_handle_active(dev_no: int, active: bool):
    """
    Update the handle state for a device.

    Args:
        dev_no:
            Logical device number.

        active:
            True if the device currently has an active DLL handle,
            False otherwise.
    """
    HANDLE_ACTIVE[int(dev_no)] = bool(active)

def _is_handle_active(dev_no: int) -> bool:
    """
    Check whether a device currently has an active DLL handle.

    Args:
        dev_no:
            Logical device number.

    Returns:
        True if a handle is considered active, otherwise False.
    """
    return bool(HANDLE_ACTIVE.get(int(dev_no), False))

def _safe_release(gsv: GSV86CAN, dev_no: int, *, where: str = ""):
    """
    Safely release a device handle.

    The release is only attempted if the runtime believes that the
    device currently has an active handle. This prevents crashes in
    the underlying DLL caused by releasing an invalid or already
    released handle.

    Args:
        gsv:
            GSV86CAN instance.

        dev_no:
            Logical device number.

        where:
            Optional context string describing where the release is
            triggered from. Used only for logging.
    """
    # If no active handle is known, there is nothing to release.
    if not _is_handle_active(dev_no):
        return  

    try:
        gsv.release(dev_no)
        _set_handle_active(dev_no, False)

        # Small delay to give the DLL time to settle before the next action.
        time.sleep(0.05)
    except Exception as e:
        # If release fails, do not blindly change the state flag.
        # The handle might still exist internally.
        print(f"[DEV {dev_no}] WARN: release failed{(' @'+where) if where else ''}: {e}")