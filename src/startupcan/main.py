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
    DEVICE_NEW,
    CURRENT_DEFAULT_MODE,
    NEW_DEFAULT_MODE,
    DEFAULT_CMD_ID,
    DEFAULT_ANS_ID,
    CONFIG_PATH
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


def fmt_can_id(x: int) -> str:
    # CAN IDs sind oft 11-bit: 0..0x7FF; manche Systeme nutzen 29-bit.
    # Wir formatieren flexibel.
    if x <= 0x7FF:
        return f"0x{x:03X}"
    return f"0x{x:X}"

def _new_ids_for(dev_no: int) -> tuple[int, int]:
    for d in DEVICE_NEW:
        if int(d["dev_no"]) == int(dev_no):
            return int(d["cmd_id"]), int(d["answer_id"])
    raise KeyError(f"DEV {dev_no}: keine Ziel-IDs in devices.config.new gefunden")

def _pause(msg: str):
    print(msg)
    input("➡️  ENTER drücken, um fortzufahren ... ")

def _verify_ids(gsv: GSV86CAN, dev_no: int, exp_cmd: int, exp_ans: int):
    try:
        cmd_read = gsv.get_can_settings(dev_no, IDX_CAN_CMD_ID)
        ans_read = gsv.get_can_settings(dev_no, IDX_CAN_ANSWER_ID)

        ok = (cmd_read == exp_cmd and ans_read == exp_ans)
        print(f"[DEV {dev_no}] verify CMD_ID   = {fmt_can_id(cmd_read)} (raw={cmd_read})")
        print(f"[DEV {dev_no}] verify ANSWER_ID= {fmt_can_id(ans_read)} (raw={ans_read})")
        if not ok:
            print(f"[DEV {dev_no}] WARN: verify differs from expected "
                  f"(expected CMD={fmt_can_id(exp_cmd)} ANS={fmt_can_id(exp_ans)})")
        return ok
    except Exception as e:
        print(f"[DEV {dev_no}] WARN: verify failed: {e}")
        return False


def _activate(gsv: GSV86CAN, dev_no: int, cmd: int, ans: int) -> bool:
    print(f"[DEV {dev_no}] activating with CMD={fmt_can_id(cmd)} ANS={fmt_can_id(ans)} ...")
    try:
        nchan = gsv.activate(dev_no, cmd, ans)
        print(f"[DEV {dev_no}] activate OK, channels={nchan}")
        return True
    except Exception as e:
        print(f"[DEV {dev_no}] activate FAIL: {e}")
        return False
    
def _set_ids_and_optionally_reconnect(
    gsv: GSV86CAN,
    dev_no: int,
    cmd_new: int,
    ans_new: int,
    reactivate: bool,
) -> bool:
    try:
        print(f"[DEV {dev_no}] set NEW IDs: CMD={fmt_can_id(cmd_new)} ANS={fmt_can_id(ans_new)}")
        gsv.set_can_settings(dev_no, CANSET_CAN_IN_CMD_ID, cmd_new)
        gsv.set_can_settings(dev_no, CANSET_CAN_OUT_ANS_ID, ans_new)

        # Änderungen wirksam machen
        gsv.reset_device(dev_no)
        time.sleep(2)

        # Session lösen und mit neuen IDs wieder verbinden
        gsv.release(dev_no)
        time.sleep(0.2)

        if not reactivate:
            print(f"[DEV {dev_no}] INFO: Re-Activate übersprungen "
                  f"(devices.config.new.default=true ⇒ Ziel-IDs sind Default, Kollision möglich).")
            return True

        if not _activate(gsv, dev_no, cmd_new, ans_new):
            return False

        _verify_ids(gsv, dev_no, cmd_new, ans_new)
        return True
    except Exception as e:
        print(f"[DEV {dev_no}] set/reset/reconnect FAIL: {e}")
        return False
    
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
        print(
            f"DEV {dev_no}: "
            f"{'OK ' if ok else 'FAIL'} | "
            f"{fmt_can_id(cmd_old)}/{fmt_can_id(ans_old)}  ->  "
            f"{fmt_can_id(cmd_new)}/{fmt_can_id(ans_new)}"
        )
    print("=" * 80 + "\n")

def _hex_str(x: int) -> str:
    return f"0x{x:X}"

def _all_ok(results: list[dict], expected: int) -> bool:
    return (len(results) == expected) and all(bool(r.get("ok")) for r in results)

def _current_ids_from_results(results: list[dict]) -> list[dict]:
    out = []
    for r in results:
        out.append({
            "dev_no": int(r["dev_no"]),
            "cmd_id": int(r["cmd_new"]),
            "answer_id": int(r["ans_new"]),
        })
    out.sort(key=lambda d: d["dev_no"])
    return out

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
            "cmd_id": _hex_str(int(d["cmd_id"])),
            "answer_id": _hex_str(int(d["answer_id"])),
        }
        for d in current_ids
    ]

    # Optional: new "safe" machen – aber ohne verbotene Kombination zu erzeugen!
    # Du willst meist verhindern, dass ein Run direkt nochmal "umstellt".
    if make_new_safe:
        # Wenn current.default=true wäre, darf new.default NICHT true werden (laut deiner Regel).
        # Daher: in wizard-artigen Fällen new.default auf false und ids leeren.
        if bool(current["default"]) is True:
            new["default"] = False
            new["ids"] = []
        else:
            # current.default=false: new.default=true ist erlaubt (Reset-Ziel),
            # aber ids=[] passt dazu.
            new["default"] = True
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
        reactivate_after_change = not bool(NEW_DEFAULT_MODE)

        if NEW_DEFAULT_MODE:
            print("\nHINWEIS:")
            print("- devices.config.new.default=true ⇒ Ziel ist Rücksetzen auf Default IDs.")
            print(f"- Ziel-Default IDs: CMD={fmt_can_id(DEFAULT_CMD_ID)} ANS={fmt_can_id(DEFAULT_ANS_ID)}")
            print("⚠️  Danach dürfen die Geräte NICHT gleichzeitig am Bus sein,")
            print("   weil mehrere Geräte dieselbe CAN-ID hätten (Kollision / Bus-Off möglich).")
            print("   => Geräte nur EINZELN anschließen und aktivieren.\n")

        # -------------------------------------------------------------------
        # CURRENT_DEFAULT_MODE = true -> Wizard (one device at a time)
        # -------------------------------------------------------------------
        if wizard_mode:
            print("\nWICHTIG:")
            print(f"- Default IDs: CMD={fmt_can_id(DEFAULT_CMD_ID)} ANS={fmt_can_id(DEFAULT_ANS_ID)}")
            print("- Schließe IMMER nur EINEN Messverstärker gleichzeitig an (sonst CAN-Kollisionen).")
            print("- Ziel-IDs werden aus devices.config.new.ids übernommen.")

            for d in DEVICE_CONFIG:
                dev_no = int(d["dev_no"])
                cmd_new, ans_new = _new_ids_for(dev_no)

                print("\n" + "=" * 80)
                print(f"[WIZARD] Schritt für DEV {dev_no}")
                print(f"Bitte GENAU EINEN Messverstärker anschließen (noch auf Default IDs).")
                print("=" * 80)

                _pause("Wenn angeschlossen:")

                # Aktivieren immer mit Default IDs
                ok = _activate(gsv, dev_no, DEFAULT_CMD_ID, DEFAULT_ANS_ID)

                if not ok:
                    results.append({
                        "dev_no": dev_no,
                        "ok": False,
                        "cmd_old": DEFAULT_CMD_ID,
                        "ans_old": DEFAULT_ANS_ID,
                        "cmd_new": cmd_new,
                        "ans_new": ans_new,
                    })
                    retry = input("[WIZARD] Nochmal versuchen? [j/N]: ").strip().lower()
                    if retry in ("j", "ja", "y", "yes"):
                        continue
                    else:
                        _print_summary(results)
                        return 2
                
                # Optional: Seriennummer lesen (falls verfügbar)
                try:
                    sn = gsv.get_serial_no(dev_no)
                    print(f"[DEV {dev_no}] Seriennummer: {sn}")
                except Exception:
                    pass
                
                ok2 = _set_ids_and_optionally_reconnect(gsv, dev_no, cmd_new, ans_new, reactivate=reactivate_after_change)

                results.append({
                    "dev_no": dev_no,
                    "ok": bool(ok2),
                    "cmd_old": DEFAULT_CMD_ID,
                    "ans_old": DEFAULT_ANS_ID,
                    "cmd_new": cmd_new,
                    "ans_new": ans_new,
                })

                if ok2:
                    print(f"[WIZARD] ✅ DEV {dev_no} umgestellt. Du kannst dieses Gerät jetzt angeschlossen lassen.")
                else:
                    print(f"[WIZARD] ❌ DEV {dev_no} Umstellung fehlgeschlagen.")

                more = input("[WIZARD] Nächstes Gerät umstellen? [j/N]: ").strip().lower()
                if more not in ("j", "ja", "y", "yes"):
                    break

            _print_summary(results)
            expected = len(DEVICE_CONFIG)
            if _all_ok(results, expected):
                current_ids = _current_ids_from_results(results)
                # Nach dem Run sind die Geräte auf "cmd_new/ans_new"
                # current.default ist TRUE nur wenn Ziel Default war (new.default=true)
                current_default = bool(NEW_DEFAULT_MODE)

                dst = Path(CONFIG_PATH).with_name("config.updated.yaml")
                _write_updated_yaml(
                    src_path=Path(CONFIG_PATH),
                    dst_path=dst,
                    current_default=current_default,
                    current_ids=current_ids,
                    make_new_safe=True,
                )
                print(f"[INFO] ✅ Updated YAML geschrieben: {dst}")
            else:
                print("[INFO] Updated YAML NICHT geschrieben (nicht alle Devices erfolgreich).")

            print("[INFO] Wenn alle Geräte umgestellt sind, dürfen alle gleichzeitig an den Bus.")
            return 0

        # -------------------------------------------------------------------
        # CURRENT_DEFAULT_MODE = false -> All devices can be connected
        # -------------------------------------------------------------------
        else:
            print("\n[INFO] current.default=false: Geräte dürfen gleichzeitig am Bus sein (IDs eindeutig).")
            if NEW_DEFAULT_MODE:
                print("[INFO] Ziel ist Rücksetzen auf Default IDs => danach nicht mehr gleichzeitig betreiben!")
            else:
                print("[INFO] Ziel-IDs aus devices.config.new.ids.\n")

            for d in DEVICE_CONFIG:
                dev_no = int(d["dev_no"])
                cmd_id = int(d["cmd_id"])
                ans_id = int(d["answer_id"])
                cmd_new, ans_new = _new_ids_for(dev_no)

                ok = _activate(gsv, dev_no, cmd_id, ans_id)
                if not ok:
                    results.append({
                        "dev_no": dev_no,
                        "ok": False,
                        "cmd_old": cmd_id,
                        "ans_old": ans_id,
                        "cmd_new": cmd_new,
                        "ans_new": ans_new,
                    })
                    print("-" * 80)
                    continue
                
                # Optional: prüfen
                _verify_ids(gsv, dev_no, cmd_id, ans_id)

                ok2 = _set_ids_and_optionally_reconnect(gsv, dev_no, cmd_new, ans_new, reactivate=reactivate_after_change)

                results.append({
                    "dev_no": dev_no,
                    "ok": bool(ok2),
                    "cmd_old": cmd_id,
                    "ans_old": ans_id,
                    "cmd_new": cmd_new,
                    "ans_new": ans_new,
                })
                print("-" * 80)

            _print_summary(results)
            expected = len(DEVICE_CONFIG)
            if _all_ok(results, expected):
                current_ids = _current_ids_from_results(results)
                current_default = bool(NEW_DEFAULT_MODE)

                dst = Path(CONFIG_PATH).with_name("config.updated.yaml")
                _write_updated_yaml(
                    src_path=Path(CONFIG_PATH),
                    dst_path=dst,
                    current_default=current_default,
                    current_ids=current_ids,
                    make_new_safe=True,
                )
                print(f"[INFO] ✅ Updated YAML geschrieben: {dst}")
            else:
                print("[INFO] Updated YAML NICHT geschrieben (nicht alle Devices erfolgreich).")

            if NEW_DEFAULT_MODE:
                print("⚠️  HINWEIS: Geräte wurden auf Default IDs gesetzt.")
                print("   => NICHT gleichzeitig am Bus betreiben/aktivieren (Kollisionen).")
            return 0 




        # for d in DEVICE_CONFIG:
        #     dev_no = int(d["dev_no"])
        #     cmd_id = int(d["cmd_id"])
        #     ans_id = int(d["answer_id"])

        #     print(f"[DEV {dev_no}] activating with CMD={fmt_can_id(cmd_id)} ANS={fmt_can_id(ans_id)} ...")

        #     try:
        #         nchan = gsv.activate(dev_no, cmd_id, ans_id)
        #         print(f"[DEV {dev_no}] activate OK, channels={nchan}")

        #     except Exception as e:
        #         print(f"[DEV {dev_no}] activate FAIL: {e}")
        #         print("-" * 80)
        #         continue

        #     # --- Read CAN settings (requires correct index constants) ---
        #     try:
        #         cmd_read = gsv.get_can_settings(dev_no, IDX_CAN_CMD_ID)
        #         ans_read = gsv.get_can_settings(dev_no, IDX_CAN_ANSWER_ID)

        #         print(f"[DEV {dev_no}] get_can_settings CMD_ID   = {fmt_can_id(cmd_read)} (raw={cmd_read})")
        #         print(f"[DEV {dev_no}] get_can_settings ANSWER_ID= {fmt_can_id(ans_read)} (raw={ans_read})")

        #         # Optional sanity check vs config
        #         if cmd_read != cmd_id or ans_read != ans_id:
        #             print(f"[DEV {dev_no}] WARN: IDs differ from YAML "
        #                   f"(yaml CMD={fmt_can_id(CMD_BASE)} ANS={fmt_can_id(ANS_BASE)})")
                    
        #         # gsv.load_settings(dev_no, 1)

        #         if dev_no == 6:
        #             cmd_new = 0x100
        #             ans_new = 0x101

        #             gsv.set_can_settings(dev_no, CANSET_CAN_IN_CMD_ID, cmd_new)
        #             gsv.set_can_settings(dev_no, CANSET_CAN_OUT_ANS_ID, ans_new)

        #             # optional verifizieren
        #             cmd_new = gsv.get_can_settings(dev_no, CANSET_CAN_IN_CMD_ID)
        #             ans_new = gsv.get_can_settings(dev_no, CANSET_CAN_OUT_ANS_ID)
        #             print(hex(cmd_new), hex(ans_new))

        #             # we need this to make the changes take effect
        #             gsv.reset_device(dev_no)

        #             time.sleep(2)

        #             # this is for reaching our device again 
        #             # without it we get a timeout on every call
        #             gsv.release(dev_no)

        #             gsv.activate(dev_no, cmd_new, ans_new)
        #             print(f"[DEV {dev_no}] activated with new CMD_ID   = {cmd_new}")
        #             print(f"[DEV {dev_no}] activated with new ANS_ID   = {ans_new}")

        #             # sn = gsv.get_serial_no(dev_no)
        #             # print(f"serial number is {sn}")

        #             # optional verifizieren
        #             cmd_new = gsv.get_can_settings(dev_no, CANSET_CAN_IN_CMD_ID)
        #             ans_new = gsv.get_can_settings(dev_no, CANSET_CAN_OUT_ANS_ID)
        #             print(hex(cmd_new), hex(ans_new))


        #     except Exception as e:
        #         print(f"[DEV {dev_no}] get_can_settings FAIL: {e}")

        #     print("-" * 80)

        # dev_no = 1

        # # neue IDs schreiben
        # # gsv.set_can_settings(dev_no, CANSET_CAN_IN_CMD_ID, 0x100)
        # # gsv.set_can_settings(dev_no, CANSET_CAN_OUT_ANS_ID, 0x101)
        # gsv.set_can_settings(dev_no, CANSET_CAN_IN_CMD_ID, 0x0F4)
        # gsv.set_can_settings(dev_no, CANSET_CAN_OUT_ANS_ID, 0x0F5)

        # # optional verifizieren
        # cmd_new = gsv.get_can_settings(dev_no, CANSET_CAN_IN_CMD_ID)
        # ans_new = gsv.get_can_settings(dev_no, CANSET_CAN_OUT_ANS_ID)
        # print(hex(cmd_new), hex(ans_new))

        # # we need this to make the changes take effect
        # gsv.reset_device(dev_no)

        # time.sleep(2)

        # # this is for reaching our device again 
        # # without it we get a timeout on every call
        # gsv.release(dev_no)

        # gsv.activate(dev_no, cmd_new, ans_new)
        # print(f"[DEV {dev_no}] activated with new CMD_ID   = {cmd_new}")
        # print(f"[DEV {dev_no}] activated with new ANS_ID   = {ans_new}")

        # # sn = gsv.get_serial_no(dev_no)
        # # print(f"serial number is {sn}")

        # # optional verifizieren
        # cmd_new = gsv.get_can_settings(dev_no, CANSET_CAN_IN_CMD_ID)
        # ans_new = gsv.get_can_settings(dev_no, CANSET_CAN_OUT_ANS_ID)
        # print(hex(cmd_new), hex(ans_new))

        # return 0

    finally:
        try:
            gsv.release(0)
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())