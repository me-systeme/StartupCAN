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

- DEFAULT_MODE = false:
  All devices can be connected at once using YAML 'current' IDs.
  Each device is activated with current IDs and then updated to YAML 'new' IDs.
"""

import sys
import time
from pathlib import Path
from ruamel.yaml import YAML

from startupcan.config import (
    DEVICE_CONFIG,
    DEVICE_CURRENT,
    DEVICE_NEW,
    CURRENT_DEFAULT_MODE,
    NEW_DEFAULT_MODE,
    SN_MODE,
    DEFAULT_CMD_ID,
    DEFAULT_ANS_ID,
    CONFIG_PATH,
    IGNORE_NEW_SERIALS,
)
from startupcan.gsv86can import (
    GSV86CAN,
    CANSET_CAN_IN_CMD_ID,
    CANSET_CAN_OUT_ANS_ID,
)


# ---------------------------------------------------------------------------
# TODO: Diese Indizes musst du passend zur DLL/Device-Doku setzen!
# ---------------------------------------------------------------------------
# Beispiele (Platzhalter!):
IDX_CAN_CMD_ID     = 0   # <-- hier richtigen Index eintragen
IDX_CAN_ANSWER_ID  = 1   # <-- hier richtigen Index eintragen
IDX_CAN_BAUD       = 4
# ggf. weitere Settings:
# IDX_CAN_BAUD      = 2
# IDX_CAN_FLAGS     = 3

# Track if DLL handle is active per dev_no (best-effort safety vs DLL double-release bug)
HANDLE_ACTIVE: dict[int, bool] = {}


def _set_handle_active(dev_no: int, active: bool):
    HANDLE_ACTIVE[int(dev_no)] = bool(active)

def _is_handle_active(dev_no: int) -> bool:
    return bool(HANDLE_ACTIVE.get(int(dev_no), False))

def _safe_release(gsv: GSV86CAN, dev_no: int, *, where: str = ""):
    """
    Release only if we believe a valid handle exists.
    Prevents DLL crash when releasing twice / releasing invalid handle.
    """
    if not _is_handle_active(dev_no):
        return  # nothing to do

    try:
        gsv.release(dev_no)
        _set_handle_active(dev_no, False)
        time.sleep(0.05)
    except Exception as e:
        # If release fails, don't flip state blindly; keep it as-is
        print(f"[DEV {dev_no}] WARN: release failed{(' @'+where) if where else ''}: {e}")


def fmt_can_id(x: int) -> str:
    # CAN IDs sind oft 11-bit: 0..0x7FF; manche Systeme nutzen 29-bit.
    # Wir formatieren flexibel.
    if x <= 0x7FF:
        return f"0x{x:03X}"
    return f"0x{x:X}"

def _same_ids(cmd_a: int, ans_a: int, cmd_b: int, ans_b: int) -> bool:
    return int(cmd_a) == int(cmd_b) and int(ans_a) == int(ans_b)

def _connect_one(dev_no: int):
    print("\n" + "=" * 80)
    print(f"[WIZARD] DEV {dev_no}")
    print("⚠️  WICHTIG: Es darf GENAU EIN Gerät am CAN-Bus angeschlossen sein.")
    print(f"➡️  Bitte jetzt DEV {dev_no} anschließen.")
    print("=" * 80)
    _pause("Wenn angeschlossen:")

def _disconnect_one(gsv: GSV86CAN, dev_no: int, serial: int | None, reason: str = ""):
    # IMMER device entfernen lassen (dein neues Safety-Modell)
    _finish_device_step(gsv, dev_no, serial, reason=reason)

def _target_ids(dev_no: int, serial: int | None) -> tuple[int, int]:
    """
    Liefert Ziel-IDs aus DEVICE_NEW.
    - SN_MODE=True  => mapping per serial (muss lesbar sein)
    - SN_MODE=False => mapping per dev_no
    """
    if SN_MODE:
        if serial is None:
            raise KeyError(f"SN_MODE aktiv, aber Seriennummer konnte nicht gelesen werden (dev_no={dev_no}).")
        return _new_ids_for_serial(serial)
    return _new_ids_for(dev_no)

def _new_ids_for(dev_no: int) -> tuple[int, int]:
    for d in DEVICE_NEW:
        if int(d["dev_no"]) == int(dev_no):
            return int(d["cmd_id"]), int(d["answer_id"])
    raise KeyError(f"DEV {dev_no}: keine Ziel-IDs in devices.config.new gefunden")

def _pause(msg: str):
    print(msg)
    input("➡️  ENTER drücken, um fortzufahren ... ")

def _ask_continue(prompt: str = "[WIZARD] Nächstes Gerät bearbeiten? [j/N]: ") -> bool:
    more = input(prompt).strip().lower()
    return more in ("j", "ja", "y", "yes")


def _read_serial(gsv: GSV86CAN, dev_no: int) -> int | None:
    try:
        sn = int(gsv.get_serial_no(dev_no))
        return sn
    except Exception as e:
        print(f"[DEV {dev_no}] WARN: Seriennummer konnte nicht gelesen werden: {e}")
        return None


def _fmt_dev(dev_no: int, serial: int | None) -> str:
    if serial is None:
        return f"DEV {dev_no} (SN=?)"
    return f"DEV {dev_no} (SN={serial})"


def _new_ids_for_serial(serial: int) -> tuple[int, int]:
    """
    Sucht in DEVICE_NEW einen Eintrag mit passender Seriennummer.
    """
    for d in DEVICE_NEW:
        if "serial" in d and int(d["serial"]) == int(serial):
            return int(d["cmd_id"]), int(d["answer_id"])
    raise KeyError(f"Keine new.ids Zuordnung für SN={serial} gefunden")


def _verify_ids(gsv: GSV86CAN, dev_no: int, serial: int | None, exp_cmd: int, exp_ans: int):
    try:
        cmd_read = gsv.get_can_settings(dev_no, IDX_CAN_CMD_ID)
        ans_read = gsv.get_can_settings(dev_no, IDX_CAN_ANSWER_ID)

        ok = (cmd_read == exp_cmd and ans_read == exp_ans)
        tag = _fmt_dev(dev_no, serial)
        print(f"[{tag}] verify CMD_ID   = {fmt_can_id(cmd_read)} (raw={cmd_read})")
        print(f"[{tag}] verify ANSWER_ID= {fmt_can_id(ans_read)} (raw={ans_read})")
        if not ok:
            print(f"[{tag}] WARN: verify differs from expected "
                  f"(expected CMD={fmt_can_id(exp_cmd)} ANS={fmt_can_id(exp_ans)})")
        return ok
    except Exception as e:
        print(f"[{_fmt_dev(dev_no, serial)}] WARN: verify failed: {e}")
        return False

def _set_ids_reset_reactivate_verify_release(
    gsv: GSV86CAN,
    dev_no: int,
    serial: int | None,
    cmd_new: int,
    ans_new: int,
) -> tuple[bool, int | None]:
    """
    Setzt neue IDs, macht reset, verbindet nochmal mit den neuen IDs,
    verifiziert und released am Ende wieder.

    Danach ist das Device NICHT mehr aktiv (bewusst).
    """
    try:
        # if dev_no == 1:
        #     # cmd_new_test = 264    # 0x108
        #     # ans_new_test = 265    # 0x109
        #     cmd_new_test = 514 # 0x202
        #     ans_new_test = 515 # 0x203
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

        # Änderungen wirksam machen
        gsv.reset_device(dev_no)
        time.sleep(2)

        # Session lösen
        _safe_release(gsv, dev_no, where="after reset")
        time.sleep(0.2)

        print(f"[{_fmt_dev(dev_no, serial)}] Re-activation after setting can ids: CMD={fmt_can_id(cmd_new)} ANS={fmt_can_id(ans_new)}")
        ok, sn2 = _try_activate(gsv, dev_no, cmd_new, ans_new, tries=8, delay=0.5, read_sn=True, verbose=False)
        if not ok:
            print(f"[{_fmt_dev(dev_no, serial)}] Re-activation with CMD={fmt_can_id(cmd_new)} ANS={fmt_can_id(ans_new)} failed.")
            return False, serial

        sn_out = sn2 if sn2 is not None else serial

        ok_verify = _verify_ids(gsv, dev_no, sn_out, cmd_new, ans_new)

        if not ok_verify:
            print(f"[{_fmt_dev(dev_no, sn_out)}] WARN: Verify nach Re-Activate stimmt nicht "
                  f"(expected CMD={fmt_can_id(cmd_new)} ANS={fmt_can_id(ans_new)}).")

        time.sleep(0.1)

        return ok_verify, sn_out
    
    except Exception as e:
        print(f"[{_fmt_dev(dev_no, serial)}] set/reset/reactivate/verify/release FAIL: {e}")
        _safe_release(gsv, dev_no, where="set_ids:except")
        return False, serial
    
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

def _hex_str(x: int) -> str:
    return f"0x{x:X}"

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
        elif state == "old":
            cmd_eff, ans_eff = r["cmd_old"], r["ans_old"]
        else:
            # unknown: du kannst entweder old drin lassen (aber markieren)
            # oder bewusst new drin lassen, weil das Ziel war.
            # Ich würde: old drin lassen + unknown Flag separat (siehe next step)
            cmd_eff, ans_eff = r["cmd_old"], r["ans_old"]
        
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



def _finish_device_step(gsv: GSV86CAN, dev_no: int, serial: int | None, reason: str = ""):
    """
    Best-effort: Session freigeben und den User auffordern, GENAU dieses Gerät abzunehmen.
    """
    tag = _fmt_dev(dev_no, serial)
    if reason:
        print(f"[{tag}] {reason}")

    _safe_release(gsv, dev_no, where="finish_device_step")

    _pause(f"➡️  Bitte dieses Gerät {tag} JETZT vom Bus abnehmen/abschrauben, dann ENTER ...")

def _try_activate_n(gsv, dev_no, cmd, ans, tries=5, delay=0.3) -> bool:
    ok, _ = _try_activate(gsv, dev_no, cmd, ans, tries=tries, delay=delay, read_sn=False, verbose=False)
    return ok

def _try_activate(
    gsv: GSV86CAN,
    dev_no: int,
    cmd: int,
    ans: int,
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
    

    for i in range(tries):
        if verbose:
            print(f"[DEV {dev_no}] activate try {i+1}/{tries}: CMD={fmt_can_id(cmd)} ANS={fmt_can_id(ans)}")
        try:
            gsv.activate(dev_no, cmd, ans)
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


def _probe_state_after_fail(
    gsv: GSV86CAN,
    dev_no: int,
    cmd_old: int, ans_old: int,
    cmd_new: int, ans_new: int,
) -> str:
    """
    Best-effort: herausfinden, welche IDs gerade wirklich aktiv sind.
    Returns: "old" | "new" | "unknown"
    """

    _safe_release(gsv, dev_no, where="probe:pre")
    time.sleep(0.3)  

    # 1) old testen
    # cmd_old = 258   # 0x102
    # ans_old = 259   # 0x103
    print(f"[DEV {dev_no}] Starting probe state=old: CMD={fmt_can_id(cmd_old)} ANS={fmt_can_id(ans_old)}")
    ok_old = _try_activate_n(gsv, dev_no, cmd_old, ans_old)
    if ok_old:
        print(f"[DEV {dev_no}] Probe state=old was successfull. Die current IDs im YAML bleiben auf den alten IDs.")
        _safe_release(gsv, dev_no, where="probe:old-ok")
        return "old"

    print(f"[DEV {dev_no}] Starting probe state=new: CMD={fmt_can_id(cmd_new)} ANS={fmt_can_id(ans_new)}")
    # 2) new testen
    # cmd_new = 256    # 0x100
    # ans_new = 257    # 0x101
    ok_new = _try_activate_n(gsv, dev_no, cmd_new, ans_new)
    if ok_new:
        print(f"[DEV {dev_no}] Probe state=new was successfull. Die current IDs im YAML werden mit den neuen IDs überschrieben.")
        _safe_release(gsv, dev_no, where="probe:new-ok")
        return "new"

    print(f"[DEV {dev_no}] Probes state=old and state=new failed. CAN IDs are unknown. Die current IDs im YAML bleiben auf den alten IDs und es wird unknown=true gesetzt.")
    return "unknown"

def _write_updated_yaml(
    src_path: Path,
    dst_path: Path,
    current_default: bool,
    current_ids: list[dict],
    make_new_safe: bool = True,
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

        wizard_mode = bool(CURRENT_DEFAULT_MODE)
        forced_reset_wizard = (not CURRENT_DEFAULT_MODE) and bool(NEW_DEFAULT_MODE)

        if forced_reset_wizard:
            print("\nWICHTIG (Reset auf DEFAULT):")
            print("- Es darf immer nur ein Gerät am Bus sein (damit es nicht zur Kollision kommt).")
            print("- Nach jedem Schritt Gerät abnehmen (immer nur eins am Bus).")
            print("  bevor du das nächste Gerät auf DEFAULT setzt.")
            print(f"- Default IDs: CMD={fmt_can_id(DEFAULT_CMD_ID)} ANS={fmt_can_id(DEFAULT_ANS_ID)}\n")
        if NEW_DEFAULT_MODE:
            print("\nHINWEIS:")
            print("- devices.config.new.default=true ⇒ Ziel ist Rücksetzen auf Default IDs.")
            print(f"- Ziel-Default IDs: CMD={fmt_can_id(DEFAULT_CMD_ID)} ANS={fmt_can_id(DEFAULT_ANS_ID)}")
            print("⚠️  Danach dürfen die Geräte NICHT gleichzeitig am Bus sein,")
            print("   weil mehrere Geräte dieselbe CAN-ID hätten (Kollision / Bus-Off möglich).")
            print("   => Geräte nur EINZELN anschließen und aktivieren.\n")

        # -------------------------------------------------------------------
        # Case 2 CURRENT_DEFAULT_MODE = true -> Wizard (one device at a time) 
        # -------------------------------------------------------------------
        if wizard_mode:
            print("\nWICHTIG:")
            print(f"- Default IDs: CMD={fmt_can_id(DEFAULT_CMD_ID)} ANS={fmt_can_id(DEFAULT_ANS_ID)}")
            print("- Schließe IMMER nur EINEN Messverstärker gleichzeitig an (sonst CAN-Kollisionen).")
            print("- Ziel-IDs werden aus devices.config.new.ids übernommen.")

            for d in DEVICE_CONFIG:
                dev_no = int(d["dev_no"])

                _connect_one(dev_no)

                sn = None
                skip_programming = False
                disconnect_reason = "Weiter mit nächstem Gerät."

                try: 
                    # if dev_no == 1:
                    #     DEFAULT_CMD_ID_TEST = 258    # 0x102
                    #     DEFAULT_ANS_ID_TEST = 259    # 0x103
                    #     ok, sn = _try_activate(gsv, dev_no, DEFAULT_CMD_ID_TEST, DEFAULT_ANS_ID_TEST, tries=5, delay=0.3, read_sn=True)
                    # else: 
                    #     ok, sn = _try_activate(gsv, dev_no, DEFAULT_CMD_ID, DEFAULT_ANS_ID, tries=5, delay=0.3, read_sn=True)
                    # Aktivieren immer mit Default IDs
                    ok, sn = _try_activate(gsv, dev_no, DEFAULT_CMD_ID, DEFAULT_ANS_ID, tries=5, delay=0.3, read_sn=True)

                    if not ok:
                        if SN_MODE:
                            # target IDs sind ohne SN nicht bestimmbar → keine sinnvolle "already new" Probe
                            state = "unknown"
                            cmd_new = DEFAULT_CMD_ID
                            ans_new = DEFAULT_ANS_ID
                        else:
                            cmd_new, ans_new = _new_ids_for(dev_no)   # target schon jetzt bekannt
                            state = _probe_state_after_fail(gsv, dev_no, DEFAULT_CMD_ID, DEFAULT_ANS_ID, cmd_new, ans_new)

                        results.append({
                            "dev_no": dev_no,
                            "serial": sn,
                            "ok": False,
                            "state": state,
                            "cmd_old": DEFAULT_CMD_ID,
                            "ans_old": DEFAULT_ANS_ID,
                            "cmd_new": cmd_new,
                            "ans_new": ans_new,
                        })
                        skip_programming = True
                        disconnect_reason = f"Aktivierung (DEFAULT) fehlgeschlagen. State probe={state}. Gerät abnehmen."
                        
                    if not skip_programming:
                        if SN_MODE:
                            try:
                                cmd_new, ans_new = _target_ids(dev_no, sn)
                                print(f"[{_fmt_dev(dev_no, sn)}] Ziel-IDs per SN-Mapping.")
                            except KeyError as e:
                                print(f"[{_fmt_dev(dev_no, sn)}] FEHLER: {e}")

                                results.append({
                                    "dev_no": dev_no,
                                    "serial": sn,
                                    "ok": False,
                                    "cmd_old": DEFAULT_CMD_ID,
                                    "ans_old": DEFAULT_ANS_ID,
                                    "cmd_new": DEFAULT_CMD_ID,
                                    "ans_new": DEFAULT_ANS_ID,
                                })
                                skip_programming = True
                                disconnect_reason = "FEHLER: Ziel-IDs konnten nicht bestimmt werden. Dieses Gerät wird übersprungen."
                        else: 
                            # dev_no mapping: sollte durch config garantiert sein → kein try/except nötig
                            cmd_new, ans_new = _new_ids_for(dev_no)
                            print(f"[{_fmt_dev(dev_no, sn)}] Ziel-IDs per dev_no-Mapping.")
                
        
                            
                    if not skip_programming:
                        # --- SKIP: Ziel ist Default und wir sind bereits auf Default aktiv ---
                        if _same_ids(DEFAULT_CMD_ID, DEFAULT_ANS_ID, cmd_new, ans_new):
                            print(f"[{_fmt_dev(dev_no, sn)}] Ziel-IDs sind DEFAULT. Skip umstellen.")

                            results.append({
                                "dev_no": dev_no,
                                "serial": sn,
                                "ok": True,          # skip ist erfolgreich
                                "state": "old",      
                                "cmd_old": DEFAULT_CMD_ID,
                                "ans_old": DEFAULT_ANS_ID,
                                "cmd_new": cmd_new,
                                "ans_new": ans_new,
                            })
                            skip_programming = True
                            disconnect_reason = "OK (skip). Bitte Gerät abnehmen."
                            
                    if not skip_programming:
                        # optional: verify start
                        ok = _verify_ids(gsv, dev_no, sn, DEFAULT_CMD_ID, DEFAULT_ANS_ID)

                        if not ok:
                            print(f"[{_fmt_dev(dev_no, sn)}] WARN: Start-IDs stimmen nicht (trotz activation).")

                        ok2, sn2 = _set_ids_reset_reactivate_verify_release(gsv, dev_no, sn, cmd_new, ans_new)

                        if sn2 is not None:
                            sn = sn2


                        state = "new" if ok2 else _probe_state_after_fail(
                            gsv, dev_no,
                            DEFAULT_CMD_ID, DEFAULT_ANS_ID,
                            cmd_new, ans_new
                        )

                        results.append({
                            "dev_no": dev_no,
                            "serial": sn2 if sn2 is not None else sn,
                            "ok": bool(ok2),
                            "state": state,
                            "cmd_old": DEFAULT_CMD_ID,
                            "ans_old": DEFAULT_ANS_ID,
                            "cmd_new": cmd_new,
                            "ans_new": ans_new,
                        })

                        if ok2:
                            print(f"[WIZARD] ✅ {_fmt_dev(dev_no, sn2)} umgestellt.")

                            disconnect_reason = "OK. Bitte Gerät abnehmen (Safety: immer nur eins am Bus)."
                            

                        else:
                            # bei Fail willst du ziemlich sicher: Gerät abnehmen (weil Zustand unklar / evtl Default)
                            disconnect_reason = "FEHLER: Umstellung fehlgeschlagen."
                        
                finally:
                    
                    _disconnect_one(gsv, dev_no, sn, reason=disconnect_reason)

                if not _ask_continue("[WIZARD] Nächstes Gerät umstellen? [j/N]: "):
                    break

            _print_summary(results)
            updated_subset = _effective_current_ids_from_results(results)

            # original_current kommt aus YAML current.ids
            original_current = DEVICE_CURRENT or []

            current_ids = _merge_current_ids(original_current, updated_subset)

            # current_default bleibt wie gehabt (deine Logik)
            current_default = _all_fail(results, len(DEVICE_CONFIG))

            all_ok = _all_ok(results, len(DEVICE_CONFIG))
            dst = Path(CONFIG_PATH).with_name("config.updated.yaml")
            _write_updated_yaml(
                src_path=Path(CONFIG_PATH),
                dst_path=dst,
                current_default=current_default,
                current_ids=current_ids,
                make_new_safe=all_ok,
            )
            print(f"[INFO] ✅ Updated YAML geschrieben: {dst}")

            if _all_ok(results, len(DEVICE_CONFIG)):
                print("[INFO] Alle Geräte umgestellt ⇒ dürfen jetzt gleichzeitig an den Bus (IDs eindeutig).")
            else:
                print("[INFO] Nach dem Run dürfen Geräte nur dann gleichzeitig an den Bus, wenn config.updated.yaml keine doppelten IDs enthält und keine unknown:true Einträge hat.")
                print("[WARN] Nicht alle Geräte umgestellt ⇒ erst config.updated.yaml prüfen, bevor alle gleichzeitig an den Bus.")

            if current_default:
                print("[INFO] Kein Gerät wurde umgestellt: current.default bleibt TRUE (alle weiterhin DEFAULT).")
            
            return 0

        # Case 3
        elif forced_reset_wizard:
            print("\n[INFO] Forced-Reset Wizard: current.default=false & new.default=true")
            print("[INFO] Wir stellen jetzt JEWEILS EIN Gerät auf DEFAULT und nehmen es danach ab.\n")

            for d in DEVICE_CONFIG:
                dev_no = int(d["dev_no"])
                cmd_start = int(d["cmd_id"])
                ans_start = int(d["answer_id"])

                cmd_new = int(DEFAULT_CMD_ID)
                ans_new = int(DEFAULT_ANS_ID)

                _connect_one(dev_no)

                sn = None
                skip_programming = False

                disconnect_reason = "Weiter mit nächstem Gerät."

                try:
                    expected_sn = d.get("serial") if isinstance(d, dict) else None

                    # 1) activate mit current IDs
                    ok, sn = _try_activate(gsv, dev_no, cmd_start, ans_start, tries=5, delay=0.3, read_sn=True)
                    if not ok:
                        state = _probe_state_after_fail(gsv, dev_no, cmd_start, ans_start, DEFAULT_CMD_ID, DEFAULT_ANS_ID)

                        results.append({
                            "dev_no": dev_no,
                            "serial": sn,
                            "ok": False,
                            "state": state,
                            "cmd_old": cmd_start,
                            "ans_old": ans_start,
                            "cmd_new": DEFAULT_CMD_ID,
                            "ans_new": DEFAULT_ANS_ID,
                        })
                        skip_programming = True
                        disconnect_reason = f"Aktivierung (current IDs) fehlgeschlagen. State probe={state}. Gerät abnehmen."
                        
                    
                    # Wenn current.ids serial angibt: muss matchen (und muss lesbar sein)
                    if expected_sn is not None:
                        if not skip_programming:
                            if sn is None:
                                print(
                                    f"[DEV {dev_no}] FEHLER: YAML erwartet SN={expected_sn}, "
                                    "aber Seriennummer konnte nicht gelesen werden. => Device wird NICHT umgestellt."
                                )
                                results.append({
                                    "dev_no": dev_no,
                                    "serial": sn,
                                    "ok": False,
                                    "cmd_old": cmd_start,
                                    "ans_old": ans_start,
                                    "cmd_new": cmd_new,
                                    "ans_new": ans_new,
                                })
                                skip_programming = True
                                disconnect_reason ="Die Seriennummer konnte nicht gelesen werden."
                                print("-" * 80)
                                
                        if not skip_programming:
                            if int(expected_sn) != int(sn):
                                print(
                                    f"[{_fmt_dev(dev_no, sn)}] FEHLER: Die gelesene Seriennummer {sn} passt nicht zu YAML current.ids "
                                    f"(yaml SN={expected_sn}). => Device wird NICHT umgestellt."
                                )
                                results.append({
                                    "dev_no": dev_no,
                                    "serial": sn,
                                    "ok": False,
                                    "cmd_old": cmd_start,
                                    "ans_old": ans_start,
                                    "cmd_new": cmd_new,
                                    "ans_new": ans_new,
                                })
                                skip_programming = True
                                disconnect_reason ="Die gelesene Seriennummer stimmt nicht mit der konfigurierten Seriennummer aus dem YAML überein. Die Seriennummer aus dem YAML wird im neuen YAML mit der gelesenen Seriennummer überschrieben."
                                print("-" * 80)
                                
                    if not skip_programming:
                        # --- SKIP: Gerät ist laut current.ids bereits DEFAULT ---
                        if _same_ids(cmd_start, ans_start, DEFAULT_CMD_ID, DEFAULT_ANS_ID):
                            print(f"[DEV {dev_no}] current.ids ist bereits DEFAULT und activation OK. Skip reset.")

                            results.append({
                                "dev_no": dev_no,
                                "serial": sn,
                                "ok": True,          # skip ist erfolgreich
                                "state": "new",      # "old" oder "new" je nachdem was es effektiv ist
                                "cmd_old": cmd_start,
                                "ans_old": ans_start,
                                "cmd_new": DEFAULT_CMD_ID,
                                "ans_new": DEFAULT_ANS_ID,
                            })
                            skip_programming = True
                            disconnect_reason = "OK (skip). Gerät ist bereits DEFAULT. Bitte Gerät abnehmen."
                            
                    if not skip_programming:
                        # optional: verify start
                        ok = _verify_ids(gsv, dev_no, sn, cmd_start, ans_start)

                        if not ok:
                            print(f"[{_fmt_dev(dev_no, sn)}] WARN: Start-IDs stimmen nicht (trotz activation).")

                        # 2-5) set default, reset, release, reactivate default, verify, release
                        ok2, sn2 = _set_ids_reset_reactivate_verify_release(gsv, dev_no, sn, cmd_new, ans_new)

                        if sn2 is not None:
                            sn = sn2

                        state = "new" if ok2 else _probe_state_after_fail(
                            gsv, dev_no,
                            cmd_start, ans_start,
                            cmd_new, ans_new
                        )

                        results.append({
                            "dev_no": dev_no, "serial": sn2 if sn2 is not None else sn,
                            "ok": bool(ok2), "state": state,
                            "cmd_old": cmd_start, "ans_old": ans_start,
                            "cmd_new": cmd_new, "ans_new": ans_new,
                        })

                        if ok2:
                            disconnect_reason = "OK: Gerät ist jetzt DEFAULT. Bitte abnehmen."
                        else:
                            disconnect_reason = f"FEHLER: Reset auf DEFAULT fehlgeschlagen (state={state}). Bitte abnehmen."

                    
                    
                finally:
                    _disconnect_one(gsv, dev_no, sn, reason=disconnect_reason)

                if not _ask_continue("[WIZARD] Nächstes Gerät auf DEFAULT setzen? [j/N]: "):
                    break

            _print_summary(results)
            updated_subset = _effective_current_ids_from_results(results)

            # In Case 3 ist DEVICE_CONFIG = YAML current.ids (bei dir so)
            original_current = DEVICE_CONFIG or []

            current_ids = _merge_current_ids(original_current, updated_subset)
            current_default = _all_ok(results, len(DEVICE_CONFIG))  # nur wenn ALLE wirklich default sind

            all_ok = _all_ok(results, len(DEVICE_CONFIG))
            dst = Path(CONFIG_PATH).with_name("config.updated.yaml")
            _write_updated_yaml(
                src_path=Path(CONFIG_PATH),
                dst_path=dst,
                current_default=current_default,
                current_ids=current_ids,
                make_new_safe=all_ok,
            )
            print(f"[INFO] ✅ Updated YAML geschrieben: {dst}")

            if not current_default:
                print("[WARN] Nicht alle Geräte wurden auf DEFAULT gesetzt. current.default bleibt FALSE.")

            if current_default:
                print("⚠️  HINWEIS: Geräte sind jetzt auf DEFAULT IDs.")
                print("   => NICHT gleichzeitig am Bus betreiben/aktivieren.")
            else:
                print("⚠️  HINWEIS: NICHT alle Geräte sind DEFAULT. (Gemischter Zustand möglich.)")
                print("   => Bus-Betrieb nur mit den IDs aus config.updated.yaml!")
            return 0

        # -------------------------------------------------------------------
        # Case 1: CURRENT_DEFAULT_MODE = false -> All devices can be connected
        # -------------------------------------------------------------------
        else:
            print("[INFO] new.default=false: Ziel-IDs aus devices.config.new.ids.\n")
            if IGNORE_NEW_SERIALS:
                print("[HINWEIS] devices.config.new.ids enthält 'serial' Einträge, "
                    "aber in Case 1 wird IMMER per dev_no gemappt. "
                    "Die Seriennummern in new.ids werden ignoriert.")

            for d in DEVICE_CONFIG:
                dev_no = int(d["dev_no"])
                cmd_id = int(d["cmd_id"])
                ans_id = int(d["answer_id"])
                
                _connect_one(dev_no)

                sn = None

                skip_programming = False

                disconnect_reason = "Weiter mit nächstem Gerät."

                try: 

                    expected_sn = d.get("serial") if isinstance(d, dict) else None

                    ok, sn = _try_activate(gsv, dev_no, cmd_id, ans_id, tries=5, delay=0.3, read_sn=True)
                    if not ok:
                        if SN_MODE:
                            # Target unbekannt ohne SN -> keine probe auf new
                            state = "unknown"
                            cmd_new, ans_new = cmd_id, ans_id  # oder 0/0
                        else:
                            cmd_new, ans_new = _new_ids_for(dev_no)
                            state = _probe_state_after_fail(gsv, dev_no, cmd_id, ans_id, cmd_new, ans_new)

                        results.append({
                            "dev_no": dev_no,
                            "serial": sn,
                            "ok": False,
                            "state": state,
                            "cmd_old": cmd_id,
                            "ans_old": ans_id,
                            "cmd_new": cmd_new,
                            "ans_new": ans_new,
                        })
                        skip_programming = True
                        disconnect_reason = f"Activation failed. State probe={state}. Gerät abnehmen."
                        
                    
                    if expected_sn is not None:
                        if not skip_programming:
                            if sn is None:
                                print(
                                    f"[DEV {dev_no}] FEHLER: YAML erwartet SN={expected_sn}, "
                                    "aber Seriennummer konnte nicht gelesen werden. => Device wird NICHT umgestellt."
                                )
                                results.append({
                                    "dev_no": dev_no,
                                    "serial": sn,
                                    "ok": False,
                                    "cmd_old": cmd_id,
                                    "ans_old": ans_id,
                                    "cmd_new": cmd_new,
                                    "ans_new": ans_new,
                                })

                                skip_programming = True
                                disconnect_reason = "Die Seriennummer konnte nicht gelesen werden."
                                print("-" * 80)

                        if not skip_programming:
                            if int(expected_sn) != int(sn):
                                print(
                                    f"[{_fmt_dev(dev_no, sn)}] FEHLER: Die gelesene Seriennummer {sn} passt nicht zu YAML current.ids"
                                    f"(yaml SN={expected_sn}). => Device wird NICHT umgestellt."
                                )
                                results.append({
                                    "dev_no": dev_no,
                                    "serial": sn,
                                    "ok": False,
                                    "cmd_old": cmd_id,
                                    "ans_old": ans_id,
                                    "cmd_new": cmd_new,
                                    "ans_new": ans_new,
                                })
                                skip_programming = True
                                disconnect_reason = "Die gelesene Seriennummer stimmt nicht mit der konfigurierten Seriennummer aus dem YAML überein. Die Seriennummer aus dem YAML wird im neuen YAML mit der gelesenen Seriennummer überschrieben."
                                print("-" * 80)

                    if not skip_programming:
                        if SN_MODE:
                            try:
                                cmd_new, ans_new = _target_ids(dev_no, sn)
                                print(f"[{_fmt_dev(dev_no, sn)}] Ziel-IDs per SN-Mapping.")
                            except KeyError as e:
                                print(f"[{_fmt_dev(dev_no, sn)}] FEHLER: {e}")

                                results.append({
                                    "dev_no": dev_no,
                                    "serial": sn,
                                    "ok": False,
                                    "cmd_old": DEFAULT_CMD_ID,
                                    "ans_old": DEFAULT_ANS_ID,
                                    "cmd_new": DEFAULT_CMD_ID,
                                    "ans_new": DEFAULT_ANS_ID,
                                })
                                skip_programming = True
                                disconnect_reason = "FEHLER: Ziel-IDs konnten nicht bestimmt werden. Dieses Gerät wird übersprungen."
                        else: 
                            # dev_no mapping: sollte durch config garantiert sein → kein try/except nötig
                            cmd_new, ans_new = _new_ids_for(dev_no)
                            print(f"[{_fmt_dev(dev_no, sn)}] Ziel-IDs per dev_no-Mapping.")
                                
                    if not skip_programming:
                        # Optional: prüfen
                        ok = _verify_ids(gsv, dev_no, sn, cmd_id, ans_id)

                        if not ok:
                            print(f"[{_fmt_dev(dev_no, sn)}] WARN: Start-IDs stimmen nicht (trotz activation).")

                        if _same_ids(cmd_id, ans_id, cmd_new, ans_new):
                            print(f"[{_fmt_dev(dev_no, sn)}] Ziel-IDs == aktuelle YAML-IDs. Skip reprogram/reset.")
                            results.append({
                                "dev_no": dev_no,
                                "serial": sn,
                                "ok": True,
                                "state": "new",     # effektiv "already new"
                                "cmd_old": cmd_id,
                                "ans_old": ans_id,
                                "cmd_new": cmd_new,
                                "ans_new": ans_new,
                            })
                            skip_programming = True
                            disconnect_reason = "OK (skip). Bitte Gerät abnehmen."
                            print("-" * 80)
                            
                    if not skip_programming:
                        ok2, sn2 = _set_ids_reset_reactivate_verify_release(gsv, dev_no, sn, cmd_new, ans_new)
                        
                        if sn2 is not None:
                            sn = sn2

                        state = "new" if ok2 else _probe_state_after_fail(gsv, dev_no, cmd_id, ans_id, cmd_new, ans_new)

                        results.append({
                            "dev_no": dev_no,
                            "serial": sn2 if sn2 is not None else sn,
                            "ok": bool(ok2),
                            "state": state,
                            "cmd_old": cmd_id,
                            "ans_old": ans_id,
                            "cmd_new": cmd_new,
                            "ans_new": ans_new,
                        })
                        print("-" * 80)

                        disconnect_reason = "Weiter mit nächstem Gerät."
                finally:
                    _disconnect_one(gsv, dev_no, sn, reason=disconnect_reason)
                
                if not _ask_continue("[WIZARD] Nächstes Gerät bearbeiten? [j/N]: "):
                    break


            _print_summary(results)

            updated_subset = _effective_current_ids_from_results(results)

            # In Case 1 ist DEVICE_CONFIG = YAML current.ids (bei dir so)
            original_current = DEVICE_CONFIG or []

            current_ids = _merge_current_ids(original_current, updated_subset)
            current_default = False

            all_ok = _all_ok(results, len(DEVICE_CONFIG))
            dst = Path(CONFIG_PATH).with_name("config.updated.yaml")
            _write_updated_yaml(
                src_path=Path(CONFIG_PATH),
                dst_path=dst,
                current_default=current_default,
                current_ids=current_ids,
                make_new_safe=all_ok,
            )
            print(f"[INFO] ✅ Updated YAML geschrieben: {dst}")
            
            if _all_ok(results, len(DEVICE_CONFIG)):
                print("\n[INFO] new.default=false: Geräte dürfen gleichzeitig am Bus sein (IDs eindeutig).")
            else:
                print("[WARN] Nicht alle Devices erfolgreich. YAML enthält Ist-Stand (teils alte IDs). Prüfe zunächst die YAML bevor alle Geräte gleichzeitig am Bus angeschlossen werden. (Keine doppelten CAN IDs oder unknown: true!)")

            
            return 0 

    finally:
        try:
            gsv.release(0)
        except Exception:
            pass
        finally:
            HANDLE_ACTIVE.clear()  


if __name__ == "__main__":
    sys.exit(main())