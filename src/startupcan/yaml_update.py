"""
yaml_update.py

YAML output helpers for StartupCAN.

This module is responsible for writing the final configuration file after a run.
It converts the collected runtime results into an updated `config.updated.yaml`
that reflects the detected actual device state.

Main responsibilities:
- preserve YAML formatting/comments using ruamel.yaml
- update `devices.config.current`
- optionally clear `devices.config.new` after a fully successful run
- print the final summary and status message
"""

from pathlib import Path

from ruamel.yaml import YAML

from startupcan.config import CONFIG_PATH, DEVICE_CONFIG
from startupcan.results import (
    _print_summary,
    _effective_current_ids_from_results,
    _merge_current_ids,
    _all_ok,
    _hex_str,
)


def _write_updated_yaml(
    src_path: Path,
    dst_path: Path,
    current_default: bool,
    current_ids: list[dict],
    make_new_safe: bool = True,
    drop_canbaud: bool = False
):
    """
    Write an updated YAML file with the detected current device state.

    The file is written as a round-trip YAML using ruamel.yaml, which means:
    - comments are preserved
    - formatting is preserved as much as possible
    - key order is preserved

    Args:
        src_path:
            Path to the original YAML file.

        dst_path:
            Path where the updated YAML file should be written.

        current_default:
            Value for `devices.config.current.default`.

        current_ids:
            List of current device entries that should be written into
            `devices.config.current.ids`.

        make_new_safe:
            If True, `devices.config.new` is cleared and `new.default` is set
            to False. This is used after a fully successful run to prevent
            accidental reconfiguration on the next run.

        drop_canbaud:
            If True, `canbaud` is omitted from written `current.ids` entries.
            This is typically used after a fully successful run, because the
            baudrate is then implicitly defined by the main configuration.
    """

    # Create a round-trip YAML handler
    y = YAML()
    y.preserve_quotes = True
    y.indent(mapping=2, sequence=4, offset=2)  

    # Load the existing YAML file
    with open(src_path, "r", encoding="utf-8") as f:
        cfg = y.load(f) or {}

    # Ensure the required nested sections exist
    devices = cfg.setdefault("devices", {})
    config = devices.setdefault("config", {})
    current = config.setdefault("current", {})
    new = config.setdefault("new", {})

    # Update the "current" section to reflect the detected actual state
    current["default"] = bool(current_default)
    current["ids"] = [
        {
            "dev_no": int(d["dev_no"]),
            **({"serial": int(d["serial"])} if "serial" in d and d["serial"] is not None else {}),
            **({"unknown": True} if d.get("unknown") else {}),
            **({} if drop_canbaud or d.get("canbaud") is None else {"canbaud": int(d["canbaud"])}),
            "cmd_id": _hex_str(int(d["cmd_id"])),
            "answer_id": _hex_str(int(d["answer_id"])),
        }
        for d in current_ids
    ]

    # Optionally clear the "new" section after a fully successful run
    # so the next execution does not try to reconfigure the same devices again.
    if make_new_safe:
        new["default"] = False
        new["ids"] = []

    # Write the updated YAML file
    with open(dst_path, "w", encoding="utf-8") as f:
        y.dump(cfg, f)
    
def _finalize_run_and_write_yaml(
    *,
    results: list[dict],
    base_current_ids: list[dict],
    current_default: bool,
    success_message: str,
    warning_message: str,
) -> int:
    """
    Finalize a StartupCAN run and write `config.updated.yaml`.

    This function:
    1. prints the run summary
    2. derives the effective current device state from the collected results
    3. merges that state into the base current configuration
    4. writes `config.updated.yaml`
    5. prints a final success/warning message

    Args:
        results:
            Per-device results collected during the run.

        base_current_ids:
            Base list used as starting point for the final current state.
            This depends on the selected operating mode.

        current_default:
            Value to write into `devices.config.current.default`.

        success_message:
            Message printed if all devices were processed successfully.

        warning_message:
            Message printed if the run was only partially successful.

    Returns:
        Exit code 0.
    """

    # Print a per-device summary first
    _print_summary(results)

    # Convert the raw device results into effective current.ids entries
    updated_subset = _effective_current_ids_from_results(results)

    # Merge these entries with the base current configuration
    current_ids = _merge_current_ids(base_current_ids, updated_subset)

    # Determine whether the full run succeeded
    all_ok = _all_ok(results, len(DEVICE_CONFIG))

    # The updated configuration is always written next to the original config
    dst = Path(CONFIG_PATH).with_name("config.updated.yaml")

    _write_updated_yaml(
        src_path=Path(CONFIG_PATH),
        dst_path=dst,
        current_default=current_default,
        current_ids=current_ids,
        make_new_safe=all_ok,
        drop_canbaud=all_ok,
    )

    print(f"[INFO] Updated YAML written: {dst}")

    if all_ok:
        print(success_message)
    else:
        print(warning_message)

    return 0