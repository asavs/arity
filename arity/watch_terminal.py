"""Pure, blind-safe terminal rendering for watch snapshots and follow frames."""

from __future__ import annotations

import time
import unicodedata
from dataclasses import dataclass
from typing import Optional

from .watch_view_model import BoundedCount, WatchIssue, WatchTrial, WatchViewModel


UNKNOWN_READ_TIME = "??:??:??"

FOLLOW_ERROR_CODES = frozenset(
    {
        "record_read_error",
        "record_store_changed",
        "record_store_corrupt",
        "trial_not_found",
        "watch_render_error",
        "watch_terminal_error",
    }
)


@dataclass(frozen=True)
class TerminalCapabilities:
    """Independent presentation capabilities for one interactive frame."""

    width: int = 80
    ascii: bool = False
    motion: bool = True
    color: bool = True

    def __post_init__(self) -> None:
        if type(self.width) is not int or self.width < 1:
            raise ValueError("terminal width must be a positive integer")
        for value in (self.ascii, self.motion, self.color):
            if type(value) is not bool:
                raise TypeError("terminal capabilities must be booleans")


def _default_read_time(read_at: float) -> str:
    try:
        return time.strftime("%H:%M:%S", time.localtime(read_at))
    except (OverflowError, OSError, ValueError):
        return UNKNOWN_READ_TIME


def _read_time(read_at: float) -> str:
    rendered = _default_read_time(read_at)
    if type(rendered) is not str:
        raise TypeError("read time must be a plain string")
    if rendered == UNKNOWN_READ_TIME:
        return rendered
    if len(rendered) != 8:
        raise ValueError("read time must use HH:MM:SS")
    if rendered[2] != ":" or rendered[5] != ":":
        raise ValueError("read time must use HH:MM:SS")
    digits = rendered[0:2] + rendered[3:5] + rendered[6:8]
    if not digits.isascii() or not digits.isdigit():
        raise ValueError("read time must use HH:MM:SS")
    if int(rendered[0:2]) > 23 or int(rendered[3:5]) > 59 or int(rendered[6:8]) > 59:
        raise ValueError("read time must use HH:MM:SS")
    return rendered


def _count(value: BoundedCount) -> str:
    suffix = " (more omitted)" if value.more_omitted else ""
    return f"{value.value}{suffix}"


def _issue_lines(prefix: str, issue: WatchIssue) -> list[str]:
    return [
        f"{prefix}issue {issue.code}",
        f"{prefix}  {issue.message}",
    ]


def _trial_row(trial: WatchTrial) -> str:
    marker = ">" if trial.selected else " "
    if trial.detail is None:
        completion = "details unavailable"
    else:
        completion = (
            f"completions {_count(trial.detail.completed_agents)}/"
            f"{_count(trial.detail.arms)}"
        )
    return (
        f"{marker} {trial.label} | {trial.lifecycle} | "
        f"{trial.integrity} | {completion}"
    )


def _selected_lines(trial: WatchTrial) -> list[str]:
    if trial.detail is None:
        return [f"selected: {trial.label} | details unavailable"]

    detail = trial.detail
    lines = [f"selected: {trial.label}"]
    lines.append(
        "  "
        f"evidence {_count(detail.evidence)} | "
        f"reviews {_count(detail.reviews)} | "
        f"resolutions {_count(detail.resolutions)} | "
        f"delivery {'yes' if detail.delivery_recorded else 'no'}"
    )
    return lines


def _selected_agent_lines(trial: WatchTrial) -> list[str]:
    detail = trial.detail
    if detail is None:
        return []
    lines = [f"    {agent.label} | {agent.status}" for agent in detail.agents]
    if detail.arms.more_omitted:
        lines.append("    more agents omitted")
    return lines


def _selected_observation_lines(trial: WatchTrial) -> list[str]:
    detail = trial.detail
    if detail is None:
        return []
    return [
        "  observations "
        f"mechanical {_count(detail.mechanical_observations)} | "
        f"model {_count(detail.model_observations)} | "
        f"human {_count(detail.human_observations)}"
    ]


def _validate_frame(frame: str) -> None:
    if not frame.endswith("\n") or frame.endswith("\n\n"):
        raise RuntimeError("watch frame must end with one newline")
    if not frame.isascii() or "\x1b" in frame:
        raise RuntimeError("watch frame must be printable ASCII")
    for character in frame:
        if character != "\n" and not 0x20 <= ord(character) <= 0x7E:
            raise RuntimeError("watch frame must be printable ASCII")


def render_watch_snapshot(model: WatchViewModel) -> str:
    """Render one complete, printable-ASCII snapshot from an exact view model."""

    if type(model) is not WatchViewModel:
        raise TypeError("model must be an exact WatchViewModel")

    if not model.trials and not model.catalog_issues and not model.more_trials_omitted:
        return "No persisted trials.\n"

    read_time = _read_time(model.read_at)
    trial_word = "trial" if len(model.trials) == 1 else "trials"
    trial_count = f"{len(model.trials)} {trial_word}"

    lines = [f"arity watch | {model.backend} | {trial_count} | read {read_time}"]
    selected_trial: WatchTrial | None = None
    for trial in model.trials:
        lines.append(_trial_row(trial))
        if trial.selected:
            lines.extend(_selected_agent_lines(trial))
        if trial.issue is not None:
            lines.extend(_issue_lines("    ", trial.issue))
        if trial.selected:
            selected_trial = trial

    if model.more_trials_omitted:
        lines.append("  more trials omitted")

    for issue in model.catalog_issues:
        lines.extend(_issue_lines("  ", issue))

    if model.selected_trial_omitted:
        lines.append("selected: omitted trial | details unavailable")
    elif model.selected_trial_number is not None:
        if selected_trial is None:
            raise RuntimeError("visible selection is missing its trial row")
        else:
            lines.extend(_selected_lines(selected_trial))

    frame = "\n".join(lines) + "\n"
    _validate_frame(frame)
    return frame


def _cell_width(character: str) -> int:
    if unicodedata.combining(character):
        return 0
    if unicodedata.east_asian_width(character) in {"F", "W"}:
        return 2
    return 1


def _fit_line(value: str, width: int) -> str:
    """Crop a trusted presentation string to a terminal's visible cell width."""

    cells = 0
    fitted: list[str] = []
    for character in value:
        character_width = _cell_width(character)
        if cells + character_width > width:
            break
        fitted.append(character)
        cells += character_width
    return "".join(fitted)


def _follow_trial_row(trial: WatchTrial) -> str:
    marker = ">" if trial.selected else " "
    if trial.detail is None:
        completion = "details unavailable"
    else:
        completion = (
            f"completions {_count(trial.detail.completed_agents)}/"
            f"{_count(trial.detail.arms)}"
        )
    return (
        f"{marker} {trial.label} | {trial.lifecycle} | "
        f"{trial.integrity} | {completion}"
    )


def _pulse_line(capabilities: TerminalCapabilities, phase: int) -> str:
    if type(phase) is not int or phase < 0:
        raise ValueError("pulse phase must be a non-negative integer")
    if not capabilities.motion:
        marker = "*" if capabilities.ascii else "●"
    elif capabilities.ascii:
        marker = (". o * @ * o .", "  o * @ * o", "    * @ *")[phase % 3]
    else:
        marker = (
            "· ○ ✦ ● ✦ ○ ·",
            "  ○ ✦ ● ✦ ○",
            "    ✦ ● ✦",
        )[phase % 3]
    return f"{marker} journal update"


def render_watch_follow_frame(
    model: Optional[WatchViewModel],
    capabilities: TerminalCapabilities,
    *,
    expanded: bool = False,
    help_visible: bool = False,
    error_code: Optional[str] = None,
    pulse_phase: Optional[int] = None,
) -> str:
    """Render one width-bounded live frame from the blind-safe model only."""

    if model is not None and type(model) is not WatchViewModel:
        raise TypeError("model must be an exact WatchViewModel or None")
    if type(capabilities) is not TerminalCapabilities:
        raise TypeError("capabilities must be exact TerminalCapabilities")
    if type(expanded) is not bool or type(help_visible) is not bool:
        raise TypeError("follow presentation flags must be booleans")
    if error_code is not None and error_code not in FOLLOW_ERROR_CODES:
        raise ValueError("unsupported follow error code")
    if pulse_phase is not None and (type(pulse_phase) is not int or pulse_phase < 0):
        raise ValueError("pulse_phase must be a non-negative integer or None")

    if model is None:
        title = "arity watch" if capabilities.ascii else "arity watch ·"
        lines = [f"{title} | snapshot unavailable"]
    else:
        read_time = _read_time(model.read_at)
        trial_word = "trial" if len(model.trials) == 1 else "trials"
        title = "arity watch" if capabilities.ascii else "arity watch ·"
        lines = [
            f"{title} | {model.backend} | {len(model.trials)} {trial_word} | "
            f"read {read_time}"
        ]

    if pulse_phase is not None:
        lines.append(_pulse_line(capabilities, pulse_phase))
    if error_code is not None:
        prefix = "last good snapshot | " if model is not None else ""
        lines.append(f"{prefix}watch error: {error_code}")

    if model is not None:
        if not model.trials and not model.catalog_issues:
            lines.append("No persisted trials.")
        selected: WatchTrial | None = None
        for trial in model.trials:
            lines.append(_follow_trial_row(trial))
            if trial.issue is not None:
                lines.extend(_issue_lines("    ", trial.issue))
            if trial.selected:
                selected = trial

        if model.more_trials_omitted:
            lines.append("  more trials omitted")
        for issue in model.catalog_issues:
            lines.extend(_issue_lines("  ", issue))

        if model.selected_trial_omitted:
            lines.append("selected: omitted trial | details unavailable")
        elif selected is not None:
            lines.extend(_selected_lines(selected))
            if expanded:
                lines.extend(_selected_agent_lines(selected))
                lines.extend(_selected_observation_lines(selected))

    if help_visible:
        lines.extend(
            (
                "j/k or down/up select | Enter expand/collapse",
                "r retry/refresh | ? close help | q quit",
            )
        )
    else:
        lines.append("[j/k] select  [Enter] expand  [r] retry  [?] help  [q] quit")

    fitted = [_fit_line(line, capabilities.width) for line in lines]
    if capabilities.color and fitted:
        fitted[0] = f"\x1b[1m{fitted[0]}\x1b[0m"
        for index, line in enumerate(fitted):
            if line.startswith("> "):
                fitted[index] = f"\x1b[36m{line}\x1b[0m"
            elif "watch error:" in line:
                fitted[index] = f"\x1b[31m{line}\x1b[0m"
    return "\n".join(fitted) + "\n"


__all__ = [
    "FOLLOW_ERROR_CODES",
    "TerminalCapabilities",
    "render_watch_follow_frame",
    "render_watch_snapshot",
]
