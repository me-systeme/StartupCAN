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

    device_config = []
    for d in devices_section["config"]:

        device_config.append({
            "dev_no": int(d["dev_no"]),
            "cmd_id": _parse_hex(d["cmd_id"]),
            "answer_id": _parse_hex(d["answer_id"]),
        })

    # -------------------------------------------------------------------------
    # Return normalized configuration
    # -------------------------------------------------------------------------
    return {
        "MYBUFFERSIZE": mybuffersize,
        "CANBAUD": canbaud,
        "DEVICE_CONFIG": device_config,
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
