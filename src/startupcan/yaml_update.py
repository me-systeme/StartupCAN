
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
    Schreibt eine neue YAML, in der devices.config.current.* auf den Ist-Zustand gesetzt wird.

    ruamel.yaml = Round-Trip:
    - Kommentare bleiben erhalten
    - Formatierung bleibt erhalten
    - Reihenfolge bleibt erhalten
    """

    y = YAML()
    y.preserve_quotes = True
    y.indent(mapping=2, sequence=4, offset=2)  # schön lesbar

    with open(src_path, "r", encoding="utf-8") as f:
        cfg = y.load(f) or {}

    devices = cfg.setdefault("devices", {})
    config = devices.setdefault("config", {})
    current = config.setdefault("current", {})
    new = config.setdefault("new", {})

    # current aktualisieren
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

    # Optional: new "safe" machen – aber ohne verbotene Kombination zu erzeugen!
    # Du willst meist verhindern, dass ein Run direkt nochmal "umstellt".
    if make_new_safe:
        new["default"] = False
        new["ids"] = []

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
    _print_summary(results)

    updated_subset = _effective_current_ids_from_results(results)
    current_ids = _merge_current_ids(base_current_ids, updated_subset)

    all_ok = _all_ok(results, len(DEVICE_CONFIG))

    dst = Path(CONFIG_PATH).with_name("config.updated.yaml")

    _write_updated_yaml(
        src_path=Path(CONFIG_PATH),
        dst_path=dst,
        current_default=current_default,
        current_ids=current_ids,
        make_new_safe=all_ok,
        drop_canbaud=all_ok,
    )

    print(f"[INFO] ✅ Updated YAML geschrieben: {dst}")

    if all_ok:
        print(success_message)
    else:
        print(warning_message)

    return 0