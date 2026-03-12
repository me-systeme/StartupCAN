from dataclasses import dataclass

@dataclass(frozen=True)
class RunConfig:
    intro_lines: list[str]
    continue_prompt: str
    base_current_ids: list[dict]
    success_message: str
    warning_message: str
    resolve_target_after_activate: bool
    validate_expected_serial: bool

@dataclass(frozen=True)
class DevicePlan:
    dev_no: int
    cmd_old: int
    ans_old: int
    baud_old: int
    cmd_new: int | None
    ans_new: int | None
    baud_new: int

    def with_new_ids(self, cmd_new: int, ans_new: int) -> "DevicePlan":
        return DevicePlan(
            dev_no=self.dev_no,
            cmd_old=self.cmd_old,
            ans_old=self.ans_old,
            baud_old=self.baud_old,
            cmd_new=int(cmd_new),
            ans_new=int(ans_new),
            baud_new=self.baud_new,
        )
    def with_safe_new_ids(self) -> "DevicePlan":
        return DevicePlan(
            dev_no=self.dev_no,
            cmd_old=self.cmd_old,
            ans_old=self.ans_old,
            baud_old=self.baud_old,
            cmd_new=int(self.cmd_new) if self.cmd_new is not None else int(self.cmd_old),
            ans_new=int(self.ans_new) if self.ans_new is not None else int(self.ans_old),
            baud_new=self.baud_new,
        )