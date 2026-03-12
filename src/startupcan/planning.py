from startupcan.models import RunConfig, DevicePlan

from startupcan.config import (
    DEVICE_CONFIG,
    DEVICE_NEW,
    CURRENT_DEFAULT_MODE,
    NEW_DEFAULT_MODE,
    SN_MODE,
    DEFAULT_CMD_ID,
    DEFAULT_ANS_ID,
    DEFAULT_CANBAUD,
    CANBAUD,
)

from startupcan.ui import fmt_can_id


def _build_run_config() -> RunConfig:
    if CURRENT_DEFAULT_MODE:
        return RunConfig(
            intro_lines=[
                "",
                "WICHTIG:",
                f"- Default IDs: CMD={fmt_can_id(DEFAULT_CMD_ID)} ANS={fmt_can_id(DEFAULT_ANS_ID)}",
                "- Schließe IMMER nur EINEN Messverstärker gleichzeitig an (sonst CAN-Kollisionen).",
                "- Ziel-IDs werden aus devices.config.new.ids übernommen.",
            ],
            continue_prompt="[WIZARD] Nächstes Gerät umstellen? [j/N]: ",
            base_current_ids=_baseline_current_for_case2_with_baud(),
            success_message="[INFO] Alle Geräte umgestellt ⇒ dürfen jetzt gleichzeitig an den Bus (IDs eindeutig).",
            warning_message=(
                "[WARN] Nicht alle Geräte umgestellt. Erst config.updated.yaml prüfen, "
                "bevor alle gleichzeitig an den Bus kommen. "
                "(Keine doppelten CAN IDs und keine unknown:true Einträge.)"
            ),
            resolve_target_after_activate=True,
            validate_expected_serial=False,
        )

    if NEW_DEFAULT_MODE:
        return RunConfig(
            intro_lines=[
                "",
                "[INFO] Forced-Reset Wizard: current.default=false & new.default=true",
                "[INFO] Ziel ist Rücksetzen auf Default IDs.",
                "[INFO] Wir stellen jetzt JEWEILS EIN Gerät auf DEFAULT und nehmen es danach ab.",
                f"- Default IDs: CMD={fmt_can_id(DEFAULT_CMD_ID)} ANS={fmt_can_id(DEFAULT_ANS_ID)}",
            ],
            continue_prompt="[WIZARD] Nächstes Gerät auf DEFAULT setzen? [j/N]: ",
            base_current_ids=DEVICE_CONFIG or [],
            success_message=(
                "⚠️  HINWEIS: Alle Geräte sind jetzt auf DEFAULT IDs.\n"
                "Alle Geräte haben dieselbe CAN-ID (Kollision / Bus-Off möglich)."
                "   => NICHT gleichzeitig am Bus betreiben/aktivieren."
                "   => Geräte nur EINZELN anschließen und aktivieren.\n"
            ),
            warning_message=(
                "[WARN] Nicht alle Geräte wurden erfolgreich auf DEFAULT gesetzt. "
                "Prüfe zuerst config.updated.yaml. "
                "Bus-Betrieb nur mit den dort eingetragenen IDs."
            ),
            resolve_target_after_activate=False,
            validate_expected_serial=True,
        )

    return RunConfig(
        intro_lines=[
            "[INFO] new.default=false: Ziel-IDs aus devices.config.new.ids.",
        ],
        continue_prompt="[WIZARD] Nächstes Gerät bearbeiten? [j/N]: ",
        base_current_ids=DEVICE_CONFIG or [],
        success_message="\n[INFO] new.default=false: Geräte dürfen gleichzeitig am Bus sein (IDs eindeutig).",
        warning_message=(
            "[WARN] Nicht alle Devices erfolgreich. YAML enthält Ist-Stand (teils alte IDs). "
            "Prüfe zunächst die YAML bevor alle Geräte gleichzeitig am Bus angeschlossen werden. "
            "(Keine doppelten CAN IDs oder unknown: true!)"
        ),
        resolve_target_after_activate=True,
        validate_expected_serial=True,
    )

def _build_device_plan(d: dict) -> tuple[DevicePlan, int | None]:
    dev_no = int(d["dev_no"])
    expected_sn = d.get("serial") if isinstance(d, dict) else None

    if CURRENT_DEFAULT_MODE:
        cmd_old = DEFAULT_CMD_ID
        ans_old = DEFAULT_ANS_ID
        baud_old = DEFAULT_CANBAUD
    else:
        cmd_old = int(d["cmd_id"])
        ans_old = int(d["answer_id"])
        baud_old = _current_canbaud_for(dev_no) or CANBAUD

    if NEW_DEFAULT_MODE and not CURRENT_DEFAULT_MODE:
        return (
            DevicePlan(
                dev_no=dev_no,
                cmd_old=cmd_old,
                ans_old=ans_old,
                baud_old=baud_old,
                cmd_new=DEFAULT_CMD_ID,
                ans_new=DEFAULT_ANS_ID,
                baud_new=DEFAULT_CANBAUD,
            ),
            expected_sn,
        )

    if SN_MODE:
        return (
            DevicePlan(
                dev_no=dev_no,
                cmd_old=cmd_old,
                ans_old=ans_old,
                baud_old=baud_old,
                cmd_new=None,
                ans_new=None,
                baud_new=CANBAUD,
            ),
            expected_sn,
        )

    target_cmd, target_ans = _new_ids_for(dev_no)
    return (
        DevicePlan(
            dev_no=dev_no,
            cmd_old=cmd_old,
            ans_old=ans_old,
            baud_old=baud_old,
            cmd_new=target_cmd,
            ans_new=target_ans,
            baud_new=CANBAUD,
        ),
        expected_sn,
    )

def _new_ids_for(dev_no: int) -> tuple[int, int]:
    for d in DEVICE_NEW:
        if int(d["dev_no"]) == int(dev_no):
            return int(d["cmd_id"]), int(d["answer_id"])
    raise KeyError(f"DEV {dev_no}: keine Ziel-IDs in devices.config.new gefunden")

def _new_ids_for_serial(serial: int) -> tuple[int, int]:
    """
    Sucht in DEVICE_NEW einen Eintrag mit passender Seriennummer.
    """
    for d in DEVICE_NEW:
        if "serial" in d and int(d["serial"]) == int(serial):
            return int(d["cmd_id"]), int(d["answer_id"])
    raise KeyError(f"Keine new.ids Zuordnung für SN={serial} gefunden")

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

def _current_canbaud_for(dev_no: int) -> int | None:
    for d in (DEVICE_CONFIG or []):
        if int(d.get("dev_no")) == int(dev_no):
            cb = d.get("canbaud")
            return int(cb) if cb is not None else None
    return None

def _baseline_current_for_case2_with_baud() -> list[dict]:
    """
    Case 2 (Wizard): current.ids soll ALLE Geräte enthalten, die in new.ids vorkommen:
    - noch nicht bearbeitet: DEFAULT IDs, ohne serial
    - bearbeitet: kommt später über _merge_current_ids(updated_subset) rein (inkl. serial falls gemessen)
    """
    baseline: list[dict] = []
    for d in (DEVICE_NEW or []):  # wichtig: Quelle ist new.ids
        baseline.append({
            "dev_no": int(d["dev_no"]),
            "cmd_id": int(DEFAULT_CMD_ID),
            "answer_id": int(DEFAULT_ANS_ID),
            "canbaud": int(DEFAULT_CANBAUD),
        })
    baseline.sort(key=lambda x: int(x["dev_no"]))
    return baseline

