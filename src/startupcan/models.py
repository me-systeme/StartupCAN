"""
models.py

Data models used by StartupCAN.

This module defines the immutable configuration objects shared across the
application:

- RunConfig:
    High-level settings for one complete StartupCAN run
- DevicePlan:
    Per-device start/target CAN settings used during processing

Both dataclasses are frozen to make the workflow more predictable and to avoid
accidental in-place mutation.
"""

from dataclasses import dataclass

@dataclass(frozen=True)
class RunConfig:
    """
    High-level runtime configuration for one StartupCAN execution mode.

    Attributes:
        intro_lines:
            Informational lines printed before device processing starts.

        continue_prompt:
            Prompt shown after each processed device to decide whether the next
            device should be handled.

        base_current_ids:
            Base list used when generating config.updated.yaml.
            This represents the starting point that is merged with the detected
            per-device results.

        success_message:
            Final message printed when all processed devices completed
            successfully.

        warning_message:
            Final message printed when at least one device failed or the run was
            only partially successful.

        resolve_target_after_activate:
            If True, target IDs may be resolved only after activation, for
            example in serial-based mapping mode.

        validate_expected_serial:
            If True, the serial number read from the device is checked against
            the expected serial number from the YAML configuration.
    """
    intro_lines: list[str]
    continue_prompt: str
    base_current_ids: list[dict]
    success_message: str
    warning_message: str
    resolve_target_after_activate: bool
    validate_expected_serial: bool

@dataclass(frozen=True)
class DevicePlan:
    """
    Per-device execution plan.

    A DevicePlan contains the full start and target endpoint information for one
    device:

    - old/current/default endpoint:
        cmd_old, ans_old, baud_old

    - new/target/default endpoint:
        cmd_new, ans_new, baud_new

    - value_old / value_new:
        CAN VALUE ID (CV ID) used for cyclic value transmission.
        May be None if not known at planning time (e.g. SN_MODE before resolution).

    Attributes:
        dev_no:
            Logical device number from the YAML configuration.

        cmd_old:
            Starting command CAN ID used to reach the device.

        ans_old:
            Starting answer CAN ID used to reach the device.

        baud_old:
            Starting CAN baudrate used to reach the device.

        cmd_new:
            Target command CAN ID to be written to the device.
            May be None if the target must first be resolved dynamically
            (for example via serial mapping).

        ans_new:
            Target answer CAN ID to be written to the device.
            May be None if the target must first be resolved dynamically.

        baud_new:
            Target CAN baudrate to be written to the device.
    """
    dev_no: int
    cmd_old: int
    ans_old: int
    value_old: int | None
    baud_old: int
    cmd_new: int | None
    ans_new: int | None
    value_new: int | None
    baud_new: int

    def with_new_ids(self, cmd_new: int, ans_new: int, value_new: int) -> "DevicePlan":
        """
        Return a copy of this plan with resolved target CAN IDs.

        This is mainly used when the target IDs are determined only after
        activation, for example in serial-based mapping mode.
        """
        return DevicePlan(
            dev_no=self.dev_no,
            cmd_old=self.cmd_old,
            ans_old=self.ans_old,
            value_old=self.value_old,
            baud_old=self.baud_old,
            cmd_new=int(cmd_new),
            ans_new=int(ans_new),
            value_new=int(value_new),
            baud_new=self.baud_new,
        )
    def with_safe_new_ids(self) -> "DevicePlan":
        """
        Return a copy of this plan with guaranteed non-None target IDs.

        This helper is only meant for safe failure handling paths such as:
        - result recording
        - fallback probing
        - interruption/error handling

        It does not imply that these fallback IDs are confirmed to be active on the device.

        These values are used as best-effort placeholders for:
        - result recording
        - state probing
        - unknown-state handling

        They should not be interpreted as verified device configuration.
        """
        return DevicePlan(
            dev_no=self.dev_no,
            cmd_old=self.cmd_old,
            ans_old=self.ans_old,
            value_old=self.value_old,
            baud_old=self.baud_old,
            cmd_new=int(self.cmd_new) if self.cmd_new is not None else int(self.cmd_old),
            ans_new=int(self.ans_new) if self.ans_new is not None else int(self.ans_old),
            value_new=(
                int(self.value_new)
                if self.value_new is not None
                else (
                    int(self.value_old)
                    if self.value_old is not None
                    else int(self.ans_old)
                )
            ),
            baud_new=self.baud_new,
        )