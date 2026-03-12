import time


from startupcan.config import CANBAUD
from startupcan.gsv86can import (
    GSV86CAN,
    CANSET_CAN_IN_CMD_ID,
    CANSET_CAN_OUT_ANS_ID,
    CANSET_CAN_BAUD_HZ,
)

from startupcan.runtime import _set_handle_active, _safe_release
from startupcan.ui import fmt_can_id, _fmt_dev



def _read_serial(gsv: GSV86CAN, dev_no: int) -> int | None:
    try:
        sn = int(gsv.get_serial_no(dev_no))
        return sn
    except Exception as e:
        print(f"[DEV {dev_no}] WARN: Seriennummer konnte nicht gelesen werden: {e}")
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
    Mehrfaches Activate (robust gegen sporadische CAN/Timing Issues).
    Gibt (ok, serial) zurück.
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
            # sn = None
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
    ok, _ = _try_activate(gsv, dev_no, cmd, ans, canbaud=canbaud, tries=tries, delay=delay, read_sn=False, verbose=False)
    return ok

def _verify_ids(gsv: GSV86CAN, dev_no: int, serial: int | None, exp_cmd: int, exp_ans: int, exp_canbaud: int):
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
        print(f"[{_fmt_dev(dev_no, serial)}] WARN: verify failed: {e}")
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
    Setzt neue IDs, macht reset, verbindet nochmal mit den neuen IDs,
    verifiziert und released am Ende wieder.

    Danach ist das Device NICHT mehr aktiv (bewusst).
    """
    try:
        # if dev_no == 1:
        #     cmd_new_test = 264    # 0x108
        #     ans_new_test = 265    # 0x109
        #     # cmd_new_test = 514 # 0x202
        #     # ans_new_test = 515 # 0x203
        #     print(f"[{_fmt_dev(dev_no, serial)}] set NEW IDs: CMD={fmt_can_id(cmd_new)} ANS={fmt_can_id(ans_new)}")
        #     gsv.set_can_settings(dev_no, CANSET_CAN_IN_CMD_ID, cmd_new_test)
        #     gsv.set_can_settings(dev_no, CANSET_CAN_OUT_ANS_ID, ans_new_test)
        # else:
        #     print(f"[{_fmt_dev(dev_no, serial)}] set NEW IDs: CMD={fmt_can_id(cmd_new)} ANS={fmt_can_id(ans_new)}")
        #     gsv.set_can_settings(dev_no, CANSET_CAN_IN_CMD_ID, cmd_new)
        #     gsv.set_can_settings(dev_no, CANSET_CAN_OUT_ANS_ID, ans_new)

        print(f"[{_fmt_dev(dev_no, serial)}] set NEW IDs: CMD={fmt_can_id(cmd_new)} ANS={fmt_can_id(ans_new)}")
        gsv.set_can_settings(dev_no, CANSET_CAN_IN_CMD_ID, cmd_new)
        gsv.set_can_settings(dev_no, CANSET_CAN_OUT_ANS_ID, ans_new)

        if baud_new is not None:
            print(f"[{_fmt_dev(dev_no, serial)}] set NEW BAUD: {baud_new}")
            gsv.set_can_settings(dev_no, CANSET_CAN_BAUD_HZ, int(baud_new))

        # Änderungen wirksam machen
        gsv.reset_device(dev_no)
        time.sleep(2)

        # Session lösen
        _safe_release(gsv, dev_no, where="after reset")
        time.sleep(0.2)

        activate_baud = baud_new if baud_new is not None else CANBAUD

        print(f"[{_fmt_dev(dev_no, serial)}] Re-activation after setting can ids: CMD={fmt_can_id(cmd_new)} ANS={fmt_can_id(ans_new)} CANBAUD={activate_baud}")
        ok, sn2 = _try_activate(gsv, dev_no, cmd_new, ans_new, canbaud=activate_baud, tries=8, delay=0.5, read_sn=True, verbose=False)
        if ok: 
            print(f"[{_fmt_dev(dev_no, serial)}] Re-activation with CMD={fmt_can_id(cmd_new)} ANS={fmt_can_id(ans_new)} CANBAUD={activate_baud} was successfull.")
        else:
            print(f"[{_fmt_dev(dev_no, serial)}] Re-activation with CMD={fmt_can_id(cmd_new)} ANS={fmt_can_id(ans_new)} CANBAUD={activate_baud} failed.")
            return False, serial

        sn_out = sn2 if sn2 is not None else serial

        ok_verify = _verify_ids(gsv, dev_no, sn_out, cmd_new, ans_new, activate_baud)

        if not ok_verify:
            print(f"[{_fmt_dev(dev_no, sn_out)}] WARN: Verify nach Re-Activate stimmt nicht "
                  f"(expected CMD={fmt_can_id(cmd_new)} ANS={fmt_can_id(ans_new)}).")

        time.sleep(0.1)

        return ok_verify, sn_out
    
    except Exception as e:
        print(f"[{_fmt_dev(dev_no, serial)}] set/reset/reactivate/verify/release FAIL: {e}")
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
    Best-effort: herausfinden, welche IDs gerade wirklich aktiv sind.
    Returns: "old" | "new" | "unknown"
    """

    # Fallbacks: wenn None => sinnvoller Default
    if baud_old is None:
        baud_old = CANBAUD
    if baud_new is None:
        baud_new = CANBAUD

    _safe_release(gsv, dev_no, where="probe:pre")
    time.sleep(0.3)  

    def _probe(label: str, cmd: int, ans: int, baud: int) -> bool:
        print(f"[DEV {dev_no}] Probe {label}: BAUD={baud} CMD={fmt_can_id(cmd)} ANS={fmt_can_id(ans)}")
        ok = _try_activate_n(gsv, dev_no, cmd, ans, canbaud=baud, tries=5, delay=0.3)
        if ok:
            _safe_release(gsv, dev_no, where=f"probe:{label}-ok")
        return ok

    # 1) old@baud_old
    if _probe("old@oldbaud", cmd_old, ans_old, baud_old):
        print(f"[DEV {dev_no}] Probe success: state=old")
        return "old"

    # 2) new@baud_new
    if _probe("new@newbaud", cmd_new, ans_new, baud_new):
        print(f"[DEV {dev_no}] Probe success: state=new")
        return "new"
    
    # 3) Cross-checks nur wenn die Baudraten verschieden sind
    if int(baud_old) != int(baud_new):
        if _probe("old@newbaud", cmd_old, ans_old, baud_new):
            print(f"[DEV {dev_no}] Probe success: state=old (baud mismatch case)")
            return "old_newbaud"
        if _probe("new@oldbaud", cmd_new, ans_new, baud_old):
            print(f"[DEV {dev_no}] Probe success: state=new (baud mismatch case)")
            return "new_oldbaud"

    print(f"[DEV {dev_no}] Probes failed (old/new and cross). CAN IDs are unknown.")
    return "unknown"

def _same_endpoint(cmd_a: int, ans_a: int, cmd_b: int, ans_b: int, baud_a: int, baud_b: int) -> bool:
    return int(cmd_a) == int(cmd_b) and int(ans_a) == int(ans_b) and int(baud_a) == int(baud_b)