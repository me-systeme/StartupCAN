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

    # current/new blocks (new YAML structure)
    current_block = cfg_block.get("current", {}) or {}
    new_block = cfg_block.get("new", {}) or {}

    current_default_mode = bool(current_block.get("default", False))
    new_default_mode = bool(new_block.get("default", False))

    def _norm_list(lst):
        out = []
        for d in (lst or []):
            out.append({
                "dev_no": int(d["dev_no"]),
                "cmd_id": _parse_hex(d["cmd_id"]),
                "answer_id": _parse_hex(d["answer_id"]),
            })
        return out
    
    device_current = _norm_list(current_block.get("ids", []))
    device_new_raw = _norm_list(new_block.get("ids", []))

    # -------------------------------------------------------------------------
    # Allowed mode combinations
    # -------------------------------------------------------------------------
    
    def _id_pairs(items: list[dict]) -> set[tuple[int, int]]:
        return {(int(d["cmd_id"]), int(d["answer_id"])) for d in (items or [])}
    
    def _id_numbers(items: list[dict]) -> set[int]:
        s: set[int] = set()
        for d in (items or []):
            s.add(int(d["cmd_id"]))
            s.add(int(d["answer_id"]))
        return s

    DEFAULT_PAIR = (int(default_cmd_id), int(default_ans_id))
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
        
        current_nums = _id_numbers(device_current)
        new_nums = _id_numbers(device_new_raw)

        overlap_nums = current_nums & new_nums
        if overlap_nums:
            ex = next(iter(overlap_nums))
            raise ValueError(
                "current.default=false & new.default=false: "
                "Keine einzelne CAN-ID Zahl (weder cmd noch ans) aus new.ids darf in current.ids vorkommen "
                f"(und umgekehrt). Beispiel Überschneidung: 0x{ex:X} ({ex})."
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
        "DEVICE_CONFIG": device_config,
        "DEVICE_CURRENT": device_current,   # optional
        "DEVICE_NEW": device_new,           # Ziel-IDs
        "ASSIGN": {"DEFAULT_CMD_ID": default_cmd_id, "DEFAULT_ANS_ID": default_ans_id},
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
CONFIG_PATH = PROJECT_ROOT / "config.yaml"

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
DEVICE_CURRENT = CONFIG["DEVICE_CURRENT"]
DEVICE_NEW = CONFIG["DEVICE_NEW"]
ASSIGN = CONFIG["ASSIGN"]
DEFAULT_CMD_ID = ASSIGN["DEFAULT_CMD_ID"]
DEFAULT_ANS_ID = ASSIGN["DEFAULT_ANS_ID"]
