"""
planning.py

Planning helpers for StartupCAN.

This module builds the high-level run configuration and the per-device execution
plans used by the main workflow.

Responsibilities:
- determine which execution mode is active
- build the RunConfig for the selected mode
- build a DevicePlan for each device
- resolve target IDs by dev_no or serial number
- determine fallback/current baudrates
- provide the baseline current.ids structure for default-mode runs
"""
from startupcan.models import RunConfig, DevicePlan

from startupcan.config import (
    DEVICE_CONFIG,
    DEVICE_NEW,
    CURRENT_DEFAULT_MODE,
    NEW_DEFAULT_MODE,
    SN_MODE,
    DEFAULT_CMD_ID,
    DEFAULT_ANS_ID,
    DEFAULT_CANBAUD,
    CANBAUD,
)

from startupcan.ui import fmt_can_id


def _build_run_config() -> RunConfig:
    """
    Build the run-level configuration for the active StartupCAN mode.

    There are three supported modes:

    1. current.default = true,  new.default = false
       Devices start from default CAN settings and are assigned new target IDs.

    2. current.default = false, new.default = true
       Devices start from current CAN settings and are reset back to default IDs.

    3. current.default = false, new.default = false
       Devices start from current CAN settings and are reassigned to new target IDs.

    Returns:
        RunConfig describing prompts, messages, YAML merge base, and device-flow
        behavior for the current mode.
    """
    if CURRENT_DEFAULT_MODE:
        return RunConfig(
            intro_lines=[
                "",
                "IMPORTANT:",
                f"- Default IDs: CMD={fmt_can_id(DEFAULT_CMD_ID)} ANS={fmt_can_id(DEFAULT_ANS_ID)}",
                "- Connect EXACTLY ONE amplifier at a time to avoid CAN collisions.",
                "- Target IDs are taken from devices.config.new.ids.",
            ],
            continue_prompt="[WIZARD] Reconfigure next device? [y/N]: ",
            base_current_ids=_baseline_current_for_case2_with_baud(),
            success_message="[INFO] All devices were reconfigured successfully and may now be connected together on the bus.",
            warning_message=(
                "[WARN] Not all devices were reconfigured successfully. "
                "Check config.updated.yaml before connecting all devices together. "
                "(No duplicate CAN IDs and no unknown:true entries.)"
            ),
            resolve_target_after_activate=True,
            validate_expected_serial=False,
        )

    if NEW_DEFAULT_MODE:
        return RunConfig(
            intro_lines=[
                "",
                "[INFO] Forced-Reset Wizard: current.default=false & new.default=true",
                "[INFO] Goal: reset all devices back to default CAN IDs.",
                "[INFO] Each device will be reset individually and then removed from the bus.",
                f"- Default IDs: CMD={fmt_can_id(DEFAULT_CMD_ID)} ANS={fmt_can_id(DEFAULT_ANS_ID)}",
            ],
            continue_prompt="[WIZARD] Reset next device to DEFAULT? [y/N]: ",
            base_current_ids=DEVICE_CONFIG or [],
            success_message=(
                "⚠️  NOTICE: All devices are now on DEFAULT IDs.\n"
                "All devices now share the same CAN IDs, which may cause collisions or bus-off.\n"
                "=> Do NOT operate or activate them together on the bus.\n"
                "=> Connect and activate them ONE BY ONE only.\n"
            ),
            warning_message=(
                "[WARN] Not all devices were successfully reset to DEFAULT. "
                "Check config.updated.yaml first. "
                "Only operate the bus with the IDs listed there."
            ),
            resolve_target_after_activate=False,
            validate_expected_serial=True,
        )

    return RunConfig(
        intro_lines=[
            "[INFO] new.default=false: target IDs are taken from devices.config.new.ids.",
        ],
        continue_prompt="[WIZARD] Process next device? [y/N]: ",
        base_current_ids=DEVICE_CONFIG or [],
        success_message="\n[INFO] new.default=false: devices may now be connected together on the bus (IDs are unique).",
        warning_message=(
            "[WARN] Not all devices were processed successfully. "
            "The YAML now reflects the detected actual state (some devices may still have old IDs). "
            "(No duplicate CAN IDs and no unknown:true entries.)"
        ),
        resolve_target_after_activate=True,
        validate_expected_serial=True,
    )

def _build_device_plan(d: dict) -> tuple[DevicePlan, int | None]:
    """
    Build the execution plan for one device.

    Depending on the active mode, the starting endpoint is either:
    - default CMD/ANS/baud
    - current CMD/ANS/baud from YAML

    The target endpoint is either:
    - default CMD/ANS/baud
    - target CMD/ANS from new.ids plus CANBAUD
    - temporarily unresolved (SN mode), to be resolved later after activation

    Args:
        d:
            One device entry from DEVICE_CONFIG.

    Returns:
        A tuple of:
        - DevicePlan
        - expected serial number from YAML, if present
    """
    dev_no = int(d["dev_no"])
    expected_sn = d.get("serial") if isinstance(d, dict) else None

    # Determine the starting endpoint used for activation
    if CURRENT_DEFAULT_MODE:
        # Case 2
        cmd_old = DEFAULT_CMD_ID
        ans_old = DEFAULT_ANS_ID
        baud_old = DEFAULT_CANBAUD
    else:
        # Case 1 + 3
        cmd_old = int(d["cmd_id"])
        ans_old = int(d["answer_id"])
        baud_old = _current_canbaud_for(dev_no) or CANBAUD

    # Case 3: forced reset to default endpoint
    if NEW_DEFAULT_MODE and not CURRENT_DEFAULT_MODE:
        return (
            DevicePlan(
                dev_no=dev_no,
                cmd_old=cmd_old,
                ans_old=ans_old,
                baud_old=baud_old,
                cmd_new=DEFAULT_CMD_ID,
                ans_new=DEFAULT_ANS_ID,
                baud_new=DEFAULT_CANBAUD,
            ),
            expected_sn,
        )

    # Case 1 + 2: target IDs are resolved later using serial mapping
    if SN_MODE:
        return (
            DevicePlan(
                dev_no=dev_no,
                cmd_old=cmd_old,
                ans_old=ans_old,
                baud_old=baud_old,
                cmd_new=None,
                ans_new=None,
                baud_new=CANBAUD,
            ),
            expected_sn,
        )

    # Case 1 + 2: target IDs are known directly via dev_no mapping
    target_cmd, target_ans = _new_ids_for(dev_no)
    return (
        DevicePlan(
            dev_no=dev_no,
            cmd_old=cmd_old,
            ans_old=ans_old,
            baud_old=baud_old,
            cmd_new=target_cmd,
            ans_new=target_ans,
            baud_new=CANBAUD,
        ),
        expected_sn,
    )

def _new_ids_for(dev_no: int) -> tuple[int, int]:
    """
    Look up target CAN IDs for a device via dev_no.

    Args:
        dev_no:
            Logical device number from the YAML configuration.

    Returns:
        Tuple (cmd_id, answer_id)

    Raises:
        KeyError:
            If no matching target entry exists in devices.config.new.ids.
    """
    for d in DEVICE_NEW:
        if int(d["dev_no"]) == int(dev_no):
            return int(d["cmd_id"]), int(d["answer_id"])
    raise KeyError(f"DEV {dev_no}: no target IDs found in devices.config.new.ids")

def _new_ids_for_serial(serial: int) -> tuple[int, int]:
    """
    Look up target CAN IDs via serial number.

    Args:
        serial:
            Device serial number read from the device.

    Returns:
        Tuple (cmd_id, answer_id)

    Raises:
        KeyError:
            If no matching serial entry exists in devices.config.new.ids.
    """
    for d in DEVICE_NEW:
        if "serial" in d and int(d["serial"]) == int(serial):
            return int(d["cmd_id"]), int(d["answer_id"])
    raise KeyError(f"No target IDs found in new.ids for serial={serial}")

def _target_ids(dev_no: int, serial: int | None) -> tuple[int, int]:
    """
    Resolve target CAN IDs for a device.

    Behavior:
    - SN_MODE=True  -> resolve via serial number
    - SN_MODE=False -> resolve via dev_no

    Args:
        dev_no:
            Logical device number.
        serial:
            Serial number read from the device, if available.

    Returns:
        Tuple (cmd_id, answer_id)

    Raises:
        KeyError:
            If serial-based resolution is required but serial is missing,
            or if no matching target entry exists.
    """
    if SN_MODE:
        if serial is None:
            raise KeyError(f"SN_MODE is active but the serial number could not be read (dev_no={dev_no}).")
        return _new_ids_for_serial(serial)
    
    return _new_ids_for(dev_no)

def _current_canbaud_for(dev_no: int) -> int | None:
    """
    Look up the configured current CAN baudrate for one device.

    Args:
        dev_no:
            Logical device number.

    Returns:
        The configured current baudrate if present in DEVICE_CONFIG,
        otherwise None.
    """
    for d in (DEVICE_CONFIG or []):
        if int(d.get("dev_no")) == int(dev_no):
            cb = d.get("canbaud")
            return int(cb) if cb is not None else None
    return None

def _baseline_current_for_case2_with_baud() -> list[dict]:
    """
    Build the baseline current.ids list for case 2
    (current.default=true, new.default=false).

    In this mode, the current.ids list is ignored as input, but the updated
    YAML still needs a complete current.ids section after the run.

    Baseline behavior:
    - every device from new.ids is assumed to start on default IDs
    - default baudrate is used as the starting baudrate
    - serial numbers are not included here initially
    - processed devices will later overwrite these entries during merge

    Returns:
        List of current.ids-style dictionaries using default CAN settings.
    """
    baseline: list[dict] = []

    for d in (DEVICE_NEW or []): 
        baseline.append({
            "dev_no": int(d["dev_no"]),
            "cmd_id": int(DEFAULT_CMD_ID),
            "answer_id": int(DEFAULT_ANS_ID),
            "canbaud": int(DEFAULT_CANBAUD),
        })
    baseline.sort(key=lambda x: int(x["dev_no"]))
    return baseline

