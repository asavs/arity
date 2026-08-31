"""Implementation-level checks for the live watch terminal slice."""

from __future__ import annotations

import io
from argparse import Namespace
from collections.abc import Callable

import pytest

from arity.record_readers import RecordChanged
from arity.watch_cli import run_watch_command
from arity.watch_follow import TerminalSession, TerminalUnavailable
from arity.watch_terminal import TerminalCapabilities, render_watch_follow_frame
from arity.watch_view_model import (
    WatchIssue,
    WatchProjector,
    WatchTrial,
    WatchViewModel,
)


RAW_ONE = "raw-one-DO-NOT-RENDER"
RAW_TWO = "raw-two-DO-NOT-RENDER"
RAW_THREE = "raw-three-DO-NOT-RENDER"


def _args(*, follow: bool = True, ascii: bool = True, no_motion: bool = True) -> Namespace:
    return Namespace(
        trial_id=None,
        ascii=ascii,
        no_motion=no_motion,
        follow=follow,
    )


def _empty_model(read_at: float = 1.0) -> WatchViewModel:
    return WatchViewModel(
        backend="jsonl",
        read_at=float(read_at),
        trials=(),
        more_trials_omitted=False,
        catalog_integrity="valid",
    )


def _model(
    projector: WatchProjector,
    raw_order: tuple[str, ...],
    selected_trial_id: str | None,
    *,
    read_at: float,
) -> WatchViewModel:
    projector._label_registry.assign(raw_order)
    rows = tuple(
        WatchTrial(
            trial_number=projector._label_registry.number_for(raw_id),
            integrity="corrupt",
            lifecycle="unknown",
            detail=None,
            issue=WatchIssue("inspection_incomplete"),
            selected=raw_id == selected_trial_id,
        )
        for raw_id in raw_order
    )
    selected = next((trial.trial_number for trial in rows if trial.selected), None)
    return WatchViewModel(
        backend="jsonl",
        read_at=float(read_at),
        trials=rows,
        more_trials_omitted=False,
        catalog_integrity="corrupt",
        selected_trial_number=selected,
        requested_trial_missing=(
            selected_trial_id is not None and selected_trial_id not in raw_order
        ),
    )


class FakeTerminal:
    def __init__(
        self,
        keys: list[str | None | BaseException],
        *,
        stdin_tty: bool = True,
        stdout_tty: bool = True,
        interactive: bool = True,
        width: object = 80,
        setup_error: BaseException | None = None,
        draw_error: BaseException | None = None,
    ) -> None:
        self.keys = list(keys)
        self.stdin_tty = stdin_tty
        self.stdout_tty = stdout_tty
        self.interactive = interactive
        self.reported_width = width
        self.setup_error = setup_error
        self.draw_error = draw_error
        self.events: list[str] = []
        self.frames: list[str] = []
        self.timeouts: list[float] = []

    def stdin_isatty(self) -> bool:
        self.events.append("stdin_isatty")
        return self.stdin_tty

    def stdout_isatty(self) -> bool:
        self.events.append("stdout_isatty")
        return self.stdout_tty

    def supports_interactive(self) -> bool:
        self.events.append("supports_interactive")
        return self.interactive

    def width(self) -> object:
        self.events.append("width")
        return self.reported_width

    def setup(self) -> None:
        self.events.append("setup")
        if self.setup_error is not None:
            raise self.setup_error

    def restore(self) -> None:
        self.events.append("restore")

    def draw(self, frame: str) -> None:
        self.events.append("draw")
        if self.draw_error is not None:
            raise self.draw_error
        self.frames.append(frame)

    def read_key(self, timeout: float) -> str | None:
        self.events.append("read_key")
        assert timeout > 0
        self.timeouts.append(timeout)
        value = self.keys.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value


def _loader_for(
    models: list[WatchViewModel | BaseException],
    calls: list[tuple[str | None, WatchProjector]],
) -> Callable[..., WatchViewModel]:
    queue = list(models)

    def loader(
        store_spec: object = None,
        *,
        selected_trial_id: str | None,
        clock: Callable[[], float] | None,
        projector: WatchProjector | None,
        **kwargs: object,
    ) -> WatchViewModel:
        del store_spec, clock, kwargs
        assert type(projector) is WatchProjector
        calls.append((selected_trial_id, projector))
        value = queue.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value

    return loader


def test_follow_uses_injected_terminal_and_restores_on_quit() -> None:
    terminal = FakeTerminal(["q"])
    calls: list[tuple[str | None, WatchProjector]] = []

    code = run_watch_command(
        _args(),
        terminal=terminal,
        model_loader=_loader_for([_empty_model()], calls),
        stdout=io.StringIO(),
        stderr=io.StringIO(),
        environ={"NO_COLOR": "1"},
    )

    assert code == 0
    assert terminal.events[:4] == [
        "stdin_isatty",
        "stdout_isatty",
        "supports_interactive",
        "setup",
    ]
    assert terminal.events[-1] == "restore"
    assert terminal.events.count("restore") == 1
    assert terminal.frames and "No persisted trials." in terminal.frames[0]
    assert len(calls) == 1
    assert calls[0][1] is not None


def test_non_tty_follow_performs_minimal_gate_then_exact_one_shot() -> None:
    terminal = FakeTerminal([], stdin_tty=False)
    calls: list[tuple[str | None, WatchProjector]] = []
    stdout = io.StringIO()

    code = run_watch_command(
        _args(),
        terminal=terminal,
        model_loader=_loader_for([_empty_model()], calls),
        stdout=stdout,
        stderr=io.StringIO(),
    )

    assert code == 0
    assert stdout.getvalue() == "No persisted trials.\n"
    assert terminal.events == ["stdin_isatty"]
    assert len(calls) == 1


def test_setup_failure_restores_then_falls_back_without_leaking() -> None:
    terminal = FakeTerminal([], setup_error=OSError("SECRET setup detail"))
    calls: list[tuple[str | None, WatchProjector]] = []
    stdout = io.StringIO()
    stderr = io.StringIO()

    code = run_watch_command(
        _args(),
        terminal=terminal,
        model_loader=_loader_for([_empty_model()], calls),
        stdout=stdout,
        stderr=stderr,
    )

    assert code == 0
    assert stdout.getvalue() == "No persisted trials.\n"
    assert stderr.getvalue() == ""
    assert terminal.events[-2:] == ["setup", "restore"]
    assert len(calls) == 1


@pytest.mark.parametrize(
    ("stopped", "expected_code"),
    [(EOFError(), 0), (KeyboardInterrupt(), 130)],
)
def test_eof_and_interrupt_restore_cleanly(
    stopped: BaseException,
    expected_code: int,
) -> None:
    terminal = FakeTerminal([stopped])

    code = run_watch_command(
        _args(),
        terminal=terminal,
        model_loader=_loader_for([_empty_model()], []),
        stdout=io.StringIO(),
        stderr=io.StringIO(),
        environ={"NO_COLOR": "1"},
    )

    assert code == expected_code
    assert terminal.events[-1] == "restore"
    assert terminal.events.count("restore") == 1


def test_draw_failure_restores_and_emits_only_canned_code() -> None:
    terminal = FakeTerminal([], draw_error=OSError("SECRET draw detail"))
    stderr = io.StringIO()

    code = run_watch_command(
        _args(),
        terminal=terminal,
        model_loader=_loader_for([_empty_model()], []),
        stdout=io.StringIO(),
        stderr=stderr,
    )

    assert code == 1
    assert stderr.getvalue() == "arity: watch_render_error\n"
    assert "SECRET" not in stderr.getvalue()
    assert terminal.events[-1] == "restore"


def test_unchanged_fingerprint_redraw_has_no_update_cue() -> None:
    terminal = FakeTerminal([None, "q"])
    calls: list[tuple[str | None, WatchProjector]] = []

    code = run_watch_command(
        _args(no_motion=True),
        terminal=terminal,
        model_loader=_loader_for([_empty_model(1.0), _empty_model(2.0)], calls),
        stdout=io.StringIO(),
        stderr=io.StringIO(),
        monotonic=lambda: 0.0,
        environ={"NO_COLOR": "1"},
    )

    assert code == 0
    assert len(calls) == 2
    assert calls[0][1] is calls[1][1]
    assert len(terminal.frames) == 2
    assert terminal.frames[0] == terminal.frames[1]
    assert all("journal update" not in frame for frame in terminal.frames)


def test_selection_uses_private_inverse_and_never_renders_raw_ids() -> None:
    terminal = FakeTerminal(["j", "j", "q"])
    calls: list[tuple[str | None, WatchProjector]] = []

    def loader(
        store_spec: object = None,
        *,
        selected_trial_id: str | None,
        clock: Callable[[], float] | None,
        projector: WatchProjector | None,
        **kwargs: object,
    ) -> WatchViewModel:
        del store_spec, clock, kwargs
        assert type(projector) is WatchProjector
        calls.append((selected_trial_id, projector))
        return _model(projector, (RAW_ONE, RAW_TWO), selected_trial_id, read_at=1.0)

    code = run_watch_command(
        _args(),
        terminal=terminal,
        model_loader=loader,
        stdout=io.StringIO(),
        stderr=io.StringIO(),
        environ={"NO_COLOR": "1"},
    )

    rendered = "".join(terminal.frames)
    assert code == 5
    assert "> Trial 1" in terminal.frames[1]
    assert "> Trial 2" in terminal.frames[-1]
    assert RAW_ONE not in rendered
    assert RAW_TWO not in rendered
    assert calls[0][0] is None


def test_reorder_and_insertion_retain_selection_and_labels() -> None:
    terminal = FakeTerminal([None, "q"])
    calls: list[tuple[str | None, WatchProjector]] = []
    reads = 0

    def loader(
        store_spec: object = None,
        *,
        selected_trial_id: str | None,
        clock: Callable[[], float] | None,
        projector: WatchProjector | None,
        **kwargs: object,
    ) -> WatchViewModel:
        nonlocal reads
        del store_spec, clock, kwargs
        assert type(projector) is WatchProjector
        calls.append((selected_trial_id, projector))
        reads += 1
        order = (
            (RAW_ONE, RAW_TWO)
            if reads == 1
            else (RAW_THREE, RAW_TWO, RAW_ONE)
        )
        return _model(projector, order, selected_trial_id, read_at=float(reads))

    args = _args(no_motion=True)
    args.trial_id = RAW_ONE
    code = run_watch_command(
        args,
        terminal=terminal,
        model_loader=loader,
        stdout=io.StringIO(),
        stderr=io.StringIO(),
        monotonic=lambda: 0.0,
        environ={"NO_COLOR": "1"},
    )

    assert code == 5
    assert calls[0][1] is calls[1][1]
    assert calls[1][0] == RAW_ONE
    assert "> Trial 1" in terminal.frames[-1]
    assert "Trial 3" in terminal.frames[-1]
    assert "journal update" in terminal.frames[-1]
    assert not any(raw_id in "".join(terminal.frames) for raw_id in (RAW_ONE, RAW_TWO, RAW_THREE))


def test_read_error_keeps_last_good_frame_and_only_canned_error() -> None:
    terminal = FakeTerminal([None, "q"])
    calls: list[tuple[str | None, WatchProjector]] = []
    projector = WatchProjector()
    first = _model(projector, (RAW_ONE,), None, read_at=1.0)
    failure = RecordChanged("SECRET journal path")

    code = run_watch_command(
        _args(no_motion=True),
        terminal=terminal,
        model_loader=_loader_for([first, failure], calls),
        stdout=io.StringIO(),
        stderr=io.StringIO(),
        monotonic=lambda: 0.0,
        environ={"NO_COLOR": "1"},
    )

    assert code == 1
    assert "Trial 1" in terminal.frames[-1]
    assert "watch error: record_store_changed" in terminal.frames[-1]
    assert "SECRET" not in "".join(terminal.frames)
    assert RAW_ONE not in "".join(terminal.frames)


def test_follow_renderer_bounds_cells_and_capabilities_independently() -> None:
    model = _empty_model()
    frame = render_watch_follow_frame(
        model,
        TerminalCapabilities(width=11, ascii=True, motion=False, color=False),
        help_visible=True,
        pulse_phase=0,
    )

    assert frame.isascii()
    assert "\x1b" not in frame
    assert all(len(line) <= 11 for line in frame.splitlines())
    assert "* journal u" in frame


class _TTYText(io.StringIO):
    @property
    def encoding(self) -> str:
        return "utf-8"

    def isatty(self) -> bool:
        return True

    def fileno(self) -> int:
        return 7


class _Backend:
    def __init__(self) -> None:
        self.events: list[str] = []

    def enter(self) -> None:
        self.events.append("enter")

    def restore(self) -> None:
        self.events.append("restore")

    def read_key(self, timeout: float) -> str:
        del timeout
        return "q"


class _FailingControlText(_TTYText):
    def __init__(self, *failed_writes: int) -> None:
        super().__init__()
        self._failed_writes = set(failed_writes)
        self._write_calls = 0

    def write(self, value: str) -> int:
        self._write_calls += 1
        if self._write_calls in self._failed_writes:
            raise RuntimeError("injected terminal write failure")
        return super().write(value)


def test_real_session_restores_backend_cursor_and_alt_screen_once() -> None:
    backend = _Backend()
    output = _TTYText()
    session = TerminalSession(
        _TTYText(),
        output,
        backend=backend,
        width_getter=lambda: 40,
        environ={"NO_COLOR": "1"},
    )

    with session:
        session.draw("frame\n")
        session.draw("frame\n")
    session.close()

    transport = output.getvalue()
    assert backend.events == ["enter", "restore"]
    assert transport.count("\x1b[?25h") == 1
    assert transport.count("\x1b[?1049l") == 1
    assert transport.count("\x1b[H\x1b[2J") == 1
    assert transport.index("\x1b[?25h") < transport.index("\x1b[?1049l")


def test_real_session_setup_failure_continues_every_registered_restore() -> None:
    backend = _Backend()
    output = _FailingControlText(2, 3)
    session = TerminalSession(
        _TTYText(),
        output,
        backend=backend,
        width_getter=lambda: 40,
        environ={"NO_COLOR": "1"},
    )

    with pytest.raises(TerminalUnavailable):
        session.__enter__()
    session.close()

    assert backend.events == ["enter", "restore"]
    assert "\x1b[?1049l" in output.getvalue()
