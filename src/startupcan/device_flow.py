from startupcan.models import DevicePlan
from startupcan.gsv86can import GSV86CAN
from startupcan.device_ops import (
    _try_activate,
    _verify_ids,
    _apply_target_and_reconnect,
    _probe_state_after_fail,
    _same_endpoint
)
from startupcan.results import _record_result
from startupcan.ui import _connect_one, _disconnect_one, _fmt_dev
from startupcan.runtime import _safe_release
from startupcan.planning import _target_ids
from startupcan.config import SN_MODE


def _activate_or_record_failure(
    *,
    gsv: GSV86CAN,
    results: list[dict],
    plan: DevicePlan,
    tries: int = 5,
    delay: float = 0.3,
    read_sn: bool = True,
    warn_where: str = "state-probe",
    fail_message: str = "Activation failed.",
) -> tuple[bool, int | None, bool, bool, str]:
    """
    Führt initiales activate() aus.
    Wenn activate fehlschlägt:
      - state probe / unknown handling
      - results append
      - skip_programming=True
    Wenn activate erfolgreich:
      - nur ok/sn zurück, keine Ergebniszeile

    Returns:
        ok,
        sn,
        already_recorded,
        skip_programming,
        disconnect_reason
    """
    ok, sn = _try_activate(
        gsv,
        plan.dev_no,
        plan.cmd_old,
        plan.ans_old,
        canbaud=plan.baud_old,
        tries=tries,
        delay=delay,
        read_sn=read_sn,
    )

    if ok:
        return (
            True,
            sn,
            False,   # already_recorded
            False,   # skip_programming
            "",
        )

    # activate fehlgeschlagen
    if plan.cmd_new is None or plan.ans_new is None:
        state = "unknown"
        fail_plan = plan.with_safe_new_ids()
    else:
        fail_plan = plan

        state = _probe_state_after_fail(
            gsv,
            fail_plan.dev_no,
            fail_plan.cmd_old,
            fail_plan.ans_old,
            fail_plan.cmd_new,
            fail_plan.ans_new,
            baud_old=fail_plan.baud_old,
            baud_new=fail_plan.baud_new,
        )

    _record_result(
        results=results,
        dev_no=fail_plan.dev_no,
        sn=sn,
        ok=False,
        state=state,
        cmd_old=fail_plan.cmd_old,
        ans_old=fail_plan.ans_old,
        cmd_new=fail_plan.cmd_new,
        ans_new=fail_plan.ans_new,
        baud_old=fail_plan.baud_old,
        baud_new=fail_plan.baud_new,
        warn_unknown=True,
        warn_where=warn_where,
    )

    return (
        False,
        sn,
        True,   # already_recorded
        True,   # skip_programming
        f"{fail_message} State probe={state}. Gerät abnehmen.",
    )

def _resolve_target_ids_after_activate(
    *,
    results: list[dict],
    plan: DevicePlan,
    sn: int | None,
    fail_state: str = "old",
    fail_message: str = "FEHLER: Ziel-IDs konnten nicht bestimmt werden. Dieses Gerät wird übersprungen.",
) -> tuple[DevicePlan, bool, bool, str]:
    """
    Wird NACH erfolgreichem activate() aufgerufen.

    Verhalten:
    - SN_MODE=False:
        cmd_new/ans_new müssen bereits gesetzt sein -> einfach zurückgeben
    - SN_MODE=True:
        target IDs werden per SN bestimmt

    Returns:
        plan,
        already_recorded,
        skip_programming,
        disconnect_reason
    """
    # SN_MODE=False => target wurde vorher schon per dev_no bestimmt
    if not SN_MODE:
        if plan.cmd_new is None or plan.ans_new is None:
            raise ValueError(f"SN_MODE=False, aber cmd_new/ans_new fehlen für dev_no={plan.dev_no}")
        return plan, False, False, ""

    # SN_MODE=True => target jetzt per Seriennummer bestimmen
    try:
        cmd_new, ans_new = _target_ids(plan.dev_no, sn)
        print(f"[{_fmt_dev(plan.dev_no, sn)}] Ziel-IDs per SN-Mapping.")
        return plan.with_new_ids(cmd_new, ans_new), False, False, ""

    except KeyError as e:
        print(f"[{_fmt_dev(plan.dev_no, sn)}] FEHLER: {e}")

        fail_plan = plan.with_safe_new_ids()

        _record_result(
            results=results,
            dev_no=fail_plan.dev_no,
            sn=sn,
            ok=False,
            state=fail_state,
            cmd_old=fail_plan.cmd_old,
            ans_old=fail_plan.ans_old,
            cmd_new=fail_plan.cmd_new,
            ans_new=fail_plan.ans_new,
            baud_old=fail_plan.baud_old,
            baud_new=fail_plan.baud_new,
        )

        return fail_plan, True, True, fail_message

def _validate_expected_serial(
    *,
    results: list[dict],
    plan: DevicePlan,
    expected_sn: int | None,
    sn: int | None,
    serial_missing_message: str = "Die Seriennummer konnte nicht gelesen werden.",
    serial_mismatch_message: str = (
        "Die gelesene Seriennummer stimmt nicht mit der konfigurierten Seriennummer "
        "aus dem YAML überein. Die Seriennummer aus dem YAML wird im neuen YAML "
        "mit der gelesenen Seriennummer überschrieben."
    ),
) -> tuple[bool, bool, str]:
    """
    Prüft erwartete Seriennummer aus YAML gegen gelesene Seriennummer.

    Verhalten:
    - expected_sn is None -> keine Prüfung, alles OK
    - sn is None -> Fail append
    - sn != expected_sn -> Fail append
    - sonst OK

    Returns:
        already_recorded,
        skip_programming,
        disconnect_reason
    """
    if expected_sn is None:
        return False, False, ""
    
    safe_plan = plan.with_safe_new_ids()

    if sn is None:
        print(
            f"[DEV {safe_plan.dev_no}] FEHLER: YAML erwartet SN={expected_sn}, "
            "aber Seriennummer konnte nicht gelesen werden. => Device wird NICHT umgestellt."
        )

        _record_result(
            results=results,
            dev_no=safe_plan.dev_no,
            sn=sn,
            ok=False,
            state="old",
            cmd_old=safe_plan.cmd_old,
            ans_old=safe_plan.ans_old,
            cmd_new=safe_plan.cmd_new,
            ans_new=safe_plan.ans_new,
            baud_old=safe_plan.baud_old,
            baud_new=safe_plan.baud_new,
        )
        print("-" * 80)
        return True, True, serial_missing_message

    if int(expected_sn) != int(sn):
        print(
            f"[{_fmt_dev(plan.dev_no, sn)}] FEHLER: Die gelesene Seriennummer {sn} passt nicht zu YAML "
            f"(yaml SN={expected_sn}). => Device wird NICHT umgestellt."
        )

        _record_result(
            results=results,
            dev_no=safe_plan.dev_no,
            sn=sn,
            ok=False,
            state="old",
            cmd_old=safe_plan.cmd_old,
            ans_old=safe_plan.ans_old,
            cmd_new=safe_plan.cmd_new,
            ans_new=safe_plan.ans_new,
            baud_old=safe_plan.baud_old,
            baud_new=safe_plan.baud_new,
        )
        
        print("-" * 80)
        return True, True, serial_mismatch_message

    return False, False, ""

def _handle_skip_if_same_endpoint(
    *,
    results: list[dict],
    plan: DevicePlan,
    sn: int | None,
    state_on_skip: str = "old",
    print_message: str = "Ziel-IDs entsprechen bereits dem aktuellen Zustand. Skip umstellen.",
    disconnect_message: str = "OK (skip). Bitte Gerät abnehmen.",
) -> tuple[bool, bool, str]:
    """
    Wenn alter und neuer Endpoint identisch sind, wird der Schritt als erfolgreich
    übersprungen und direkt in results eingetragen.

    Returns:
        already_recorded,
        skip_programming,
        disconnect_reason
    """
    if plan.cmd_new is None or plan.ans_new is None:
        return False, False, ""
    
    if not _same_endpoint(
        plan.cmd_old, plan.ans_old,
        plan.cmd_new, plan.ans_new,
        plan.baud_old, plan.baud_new,
    ):
        return False, False, ""

    print(f"[{_fmt_dev(plan.dev_no, sn)}] {print_message}")

    _record_result(
        results=results,
        dev_no=plan.dev_no,
        sn=sn,
        ok=True,
        state=state_on_skip,
        cmd_old=plan.cmd_old,
        ans_old=plan.ans_old,
        cmd_new=plan.cmd_new,
        ans_new=plan.ans_new,
        baud_old=plan.baud_old,
        baud_new=plan.baud_new,
    )

    return True, True, disconnect_message

def _apply_target_or_record_result(
    *,
    gsv: GSV86CAN,
    results: list[dict],
    plan: DevicePlan,
    sn: int | None,
    success_message: str,
    failure_message: str,
) -> tuple[int | None, bool, bool, str]:
    """
    Programmiert Ziel-IDs/Ziel-Baud, reaktiviert, bestimmt den effektiven state
    und schreibt das Ergebnis direkt nach results.

    Returns:
        sn_out,
        already_recorded,
        skip_programming,
        disconnect_reason
    """
    if plan.cmd_new is None or plan.ans_new is None:
        raise ValueError(f"plan.cmd_new/ans_new fehlen für dev_no={plan.dev_no}")
    ok2, sn2 = _apply_target_and_reconnect(
        gsv,
        plan.dev_no,
        sn,
        plan.cmd_new,
        plan.ans_new,
        baud_new=plan.baud_new,
    )

    sn_out = sn2 if sn2 is not None else sn

    state = "new" if ok2 else _probe_state_after_fail(
        gsv,
        plan.dev_no,
        plan.cmd_old,
        plan.ans_old,
        plan.cmd_new,
        plan.ans_new,
        baud_old=plan.baud_old,
        baud_new=plan.baud_new,
    )

    _record_result(
        results=results,
        dev_no=plan.dev_no,
        sn=sn_out,
        ok=bool(ok2),
        state=state,
        cmd_old=plan.cmd_old,
        ans_old=plan.ans_old,
        cmd_new=plan.cmd_new,
        ans_new=plan.ans_new,
        baud_old=plan.baud_old,
        baud_new=plan.baud_new,
        warn_unknown=True,
        warn_where="state-probe",
    )

    disconnect_reason = success_message if ok2 else failure_message.format(state=state)

    return sn_out, True, True, disconnect_reason

def _handle_keyboard_interrupt(
    *,
    gsv: GSV86CAN,
    results: list[dict],
    plan: DevicePlan,
    sn: int | None,
    already_recorded: bool,
    disconnect_message: str = "⏭️ Abbruch (Ctrl+C) im Device-Step. Gerät wird freigegeben. Bitte abnehmen.",
    warn_where: str = "Abbruch (Ctrl+C)",
) -> tuple[bool, str]:
    """
    Einheitliches Handling für Ctrl+C im Device-Step.

    Returns:
        already_recorded,
        disconnect_reason
    """
    disconnect_reason = disconnect_message

    safe_plan = plan.with_safe_new_ids()
    _safe_release(gsv, safe_plan.dev_no, where="device-step/Ctrl+C")

    if not already_recorded:
        already_recorded = _record_result(
            results=results,
            dev_no=safe_plan.dev_no,
            sn=sn,
            ok=False,
            state="unknown",
            cmd_old=safe_plan.cmd_old,
            ans_old=safe_plan.ans_old,
            cmd_new=safe_plan.cmd_new,
            ans_new=safe_plan.ans_new,
            baud_old=safe_plan.baud_old,
            baud_new=safe_plan.baud_new,
            warn_unknown=True,
            warn_where=warn_where,
        )

    return already_recorded, disconnect_reason

def _device_fail(
    *,
    gsv: GSV86CAN,
    results: list[dict],
    plan: DevicePlan,
    sn: int | None,
    err: Exception,
    where: str,
) -> str:
    """
    Einheitliches Fehlerhandling pro Device:
    - prints
    - state probe (best effort)
    - results append
    - returns disconnect_reason
    """
    safe_plan = plan.with_safe_new_ids()
    print(f"[DEV {safe_plan.dev_no}] FEHLER ({where}): {err}")

    state = "unknown"
    try:
        state = _probe_state_after_fail(
            gsv,
            safe_plan.dev_no,
            safe_plan.cmd_old,
            safe_plan.ans_old,
            safe_plan.cmd_new,
            safe_plan.ans_new,
            baud_old=safe_plan.baud_old,
            baud_new=safe_plan.baud_new,
        )
    except Exception as e2:
        print(f"[DEV {safe_plan.dev_no}] WARN: state-probe failed: {e2}")

    _record_result(
        results=results,
        dev_no=safe_plan.dev_no,
        sn=sn,
        ok=False,
        state=state,
        cmd_old=safe_plan.cmd_old,
        ans_old=safe_plan.ans_old,
        cmd_new=safe_plan.cmd_new,
        ans_new=safe_plan.ans_new,
        baud_old=safe_plan.baud_old,
        baud_new=safe_plan.baud_new,
        warn_unknown=True,
        warn_where=f"{where}/state-probe",
    )

    return f"FEHLER: {err} (state={state}). Bitte Gerät abnehmen."

def _run_device_step(
    *,
    gsv: GSV86CAN,
    results: list[dict],
    plan: DevicePlan,
    expected_sn: int | None,
    resolve_target_after_activate: bool,
    validate_expected_serial: bool,
) -> None:

    sn = None
    already_recorded = False
    skip_programming = False
    disconnect_reason = "Weiter mit nächstem Gerät."

    try:
        _connect_one(plan.dev_no)

        _, sn, already_recorded, skip_programming, disconnect_reason = _activate_or_record_failure(
            gsv=gsv,
            results=results,
            plan=plan,
            tries=5,
            delay=0.3,
            read_sn=True,
            warn_where="activation",
            fail_message="Activation failed.",
        )

        if not skip_programming and resolve_target_after_activate:
            plan, already_recorded, skip_programming, disconnect_reason = _resolve_target_ids_after_activate(
                results=results,
                plan=plan,
                sn=sn,
                fail_state="old",
                fail_message="FEHLER: Ziel-IDs konnten nicht bestimmt werden. Dieses Gerät wird übersprungen.",
            )

        if not skip_programming and validate_expected_serial:
            already_recorded, skip_programming, disconnect_reason = _validate_expected_serial(
                results=results,
                plan=plan,
                expected_sn=expected_sn,
                sn=sn,
            )

        if not skip_programming:
            ok = _verify_ids(gsv, plan.dev_no, sn, plan.cmd_old, plan.ans_old, plan.baud_old)
            if not ok:
                print(f"[{_fmt_dev(plan.dev_no, sn)}] WARN: Start-IDs stimmen nicht (trotz activation).")

        if not skip_programming:
            already_recorded, skip_programming, disconnect_reason = _handle_skip_if_same_endpoint(
                results=results,
                plan=plan,
                sn=sn,
                state_on_skip="new",
                print_message="Gerät hat bereits die Ziel-CAN-Settings.",
                disconnect_message="OK (skip). Bitte Gerät abnehmen.",
            )

        if not skip_programming:
            sn, already_recorded, skip_programming, disconnect_reason = _apply_target_or_record_result(
                gsv=gsv,
                results=results,
                plan=plan,
                sn=sn,
                success_message="✅ OK: Gerät wurde auf die neuen CAN settings umgestellt. Bitte abnehmen.",
                failure_message="FEHLER: Umstellung fehlgeschlagen (state={state}). Bitte abnehmen.",
            )

    except KeyboardInterrupt:
        already_recorded, disconnect_reason = _handle_keyboard_interrupt(
            gsv=gsv,
            results=results,
            plan=plan,
            sn=sn,
            already_recorded=already_recorded,
        )

    except Exception as e:
        if not already_recorded:
            disconnect_reason = _device_fail(
                gsv=gsv,
                results=results,
                plan=plan,
                sn=sn,
                err=e,
                where="Exception",
            )
        else:
            disconnect_reason = f"FEHLER nach Ergebnis-Append: {e}. Bitte Gerät abnehmen."

    finally:
        try:
            _disconnect_one(gsv, plan.dev_no, sn, reason=disconnect_reason)
        except KeyboardInterrupt:
            _safe_release(gsv, plan.dev_no, where="finally/KeyboardInterrupt")
            raise
        except Exception as e:
            print(f"[DEV {plan.dev_no}] WARN: disconnect step failed: {e}")
            _safe_release(gsv, plan.dev_no, where="finally/disconnect-except")