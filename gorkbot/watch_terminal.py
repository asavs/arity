"""Pure, blind-safe terminal rendering for one-shot watch snapshots."""

from __future__ import annotations

import time
from collections.abc import Callable

from .watch_view_model import BoundedCount, WatchIssue, WatchTrial, WatchViewModel


ReadTimeFormatter = Callable[[float], str]
UNKNOWN_READ_TIME = "??:??:??"


def _default_read_time(read_at: float) -> str:
    try:
        return time.strftime("%H:%M:%S", time.localtime(read_at))
    except (OverflowError, OSError, ValueError):
        return UNKNOWN_READ_TIME


def _validated_read_time(read_at: float, formatter: ReadTimeFormatter) -> str:
    rendered = formatter(read_at)
    if rendered == UNKNOWN_READ_TIME:
        return rendered
    if type(rendered) is not str or len(rendered) != 8:
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


def _validate_frame(frame: str) -> None:
    if not frame.endswith("\n") or frame.endswith("\n\n"):
        raise RuntimeError("watch frame must end with one newline")
    if not frame.isascii() or "\x1b" in frame:
        raise RuntimeError("watch frame must be printable ASCII")
    for character in frame:
        if character != "\n" and not 0x20 <= ord(character) <= 0x7E:
            raise RuntimeError("watch frame must be printable ASCII")


def render_watch_snapshot(
    model: WatchViewModel,
    *,
    format_read_time: ReadTimeFormatter | None = None,
) -> str:
    """Render one complete, printable-ASCII snapshot from an exact view model."""

    if type(model) is not WatchViewModel:
        raise TypeError("model must be an exact WatchViewModel")

    if not model.trials and not model.catalog_issues and not model.more_trials_omitted:
        return "No persisted trials.\n"

    formatter = format_read_time or _default_read_time
    read_time = _validated_read_time(model.read_at, formatter)
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

    if model.selected_trial_number is not None:
        if selected_trial is None:
            lines.append(
                f"selected: Trial {model.selected_trial_number} | details unavailable"
            )
        else:
            lines.extend(_selected_lines(selected_trial))

    frame = "\n".join(lines) + "\n"
    _validate_frame(frame)
    return frame


__all__ = ["ReadTimeFormatter", "render_watch_snapshot"]
