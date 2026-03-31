"""
results.py

Result handling helpers for StartupCAN.

This module is responsible for:
- recording per-device execution results
- printing a readable summary after a run
- checking whether all devices succeeded or failed
- converting recorded results into effective current.ids entries
- merging updated current.ids data back into an existing YAML structure
- formatting integer CAN IDs as hex strings for YAML output
"""

from startupcan.ui import fmt_can_id, _warn_unknown

def _record_result(
    *,
    results: list[dict],
    dev_no: int,
    sn: int | None,
    ok: bool,
    state: str,
    cmd_old: int,
    ans_old: int,
    value_old: int | None,
    cmd_new: int,
    ans_new: int,
    value_new: int,
    baud_old: int,
    baud_new: int,
    warn_unknown: bool = False,
    warn_where: str = "",
) -> bool:
    """
    Append one device result row to the results list.

    The stored result contains:
    - device number
    - serial number if available
    - success flag
    - detected state
    - old endpoint
    - new endpoint
    - old baudrate
    - new baudrate

    If requested, an additional warning is printed for state="unknown".

    Returns:
        already_recorded
    """
    row = {
        "dev_no": int(dev_no),
        "serial": sn,
        "ok": bool(ok),
        "state": state,
        "cmd_old": int(cmd_old),
        "ans_old": int(ans_old),
        "value_old": int(value_old) if value_old is not None else None,
        "cmd_new": int(cmd_new),
        "ans_new": int(ans_new),
        "value_new": int(value_new),
        "baud_old": int(baud_old),
        "baud_new": int(baud_new),
    }
    
    results.append(row)

    if warn_unknown and state == "unknown":
        _warn_unknown(dev_no, sn, where=warn_where)

    return True

def _fmt_optional_can_id(x: int | None) -> str:
    return fmt_can_id(x) if x is not None else "?"

def _print_summary(rows: list[dict]):
    """
    Print a compact run summary for all processed devices.

    Each row shows:
    - device number
    - serial number if available
    - OK / FAIL
    - old CMD/ANS IDs
    - new CMD/ANS IDs
    - optional detected state
    """

    print("\n" + "=" * 80)
    print("Summary")
    print("=" * 80)
    for r in rows:
        dev_no = r["dev_no"]
        ok = r["ok"]
        cmd_old = r["cmd_old"]
        ans_old = r["ans_old"]
        val_old = r["value_old"]
        cmd_new = r["cmd_new"]
        ans_new = r["ans_new"]
        val_new = r["value_new"]
        serial = r.get("serial")
        tag = f"DEV {dev_no} (SN={serial if serial is not None else '?'})"
        state = r.get("state", "")
        state_txt = f" | state={state}" if state else ""
        print(
            f"{tag}: "
            f"{'OK ' if ok else 'FAIL'} | "
            f"CMD/ANS/VAL {fmt_can_id(cmd_old)}/{fmt_can_id(ans_old)}/{_fmt_optional_can_id(val_old)} -> "
            f"{fmt_can_id(cmd_new)}/{fmt_can_id(ans_new)}/{fmt_can_id(val_new)}"
            f"{state_txt}"
        )
    print("=" * 80 + "\n")

def _all_ok(results: list[dict], expected: int) -> bool:
    """
    Return True if:
    - the number of result rows matches the expected number of devices
    - every recorded result has ok=True
    """
    return (len(results) == expected) and all(bool(r.get("ok")) for r in results)

def _all_fail(results: list[dict], expected: int) -> bool:
    """
    Return True if:
    - the number of result rows matches the expected number of devices
    - every recorded result has ok=False
    """
    return (len(results) == expected) and all(not bool(r.get("ok")) for r in results)

def _effective_current_ids_from_results(results: list[dict]) -> list[dict]:
    """
    Build effective devices.config.current.ids entries from recorded run results.

    The selected endpoint depends on the detected state:

    - state="new"
        use new IDs and new baudrate

    - state="old"
        use old IDs and old baudrate

    - state="old_newbaud"
        use old IDs and new baudrate

    - state="new_oldbaud"
        use new IDs and old baudrate

    - state="unknown"
        keep old IDs and old baudrate, and add unknown=true

    Returns:
        A sorted list of current.ids-style dictionaries.
    """
    out = []
    for r in results:
        state = r.get("state")  # "old"|"new"|"unknown"|None

        if state == "new":
            cmd_eff, ans_eff, value_eff = r["cmd_new"], r["ans_new"], r["value_new"]
            baud_eff = r.get("baud_new")
        elif state == "old":
            cmd_eff, ans_eff, value_eff = r["cmd_old"], r["ans_old"], r["value_old"]
            baud_eff = r.get("baud_old")
        elif state == "old_newbaud":
            cmd_eff, ans_eff, value_eff = r["cmd_old"], r["ans_old"], r["value_old"]
            baud_eff = r.get("baud_new")
        elif state == "new_oldbaud":
            cmd_eff, ans_eff, value_eff = r["cmd_new"], r["ans_new"], r["value_new"]
            baud_eff = r.get("baud_old")
        else:
            # Unknown means the actual endpoint could not be confirmed reliably.
            # Keep the old endpoint and mark the device as unknown.
            cmd_eff, ans_eff, value_eff = r["cmd_old"], r["ans_old"], r["value_old"]
            baud_eff = r.get("baud_old")
        
        item = {
            "dev_no": int(r["dev_no"]),
            "cmd_id": int(cmd_eff),
            "answer_id": int(ans_eff),
        }

        if value_eff is not None:
            item["value_id"] = int(value_eff)

        if r.get("serial") is not None:
            item["serial"] = int(r["serial"])
        
        if state == "unknown":
            item["unknown"] = True  
        
        if baud_eff is not None:
            item["canbaud"] = int(baud_eff)

        out.append(item)
    out.sort(key=lambda d: d["dev_no"])
    return out

def _merge_current_ids(
    original_current: list[dict],
    updated_subset: list[dict],
    *,
    keep_unknown_flags: bool = True,
) -> list[dict]:
    """
    Merge updated current.ids entries into an original current.ids list.

    Rules:
    - entries from updated_subset overwrite matching dev_no entries
    - entries not touched by updated_subset remain unchanged
    - new dev_no entries from updated_subset are added
    - optional unknown flags are preserved/updated

    Args:
        original_current:
            Existing current.ids list from the YAML.

        updated_subset:
            Recomputed current.ids entries derived from recorded results.

        keep_unknown_flags:
            If True, preserve and update unknown flags.

    Returns:
        A merged and dev_no-sorted current.ids list.
    """
    by_dev: dict[int, dict] = {}

    # Start with the original YAML state
    for d in (original_current or []):
        dn = int(d["dev_no"])
        by_dev[dn] = dict(d)

    # Overwrite or add updated entries
    for u in (updated_subset or []):
        dn = int(u["dev_no"])
        merged = dict(by_dev.get(dn, {}))

        merged["dev_no"] = dn
        merged["cmd_id"] = int(u["cmd_id"])
        merged["answer_id"] = int(u["answer_id"])
        
        # Preserve an existing value_id if the updated subset does not provide one.
        if "value_id" in u and u["value_id"] is not None:
            merged["value_id"] = int(u["value_id"])

        if u.get("serial") is not None:
            merged["serial"] = int(u["serial"])

        if "canbaud" in u and u["canbaud"] is not None:
            merged["canbaud"] = int(u["canbaud"])

        if keep_unknown_flags:
            if u.get("unknown"):
                merged["unknown"] = True
            else:
                merged.pop("unknown", None)

        by_dev[dn] = merged

    out = list(by_dev.values())
    out.sort(key=lambda x: int(x["dev_no"]))
    return out

def _hex_str(x: int) -> str:
    """
    Format an integer as an uppercase hexadecimal string for YAML output.

    Example:
        258 -> "0x102"
    """
    return f"0x{x:X}"