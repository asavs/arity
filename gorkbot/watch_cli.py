"""Read-only one-shot command path for ``arity watch``."""

from __future__ import annotations

import sys
import time
from argparse import Namespace
from collections.abc import Callable
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
EXIT_USAGE_OR_READ = 1
EXIT_NOT_FOUND = 3
EXIT_PARTIAL = 4
EXIT_CORRUPT = 5

Clock = Callable[[], float]
ReaderOpener = Callable[[StoreSpec], AbstractContextManager[object]]
CatalogInspector = Callable[[object], TrialCatalog]
SnapshotRenderer = Callable[[WatchViewModel], str]


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
    if model.catalog_issues or any(trial.integrity == "corrupt" for trial in model.trials):
        return EXIT_CORRUPT
    if any(trial.integrity == "partial" for trial in model.trials):
        return EXIT_PARTIAL
    return EXIT_OK


def _typed_read_failure(error: RecordReadError) -> tuple[int, str]:
    if isinstance(error, RecordCorruption):
        return EXIT_CORRUPT, RecordCorruption.code
    if isinstance(error, RecordChanged):
        return EXIT_USAGE_OR_READ, RecordChanged.code
    return EXIT_USAGE_OR_READ, RecordReadError.code


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
) -> int:
    """Execute one blind snapshot without polling or terminal interaction."""

    output = stdout if stdout is not None else sys.stdout
    errors = stderr if stderr is not None else sys.stderr
    trial_id = getattr(args, "trial_id", None)

    # Accepted now so scripts remain compatible when these flags acquire richer
    # behavior. Stage 2 intentionally produces the same fixed ASCII frame either way.
    bool(getattr(args, "ascii", False))
    bool(getattr(args, "no_motion", False))

    try:
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
        errors.write(f"arity: {safe_code}\n")
        return exit_code

    exit_code = watch_exit_code(model)
    if exit_code == EXIT_NOT_FOUND:
        errors.write("arity: trial_not_found\n")
        return exit_code

    if renderer is not None:
        frame = renderer(model)
    else:
        frame = render_watch_snapshot(model)
    output.write(frame)
    return exit_code


__all__ = [
    "EXIT_CORRUPT",
    "EXIT_NOT_FOUND",
    "EXIT_OK",
    "EXIT_PARTIAL",
    "EXIT_USAGE_OR_READ",
    "load_watch_model",
    "run_watch_command",
    "watch_exit_code",
]
