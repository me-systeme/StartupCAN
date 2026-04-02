"""
device_flow.py

Per-device execution flow for StartupCAN.

This module contains the step-by-step workflow for processing a single device:
activate the device, optionally resolve/check serial-based target settings,
verify the current endpoint, apply new CAN settings, probe fallback states on
failure, and record the result.

It is responsible for robust device-level error handling, state probing, and
safe disconnect/release behavior after each device step.
"""


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
    Perform the initial device activation.

    If activation fails:
    - perform a state probe if possible
    - record the failure in results
    - skip the programming step

    Returns:
        ok
        sn
        already_recorded
        skip_programming
        disconnect_reason
    """

    # Try to activate the device using the current endpoint
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
        # Activation successful → continue workflow
        return True, sn, False, False, ""
        

    # Activation failed
    # If target endpoint is unknown (e.g. SN mapping not resolved),
    # probing the "new" endpoint is not possible → fallback to safe plan
    if plan.cmd_new is None or plan.ans_new is None or plan.value_new is None:
        state = "unknown"
        fail_plan = plan.with_safe_new_ids()
    else:
        fail_plan = plan

        # Try to determine which endpoint is currently active
        state = _probe_state_after_fail(
            gsv,
            fail_plan.dev_no,
            fail_plan.cmd_old,
            fail_plan.ans_old,
            fail_plan.value_old,
            fail_plan.cmd_new,
            fail_plan.ans_new,
            fail_plan.value_new,
            baud_old=fail_plan.baud_old,
            baud_new=fail_plan.baud_new,
        )

    # Record the failure result
    _record_result(
        results=results,
        dev_no=fail_plan.dev_no,
        sn=sn,
        ok=False,
        state=state,
        cmd_old=fail_plan.cmd_old,
        ans_old=fail_plan.ans_old,
        value_old=fail_plan.value_old,
        cmd_new=fail_plan.cmd_new,
        ans_new=fail_plan.ans_new,
        value_new=fail_plan.value_new,
        baud_old=fail_plan.baud_old,
        baud_new=fail_plan.baud_new,
        warn_unknown=True,
        warn_where=warn_where,
    )

    return False, sn, True, True, f"{fail_message} State probe={state}. Remove device."

def _resolve_target_ids_after_activate(
    *,
    results: list[dict],
    plan: DevicePlan,
    sn: int | None,
    fail_state: str = "old",
    fail_message: str = "ERROR: Target IDs could not be determined. Device will be skipped.",
) -> tuple[DevicePlan, bool, bool, str]:
    """
    Resolve target IDs after activation.

    Behavior:
    - SN_MODE=False → target IDs already defined by dev_no
    - SN_MODE=True → resolve target IDs based on serial number

    Returns:
        DevicePlan
        already_recorded
        skip_programming
        disconnect_reason
    """

    # When SN_MODE is disabled, target IDs must already exist
    if not SN_MODE:
        if plan.cmd_new is None or plan.ans_new is None or plan.value_new is None:
            raise ValueError(f"SN_MODE=False but target IDs missing for dev_no={plan.dev_no}")
        return plan, False, False, ""

    # Resolve target IDs based on serial number
    try:
        cmd_new, ans_new, value_new = _target_ids(plan.dev_no, sn)
        print(f"[{_fmt_dev(plan.dev_no, sn)}] Target IDs resolved via serial mapping.")
        return plan.with_new_ids(cmd_new, ans_new, value_new), False, False, ""

    except KeyError as e:
        print(f"[{_fmt_dev(plan.dev_no, sn)}] ERROR: {e}")

        fail_plan = plan.with_safe_new_ids()

        # Record failure
        _record_result(
            results=results,
            dev_no=fail_plan.dev_no,
            sn=sn,
            ok=False,
            state=fail_state,
            cmd_old=fail_plan.cmd_old,
            ans_old=fail_plan.ans_old,
            value_old=fail_plan.value_old,
            cmd_new=fail_plan.cmd_new,
            ans_new=fail_plan.ans_new,
            value_new=fail_plan.value_new,
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
    serial_missing_message: str = "Serial number could not be read.",
    serial_mismatch_message: str = "Serial mismatch.",
) -> tuple[bool, bool, str]:
    """
    Validate the expected serial number from YAML.

    If the serial number cannot be read or does not match the expected value,
    the device is not reconfigured and the result is recorded as failure.

    Returns:
        already_recorded
        skip_programming
        disconnect_reason
    """
    if expected_sn is None:
        return False, False, ""
    
    safe_plan = plan.with_safe_new_ids()

    if sn is None:
        print(
            f"[DEV {safe_plan.dev_no}] ERROR: YAML expects SN={expected_sn}, "
            "but serial number could not be read."
        )

        _record_result(
            results=results,
            dev_no=safe_plan.dev_no,
            sn=sn,
            ok=False,
            state="old",
            cmd_old=safe_plan.cmd_old,
            ans_old=safe_plan.ans_old,
            value_old=safe_plan.value_old,
            cmd_new=safe_plan.cmd_new,
            ans_new=safe_plan.ans_new,
            value_new=safe_plan.value_new,
            baud_old=safe_plan.baud_old,
            baud_new=safe_plan.baud_new,
        )
        
        return True, True, serial_missing_message

    if int(expected_sn) != int(sn):
        print(
            f"[{_fmt_dev(plan.dev_no, sn)}] ERROR: serial mismatch "
            f"(expected {expected_sn}, got {sn})."
        )

        _record_result(
            results=results,
            dev_no=safe_plan.dev_no,
            sn=sn,
            ok=False,
            state="old",
            cmd_old=safe_plan.cmd_old,
            ans_old=safe_plan.ans_old,
            value_old=safe_plan.value_old,
            cmd_new=safe_plan.cmd_new,
            ans_new=safe_plan.ans_new,
            value_new=safe_plan.value_new,
            baud_old=safe_plan.baud_old,
            baud_new=safe_plan.baud_new,
        )
        
        
        return True, True, serial_mismatch_message

    return False, False, ""

def _handle_skip_if_same_endpoint(
    *,
    results: list[dict],
    plan: DevicePlan,
    sn: int | None,
    verified_on_device: bool,
    state_on_skip: str = "old",
    print_message: str = "Device already has target CAN settings.",
    disconnect_message: str = "OK (skip). Remove device.",
) -> tuple[bool, bool, str]:
    """
    Skip reconfiguration only if:
    - the planned old endpoint equals the planned target endpoint, and
    - a hard readback verification on the actual device has confirmed
      CMD, ANSWER, VALUE, and CANBAUD

    This prevents false positives caused by stale or unknown YAML data.

    Returns:
        already_recorded,
        skip_programming,
        disconnect_reason
    """
    if plan.cmd_new is None or plan.ans_new is None or plan.value_new is None:
        return False, False, ""
    
    planned_same = _same_endpoint(
        plan.cmd_old, plan.ans_old, plan.value_old,
        plan.cmd_new, plan.ans_new, plan.value_new,
        plan.baud_old, plan.baud_new,
    )
    if not planned_same:
        return False, False, ""
    
    if not verified_on_device:
        print(
            f"[{_fmt_dev(plan.dev_no, sn)}] Planned old/new endpoint is identical, "
            "but device readback is not fully correct. Re-applying settings."
        )
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
        value_old=plan.value_old,
        cmd_new=plan.cmd_new,
        ans_new=plan.ans_new,
        value_new=plan.value_new,
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
    Apply the target CAN settings, reactivate, verify effective state and record the result.

    Returns:
        sn,
        already_recorded,
        skip_programming,
        disconnect_reason
    """

    if plan.cmd_new is None or plan.ans_new is None or plan.value_new is None:
        raise ValueError(f"Missing target IDs for dev_no={plan.dev_no}")
    
    ok2, sn2 = _apply_target_and_reconnect(
        gsv,
        plan.dev_no,
        sn,
        plan.cmd_new,
        plan.ans_new,
        plan.value_new,
        baud_new=plan.baud_new,
    )

    sn_out = sn2 if sn2 is not None else sn

    # Determine resulting state
    state = "new" if ok2 else _probe_state_after_fail(
        gsv,
        plan.dev_no,
        plan.cmd_old,
        plan.ans_old,
        plan.value_old,
        plan.cmd_new,
        plan.ans_new,
        plan.value_new,
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
        value_old=plan.value_old,
        cmd_new=plan.cmd_new,
        ans_new=plan.ans_new,
        value_new=plan.value_new,
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
    disconnect_message: str = "⏭️ Aborted (Ctrl+C) during device step. Device will be released and must be removed.",
    warn_where: str = "KeyboardInterrupt (Ctrl+C)",
) -> tuple[bool, str]:
    """
    Handle Ctrl+C during a device step.

    Behavior:
    - release the current device safely
    - record an 'unknown' result if nothing has been recorded yet

    Returns:
        already_recorded,
        disconnect_reason
    """
    disconnect_reason = disconnect_message
    safe_plan = plan.with_safe_new_ids()

    # Best-effort release before leaving the device step.
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
            value_old=safe_plan.value_old,
            cmd_new=safe_plan.cmd_new,
            ans_new=safe_plan.ans_new,
            value_new=safe_plan.value_new,
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
    Handle an unexpected exception during a device step.

    Behavior:
    - print the error
    - probe the effective device state if possible
    - record the result as failure
    - return a disconnect message

    Returns:
        disconnect_reason
    """
    safe_plan = plan.with_safe_new_ids()
    print(f"[DEV {safe_plan.dev_no}] ERROR ({where}): {err}")

    state = "unknown"
    try:
        state = _probe_state_after_fail(
            gsv,
            safe_plan.dev_no,
            safe_plan.cmd_old,
            safe_plan.ans_old,
            safe_plan.value_old,
            safe_plan.cmd_new,
            safe_plan.ans_new,
            safe_plan.value_new,
            baud_old=safe_plan.baud_old,
            baud_new=safe_plan.baud_new,
        )
    except Exception as e2:
        print(f"[DEV {safe_plan.dev_no}] WARN: state probe failed: {e2}")

    _record_result(
        results=results,
        dev_no=safe_plan.dev_no,
        sn=sn,
        ok=False,
        state=state,
        cmd_old=safe_plan.cmd_old,
        ans_old=safe_plan.ans_old,
        value_old=safe_plan.value_old,
        cmd_new=safe_plan.cmd_new,
        ans_new=safe_plan.ans_new,
        value_new=safe_plan.value_new,
        baud_old=safe_plan.baud_old,
        baud_new=safe_plan.baud_new,
        warn_unknown=True,
        warn_where=f"{where}/state probe",
    )

    return f"ERROR: {err} (state={state}). Remove device."

def _run_device_step(
    *,
    gsv: GSV86CAN,
    results: list[dict],
    plan: DevicePlan,
    expected_sn: int | None,
    resolve_target_after_activate: bool,
    validate_expected_serial: bool,
) -> None:

    """
    Execute the full workflow for one device.

    Steps:
    1) ask the user to connect exactly one device
    2) activate using the start endpoint
    3) optionally resolve target IDs after activation
    4) optionally validate the serial number
    5) verify the current endpoint (best effort)
    6) skip if the current endpoint already equals the target endpoint
    7) otherwise apply the target CAN settings
    8) always disconnect and release the device safely
    """

    sn = None
    already_recorded = False
    skip_programming = False
    disconnect_reason = "Continue with next device."
    start_verify_ok = False

    try:
        # Prompt the user to connect the device physically.
        _connect_one(plan.dev_no)

        # Step 1: activate the device using the start endpoint.
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

        # Step 2.1: resolve target IDs after activation if required.
        if not skip_programming and resolve_target_after_activate:
            plan, already_recorded, skip_programming, disconnect_reason = _resolve_target_ids_after_activate(
                results=results,
                plan=plan,
                sn=sn,
                fail_state="old",
                fail_message="ERROR: Target CAN IDs could not be determined. This device will be skipped.",
            )

        # Step 2.2: validate the serial number if required.
        if not skip_programming and validate_expected_serial:
            already_recorded, skip_programming, disconnect_reason = _validate_expected_serial(
                results=results,
                plan=plan,
                expected_sn=expected_sn,
                sn=sn,
            )

        # Step 3: verify the current endpoint.
        # Verification failure is only a warning and does not stop the workflow.
        if not skip_programming:
            start_verify_ok = _verify_ids(gsv, plan.dev_no, sn, plan.cmd_old, plan.ans_old, plan.value_old, plan.baud_old)
            if not start_verify_ok:
                print(f"[{_fmt_dev(plan.dev_no, sn)}] WARN: Start CAN settings do not fully match "
                        f"(CMD/ANS/CV/BAUD) despite successful activation.")

        # Step 4: skip the programming step if the device already has the target settings.
        if not skip_programming:
            already_recorded, skip_programming, disconnect_reason = _handle_skip_if_same_endpoint(
                results=results,
                plan=plan,
                sn=sn,
                verified_on_device=start_verify_ok,
                state_on_skip="new",
                print_message="The device already has the target CAN settings.",
                disconnect_message="OK (skipped). Please remove the device.",
            )

        # Step 5: apply the target settings if still required.
        if not skip_programming:
            sn, already_recorded, skip_programming, disconnect_reason = _apply_target_or_record_result(
                gsv=gsv,
                results=results,
                plan=plan,
                sn=sn,
                success_message="✅ OK: The device was successfully updated to the new CAN settings. Please remove it.",
                failure_message="ERROR: Reconfiguration failed (state={state}). Please remove the device.",
            )

    except KeyboardInterrupt:
        # Handle Ctrl+C in a controlled way for the current device.
        already_recorded, disconnect_reason = _handle_keyboard_interrupt(
            gsv=gsv,
            results=results,
            plan=plan,
            sn=sn,
            already_recorded=already_recorded,
        )

    except Exception as e:
        # Handle unexpected device-level errors.
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
            disconnect_reason = f"ERROR after result recording: {e}. Remove device."

    finally:
        # Always ask the user to disconnect the device and release resources.
        try:
            _disconnect_one(gsv, plan.dev_no, sn, reason=disconnect_reason)
        except KeyboardInterrupt:
            _safe_release(gsv, plan.dev_no, where="finally/KeyboardInterrupt")
            raise
        except Exception as e:
            print(f"[DEV {plan.dev_no}] WARN: disconnect step failed: {e}")
            _safe_release(gsv, plan.dev_no, where="finally/disconnect-except")