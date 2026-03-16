"""
device_ops.py

Low-level device operations for StartupCAN.

This module contains helper functions for direct device interaction:
reading the serial number, activating a device, verifying CAN settings,
applying new CAN settings, reconnecting after reset, probing fallback
states after failures, and comparing endpoints.

These functions operate close to the GSV CAN device interface and are
used by the higher-level device flow logic.
"""

import time

from startupcan.config import CANBAUD, NEW_DEFAULT_MODE
from startupcan.gsv86can import (
    GSV86CAN,
    CANSET_CAN_IN_CMD_ID,
    CANSET_CAN_OUT_ANS_ID,
    CANSET_CAN_BAUD_HZ,
)
from startupcan.runtime import _set_handle_active, _safe_release
from startupcan.ui import fmt_can_id, _fmt_dev



def _read_serial(gsv: GSV86CAN, dev_no: int) -> int | None:
    """
    Read the serial number of a device.

    Returns:
        serial number as int, or None if reading fails
    """
    try:
        sn = int(gsv.get_serial_no(dev_no))
        return sn
    except Exception as e:
        print(f"[DEV {dev_no}] WARN: serial number could not be read: {e}")
        return None

def _try_activate(
    gsv: GSV86CAN,
    dev_no: int,
    cmd: int,
    ans: int,
    *,
    canbaud: int | None = None,
    tries: int = 5,
    delay: float = 0.3,
    read_sn: bool = True,
    verbose: bool = True
) -> tuple[bool, int | None]:
    """
    Try to activate a device with retry logic.

    This is intended to make activation more robust against temporary CAN
    timing issues or short communication glitches.

    Returns:
        ok
        serial
    """
    last_err = None
    
    if canbaud is None:
        canbaud = CANBAUD

    for i in range(tries):

        if verbose:
            print(f"[DEV {dev_no}] activate try {i+1}/{tries}: BAUD={canbaud} CMD={fmt_can_id(cmd)} ANS={fmt_can_id(ans)}")

        try:
            gsv.activate(dev_no, cmd, ans, canbaud=canbaud)
            _set_handle_active(dev_no, True)
            
            sn = _read_serial(gsv, dev_no) if read_sn else None

            if verbose: 
                if read_sn:
                    print(f"[{_fmt_dev(dev_no, sn)}] activate OK")
                else:
                    print(f"[DEV {dev_no}] activate OK")

            return True, sn
        
        except Exception as e:
            last_err = e
            if i < tries - 1:
                time.sleep(delay)

    if verbose:
        print(f"[DEV {dev_no}] activate FAIL after {tries} tries: {last_err}")

    return False, None

def _try_activate_n(gsv, dev_no, cmd, ans, *, canbaud: int | None = None, tries=5, delay=0.3) -> bool:
    """
    Try to activate a device without reading the serial number.

    Returns:
        True if activation succeeded, otherwise False
    """
    ok, _ = _try_activate(gsv, dev_no, cmd, ans, canbaud=canbaud, tries=tries, delay=delay, read_sn=False, verbose=False)
    return ok

def _verify_ids(gsv: GSV86CAN, dev_no: int, serial: int | None, exp_cmd: int, exp_ans: int, exp_canbaud: int) -> bool:
    """
    Read back the device CAN settings and compare them with the expected values.

    This is a best-effort verification step. Failure only produces a warning.

    Returns:
        True if all values match, otherwise False
    """
    try:
        cmd_read = gsv.get_can_settings(dev_no, CANSET_CAN_IN_CMD_ID)
        ans_read = gsv.get_can_settings(dev_no, CANSET_CAN_OUT_ANS_ID)
        canbaud_read = gsv.get_can_settings(dev_no,CANSET_CAN_BAUD_HZ)

        ok = (cmd_read == exp_cmd and ans_read == exp_ans and canbaud_read == exp_canbaud)

        tag = _fmt_dev(dev_no, serial)
        print(f"[{tag}] verify CMD_ID   = {fmt_can_id(cmd_read)} (raw={cmd_read})")
        print(f"[{tag}] verify ANSWER_ID= {fmt_can_id(ans_read)} (raw={ans_read})")
        print(f"[{tag}] verify CANBAUD= {canbaud_read}")

        if not ok:
            print(f"[{tag}] WARN: verify differs from expected "
                  f"(expected CMD={fmt_can_id(exp_cmd)} ANS={fmt_can_id(exp_ans)} CANBAUD={exp_canbaud})")
        return ok
    
    except Exception as e:
        print(f"[{_fmt_dev(dev_no, serial)}] WARN: verification failed: {e}")
        return False

def _apply_target_and_reconnect(
    gsv: GSV86CAN,
    dev_no: int,
    serial: int | None,
    cmd_new: int,
    ans_new: int,
    baud_new: int | None = None
) -> tuple[bool, int | None]:
    """
    Apply new CAN settings, reset the device, reconnect using the new endpoint,
    and verify the result.

    Steps:
    - write new CMD/ANS IDs
    - optionally write the new baudrate
    - reset the device
    - release the old session
    - reactivate using the new endpoint
    - verify via get_can_settings

    Returns:
        ok
        serial_after_reconnect

    Note:
        The device is intentionally not kept active afterwards.
    """
    try:
        # Write the new CAN IDs.
        print(f"[{_fmt_dev(dev_no, serial)}] set NEW IDs: CMD={fmt_can_id(cmd_new)} ANS={fmt_can_id(ans_new)}")
        gsv.set_can_settings(dev_no, CANSET_CAN_IN_CMD_ID, cmd_new)
        gsv.set_can_settings(dev_no, CANSET_CAN_OUT_ANS_ID, ans_new)

        # Optionally write the new baudrate.
        if baud_new is not None:
            print(f"[{_fmt_dev(dev_no, serial)}] set NEW BAUD: {baud_new}")
            gsv.set_can_settings(dev_no, CANSET_CAN_BAUD_HZ, int(baud_new))

        # Make the changes effective.
        gsv.reset_device(dev_no)
        time.sleep(2)

        # Release the current session before reconnecting.
        _safe_release(gsv, dev_no, where="after reset")
        time.sleep(0.2)

        activate_baud = baud_new if baud_new is not None else CANBAUD

        print(f"[{_fmt_dev(dev_no, serial)}] re-activate after setting CAN settings: CMD={fmt_can_id(cmd_new)} ANS={fmt_can_id(ans_new)} CANBAUD={activate_baud}")

        ok, sn2 = _try_activate(gsv, dev_no, cmd_new, ans_new, canbaud=activate_baud, tries=8, delay=0.5, read_sn=True, verbose=False)

        if ok: 
            print(f"[{_fmt_dev(dev_no, serial)}] re-activation with CMD={fmt_can_id(cmd_new)} ANS={fmt_can_id(ans_new)} CANBAUD={activate_baud} was successful.")
        else:
            print(f"[{_fmt_dev(dev_no, serial)}] re-activation with CMD={fmt_can_id(cmd_new)} ANS={fmt_can_id(ans_new)} CANBAUD={activate_baud} failed.")
            return False, serial

        sn_out = sn2 if sn2 is not None else serial

        ok_verify = _verify_ids(gsv, dev_no, sn_out, cmd_new, ans_new, activate_baud)

        if not ok_verify:
            print(f"[{_fmt_dev(dev_no, sn_out)}] WARN: verification after re-activation "
                  f"does not match expected values "
                  f"(expected CMD={fmt_can_id(cmd_new)} ANS={fmt_can_id(ans_new)}).")

        time.sleep(0.1)

        if NEW_DEFAULT_MODE:
            print(f"[{_fmt_dev(dev_no, serial)}] Loading factory default settings...")

            gsv.load_settings(dev_no, dataset_no=1)

        return ok_verify, sn_out
    
    except Exception as e:
        print(f"[{_fmt_dev(dev_no, serial)}] set/reset/reactivate/verify FAIL: {e}")
        _safe_release(gsv, dev_no, where="set_ids:except")
        return False, serial

def _probe_state_after_fail(
    gsv: GSV86CAN,
    dev_no: int,
    cmd_old: int, ans_old: int,
    cmd_new: int, ans_new: int,
    *,
    baud_old: int | None = None,
    baud_new: int | None = None,
) -> str:
    """
    Best-effort probe to determine which CAN settings are currently active.

    Possible return values:
        "old"
        "new"
        "old_newbaud"
        "new_oldbaud"
        "unknown"
    """

    # Use reasonable defaults if no baudrates are provided.
    if baud_old is None:
        baud_old = CANBAUD
    if baud_new is None:
        baud_new = CANBAUD
    
    # Make sure no stale session remains before probing.
    _safe_release(gsv, dev_no, where="probe:pre")
    time.sleep(0.3)  

    def _probe(label: str, cmd: int, ans: int, baud: int) -> bool:
        """
        Try one specific endpoint/baudrate combination.
        """
        print(f"[DEV {dev_no}] Probe {label}: BAUD={baud} CMD={fmt_can_id(cmd)} ANS={fmt_can_id(ans)}")
        ok = _try_activate_n(gsv, dev_no, cmd, ans, canbaud=baud, tries=5, delay=0.3)
        if ok:
            _safe_release(gsv, dev_no, where=f"probe:{label}-ok")
        return ok

    # 1) old IDs + old baudrate
    if _probe("old@oldbaud", cmd_old, ans_old, baud_old):
        print(f"[DEV {dev_no}] Probe success: state=old")
        return "old"

    # 2) new IDs + new baudrate
    if _probe("new@newbaud", cmd_new, ans_new, baud_new):
        print(f"[DEV {dev_no}] Probe success: state=new")
        return "new"
    
    # 3) Cross-check mixed endpoint/baudrate combinations
    if int(baud_old) != int(baud_new):
        if _probe("old@newbaud", cmd_old, ans_old, baud_new):
            print(f"[DEV {dev_no}] probe success: state=old_newbaud")
            return "old_newbaud"
        if _probe("new@oldbaud", cmd_new, ans_new, baud_old):
            print(f"[DEV {dev_no}] probe success: state=new_oldbaud")
            return "new_oldbaud"

    print(f"[DEV {dev_no}] probes failed. CAN settings are unknown.")
    return "unknown"

def _same_endpoint(cmd_a: int, ans_a: int, cmd_b: int, ans_b: int, baud_a: int, baud_b: int) -> bool:
    """
    Compare two CAN endpoints including baudrate.

    Returns:
        True if CMD ID, ANSWER ID, and baudrate are all equal
    """
    return int(cmd_a) == int(cmd_b) and int(ans_a) == int(ans_b) and int(baud_a) == int(baud_b)