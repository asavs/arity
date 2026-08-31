"""Injected, zero-dependency terminal controller for ``arity watch --follow``.

This module is a presentation client of the blind-safe watch model.  It does not
import provider, runtime, authentication, tool, or writable-store modules.
"""

from __future__ import annotations

import math
import os
import signal
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import Optional, TextIO

from .record_readers import RecordChanged, RecordCorruption, RecordReadError
from .watch_terminal import TerminalCapabilities, render_watch_follow_frame
from .watch_view_model import WatchProjector, WatchTrial, WatchViewModel


EXIT_OK = 0
EXIT_OPERATIONAL = 1
EXIT_NOT_FOUND = 3
EXIT_PARTIAL = 4
EXIT_CORRUPT = 5
EXIT_INTERRUPT = 130

MAX_TERMINAL_WIDTH = 256
DEFAULT_REFRESH_INTERVAL = 1.0

_ENTER_ALT_SCREEN = "\x1b[?1049h"
_LEAVE_ALT_SCREEN = "\x1b[?1049l"
_HIDE_CURSOR = "\x1b[?25l"
_SHOW_CURSOR = "\x1b[?25h"
_HOME_AND_CLEAR = "\x1b[H\x1b[2J"

ModelLoader = Callable[[Optional[str]], WatchViewModel]
FollowRenderer = Callable[..., str]
MonotonicClock = Callable[[], float]


class TerminalUnavailable(RuntimeError):
    """A cleaned preflight/setup failure that permits one-shot fallback."""


class FollowRenderError(RuntimeError):
    """A live frame could not be rendered or drawn safely."""


class FollowTerminalError(RuntimeError):
    """An active terminal could no longer provide input safely."""


def _stream_fileno(stream: TextIO) -> int:
    try:
        file_descriptor = stream.fileno()
    except (AttributeError, OSError, ValueError) as error:
        raise TerminalUnavailable("terminal stream has no usable descriptor") from error
    if type(file_descriptor) is not int or file_descriptor < 0:
        raise TerminalUnavailable("terminal stream has no usable descriptor")
    return file_descriptor


def _is_tty(stream: TextIO) -> bool:
    try:
        return stream.isatty() is True
    except (AttributeError, OSError, ValueError):
        return False


def supports_follow_terminal(stdin: TextIO, stdout: TextIO) -> bool:
    """Return whether both terminal directions pass the non-mutating TTY gate."""

    return _is_tty(stdin) and _is_tty(stdout)


def _boolean_terminal_probe(terminal: object, name: str) -> bool:
    try:
        probe = getattr(terminal, name)
        value = probe() if callable(probe) else probe
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        return False
    return value is True


def supports_injected_terminal(terminal: object) -> bool:
    """Apply the contract terminal's three minimal, non-mutating gates."""

    return (
        _boolean_terminal_probe(terminal, "stdin_isatty")
        and _boolean_terminal_probe(terminal, "stdout_isatty")
        and _boolean_terminal_probe(terminal, "supports_interactive")
    )


def _write_all(stream: TextIO, value: str, *, flush: bool = True) -> None:
    offset = 0
    while offset < len(value):
        written = stream.write(value[offset:])
        if type(written) is not int or written <= 0:
            raise OSError("incomplete terminal output")
        offset += written
    if flush:
        stream.flush()


def _supports_unicode(stream: TextIO) -> bool:
    encoding = getattr(stream, "encoding", None)
    if type(encoding) is not str or not encoding:
        return False
    try:
        "·○✦●".encode(encoding, errors="strict")
    except (LookupError, UnicodeError):
        return False
    return True


class _PosixTerminalBackend:
    def __init__(self, stdin: TextIO) -> None:
        try:
            import termios
            import tty
        except ImportError as error:  # pragma: no cover - platform dependent
            raise TerminalUnavailable("POSIX terminal support is unavailable") from error

        self._termios = termios
        self._tty = tty
        self._fd = _stream_fileno(stdin)
        self._encoding = getattr(stdin, "encoding", None) or "utf-8"
        try:
            self._original_mode = termios.tcgetattr(self._fd)
        except (OSError, termios.error) as error:
            raise TerminalUnavailable("POSIX terminal mode is unavailable") from error
        self._entered = False

    def enter(self) -> None:
        # Register restoration before the mutating call.  Restoring the captured
        # mode is safe even when the call failed before changing anything.
        self._entered = True
        self._tty.setcbreak(self._fd, self._termios.TCSANOW)

    def restore(self) -> None:
        if not self._entered:
            return
        self._entered = False
        try:
            self._termios.tcsetattr(
                self._fd,
                self._termios.TCSADRAIN,
                self._original_mode,
            )
        except (OSError, self._termios.error):
            pass

    def read_key(self, timeout: float) -> Optional[str]:
        import select

        ready, _, _ = select.select((self._fd,), (), (), timeout)
        if not ready:
            return None
        first = os.read(self._fd, 1)
        if not first:
            return ""
        encoded = first
        if first == b"\x1b":
            for _ in range(2):
                continuation, _, _ = select.select((self._fd,), (), (), 0.02)
                if not continuation:
                    break
                value = os.read(self._fd, 1)
                if not value:
                    break
                encoded += value
        return encoded.decode(self._encoding, errors="replace")


class _WindowsTerminalBackend:  # pragma: no cover - exercised on Windows consoles
    ENABLE_LINE_INPUT = 0x0002
    ENABLE_ECHO_INPUT = 0x0004
    ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004

    def __init__(self, stdin: TextIO, stdout: TextIO) -> None:
        try:
            import ctypes
            import msvcrt
            from ctypes import wintypes
        except ImportError as error:
            raise TerminalUnavailable("Windows terminal support is unavailable") from error

        self._ctypes = ctypes
        self._msvcrt = msvcrt
        self._wintypes = wintypes
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel32.GetConsoleMode.argtypes = (
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.DWORD),
        )
        self._kernel32.GetConsoleMode.restype = wintypes.BOOL
        self._kernel32.SetConsoleMode.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        self._kernel32.SetConsoleMode.restype = wintypes.BOOL
        self._input_handle = self._handle_for(stdin)
        self._output_handle = self._handle_for(stdout)
        self._original_input_mode = self._get_mode(self._input_handle)
        self._original_output_mode = self._get_mode(self._output_handle)
        self._input_changed = False
        self._output_changed = False

    def _handle_for(self, stream: TextIO) -> int:
        try:
            handle = self._msvcrt.get_osfhandle(_stream_fileno(stream))
        except OSError as error:
            raise TerminalUnavailable("Windows console handle is unavailable") from error
        if type(handle) is not int or handle in {0, -1}:
            raise TerminalUnavailable("Windows console handle is unavailable")
        return handle

    def _get_mode(self, handle: int) -> int:
        mode = self._wintypes.DWORD()
        if not self._kernel32.GetConsoleMode(handle, self._ctypes.byref(mode)):
            raise TerminalUnavailable("Windows console mode is unavailable")
        return int(mode.value)

    def _set_mode(self, handle: int, mode: int) -> None:
        if not self._kernel32.SetConsoleMode(handle, mode):
            raise OSError("could not set Windows console mode")

    def enter(self) -> None:
        input_mode = self._original_input_mode & ~(
            self.ENABLE_LINE_INPUT | self.ENABLE_ECHO_INPUT
        )
        self._input_changed = True
        self._set_mode(self._input_handle, input_mode)
        output_mode = (
            self._original_output_mode | self.ENABLE_VIRTUAL_TERMINAL_PROCESSING
        )
        self._output_changed = True
        self._set_mode(self._output_handle, output_mode)

    def restore(self) -> None:
        if self._output_changed:
            self._output_changed = False
            try:
                self._set_mode(self._output_handle, self._original_output_mode)
            except OSError:
                pass
        if self._input_changed:
            self._input_changed = False
            try:
                self._set_mode(self._input_handle, self._original_input_mode)
            except OSError:
                pass

    def read_key(self, timeout: float) -> Optional[str]:
        # A console handle is signaled by unread mouse/resize/key-up records too.
        # Poll the CRT's character predicate against a bounded deadline so those
        # records cannot create an immediate refresh/query loop or block getwch.
        deadline = time.monotonic() + timeout
        while not self._msvcrt.kbhit():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            time.sleep(min(0.01, remaining))
        character = self._msvcrt.getwch()
        if character in {"\x00", "\xe0"}:
            extended = self._msvcrt.getwch()
            if extended == "H":
                return "up"
            if extended == "P":
                return "down"
            return "ignored"
        if character == "\x1a":
            return ""
        return character


def _new_backend(stdin: TextIO, stdout: TextIO) -> object:
    if os.name == "nt":
        return _WindowsTerminalBackend(stdin, stdout)
    return _PosixTerminalBackend(stdin)


class TerminalSession:
    """Own terminal setup, input, drawing, and exact best-effort restoration."""

    def __init__(
        self,
        stdin: TextIO,
        stdout: TextIO,
        *,
        ascii: bool = False,
        no_motion: bool = False,
        environ: Optional[Mapping[str, str]] = None,
        backend: object | None = None,
        width_getter: Optional[Callable[[], int]] = None,
    ) -> None:
        if type(ascii) is not bool or type(no_motion) is not bool:
            raise TypeError("terminal presentation flags must be booleans")
        if not supports_follow_terminal(stdin, stdout):
            raise TerminalUnavailable("follow mode requires terminal input and output")
        environment = dict(os.environ if environ is None else environ)
        if os.name != "nt" and environment.get("TERM") == "dumb":
            raise TerminalUnavailable("terminal cursor control is unavailable")

        self._stdin = stdin
        self._stdout = stdout
        self._backend = backend if backend is not None else _new_backend(stdin, stdout)
        self._width_getter = width_getter
        self._ascii = ascii or not _supports_unicode(stdout)
        self._motion = not no_motion
        self._color = "NO_COLOR" not in environment
        self._backend_entered = False
        self._alt_screen = False
        self._cursor_hidden = False
        self._active = False
        self._last_frame: str | None = None

    @property
    def capabilities(self) -> TerminalCapabilities:
        try:
            if self._width_getter is not None:
                width = self._width_getter()
            else:
                width = os.get_terminal_size(_stream_fileno(self._stdout)).columns
        except (OSError, TypeError, ValueError, TerminalUnavailable):
            width = 80
        if type(width) is not int or width < 1:
            width = 80
        width = min(width, MAX_TERMINAL_WIDTH)
        return TerminalCapabilities(
            width=width,
            ascii=self._ascii,
            motion=self._motion,
            color=self._color,
        )

    def _control(self, value: str, mutation: str) -> None:
        # An inverse control is harmless if the write failed before taking effect;
        # registering first closes the interruption gap after bytes reach the TTY.
        setattr(self, mutation, True)
        _write_all(self._stdout, value, flush=False)
        self._stdout.flush()

    def __enter__(self) -> "TerminalSession":
        if self._active:
            raise RuntimeError("terminal session is already active")
        try:
            enter = getattr(self._backend, "enter")
            # The backend owns its own fine-grained mutation flags.  Register its
            # rollback before entry so a half-completed mode change is recoverable.
            self._backend_entered = True
            enter()
            self._control(_ENTER_ALT_SCREEN, "_alt_screen")
            self._control(_HIDE_CURSOR, "_cursor_hidden")
            self._last_frame = None
            self._active = True
            return self
        except KeyboardInterrupt:
            self.close()
            raise
        except BaseException as error:
            self.close()
            raise TerminalUnavailable("terminal setup failed") from error

    def close(self) -> None:
        """Restore every completed mutation once, even if an earlier restore fails."""

        self._active = False
        if self._cursor_hidden:
            self._cursor_hidden = False
            try:
                _write_all(self._stdout, _SHOW_CURSOR)
            except BaseException:
                pass
        if self._alt_screen:
            self._alt_screen = False
            try:
                _write_all(self._stdout, _LEAVE_ALT_SCREEN)
            except BaseException:
                pass
        if self._backend_entered:
            self._backend_entered = False
            try:
                restore = getattr(self._backend, "restore")
                restore()
            except BaseException:
                pass

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> bool:
        self.close()
        return False

    def draw(self, frame: str) -> None:
        if not self._active:
            raise OSError("terminal session is not active")
        if type(frame) is not str or not frame.endswith("\n"):
            raise ValueError("terminal frame must be a newline-terminated string")
        if frame == self._last_frame:
            return
        transport_frame = frame.replace("\n", "\r\n")
        _write_all(self._stdout, _HOME_AND_CLEAR + transport_frame)
        self._last_frame = frame

    def read_key(self, timeout: float) -> Optional[str]:
        if not self._active:
            raise OSError("terminal session is not active")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise TypeError("terminal timeout must be a finite number")
        timeout_value = float(timeout)
        if not math.isfinite(timeout_value) or timeout_value < 0:
            raise ValueError("terminal timeout must be finite and non-negative")
        read_key = getattr(self._backend, "read_key")
        value = read_key(timeout_value)
        if value is not None and type(value) is not str:
            raise TypeError("terminal key must be a string or None")
        return value

    def __repr__(self) -> str:
        return "TerminalSession(<terminal streams>)"


class InjectedTerminalSession:
    """Adapt the small acceptance terminal seam to the controller protocol."""

    def __init__(
        self,
        terminal: object,
        *,
        ascii: bool,
        no_motion: bool,
        environ: Optional[Mapping[str, str]] = None,
    ) -> None:
        if type(ascii) is not bool or type(no_motion) is not bool:
            raise TypeError("terminal presentation flags must be booleans")
        environment = dict(os.environ if environ is None else environ)
        self._terminal = terminal
        self._ascii = ascii
        self._motion = not no_motion
        self._color = "NO_COLOR" not in environment
        self._restore_needed = False
        self._active = False

    @property
    def capabilities(self) -> TerminalCapabilities:
        try:
            width_probe = getattr(self._terminal, "width")
            width = width_probe() if callable(width_probe) else width_probe
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
            width = 80
        if type(width) is not int or width < 1:
            width = 80
        return TerminalCapabilities(
            width=min(width, MAX_TERMINAL_WIDTH),
            ascii=self._ascii,
            motion=self._motion,
            color=self._color,
        )

    def __enter__(self) -> "InjectedTerminalSession":
        if self._active or self._restore_needed:
            raise RuntimeError("terminal session is already active")
        self._restore_needed = True
        try:
            setup = getattr(self._terminal, "setup")
            result = setup()
            if result is False:
                raise TerminalUnavailable("terminal setup was declined")
        except KeyboardInterrupt:
            self.close()
            raise
        except BaseException as error:
            self.close()
            if isinstance(error, TerminalUnavailable):
                raise
            raise TerminalUnavailable("terminal setup failed") from error
        self._active = True
        return self

    def close(self) -> None:
        self._active = False
        if not self._restore_needed:
            return
        self._restore_needed = False
        try:
            restore = getattr(self._terminal, "restore")
            restore()
        except BaseException:
            pass

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> bool:
        self.close()
        return False

    def draw(self, frame: str) -> None:
        if not self._active:
            raise OSError("terminal session is not active")
        draw = getattr(self._terminal, "draw")
        draw(frame)

    def read_key(self, timeout: float) -> Optional[str]:
        if not self._active:
            raise OSError("terminal session is not active")
        read_key = getattr(self._terminal, "read_key")
        return read_key(timeout)

    def __repr__(self) -> str:
        return "InjectedTerminalSession(<injected terminal>)"


class _SignalGuard:
    """Turn supported termination signals into the loop's cleanup path."""

    def __init__(self) -> None:
        self._previous: list[tuple[int, object]] = []

    @staticmethod
    def _interrupt(signum: int, frame: object) -> None:
        del signum, frame
        raise KeyboardInterrupt

    def __enter__(self) -> "_SignalGuard":
        candidates = [signal.SIGINT]
        if os.name == "nt":
            if hasattr(signal, "SIGBREAK"):
                candidates.append(signal.SIGBREAK)
        else:
            for name in ("SIGHUP", "SIGTERM"):
                if hasattr(signal, name):
                    candidates.append(getattr(signal, name))
        try:
            for candidate in candidates:
                previous = signal.getsignal(candidate)
                self._previous.append((candidate, previous))
                signal.signal(candidate, self._interrupt)
        except (OSError, RuntimeError, ValueError):
            self.close()
        except BaseException:
            self.close()
            raise
        return self

    def close(self) -> None:
        while self._previous:
            candidate, previous = self._previous[-1]
            try:
                signal.signal(candidate, previous)
            except BaseException:
                pass
            finally:
                self._previous.pop()

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> bool:
        self.close()
        return False


def _model_exit_code(model: Optional[WatchViewModel]) -> int:
    if model is None:
        return EXIT_OK
    if model.requested_trial_missing:
        return EXIT_NOT_FOUND
    if model.catalog_integrity == "corrupt":
        return EXIT_CORRUPT
    if model.catalog_integrity == "partial":
        return EXIT_PARTIAL
    return EXIT_OK


def _select_model(model: WatchViewModel, trial_number: int) -> WatchViewModel:
    rows = tuple(
        replace(trial, selected=trial.trial_number == trial_number)
        for trial in model.trials
    )
    if not any(trial.selected for trial in rows):
        raise KeyError("neutral trial label is not visible")
    return replace(
        model,
        trials=rows,
        selected_trial_number=trial_number,
        selected_trial_omitted=False,
        requested_trial_missing=False,
    )


def _read_failure(error: RecordReadError) -> tuple[int, str]:
    if isinstance(error, RecordCorruption):
        return EXIT_CORRUPT, RecordCorruption.code
    if isinstance(error, RecordChanged):
        return EXIT_OPERATIONAL, RecordChanged.code
    return EXIT_OPERATIONAL, RecordReadError.code


class FollowController:
    """Poll safe snapshots and manage only controller-private selection identity."""

    def __init__(
        self,
        *,
        terminal: object,
        loader: ModelLoader,
        projector: WatchProjector,
        renderer: FollowRenderer = render_watch_follow_frame,
        monotonic: MonotonicClock = time.monotonic,
        refresh_interval: float = DEFAULT_REFRESH_INTERVAL,
    ) -> None:
        if type(projector) is not WatchProjector:
            raise TypeError("projector must be an exact WatchProjector")
        if isinstance(refresh_interval, bool) or not isinstance(
            refresh_interval, (int, float)
        ):
            raise TypeError("refresh_interval must be a finite positive number")
        if not math.isfinite(float(refresh_interval)) or float(refresh_interval) <= 0:
            raise ValueError("refresh_interval must be a finite positive number")
        self._terminal = terminal
        self._loader = loader
        self._projector = projector
        self._renderer = renderer
        self._monotonic = monotonic
        self._refresh_interval = float(refresh_interval)
        self._model: WatchViewModel | None = None
        self._fingerprint: tuple[object, ...] | None = None
        self._selected_trial_id: str | None = None
        self._expanded = False
        self._help_visible = False
        self._error_code: str | None = None
        self._failure_exit: int | None = None
        self._pulse_phase: int | None = None

    def __repr__(self) -> str:
        return "FollowController(<controller-private selection>)"

    def current_exit_code(self) -> int:
        if self._failure_exit is not None:
            return self._failure_exit
        return _model_exit_code(self._model)

    def _capabilities(self) -> TerminalCapabilities:
        capabilities = getattr(self._terminal, "capabilities")
        if callable(capabilities):
            capabilities = capabilities()
        if type(capabilities) is not TerminalCapabilities:
            raise FollowTerminalError("terminal capabilities are invalid")
        return capabilities

    def _draw(self) -> None:
        try:
            frame = self._renderer(
                self._model,
                self._capabilities(),
                expanded=self._expanded,
                help_visible=self._help_visible,
                error_code=self._error_code,
                pulse_phase=self._pulse_phase,
            )
            if type(frame) is not str:
                raise TypeError("follow renderer must return text")
            draw = getattr(self._terminal, "draw")
            draw(frame)
        except FollowTerminalError:
            raise
        except KeyboardInterrupt:
            raise
        except BaseException as error:
            raise FollowRenderError("follow frame failed") from error

    def _establish_selection(self, model: WatchViewModel, trial: WatchTrial) -> WatchViewModel:
        trial_id = self._projector._trial_id_for_number(trial.trial_number)
        self._selected_trial_id = trial_id
        return _select_model(model, trial.trial_number)

    def _refresh(self, *, force: bool) -> None:
        previous_error = self._error_code
        try:
            model = self._loader(self._selected_trial_id)
            if type(model) is not WatchViewModel:
                raise TypeError("follow loader must return a WatchViewModel")
        except RecordReadError as error:
            self._failure_exit, self._error_code = _read_failure(error)
            if force or self._model is None or self._error_code != previous_error:
                self._pulse_phase = None
                self._draw()
            return
        except KeyboardInterrupt:
            raise
        except BaseException:
            self._failure_exit = EXIT_OPERATIONAL
            self._error_code = RecordReadError.code
            if force or self._model is None or self._error_code != previous_error:
                self._pulse_phase = None
                self._draw()
            return

        requested_missing = model.requested_trial_missing
        fingerprint = model.fingerprint
        changed = self._fingerprint is not None and fingerprint != self._fingerprint
        if self._model is None or changed or force:
            self._model = model
        else:
            # Preserve only the displayed read clock on an unchanged journal.
            # Selection/request state is presentation state and may still change.
            self._model = replace(model, read_at=self._model.read_at)
        self._fingerprint = fingerprint
        self._failure_exit = None
        self._error_code = "trial_not_found" if requested_missing else None

        if changed:
            self._pulse_phase = 0
        elif self._pulse_phase is not None and self._capabilities().motion:
            self._pulse_phase = None

        # Each query-only snapshot is a frame boundary.  The fingerprint controls
        # only the update cue, never a claim that an unchanged journal is live.
        self._draw()

    def _move(self, direction: int) -> None:
        if self._model is None or not self._model.trials:
            return
        selected_index = next(
            (
                index
                for index, trial in enumerate(self._model.trials)
                if trial.selected
            ),
            None,
        )
        if selected_index is None:
            target_index = 0 if direction > 0 else len(self._model.trials) - 1
        else:
            target_index = max(
                0,
                min(len(self._model.trials) - 1, selected_index + direction),
            )
        target = self._model.trials[target_index]
        if target.selected:
            return
        try:
            self._model = self._establish_selection(self._model, target)
        except KeyError:
            return
        self._expanded = False
        if self._error_code == "trial_not_found":
            self._error_code = None
            self._failure_exit = None
        self._draw()

    @staticmethod
    def _normalized_key(key: str) -> str:
        mapping = {
            "\x1b[A": "up",
            "\x1b[B": "down",
            "KEY_UP": "up",
            "KEY_DOWN": "down",
            "\r": "enter",
            "\n": "enter",
        }
        return mapping.get(key, key)

    def _handle_key(self, key: str) -> bool:
        key = self._normalized_key(key)
        if key == "q":
            return False
        if key == "?":
            self._help_visible = not self._help_visible
            self._draw()
        elif key in {"j", "down"}:
            self._move(1)
        elif key in {"k", "up"}:
            self._move(-1)
        elif key == "enter":
            if self._model is not None and self._model.selected_trial_number is not None:
                self._expanded = not self._expanded
                self._draw()
        elif key == "r":
            self._refresh(force=True)
        return True

    def run(self, initial_trial_id: Optional[str] = None) -> int:
        if initial_trial_id is not None and (
            type(initial_trial_id) is not str or not initial_trial_id
        ):
            raise ValueError("initial_trial_id must be a non-empty string or None")
        self._selected_trial_id = initial_trial_id
        now = float(self._monotonic())
        self._refresh(force=True)
        next_refresh = now + self._refresh_interval

        while True:
            now = float(self._monotonic())
            wake_deadline = next_refresh
            # The injected terminal contract reserves ``None`` for a real timeout
            # and requires a strictly positive wait value.
            timeout = max(0.000001, wake_deadline - now)
            try:
                read_key = getattr(self._terminal, "read_key")
                key = read_key(timeout)
            except EOFError:
                return self.current_exit_code()
            except KeyboardInterrupt:
                return EXIT_INTERRUPT
            except BaseException as error:
                raise FollowTerminalError("terminal input failed") from error
            if key is not None and type(key) is not str:
                raise FollowTerminalError("terminal input returned an invalid key")
            if key == "":
                return self.current_exit_code()

            now = float(self._monotonic())
            if key is None:
                # A timeout event is authoritative even when a deterministic test
                # clock does not advance itself while the fake terminal waits.
                now = max(now, wake_deadline)
            if now >= next_refresh:
                self._refresh(force=False)
                next_refresh = now + self._refresh_interval
            if key is not None and not self._handle_key(key):
                return self.current_exit_code()


def run_watch_follow(
    controller: FollowController,
    terminal: object,
    *,
    initial_trial_id: Optional[str] = None,
    signal_guard_factory: Callable[[], object] = _SignalGuard,
) -> int:
    """Run one controller inside the single terminal-cleanup context."""

    try:
        with terminal:  # type: ignore[attr-defined]
            with signal_guard_factory():  # type: ignore[attr-defined]
                return controller.run(initial_trial_id)
    except KeyboardInterrupt:
        return EXIT_INTERRUPT


__all__ = [
    "DEFAULT_REFRESH_INTERVAL",
    "FollowController",
    "FollowRenderError",
    "FollowTerminalError",
    "InjectedTerminalSession",
    "TerminalSession",
    "TerminalUnavailable",
    "run_watch_follow",
    "supports_follow_terminal",
    "supports_injected_terminal",
]
