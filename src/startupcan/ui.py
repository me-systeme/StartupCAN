
from startupcan.gsv86can import GSV86CAN
from startupcan.runtime import _safe_release


def fmt_can_id(x: int) -> str:
    # CAN IDs sind oft 11-bit: 0..0x7FF; manche Systeme nutzen 29-bit.
    # Wir formatieren flexibel.
    if x <= 0x7FF:
        return f"0x{x:03X}"
    return f"0x{x:X}"

UNKNOWN_HINT = (
    "CAN-ID ist unknown. Bitte Gerät per USB anschließen und mit GSVmulti "
    "die CAN-IDs (CMD/ANSWER) auslesen bzw. korrekt setzen."
)

def _fmt_dev(dev_no: int, serial: int | None) -> str:
    if serial is None:
        return f"DEV {dev_no} (SN=?)"
    return f"DEV {dev_no} (SN={serial})"

def _warn_unknown(dev_no: int, serial: int | None, *, where: str = ""):
    tag = _fmt_dev(dev_no, serial)
    prefix = f"[{tag}]"
    if where:
        prefix += f" [{where}]"
    print(f"{prefix} ⚠️  {UNKNOWN_HINT}")

def _pause(msg: str):
    print(msg)
    input("➡️  ENTER drücken, um fortzufahren ... ")

def _ask_continue(prompt: str = "[WIZARD] Nächstes Gerät bearbeiten? [j/N]: ") -> bool:
    more = input(prompt).strip().lower()
    return more in ("j", "ja", "y", "yes")

def _connect_one(dev_no: int):
    print("\n" + "=" * 80)
    print(f"[WIZARD] DEV {dev_no}")
    print("⚠️  WICHTIG: Es darf GENAU EIN Gerät am CAN-Bus angeschlossen sein.")
    print(f"➡️  Bitte jetzt DEV {dev_no} anschließen.")
    print("=" * 80)
    _pause("Wenn angeschlossen:")


def _finish_device_step(gsv: GSV86CAN, dev_no: int, serial: int | None, reason: str = ""):
    """
    Best-effort: Session freigeben und den User auffordern, GENAU dieses Gerät abzunehmen.
    """
    tag = _fmt_dev(dev_no, serial)
    if reason:
        print(f"[{tag}] {reason}")

    _safe_release(gsv, dev_no, where="finish_device_step")

    _pause(f"➡️  Bitte dieses Gerät {tag} JETZT vom Bus abnehmen/abschrauben, dann ENTER ...")

def _disconnect_one(gsv: GSV86CAN, dev_no: int, serial: int | None, reason: str = ""):
    # IMMER device entfernen lassen (dein neues Safety-Modell)
    _finish_device_step(gsv, dev_no, serial, reason=reason)