"""Read-only one-shot command path for ``arity watch``."""

from __future__ import annotations

import sys
import time
from argparse import Namespace
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from typing import TextIO

from .inspection import TrialCatalog, inspect_trials
from .record_readers import (
    RecordChanged,
    RecordCorruption,
    RecordNotFound,
    RecordReadError,
    StoreSpec,
    configured_store_spec,
    open_record_reader,
)
from .watch_terminal import render_watch_snapshot
from .watch_view_model import WatchProjector, WatchViewModel


EXIT_OK = 0
EXIT_OPERATIONAL = 1
EXIT_NOT_FOUND = 3
EXIT_PARTIAL = 4
EXIT_CORRUPT = 5
EXIT_INTERRUPT = 130

Clock = Callable[[], float]
ReaderOpener = Callable[[StoreSpec], AbstractContextManager[object]]
CatalogInspector = Callable[[object], TrialCatalog]
SnapshotRenderer = Callable[[WatchViewModel], str]
FollowRenderer = Callable[..., str]
TerminalFactory = Callable[..., object]
ModelLoader = Callable[..., WatchViewModel]


def load_watch_model(
    store_spec: StoreSpec | None = None,
    *,
    selected_trial_id: str | None = None,
    clock: Clock | None = None,
    reader_opener: ReaderOpener | None = None,
    projector: WatchProjector | None = None,
    inspector: CatalogInspector | None = None,
) -> WatchViewModel:
    """Load the full catalog once, close the reader, then sample the read clock."""

    if selected_trial_id is not None and type(selected_trial_id) is not str:
        raise TypeError("selected_trial_id must be a string or None")
    if store_spec is not None and type(store_spec) is not StoreSpec:
        raise TypeError("store_spec must be an exact StoreSpec or None")

    spec = store_spec if store_spec is not None else configured_store_spec()
    open_reader = reader_opener if reader_opener is not None else open_record_reader
    inspect_catalog = inspector if inspector is not None else inspect_trials
    try:
        with open_reader(spec) as reader:
            catalog = inspect_catalog(reader)
    except RecordNotFound:
        catalog = TrialCatalog(trials=())

    read_clock = clock if clock is not None else time.time
    read_at = read_clock()
    active_projector = projector if projector is not None else WatchProjector()
    return active_projector.project(
        catalog,
        backend=spec.backend,
        read_at=read_at,
        selected_trial_id=selected_trial_id,
    )


def watch_exit_code(model: WatchViewModel) -> int:
    """Apply the stable one-shot exit precedence to a projected snapshot."""

    if type(model) is not WatchViewModel:
        raise TypeError("model must be an exact WatchViewModel")
    if model.requested_trial_missing:
        return EXIT_NOT_FOUND
    if model.catalog_integrity == "corrupt":
        return EXIT_CORRUPT
    if model.catalog_integrity == "partial":
        return EXIT_PARTIAL
    return EXIT_OK


def _typed_read_failure(error: RecordReadError) -> tuple[int, str]:
    if isinstance(error, RecordCorruption):
        return EXIT_CORRUPT, RecordCorruption.code
    if isinstance(error, RecordChanged):
        return EXIT_OPERATIONAL, RecordChanged.code
    return EXIT_OPERATIONAL, RecordReadError.code


def _write_snapshot_text(
    stream: TextIO,
    value: str,
    *,
    raw_ascii: bool,
) -> bool:
    """Write one frame without leaking I/O failures or translating default LFs."""

    try:
        if raw_ascii:
            buffer = getattr(stream, "buffer", None)
            if buffer is not None:
                encoded = value.encode("ascii", errors="strict")
                # Standard streams wrap a BufferedWriter.  Bypass that buffer so a
                # broken pipe cannot leave pending bytes that CPython retries while
                # finalizing the TextIOWrapper after this function returns.
                binary_stream = getattr(buffer, "raw", buffer)
                offset = 0
                while offset < len(encoded):
                    written = binary_stream.write(memoryview(encoded)[offset:])
                    if type(written) is not int or written <= 0:
                        raise OSError("incomplete watch output")
                    offset += written
                binary_stream.flush()
                return True
        written = stream.write(value)
        if type(written) is not int or written != len(value):
            raise OSError("incomplete watch output")
        stream.flush()
        return True
    except (OSError, UnicodeError, ValueError):
        return False


def _try_run_watch_follow(
    args: Namespace,
    *,
    store_spec: StoreSpec | None,
    clock: Clock | None,
    reader_opener: ReaderOpener | None,
    projector: WatchProjector | None,
    inspector: CatalogInspector | None,
    stdin: TextIO,
    stdout: TextIO,
    stderr: TextIO,
    errors_are_default: bool,
    terminal: object | None,
    terminal_factory: TerminalFactory | None,
    model_loader: ModelLoader | None,
    follow_renderer: FollowRenderer | None,
    snapshot_renderer: SnapshotRenderer | None,
    monotonic: Clock | None,
    refresh_interval: float,
    environ: Mapping[str, str] | None,
    signal_guard_factory: Callable[[], object] | None,
) -> int | None:
    """Run follow mode, or return ``None`` for a cleaned one-shot fallback."""

    from .watch_follow import (
        FollowController,
        FollowRenderError,
        FollowTerminalError,
        InjectedTerminalSession,
        TerminalSession,
        TerminalUnavailable,
        run_watch_follow,
        supports_follow_terminal,
        supports_injected_terminal,
    )

    active_projector = projector if projector is not None else WatchProjector()
    ascii_only = bool(getattr(args, "ascii", False))
    no_motion = bool(getattr(args, "no_motion", False))

    try:
        if terminal is not None:
            if not supports_injected_terminal(terminal):
                return None
            terminal_session = InjectedTerminalSession(
                terminal,
                ascii=ascii_only,
                no_motion=no_motion,
                environ=environ,
            )
        elif terminal_factory is None:
            if not supports_follow_terminal(stdin, stdout):
                return None
            terminal_session = TerminalSession(
                stdin,
                stdout,
                ascii=ascii_only,
                no_motion=no_motion,
                environ=environ,
            )
        else:
            if not supports_follow_terminal(stdin, stdout):
                return None
            terminal_session = terminal_factory(
                stdin,
                stdout,
                ascii=ascii_only,
                no_motion=no_motion,
                environ=environ,
            )
    except KeyboardInterrupt:
        return EXIT_INTERRUPT
    except (OSError, RuntimeError, TypeError, ValueError):
        return None

    resolved_spec = (
        None
        if model_loader is not None
        else (store_spec if store_spec is not None else configured_store_spec())
    )

    def loader(selected_trial_id: str | None) -> WatchViewModel:
        if model_loader is not None:
            return model_loader(
                store_spec,
                selected_trial_id=selected_trial_id,
                clock=clock,
                projector=active_projector,
            )
        if resolved_spec is None:
            raise RuntimeError("follow store specification was not resolved")
        return load_watch_model(
            resolved_spec,
            selected_trial_id=selected_trial_id,
            clock=clock,
            reader_opener=reader_opener,
            projector=active_projector,
            inspector=inspector,
        )

    controller_options: dict[str, object] = {
        "terminal": terminal_session,
        "loader": loader,
        "projector": active_projector,
        "refresh_interval": refresh_interval,
    }
    if follow_renderer is not None:
        controller_options["renderer"] = follow_renderer
    elif snapshot_renderer is not None:
        def adapted_snapshot_renderer(
            model: WatchViewModel | None,
            capabilities: object,
            **presentation: object,
        ) -> str:
            del capabilities, presentation
            if model is None:
                raise RuntimeError("one-shot renderer has no last-good model")
            return snapshot_renderer(model)

        controller_options["renderer"] = adapted_snapshot_renderer
    if monotonic is not None:
        controller_options["monotonic"] = monotonic

    try:
        controller = FollowController(**controller_options)  # type: ignore[arg-type]
        runner_options: dict[str, object] = {
            "initial_trial_id": getattr(args, "trial_id", None)
        }
        if signal_guard_factory is not None:
            runner_options["signal_guard_factory"] = signal_guard_factory
        return run_watch_follow(  # type: ignore[arg-type]
            controller,
            terminal_session,
            **runner_options,
        )
    except TerminalUnavailable:
        return None
    except FollowRenderError:
        safe_code = "watch_render_error"
    except FollowTerminalError:
        safe_code = "watch_terminal_error"
    except KeyboardInterrupt:
        return EXIT_INTERRUPT
    except (KeyError, OSError, RuntimeError, TypeError, ValueError):
        safe_code = "watch_terminal_error"

    if not _write_snapshot_text(
        stderr,
        f"arity: {safe_code}\n",
        raw_ascii=errors_are_default,
    ):
        return EXIT_OPERATIONAL
    return EXIT_OPERATIONAL


def run_watch_command(
    args: Namespace,
    *,
    store_spec: StoreSpec | None = None,
    clock: Clock | None = None,
    reader_opener: ReaderOpener | None = None,
    projector: WatchProjector | None = None,
    inspector: CatalogInspector | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    renderer: SnapshotRenderer | None = None,
    stdin: TextIO | None = None,
    terminal_factory: TerminalFactory | None = None,
    terminal: object | None = None,
    model_loader: ModelLoader | None = None,
    follow_renderer: FollowRenderer | None = None,
    monotonic: Clock | None = None,
    refresh_interval: float = 1.0,
    environ: Mapping[str, str] | None = None,
    signal_guard_factory: Callable[[], object] | None = None,
) -> int:
    """Execute one blind snapshot, or an explicit terminal follow session."""

    output_is_default = stdout is None
    errors_are_default = stderr is None
    output = stdout if stdout is not None else sys.stdout
    errors = stderr if stderr is not None else sys.stderr
    trial_id = getattr(args, "trial_id", None)

    if bool(getattr(args, "follow", False)):
        input_stream = stdin if stdin is not None else sys.stdin
        follow_result = _try_run_watch_follow(
            args,
            store_spec=store_spec,
            clock=clock,
            reader_opener=reader_opener,
            projector=projector,
            inspector=inspector,
            stdin=input_stream,
            stdout=output,
            stderr=errors,
            errors_are_default=errors_are_default,
            terminal=terminal,
            terminal_factory=terminal_factory,
            model_loader=model_loader,
            follow_renderer=follow_renderer,
            snapshot_renderer=renderer,
            monotonic=monotonic,
            refresh_interval=refresh_interval,
            environ=environ,
            signal_guard_factory=signal_guard_factory,
        )
        if follow_result is not None:
            return follow_result

    # Accepted now so scripts remain compatible when these flags acquire richer
    # behavior. Stage 2 intentionally produces the same fixed ASCII frame either way.
    bool(getattr(args, "ascii", False))
    bool(getattr(args, "no_motion", False))

    try:
        if model_loader is not None:
            fallback_projector = projector if projector is not None else WatchProjector()
            model = model_loader(
                store_spec,
                selected_trial_id=trial_id,
                clock=clock,
                projector=fallback_projector,
            )
        else:
            model = load_watch_model(
                store_spec,
                selected_trial_id=trial_id,
                clock=clock,
                reader_opener=reader_opener,
                projector=projector,
                inspector=inspector,
            )
    except RecordReadError as error:
        exit_code, safe_code = _typed_read_failure(error)
        if not _write_snapshot_text(
            errors,
            f"arity: {safe_code}\n",
            raw_ascii=errors_are_default,
        ):
            return EXIT_OPERATIONAL
        return exit_code

    exit_code = watch_exit_code(model)
    if exit_code == EXIT_NOT_FOUND:
        if not _write_snapshot_text(
            errors,
            "arity: trial_not_found\n",
            raw_ascii=errors_are_default,
        ):
            return EXIT_OPERATIONAL
        return exit_code

    if renderer is not None:
        frame = renderer(model)
    else:
        frame = render_watch_snapshot(model)
    if not _write_snapshot_text(output, frame, raw_ascii=output_is_default):
        return EXIT_OPERATIONAL
    return exit_code


__all__ = [
    "EXIT_CORRUPT",
    "EXIT_INTERRUPT",
    "EXIT_NOT_FOUND",
    "EXIT_OK",
    "EXIT_OPERATIONAL",
    "EXIT_PARTIAL",
    "load_watch_model",
    "run_watch_command",
    "watch_exit_code",
]
