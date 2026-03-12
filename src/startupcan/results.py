
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
    cmd_new: int,
    ans_new: int,
    baud_old: int,
    baud_new: int,
    warn_unknown: bool = False,
    warn_where: str = "",
) -> bool:
    """
    Hängt ein fehlgeschlagenes Ergebnis an results an.

    Returns:
        already_recorded (= immer True)
    """
    row = {
        "dev_no": int(dev_no),
        "serial": sn,
        "ok": bool(ok),
        "state": state,
        "cmd_old": int(cmd_old),
        "ans_old": int(ans_old),
        "cmd_new": int(cmd_new),
        "ans_new": int(ans_new),
        "baud_old": int(baud_old),
        "baud_new": int(baud_new),
    }
    
    results.append(row)

    if warn_unknown and state == "unknown":
        _warn_unknown(dev_no, sn, where=warn_where)

    return True

def _print_summary(rows: list[dict]):
    print("\n" + "=" * 80)
    print("Zusammenfassung")
    print("=" * 80)
    for r in rows:
        dev_no = r["dev_no"]
        ok = r["ok"]
        cmd_old = r["cmd_old"]
        ans_old = r["ans_old"]
        cmd_new = r["cmd_new"]
        ans_new = r["ans_new"]
        serial = r.get("serial")
        tag = f"DEV {dev_no} (SN={serial if serial is not None else '?'})"
        state = r.get("state", "")
        state_txt = f" | state={state}" if state else ""
        print(
            f"{tag}: "
            f"{'OK ' if ok else 'FAIL'} | "
            f"{fmt_can_id(cmd_old)}/{fmt_can_id(ans_old)}  ->  "
            f"{fmt_can_id(cmd_new)}/{fmt_can_id(ans_new)}"
            f"{state_txt}"
        )
    print("=" * 80 + "\n")

def _all_ok(results: list[dict], expected: int) -> bool:
    return (len(results) == expected) and all(bool(r.get("ok")) for r in results)

def _all_fail(results: list[dict], expected: int) -> bool:
    return (len(results) == expected) and all(not bool(r.get("ok")) for r in results)

def _effective_current_ids_from_results(results: list[dict]) -> list[dict]:
    """
    Baut devices.config.current.ids so, dass es den IST-Zustand abbildet:
    - ok=True  => cmd_new/ans_new
    - ok=False => cmd_old/ans_old (Gerät wurde übersprungen/failed)
    """
    out = []
    for r in results:
        state = r.get("state")  # "old"|"new"|"unknown"|None

        if state == "new":
            cmd_eff, ans_eff = r["cmd_new"], r["ans_new"]
            baud_eff = r.get("baud_new")
        elif state == "old":
            cmd_eff, ans_eff = r["cmd_old"], r["ans_old"]
            baud_eff = r.get("baud_old")
        elif state == "old_newbaud":
            cmd_eff, ans_eff = r["cmd_old"], r["ans_old"]
            baud_eff = r.get("baud_new")
        elif state == "new_oldbaud":
            cmd_eff, ans_eff = r["cmd_new"], r["ans_new"]
            baud_eff = r.get("baud_old")
        else:
            # unknown: du kannst entweder old drin lassen (aber markieren)
            # oder bewusst new drin lassen, weil das Ziel war.
            # Ich würde: old drin lassen + unknown Flag separat (siehe next step)
            cmd_eff, ans_eff = r["cmd_old"], r["ans_old"]
            baud_eff = r.get("baud_old")
        
        item = {
            "dev_no": int(r["dev_no"]),
            "cmd_id": int(cmd_eff),
            "answer_id": int(ans_eff),
        }
        if r.get("serial") is not None:
            item["serial"] = int(r["serial"])
        # optional: unknown markieren
        if state == "unknown":
            item["unknown"] = True  # (wenn du das im YAML tolerierst)
        
        # WICHTIG: canbaud nur setzen, wenn wir einen Wert haben
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
    Merged current.ids:
    - updated_subset überschreibt die Einträge aus original_current für gleiche dev_no
    - dev_no die nicht in updated_subset sind bleiben wie original_current
    - dev_no die neu sind (in updated_subset aber nicht original_current) werden ergänzt
    """
    by_dev: dict[int, dict] = {}

    # 1) Original übernehmen
    for d in (original_current or []):
        dn = int(d["dev_no"])
        by_dev[dn] = dict(d)

    # 2) Updates drüberbügeln
    for u in (updated_subset or []):
        dn = int(u["dev_no"])
        merged = dict(by_dev.get(dn, {}))

        merged["dev_no"] = dn
        merged["cmd_id"] = int(u["cmd_id"])
        merged["answer_id"] = int(u["answer_id"])

        # serial nur setzen, wenn geliefert
        if u.get("serial") is not None:
            merged["serial"] = int(u["serial"])

        if "canbaud" in u and u["canbaud"] is not None:
            merged["canbaud"] = int(u["canbaud"])

        # unknown nur wenn erlaubt/geliefert
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
    return f"0x{x:X}"