"""
startupcan.py

Headless startup/scanner for GSV86CAN devices.
- Activates a fixed set of devices (from DEVICE_CONFIG)
- Reads CAN settings via get_can_settings()
- Logs everything to terminal
"""

import sys
import time

from startupcan.config import DEVICE_CONFIG
from startupcan.gsv86can import GSV86CAN, CANSET_CAN_IN_CMD_ID, CANSET_CAN_OUT_ANS_ID


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


def main() -> int:
    gsv = GSV86CAN()

    try:
        try:
            v = gsv.dll_version()
            print(f"[INFO] DLL Version: {v}")
        except Exception as e:
            print(f"[FAIL] DLL Version konnte nicht gelesen werden: {e}")
            return 2

        print(f"[INFO] Devices in config: {len(DEVICE_CONFIG)}")
        print("-" * 80)

        for d in DEVICE_CONFIG:
            dev_no = int(d["dev_no"])
            cmd_id = int(d["cmd_id"])
            ans_id = int(d["answer_id"])

            print(f"[DEV {dev_no}] activating with CMD={fmt_can_id(cmd_id)} ANS={fmt_can_id(ans_id)} ...")

            try:
                nchan = gsv.activate(dev_no, cmd_id, ans_id)
                print(f"[DEV {dev_no}] activate OK, channels={nchan}")

            except Exception as e:
                print(f"[DEV {dev_no}] activate FAIL: {e}")
                print("-" * 80)
                continue

            # --- Read CAN settings (requires correct index constants) ---
            try:
                cmd_read = gsv.get_can_settings(dev_no, IDX_CAN_CMD_ID)
                ans_read = gsv.get_can_settings(dev_no, IDX_CAN_ANSWER_ID)

                print(f"[DEV {dev_no}] get_can_settings CMD_ID   = {fmt_can_id(cmd_read)} (raw={cmd_read})")
                print(f"[DEV {dev_no}] get_can_settings ANSWER_ID= {fmt_can_id(ans_read)} (raw={ans_read})")

                # Optional sanity check vs config
                if cmd_read != cmd_id or ans_read != ans_id:
                    print(f"[DEV {dev_no}] WARN: IDs differ from YAML "
                          f"(yaml CMD={fmt_can_id(cmd_id)} ANS={fmt_can_id(ans_id)})")

            except Exception as e:
                print(f"[DEV {dev_no}] get_can_settings FAIL: {e}")

            print("-" * 80)

        # dev_no = 1

        # # neue IDs schreiben
        # gsv.set_can_settings(dev_no, CANSET_CAN_IN_CMD_ID, 0x100)
        # gsv.set_can_settings(dev_no, CANSET_CAN_OUT_ANS_ID, 0x101)

        # # optional verifizieren
        # cmd_new = gsv.get_can_settings(dev_no, CANSET_CAN_IN_CMD_ID)
        # ans_new = gsv.get_can_settings(dev_no, CANSET_CAN_OUT_ANS_ID)
        # print(hex(cmd_new), hex(ans_new))

        return 0

    finally:
        try:
            gsv.release(0)
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())