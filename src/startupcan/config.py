"""
config.py

YAML-based configuration loader for StartupCAN.

This module is responsible for:
- reading and normalizing the YAML configuration
- validating configuration consistency
- deriving the active operating mode
- exposing module-level constants that are loaded once at import time

The configuration is normalized so that:
- CAN IDs may be given as hexadecimal or decimal strings
- numeric values are converted to Python ints
- device lists are converted into convenient Python structures
- the selected run mode is derived from `current.default` and `new.default`
"""

import sys
from pathlib import Path

import yaml

# Supported CAN baud rates
_ALLOWED_CAN_BAUDS = {1000000, 500000, 250000, 125000, 100000, 50000, 25000}

# -----------------------------------------------------------------------------
# Parsing helpers
# -----------------------------------------------------------------------------
def _parse_hex(x):
    """
    Parse an integer that may be given as:
    - int
    - decimal string
    - hexadecimal string starting with "0x"

    Args:
        value:
            Integer-like value, for example:
            200, "256", "0x100"

    Returns:
        Parsed integer value.

    Raises:
        TypeError:
            If the value type is unsupported.
    """
    if isinstance(x, int):
        return x
    if isinstance(x, str):
        s = x.strip().lower()
        return int(s, 16) if s.startswith("0x") else int(s)
    raise TypeError(f"Unsupported CAN ID value type: {type(x)}")

# -----------------------------------------------------------------------------
# Validation helpers
# -----------------------------------------------------------------------------
def _assert_canbaud_allowed(name: str, items: list[dict]):
    """
    Validate optional per-device `canbaud` entries.

    Args:
        name:
            Human-readable section name for error messages.

        items:
            List of device dictionaries.

    Raises:
        ValueError:
            If any configured baud rate is not supported.
    """
    bad = []
    for d in (items or []):
        if "canbaud" in d and d["canbaud"] is not None:
            cb = int(d["canbaud"])
            if cb not in _ALLOWED_CAN_BAUDS:
                bad.append((int(d["dev_no"]), cb))
    if bad:
        raise ValueError(f"{name}: invalid canbaud values: {bad}")

def _assert_unknown_is_bool(name: str, items: list[dict]):
    """
    Validate that optional `unknown` flags are booleans.

    Args:
        name:
            Human-readable section name for error messages.

        items:
            List of device dictionaries.

    Raises:
        ValueError:
            If any `unknown` value is not boolean.
    """
    bad = [d.get("dev_no") for d in (items or []) if "unknown" in d and not isinstance(d["unknown"], bool)]
    if bad:
        raise ValueError(f"{name}: unknown must be bool for dev_no={bad}")
        
def _assert_unique_dev_no(name: str, items: list[dict]):
    """
    Ensure that `dev_no` values are unique within one list.

    Args:
        name:
            Human-readable section name for error messages.

        items:
            List of device dictionaries.

    Raises:
        ValueError:
            If a device number occurs more than once.
    """
    dev_nos = [int(d["dev_no"]) for d in (items or [])]
    if len(dev_nos) != len(set(dev_nos)):
        dup = next(n for n in dev_nos if dev_nos.count(n) > 1)
        raise ValueError(f"{name}: dev_no={dup} occurs more than once.")
    
def _assert_unique_can_fields(
    name: str,
    items: list[dict],
    *,
    strict_numbers: bool = True,
    require_value_id: bool = False,
):
    """
    Validate CAN ID usage within one list.

    Rules per device:
    - cmd_id != answer_id
    - if value_id is present (or required):
        - cmd_id != value_id
        - answer_id == value_id is allowed

    Global uniqueness:
    - if strict_numbers=True:
        - all CAN IDs must be globally unique across devices
        - exception: answer_id == value_id is allowed within the SAME device
    - if strict_numbers=False:
        - duplicates across devices are allowed
        - only the per-device rules above are checked

    Args:
        name:
            Human-readable section name for error messages.

        items:
            List of device dictionaries.

        strict_numbers:
            Whether CAN IDs must be globally unique across devices.

        require_value_id:
            Whether every entry must contain value_id.

    Raises:
        ValueError:
            If the validation fails.
    """
    # Tracks globally used CAN IDs and where they were first seen.
    # Example:
    #   0x103 -> "dev_no=1.answer_id"
    seen: dict[int, str] = {}

    for d in (items or []):
        dev_no = d.get("dev_no", "?")
        cmd = int(d["cmd_id"])
        ans = int(d["answer_id"])

        # -------------------------------------------------------------
        # Per-device validation
        # -------------------------------------------------------------
        if cmd == ans:
            raise ValueError(
                f"{name}: cmd_id and answer_id must not be identical "
                f"(dev_no={dev_no} ID=0x{cmd:X} / {cmd})."
            )

        val = None
        if "value_id" in d and d["value_id"] is not None:
            val = int(d["value_id"])

            if cmd == val:
                raise ValueError(
                    f"{name}: cmd_id and value_id must not be identical "
                    f"(dev_no={dev_no} ID=0x{cmd:X} / {cmd})."
                )
        elif require_value_id:
            raise ValueError(f"{name}: value_id missing for dev_no={dev_no}")

        # -------------------------------------------------------------
        # No global uniqueness check required
        # -------------------------------------------------------------
        if not strict_numbers:
            continue

        # -------------------------------------------------------------
        # Global uniqueness:
        # - cmd must be unique everywhere
        # - ans must be unique everywhere
        # - value must be unique everywhere
        # - EXCEPTION: value_id may equal answer_id of the SAME device
        # -------------------------------------------------------------

        if cmd in seen:
            raise ValueError(
                f"{name}: CAN ID 0x{cmd:X} ({cmd}) occurs more than once "
                f"(already used by {seen[cmd]})."
            )
        seen[cmd] = f"dev_no={dev_no}.cmd_id"

        if ans in seen:
            raise ValueError(
                f"{name}: CAN ID 0x{ans:X} ({ans}) occurs more than once "
                f"(already used by {seen[ans]})."
            )
        seen[ans] = f"dev_no={dev_no}.answer_id"

        if val is not None:
            # Allowed special case:
            # answer_id == value_id within the same device
            if val == ans:
                continue

            if val in seen:
                raise ValueError(
                    f"{name}: CAN ID 0x{val:X} ({val}) occurs more than once "
                    f"(already used by {seen[val]})."
                )
            seen[val] = f"dev_no={dev_no}.value_id"

def _assert_same_dev_nos(name_a: str, a: list[dict], name_b: str, b: list[dict]):
    """
    Ensure that two device lists contain the same set of `dev_no` values.

    Args:
        name_a:
            Name of first list.

        a:
            First device list.

        name_b:
            Name of second list.

        b:
            Second device list.

    Raises:
        ValueError:
            If the two lists do not contain the same device numbers.
    """
    sa = {int(d["dev_no"]) for d in (a or [])}
    sb = {int(d["dev_no"]) for d in (b or [])}
    if sa != sb:
        only_a = sorted(sa - sb)
        only_b = sorted(sb - sa)
        raise ValueError(
            f"{name_a} and {name_b} must contain the same dev_no values. "
            f"Only in {name_a}: {only_a} | Only in {name_b}: {only_b}"
        )

def _assert_default_ids_valid(cmd: int, ans: int, value: int):
    if cmd == ans:
        raise ValueError(
            "devices.config.assign: default_cmd_id and default_ans_id must not be identical."
        )
    if cmd == value:
        raise ValueError(
            "devices.config.assign: default_cmd_id and default_value_id must not be identical."
        )

def _assert_can_id_range(name: str, items: list[dict], *, require_value_id: bool = False):
    bad = []

    for d in (items or []):
        dev_no = int(d["dev_no"])

        for field in ("cmd_id", "answer_id"):
            val = int(d[field])
            if not (0 <= val <= 0x7FF):
                bad.append((dev_no, field, val))

        if "value_id" in d and d["value_id"] is not None:
            val = int(d["value_id"])
            if not (0 <= val <= 0x7FF):
                bad.append((dev_no, "value_id", val))
        elif require_value_id:
            bad.append((dev_no, "value_id", None))

    if bad:
        raise ValueError(f"{name}: invalid 11-bit CAN IDs: {bad}")
    
def _detect_and_validate_sn_mode(new_ids: list[dict]) -> bool:
    """
    Detect and validate serial-number mapping mode.

    There are only two valid modes:
    - SN_MODE = False:
      no entry in `new.ids` contains `serial`
    - SN_MODE = True:
      all entries in `new.ids` contain `serial`, and all serials are unique

    Mixed mode is not allowed.

    Args:
        new_ids:
            List of target device entries.

    Returns:
        True if serial mapping mode is active, else False.

    Raises:
        ValueError:
            If serial-number configuration is inconsistent.
    """
    if not new_ids:
        return False 

    has_any = any(("serial" in d and d["serial"] is not None) for d in new_ids)
    if not has_any:
        return False

    missing = [d.get("dev_no") for d in new_ids if ("serial" not in d or d["serial"] is None)]
    if missing:
        raise ValueError(
            "devices.config.new.ids: serial mode is active because at least one entry "
            f"contains 'serial', but the following dev_no values are missing it: {missing}. "
            "Either all entries must define 'serial' or none of them."
        )

    serials = []
    for d in new_ids:
        s = int(d["serial"])
        if s <= 0:
            raise ValueError(f"devices.config.new.ids: invalid serial {s} (dev_no={d.get('dev_no')})")
        serials.append(s)

    dup = sorted({s for s in serials if serials.count(s) > 1})
    if dup:
        raise ValueError(
            f"devices.config.new.ids: duplicate serial number(s): {dup}"
        )

    return True

# -----------------------------------------------------------------------------
# Normalization helpers
# -----------------------------------------------------------------------------
def _norm_list(lst):
    """
    Normalize a raw device list from YAML.

    The result uses Python-native numeric types and optional normalized fields.

    Args:
        lst:
            Raw list from YAML.

    Returns:
        Normalized list of device dictionaries.
    """
    out = []
    for d in (lst or []):
        item = {
            "dev_no": int(d["dev_no"]),
            "cmd_id": _parse_hex(d["cmd_id"]),
            "answer_id": _parse_hex(d["answer_id"]),
        }

        if "value_id" in d and d["value_id"] is not None:
            item["value_id"] = _parse_hex(d["value_id"])
        
        if "serial" in d and d["serial"] is not None:
            item["serial"] = int(str(d["serial"]).strip())

        if "unknown" in d:
            item["unknown"] = bool(d["unknown"])
        
        if "canbaud" in d and d["canbaud"] is not None:
            item["canbaud"] = int(str(d["canbaud"]).strip())
        
        out.append(item)
    return out

# -----------------------------------------------------------------------------
# Main configuration loader
# -----------------------------------------------------------------------------
def load_config(path: Path) -> dict:
    """
    Load, normalize, and validate the YAML configuration.

    What this function does:
    - read the YAML file
    - normalize numeric values
    - validate default IDs and baud rates
    - normalize current/new device lists
    - detect the active run mode
    - derive the effective device lists used by StartupCAN

    Args:
        path:
            Path to the YAML file.

    Returns:
        A normalized configuration dictionary.

    Raises:
        KeyError:
            If required YAML sections are missing.

        ValueError:
            If configuration validation fails.
    """
    SN_MODE = False
    # -------------------------------------------------------------------------
    # Read YAML file
    # -------------------------------------------------------------------------
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    # -------------------------------------------------------------------------
    # DLL section
    # -------------------------------------------------------------------------
    mybuffersize = int(cfg["dll"]["mybuffersize"])
    canbaud = int(cfg["dll"]["canbaud"])

    # -------------------------------------------------------------------------
    # Devices / assign section
    # -------------------------------------------------------------------------
    devices_section = cfg.get("devices", {}) or {}
    cfg_block = (devices_section.get("config", {}) or {})
    assign = cfg_block.get("assign", {}) or {}

    default_cmd_id = _parse_hex(assign.get("default_cmd_id", "0x100"))
    default_ans_id = _parse_hex(assign.get("default_ans_id", "0x101"))
    default_canbaud = int(assign.get("default_canbaud", 1000000))
    default_value_id = _parse_hex(assign.get("default_value_id", "0x101"))

    
    if default_canbaud not in _ALLOWED_CAN_BAUDS:
        raise ValueError(f"devices.config.assign.default_canbaud={default_canbaud} is invalid.")
    
    if canbaud not in _ALLOWED_CAN_BAUDS:
        raise ValueError(f"dll.canbaud={canbaud} is invalid.")
    
    _assert_default_ids_valid(int(default_cmd_id), int(default_ans_id), int(default_value_id))

    for field_name, val in (
        ("default_cmd_id", int(default_cmd_id)),
        ("default_ans_id", int(default_ans_id)),
        ("default_value_id", int(default_value_id)),
    ):
        if not (0 <= val <= 0x7FF):
            raise ValueError(
                f"devices.config.assign.{field_name}=0x{val:X} ({val}) is not a valid 11-bit CAN ID."
            )

    # -------------------------------------------------------------------------
    # Current / new blocks
    # -------------------------------------------------------------------------
    current_block = cfg_block.get("current", {}) or {}
    new_block = cfg_block.get("new", {}) or {}

    current_default_mode = bool(current_block.get("default", False))
    new_default_mode = bool(new_block.get("default", False))

    device_current = _norm_list(current_block.get("ids", []))
    device_new_raw = _norm_list(new_block.get("ids", []))

    # -------------------------------------------------------------------------
    # Per-list validation
    # -------------------------------------------------------------------------
    if device_current:
        _assert_unknown_is_bool("devices.config.current.ids", device_current)
        _assert_unique_dev_no("devices.config.current.ids", device_current)
        _assert_canbaud_allowed("devices.config.current.ids", device_current)
        _assert_can_id_range(
            "devices.config.current.ids",
            device_current,
            require_value_id=False,
        )

    if device_new_raw:
        _assert_unique_dev_no("devices.config.new.ids", device_new_raw)
        _assert_can_id_range(
            "devices.config.new.ids",
            device_new_raw,
            require_value_id=not new_default_mode,
        )

    # Current IDs may contain duplicates across devices because StartupCAN
    # processes one device at a time.
    if device_current:
        _assert_unique_can_fields(
            "devices.config.current.ids",
            device_current,
            strict_numbers=False,
            require_value_id=False,  
        )

    # New IDs must be unique unless `new.default=true`, because in reset mode
    # the target endpoint is the shared default endpoint.
    if device_new_raw:
        _assert_unique_can_fields(
            "devices.config.new.ids",
            device_new_raw,
            strict_numbers=not new_default_mode,
            require_value_id=not new_default_mode,      
        )

    # -------------------------------------------------------------------------
    # Validate mode combinations and derive effective runtime config
    # -------------------------------------------------------------------------
    if current_default_mode and new_default_mode:
        raise ValueError(
            "Invalid configuration: current.default=true and new.default=true is not allowed."
        )
    
    # Case 1:
    # current.default = false
    # new.default     = false
    if (not current_default_mode) and (not new_default_mode):
        if not device_current:
            raise ValueError("current.default=false & new.default=false: devices.config.current.ids must not be empty.")
        
        if not device_new_raw:
            raise ValueError("current.default=false & new.default=false: devices.config.new.ids must not be empty.")
        
        if len(device_current) != len(device_new_raw):
            raise ValueError(
                "current.default=false & new.default=false: current.ids and new.ids must have the same length."
            )
        
        SN_MODE = _detect_and_validate_sn_mode(device_new_raw)

        _assert_same_dev_nos(
        "devices.config.current.ids", device_current,
        "devices.config.new.ids", device_new_raw
        )

        # Start endpoint comes from current.ids
        # Target endpoint comes from new.ids
        dev_nos_for_run = [d["dev_no"] for d in device_current]
        device_new = device_new_raw
        device_config = device_current

    # Case 2:
    # current.default = true
    # new.default     = false
    elif current_default_mode and (not new_default_mode):
        if not device_new_raw:
            raise ValueError("current.default=true & new.default=false: devices.config.new.ids must not be empty.")
        
        SN_MODE = _detect_and_validate_sn_mode(device_new_raw)

        dev_nos_for_run = [d["dev_no"] for d in device_new_raw] 

        # Target endpoint comes from new.ids
        device_new = device_new_raw

        # Start endpoint is always the default endpoint
        device_config = [
            {"dev_no": int(n), "cmd_id": default_cmd_id, "answer_id": default_ans_id, "value_id": default_value_id,}
            for n in dev_nos_for_run
        ]

    
    # Case 3:
    # current.default = false
    # new.default     = true
    else: 
        SN_MODE = False

        if not device_current:
            raise ValueError("current.default=false & new.default=true: devices.config.current.ids must not be empty.")
        
        dev_nos_for_run = [d["dev_no"] for d in device_current] 

        # Target endpoint is always the shared default endpoint
        device_new = [
            {"dev_no": int(n), "cmd_id": default_cmd_id, "answer_id": default_ans_id, "value_id": default_value_id,}
            for n in dev_nos_for_run
        ]

        # Start endpoint comes from current.ids
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
        "ASSIGN": {
            "DEFAULT_CMD_ID": default_cmd_id,
            "DEFAULT_ANS_ID": default_ans_id,
            "DEFAULT_VALUE_ID": default_value_id,
            "DEFAULT_CANBAUD": default_canbaud,
        },
    }

# -----------------------------------------------------------------------------
# Runtime path helpers
# -----------------------------------------------------------------------------
def _project_root() -> Path:
    """
    Determine the runtime project root directory.

    Behavior:
    - In development mode:
      use the project root above the source tree
    - In PyInstaller mode:
      use the directory next to the executable

    Returns:
        Path to the runtime project root.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    
    return Path(__file__).resolve().parents[2]

# -----------------------------------------------------------------------------
# Load configuration once at import time
# -----------------------------------------------------------------------------
PROJECT_ROOT = _project_root()

# Default configuration file read by StartupCAN
CONFIG_PATH = PROJECT_ROOT / "config.yaml"

# DLL path:
# - in PyInstaller onefile mode, DLL is extracted into _MEIPASS
# - otherwise it is expected next to the project root
if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
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
DEFAULT_VALUE_ID = ASSIGN["DEFAULT_VALUE_ID"]
DEFAULT_CANBAUD = ASSIGN["DEFAULT_CANBAUD"]
