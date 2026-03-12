"""
startupcan.py

Headless startup/scanner for GSV86CAN devices.

Modes:
- DEFAULT_MODE = true:
  Wizard flow: devices must be connected ONE BY ONE (because they share default CAN IDs).
  For each dev_no from DEVICE_CONFIG (derived from YAML 'new' list):
    1) Ask user to connect exactly one amplifier
    2) activate() using DEFAULT_CMD_ID / DEFAULT_ANS_ID
    3) set IDs to YAML 'new' IDs for this dev_no
    4) reset + release + activate again using new IDs
    5) print summary

"""

import sys


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
    gsv = GSV86CAN()
    results = []

    try:
        try:
            v = gsv.dll_version()
            print(f"[INFO] DLL Version: {v}")
        except Exception as e:
            print(f"[FAIL] DLL Version konnte nicht gelesen werden: {e}")
            return 2

        print(f"[INFO] current.default = {CURRENT_DEFAULT_MODE}")
        print(f"[INFO] new.default     = {NEW_DEFAULT_MODE}")

        if CURRENT_DEFAULT_MODE and NEW_DEFAULT_MODE:
            print("[FAIL] Ungültige Konfiguration: current.default=true und new.default=true ist nicht erlaubt.")
            return 2
        
        print(f"[INFO] Devices in config: {len(DEVICE_CONFIG)}")
        print("-" * 80)

        unknown_in_yaml = [d for d in (DEVICE_CONFIG or []) if isinstance(d, dict) and d.get("unknown")]
        if unknown_in_yaml:
            print("\n" + "!" * 80)
            print("[WARN] In current.ids sind Geräte mit unknown=true markiert.")
            for d in unknown_in_yaml:
                dev_no = int(d["dev_no"])
                serial = d.get("serial")
                _warn_unknown(dev_no, int(serial) if serial is not None else None, where="yaml-current.ids")
            print("!" * 80 + "\n")

        print("\nWICHTIG (Umstellung auf neue CAN Settings):")
        print("- Es darf immer nur ein Gerät am Bus sein (damit es nicht zur Kollision kommt).")
        print("- Nach jedem Schritt Gerät abnehmen (immer nur eins am Bus).")
        print("  bevor du das nächste Gerät bearbeitest.")

        run_cfg = _build_run_config()

        for line in run_cfg.intro_lines:
            print(line)

        for d in DEVICE_CONFIG:

            plan, expected_sn = _build_device_plan(d)

            _run_device_step(
                gsv=gsv,
                results=results,
                plan=plan,
                expected_sn=expected_sn if run_cfg.validate_expected_serial else None,
                resolve_target_after_activate=run_cfg.resolve_target_after_activate,
                validate_expected_serial=run_cfg.validate_expected_serial,
            )
            
            if not _ask_continue(run_cfg.continue_prompt):
                break
        
        if CURRENT_DEFAULT_MODE:
            current_default = _all_fail(results, len(DEVICE_CONFIG))
        elif NEW_DEFAULT_MODE:
            current_default = _all_ok(results, len(DEVICE_CONFIG))
        else:
            current_default = False
        return _finalize_run_and_write_yaml(
            results=results,
            base_current_ids=run_cfg.base_current_ids,
            current_default=current_default,
            success_message=run_cfg.success_message,
            warning_message=run_cfg.warning_message,
        )

    finally:
        for dev_no, active in list(HANDLE_ACTIVE.items()):
            if active:
                _safe_release(gsv, dev_no, where="shutdown")
        HANDLE_ACTIVE.clear()  


if __name__ == "__main__":
    sys.exit(main())