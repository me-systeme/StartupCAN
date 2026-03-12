import time

from startupcan.gsv86can import GSV86CAN


# Track if DLL handle is active per dev_no (best-effort safety vs DLL double-release bug)
HANDLE_ACTIVE: dict[int, bool] = {}


def _set_handle_active(dev_no: int, active: bool):
    HANDLE_ACTIVE[int(dev_no)] = bool(active)

def _is_handle_active(dev_no: int) -> bool:
    return bool(HANDLE_ACTIVE.get(int(dev_no), False))

def _safe_release(gsv: GSV86CAN, dev_no: int, *, where: str = ""):
    """
    Release only if we believe a valid handle exists.
    Prevents DLL crash when releasing twice / releasing invalid handle.
    """
    if not _is_handle_active(dev_no):
        return  # nothing to do

    try:
        gsv.release(dev_no)
        _set_handle_active(dev_no, False)
        time.sleep(0.05)
    except Exception as e:
        # If release fails, don't flip state blindly; keep it as-is
        print(f"[DEV {dev_no}] WARN: release failed{(' @'+where) if where else ''}: {e}")