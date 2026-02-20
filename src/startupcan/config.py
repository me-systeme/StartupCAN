"""
config.py

YAML-based configuration loader for the GSV86CANViewer application.

This module provides:
- Helpers to parse common YAML value formats (hex CAN IDs, floats with units).
- A single load_config() function that reads and validates config.yaml.
- Module-level constants that are loaded once at import time and used throughout the app.

The returned configuration is normalized:
- Numeric strings are converted to int/float.
- CAN IDs can be specified as "0x..." or decimal strings.
- Mapping entries are converted into convenient Python structures.
"""
import sys
from pathlib import Path

import yaml

# -----------------------------------------------------------------------------
# Parsing helpers
# -----------------------------------------------------------------------------
def _parse_hex(x):
    """
    Parse an integer that may be provided in different textual formats.

    What happens:
    - If value is an int: returned as-is.
    - If value is a string:
      - "0x..." is parsed as hexadecimal
      - otherwise parsed as decimal

    Parameters
    ----------
    value:
        int or str (e.g. 200, "0x0C8", "256")

    Returns
    -------
    int
        Parsed integer value.
    """
    if isinstance(x, int):
        return x
    if isinstance(x, str):
        s = x.strip().lower()
        return int(s, 16) if s.startswith("0x") else int(s)
    raise TypeError(f"Unsupported CAN ID value type: {type(x)}")


# -----------------------------------------------------------------------------
# Main configuration loader
# -----------------------------------------------------------------------------
def load_config(path: Path) -> dict:
    """
    Load and normalize the project configuration from a YAML file.

    What happens:
    - Reads YAML using yaml.safe_load().
    - Converts all required fields to expected Python types.
    - Normalizes nested configuration blocks into convenient structures:
      - DEVICE_CONFIG: list of dicts with numeric CAN IDs
      - SENSORS_BY_NO: dict[int, sensor_info]
      - SENSOR_BY_DEVCH: dict[(dev_no, ch_idx0), sensor_no]
    - Applies defaults and validates values where appropriate:
      - logging.rate_hz defaults to 1.0 and must be > 0 if provided.

    Parameters
    ----------
    path : pathlib.Path
        Path to the YAML file (e.g. PROJECT_ROOT / "config.yaml").

    Returns
    -------
    dict
        Normalized configuration dictionary. Keys include:
        - MYBUFFERSIZE (int)
        - CANBAUD (int)
        - DEVICE_CONFIG (list[dict])
        - LOG_FILE (str|None)
        - LOG_RATE_HZ (float)
        - SENSORS_BY_NO (dict[int, dict])
        - SENSOR_BY_DEVCH (dict[tuple[int,int], int])

    Raises
    ------
    KeyError
        If required YAML keys are missing.
    ValueError
        If logging.rate_hz is provided but <= 0.
    """

    SN_MODE = False
    IGNORE_NEW_SERIALS = False
    # -------------------------------------------------------------------------
    # Read YAML file
    # -------------------------------------------------------------------------
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    # -------------------------------------------------------------------------
    # DLL block: required values
    # -------------------------------------------------------------------------
    mybuffersize = int(cfg["dll"]["mybuffersize"])
    canbaud = int(cfg["dll"]["canbaud"])

    # -------------------------------------------------------------------------
    # Devices block:
    # - Optional startup flags (defaults = False)
    # - Per-device config entries
    # -------------------------------------------------------------------------
    devices_section = cfg.get("devices", {}) or {}
    cfg_block = (devices_section.get("config", {}) or {})


    # optionaler Assign-Block (Defaultwerte ok)
    assign = cfg_block.get("assign", {}) or {}
    default_cmd_id = _parse_hex(assign.get("default_cmd_id", "0x100"))
    default_ans_id = _parse_hex(assign.get("default_ans_id", "0x101"))

    if int(default_cmd_id) == int(default_ans_id):
        raise ValueError(
            "devices.config.assign: default_cmd_id und default_ans_id dürfen nicht gleich sein "
            f"(ID=0x{int(default_cmd_id):X})."
        )

    # current/new blocks (new YAML structure)
    current_block = cfg_block.get("current", {}) or {}
    new_block = cfg_block.get("new", {}) or {}

    current_default_mode = bool(current_block.get("default", False))
    new_default_mode = bool(new_block.get("default", False))

    def _norm_list(lst):
        out = []
        for d in (lst or []):
            item = {
                "dev_no": int(d["dev_no"]),
                "cmd_id": _parse_hex(d["cmd_id"]),
                "answer_id": _parse_hex(d["answer_id"]),
            }
            # serial ist OPTIONAL
            if "serial" in d and d["serial"] is not None:
                # erlaubt int oder string
                item["serial"] = int(str(d["serial"]).strip())
            out.append(item)
        return out
    
    def _assert_unique_can_fields(name: str, items: list[dict], *, strict_numbers: bool = True):
        """
        strict_numbers=True:
        - keine einzelne Zahl (cmd oder ans) darf doppelt vorkommen (cmd-cmd, ans-ans, cmd-ans)
        strict_numbers=False:
        - erlaubt doppelte Zahlen (z.B. Default IDs in Wizard-Mode),
            prüft aber weiterhin: cmd_id != answer_id pro Gerät
        """

        numbers: list[int] = []

        for d in (items or []):
            cmd = int(d["cmd_id"])
            ans = int(d["answer_id"])

            # 1) pro Gerät: cmd != ans
            if cmd == ans:
                raise ValueError(
                    f"{name}: cmd_id und answer_id dürfen nicht gleich sein "
                    f"(dev_no={d.get('dev_no','?')} ID=0x{cmd:X} / {cmd})."
                )

            numbers.append(cmd)
            numbers.append(ans)

        if strict_numbers:
            # 2) keine Zahl darf doppelt vorkommen
            if len(numbers) != len(set(numbers)):
                # Duplikat ermitteln
                dup = next(x for x in numbers if numbers.count(x) > 1)
                raise ValueError(
                    f"{name}: CAN-ID 0x{dup:X} ({dup}) kommt mehrfach vor "
                    "(weder cmd noch ans darf irgendwo doppelt sein)."
                )
    
    def _assert_unique_dev_no(name: str, items: list[dict]):
        dev_nos = [int(d["dev_no"]) for d in (items or [])]
        if len(dev_nos) != len(set(dev_nos)):
            dup = next(n for n in dev_nos if dev_nos.count(n) > 1)
            raise ValueError(f"{name}: dev_no={dup} kommt mehrfach vor.")
        
    def _by_devno(items: list[dict]) -> dict[int, dict]:
        return {int(d["dev_no"]): d for d in (items or [])}
    
    device_current = _norm_list(current_block.get("ids", []))
    device_new_raw = _norm_list(new_block.get("ids", []))

    # dev_no muss eindeutig sein
    if device_current:
        _assert_unique_dev_no("devices.config.current.ids", device_current)
    if device_new_raw:
        _assert_unique_dev_no("devices.config.new.ids", device_new_raw)

    # -------------------------------------------------------------------------
    # Serial-number mode (2 Modi, kein Mischbetrieb erlaubt)
    # -------------------------------------------------------------------------
    def _detect_and_validate_sn_mode(new_ids: list[dict]) -> bool:
        """
        Zwei Modi:
        - SN_MODE=False: KEIN Eintrag in new.ids hat 'serial'
        - SN_MODE=True : ALLE Einträge in new.ids haben 'serial' (und unique)

        Mischbetrieb (einige mit serial, einige ohne) -> ValueError sofort.
        """
        if not new_ids:
            return False  # egal, wird später je nach Case geprüft

        has_any = any(("serial" in d and d["serial"] is not None) for d in new_ids)
        if not has_any:
            return False

        missing = [d.get("dev_no") for d in new_ids if ("serial" not in d or d["serial"] is None)]
        if missing:
            raise ValueError(
                "devices.config.new.ids: SN-Modus aktiv (mindestens ein Eintrag hat 'serial'), "
                f"aber bei folgenden dev_no fehlt 'serial': {missing}. "
                "Entweder: bei ALLEN new.ids Einträgen 'serial' setzen oder bei KEINEM."
            )

        serials = []
        for d in new_ids:
            s = int(d["serial"])
            if s <= 0:
                raise ValueError(f"devices.config.new.ids: ungültige serial {s} (dev_no={d.get('dev_no')})")
            serials.append(s)

        dup = sorted({s for s in serials if serials.count(s) > 1})
        if dup:
            raise ValueError(
                f"devices.config.new.ids: Seriennummer(n) kommen mehrfach vor: {dup}"
            )

        return True


    if device_current:
        _assert_unique_can_fields(
            "devices.config.current.ids",
            device_current,
            strict_numbers=not current_default_mode,  # <-- WICHTIG
        )

    if device_new_raw:
        _assert_unique_can_fields(
            "devices.config.new.ids",
            device_new_raw,
            strict_numbers=not new_default_mode,      # <-- WICHTIG
        )

    # -------------------------------------------------------------------------
    # Allowed mode combinations
    # -------------------------------------------------------------------------
    
    def _id_numbers(items: list[dict]) -> set[int]:
        s: set[int] = set()
        for d in (items or []):
            s.add(int(d["cmd_id"]))
            s.add(int(d["answer_id"]))
        return s
    
    def _has_serials(items: list[dict]) -> bool:
        return any(("serial" in d and d["serial"] is not None) for d in (items or []))

        
    def _assert_same_dev_nos(name_a: str, a: list[dict], name_b: str, b: list[dict]):
        sa = {int(d["dev_no"]) for d in (a or [])}
        sb = {int(d["dev_no"]) for d in (b or [])}
        if sa != sb:
            only_a = sorted(sa - sb)
            only_b = sorted(sb - sa)
            raise ValueError(
                f"{name_a} und {name_b} müssen die gleichen dev_no enthalten. "
                f"Nur in {name_a}: {only_a} | Nur in {name_b}: {only_b}"
            )
    
    # # -------------------------------------------------------------------------
    # # UNKNOWN MODE: current wird ignoriert, Ziel kommt aus new.ids
    # # -------------------------------------------------------------------------
    # if current_unknown_mode:
    #     # In unknown-mode muss es Ziel-IDs geben (wir wollen auf definierte IDs umstellen)
    #     if new_default_mode:
    #         raise ValueError(
    #             "devices.config.current.unknown=true: new.default=true ist nicht erlaubt "
    #             "(du brauchst konkrete Ziel-IDs in new.ids)."
    #         )
    #     if not device_new_raw:
    #         raise ValueError(
    #             "devices.config.current.unknown=true: devices.config.new.ids darf nicht leer sein "
    #             "(Ziel-IDs müssen definiert sein)."
    #         )
        

    # Forbidden combo
    if current_default_mode and new_default_mode:
        raise ValueError(
            "Ungültige Konfiguration: current.default=true und new.default=true ist nicht erlaubt."
        )
    
    # Case 1: current=false, new=false
    if (not current_default_mode) and (not new_default_mode):
        if not device_current:
            raise ValueError("current.default=false & new.default=false: devices.config.current.ids darf nicht leer sein.")
        if not device_new_raw:
            raise ValueError("current.default=false & new.default=false: devices.config.new.ids darf nicht leer sein.")
        if len(device_current) != len(device_new_raw):
            raise ValueError(
                "current.default=false & new.default=false: current.ids und new.ids müssen gleich lang sein."
            )
        
        _assert_same_dev_nos(
            "devices.config.current.ids", device_current,
            "devices.config.new.ids", device_new_raw
        )
        
        IGNORE_NEW_SERIALS = _has_serials(device_new_raw)

        SN_MODE = False
        
        current_nums = _id_numbers(device_current)

        cur_by = _by_devno(device_current)
        new_by = _by_devno(device_new_raw)

        # Nur IDs der Geräte betrachten, die sich WIRKLICH ändern sollen.
        changed_new_nums: set[int] = set()
        for dev_no, nd in new_by.items():
            cd = cur_by.get(dev_no)
            if cd is None:
                # sollte durch _assert_same_dev_nos nie passieren, aber sauber bleiben
                raise ValueError(f"devices.config.new.ids enthält dev_no={dev_no}, das in current.ids fehlt.")

            if int(nd["cmd_id"]) != int(cd["cmd_id"]) or int(nd["answer_id"]) != int(cd["answer_id"]):
                changed_new_nums.add(int(nd["cmd_id"]))
                changed_new_nums.add(int(nd["answer_id"]))

        # Wenn nichts geändert wird, ist das ok (no-op run).
        overlap_nums = current_nums & changed_new_nums
        if overlap_nums:
            ex = next(iter(overlap_nums))
            raise ValueError(
                "current.default=false & new.default=false (Partial allowed): "
                "Eine CAN-ID, die im current Bus bereits existiert, darf nicht als Ziel-ID "
                "für ein GEÄNDERTES Gerät verwendet werden. "
                f"Beispiel Überschneidung: 0x{ex:X} ({ex}). "
                "Hinweis: No-op Einträge (new==current pro dev_no) sind erlaubt."
            )

        dev_nos_for_run = [d["dev_no"] for d in device_current]  # Quelle: current
        # Ziel ist new.ids wie angegeben
        device_new = device_new_raw
        # Initial aktivieren mit current.ids
        device_config = device_current

    # Case 2: current=true, new=false
    elif current_default_mode and (not new_default_mode):
        if not device_new_raw:
            raise ValueError("current.default=true & new.default=false: devices.config.new.ids darf nicht leer sein.")
        
        SN_MODE = _detect_and_validate_sn_mode(device_new_raw)

        dev_nos_for_run = [d["dev_no"] for d in device_new_raw]  # Quelle: new
        device_new = device_new_raw

        # Initial aktivieren mit Default IDs (Wizard)
        device_config = [
            {"dev_no": int(n), "cmd_id": default_cmd_id, "answer_id": default_ans_id}
            for n in dev_nos_for_run
        ]

        new_nums = _id_numbers(device_new)
        if default_cmd_id in new_nums or default_ans_id in new_nums:
            # dev_no für bessere Fehlermeldung finden
            offenders = []
            for d in device_new:
                if d["cmd_id"] == default_cmd_id or d["answer_id"] == default_ans_id:
                    offenders.append(
                        f"dev_no={d['dev_no']} CMD={hex(d['cmd_id'])} ANS={hex(d['answer_id'])}"
                    )

            raise ValueError(
                "current.default=true: Ziel-IDs dürfen keine Default-ID enthalten "
                f"(Default CMD={hex(default_cmd_id)} ANS={hex(default_ans_id)}). "
                "Betroffene Einträge: " + "; ".join(offenders)
            )
    
    # Case 3: current=false, new=true
    else:  # (not current_default_mode) and new_default_mode
        SN_MODE = False
        if not device_current:
            raise ValueError("current.default=false & new.default=true: devices.config.current.ids darf nicht leer sein.")

        current_nums = _id_numbers(device_current)

        if default_cmd_id in current_nums or default_ans_id in current_nums:
            raise ValueError(
                "current.default=false & new.default=true: "
                "Keine einzelne Default CAN-ID (weder cmd noch ans) "
                "darf bereits in current.ids vorkommen "
                f"(Default CMD={hex(default_cmd_id)} "
                f"ANS={hex(default_ans_id)})."
            )
        
        dev_nos_for_run = [d["dev_no"] for d in device_current]  # Quelle: current

        # Ziel ist Default IDs (Reset), new.ids darf leer sein
        device_new = [
            {"dev_no": int(n), "cmd_id": default_cmd_id, "answer_id": default_ans_id}
            for n in dev_nos_for_run
        ]

        # Initial aktivieren mit current.ids
        device_config = device_current
    

    # -------------------------------------------------------------------------
    # Return normalized configuration
    # -------------------------------------------------------------------------
    return {
        "MYBUFFERSIZE": mybuffersize,
        "CANBAUD": canbaud,
        "CURRENT_DEFAULT_MODE": current_default_mode,
        "NEW_DEFAULT_MODE": new_default_mode,
        "SN_MODE": SN_MODE,
        "DEVICE_CONFIG": device_config,
        "DEVICE_CURRENT": device_current,   # optional
        "DEVICE_NEW": device_new,           # Ziel-IDs
        "ASSIGN": {"DEFAULT_CMD_ID": default_cmd_id, "DEFAULT_ANS_ID": default_ans_id},
        "IGNORE_NEW_SERIALS": bool(IGNORE_NEW_SERIALS),
    }

    

    

# -----------------------------------------------------------------------------
# Load global configuration once at import time
# -----------------------------------------------------------------------------

def _project_root() -> Path:
    """
    Determine the runtime root directory.

    - Dev run: project root (where config.yaml and GSV86CAN.dll live).
    - PyInstaller: directory next to the executable (dist/run).
    """
    if getattr(sys, "frozen", False):
        # When bundled, prefer the directory where the .exe resides.
        return Path(sys.executable).resolve().parent
    # Dev mode: this file is in .../src/gsv86canviewer/config.py -> parents[2] is project root.
    return Path(__file__).resolve().parents[2]

PROJECT_ROOT = _project_root()
CONFIG_PATH = PROJECT_ROOT / "config.updated.yaml"

if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
    # PyInstaller onefile extracts binaries here at runtime.
    DLL_PATH = Path(sys._MEIPASS) / "GSV86CAN.dll"
else:
    DLL_PATH = PROJECT_ROOT / "GSV86CAN.dll"

CONFIG = load_config(CONFIG_PATH)

DEVICE_CONFIG = CONFIG["DEVICE_CONFIG"]

MYBUFFERSIZE = CONFIG["MYBUFFERSIZE"]
CANBAUD = CONFIG["CANBAUD"]

CURRENT_DEFAULT_MODE = CONFIG["CURRENT_DEFAULT_MODE"]
NEW_DEFAULT_MODE = CONFIG["NEW_DEFAULT_MODE"]
SN_MODE = CONFIG["SN_MODE"]
DEVICE_CURRENT = CONFIG["DEVICE_CURRENT"]
DEVICE_NEW = CONFIG["DEVICE_NEW"]
ASSIGN = CONFIG["ASSIGN"]
DEFAULT_CMD_ID = ASSIGN["DEFAULT_CMD_ID"]
DEFAULT_ANS_ID = ASSIGN["DEFAULT_ANS_ID"]
IGNORE_NEW_SERIALS = CONFIG["IGNORE_NEW_SERIALS"]
