"""
main.py

Headless CAN startup and reconfiguration tool for GSV CAN devices.

StartupCAN configures devices over the CAN bus based on a YAML configuration
that defines the current device state (`current.ids`) and the desired target
state (`new.ids`). Devices are processed one by one and the detected final
state is written to `config.updated.yaml`.

Operating modes (defined by `current.default` and `new.default`):

1) Device Update (current.default=false, new.default=false)
   Reassign devices from `current.ids` to `new.ids`.

2) Default Mode (current.default=true, new.default=false)
   Devices start with default CAN settings and are configured to `new.ids`.

3) Forced Reset Wizard (current.default=false, new.default=true)
   Devices are reset from `current.ids` back to default CAN settings.

For safety, devices are always processed individually to avoid CAN ID
collisions on the bus.
"""

import sys
import argparse


from startupcan.config import (
    DEVICE_CONFIG,
    CURRENT_DEFAULT_MODE,
    NEW_DEFAULT_MODE,
)
from startupcan.gsv86can import GSV86CAN

from startupcan.planning import _build_run_config, _build_device_plan
from startupcan.device_flow import _run_device_step
from startupcan.results import _all_ok, _all_fail
from startupcan.yaml_update import _finalize_run_and_write_yaml
from startupcan.ui import _ask_continue
from startupcan.runtime import HANDLE_ACTIVE, _safe_release
from startupcan.ui import _warn_unknown



def main() -> int:
    """
    Main entry point for the StartupCAN wizard.

    High-level flow:
    1) Initialize DLL access
    2) Validate the selected YAML mode
    3) Print warnings / run information
    4) Build the run configuration for the active case
    5) Process devices one by one
    6) Write config.updated.yaml with the detected final state
    7) Safely release all remaining active handles on shutdown
    """
    parser = argparse.ArgumentParser(description="StartupCAN")
    parser.add_argument(
        "--in-place",
        action="store_true",
        help="Overwrite config.yaml instead of writing config.updated.yaml"
    )

    args = parser.parse_args()

    gsv = GSV86CAN()
    results = []

    try:
        # ------------------------------------------------------------------
        # Check whether the DLL is reachable before doing anything else.
        # If this fails, there is no point in continuing.
        # ------------------------------------------------------------------
        try:
            v = gsv.dll_version()
            print(f"[INFO] DLL Version: {v}")
        except Exception as e:
            print(f"[FAIL] Could not read DLL version: {e}")
            return 2

        # ------------------------------------------------------------------
        # Print the selected mode from the YAML configuration.
        # These two flags define which startup/reset workflow is used.
        # ------------------------------------------------------------------
        print(f"[INFO] current.default = {CURRENT_DEFAULT_MODE}")
        print(f"[INFO] new.default     = {NEW_DEFAULT_MODE}")

        if CURRENT_DEFAULT_MODE and NEW_DEFAULT_MODE:
            print("[FAIL] Invalid configuration: current.default=true and new.default=true is not allowed.")
            return 2
        
        print(f"[INFO] Devices in config: {len(DEVICE_CONFIG)}")
        print("-" * 80)

        # ------------------------------------------------------------------
        # Warn the user if current.ids already contains unknown=true entries.
        # This does not stop the run, but it is an important diagnostic hint.
        # ------------------------------------------------------------------
        unknown_in_yaml = [d for d in (DEVICE_CONFIG or []) if isinstance(d, dict) and d.get("unknown")]
        if unknown_in_yaml:
            print("\n" + "!" * 80)
            print("[WARN] Some devices in current.ids are marked with unknown=true.")
            for d in unknown_in_yaml:
                dev_no = int(d["dev_no"])
                serial = d.get("serial")
                _warn_unknown(dev_no, int(serial) if serial is not None else None, where="yaml-current.ids")
            print("!" * 80 + "\n")

        # ------------------------------------------------------------------
        # General safety note:
        # even if devices currently have unique IDs, this tool processes them
        # one by one to avoid bus collisions and configuration ambiguity.
        # ------------------------------------------------------------------
        print("\nIMPORTANT (switching CAN settings):")
        print("- Only ONE device may be connected to the CAN bus at a time.")
        print("- Remove the device after each step.")
        print("- Only then continue with the next device.")

        # ------------------------------------------------------------------
        # Build the case-specific runtime configuration.
        # This includes:
        # - intro text
        # - continue prompt
        # - whether serial validation is required
        # - whether target IDs are resolved after activate()
        # - how the final YAML should be written
        # ------------------------------------------------------------------
        run_cfg = _build_run_config()

        for line in run_cfg.intro_lines:
            print(line)

        # ------------------------------------------------------------------
        # Main device loop:
        # Each device is handled as an isolated step.
        # The low-level activation / validation / programming logic lives in
        # _run_device_step().
        # ------------------------------------------------------------------
        for d in DEVICE_CONFIG:
            # Build the plan for this device:
            # - device number
            # - start endpoint (old/default)
            # - target endpoint (new/default)
            plan, expected_sn = _build_device_plan(d)

            _run_device_step(
                gsv=gsv,
                results=results,
                plan=plan,
                expected_sn=expected_sn if run_cfg.validate_expected_serial else None,
                resolve_target_after_activate=run_cfg.resolve_target_after_activate,
                validate_expected_serial=run_cfg.validate_expected_serial,
            )
            
            # Ask the user whether the next device should be processed.
            if not _ask_continue(run_cfg.continue_prompt):
                break
        
        # ------------------------------------------------------------------
        # Determine the final current.default value for config.updated.yaml.
        #
        # Case 2: current.default=true  -> remains true only if all processed
        #         devices effectively stayed in default mode / were not changed.
        #
        # Case 3: new.default=true      -> current.default becomes true only if
        #         all devices were successfully reset to default.
        #
        # Case 1: normal re-addressing  -> current.default is always false.
        # ------------------------------------------------------------------
        if CURRENT_DEFAULT_MODE:
            current_default = _all_fail(results, len(DEVICE_CONFIG))
        elif NEW_DEFAULT_MODE:
            current_default = _all_ok(results, len(DEVICE_CONFIG))
        else:
            current_default = False
        
        # ------------------------------------------------------------------
        # Write config.updated.yaml based on the detected final states.
        # This also prints the summary and success/warning message.
        # ------------------------------------------------------------------
        return _finalize_run_and_write_yaml(
            results=results,
            base_current_ids=run_cfg.base_current_ids,
            current_default=current_default,
            success_message=run_cfg.success_message,
            warning_message=run_cfg.warning_message,
            inplace=args.in_place,
        )

    finally:
        # ------------------------------------------------------------------
        # Last-resort cleanup:
        # release every device handle that is still marked as active.
        # This protects against leaving stale DLL/device sessions behind.
        # ------------------------------------------------------------------
        for dev_no, active in list(HANDLE_ACTIVE.items()):
            if active:
                _safe_release(gsv, dev_no, where="shutdown")
        HANDLE_ACTIVE.clear()  


if __name__ == "__main__":
    sys.exit(main())