"""Presentation contract for the plain-``arity`` terminal lobby."""
from __future__ import annotations

import io
from argparse import Namespace

from arity.cache_heat import CacheHeatView
from arity.watch_cli import run_watch_command
from arity.watch_terminal import (
    TerminalCapabilities,
    render_arity_lobby_frame,
    render_watch_follow_frame,
)
from arity.watch_view_model import (
    BoundedCount,
    WatchAgent,
    WatchTrial,
    WatchTrialDetail,
    WatchProjector,
    WatchViewModel,
)


def _detail(*, completed: int, cache: bool = False) -> WatchTrialDetail:
    return WatchTrialDetail(
        agents=(WatchAgent(0, completed > 0), WatchAgent(1, completed > 1)),
        arms=BoundedCount(2),
        completed_agents=BoundedCount(completed),
        evidence=BoundedCount(1),
        reviews=BoundedCount(1),
        resolutions=BoundedCount(0),
        delivery_recorded=False,
        cache_heat=(
            CacheHeatView(
                state="confirmed",
                activity_confidence="confirmed",
                deadline_at=300.0,
                seconds_remaining=200,
            )
            if cache
            else None
        ),
        mechanical_observations=BoundedCount(2),
        model_observations=BoundedCount(1),
        human_observations=BoundedCount(0),
    )


def _model() -> WatchViewModel:
    return WatchViewModel(
        backend="jsonl",
        read_at=100.0,
        trials=(
            WatchTrial(
                trial_number=1,
                integrity="valid",
                lifecycle="evidenced",
                detail=_detail(completed=1, cache=True),
                issue=None,
                selected=True,
            ),
            WatchTrial(
                trial_number=2,
                integrity="valid",
                lifecycle="started",
                detail=_detail(completed=0),
                issue=None,
            ),
        ),
        more_trials_omitted=False,
        catalog_integrity="valid",
        selected_trial_number=1,
    )


def test_lobby_uses_plain_brand_and_nests_expanded_detail_under_selected_row() -> None:
    frame = render_arity_lobby_frame(
        _model(),
        TerminalCapabilities(width=120, ascii=True, motion=False, color=False),
        expanded=True,
    )
    lines = frame.splitlines()

    assert lines[0].startswith("arity | jsonl | 2 trials | read ")
    assert "arity watch" not in frame
    selected_row = lines.index("> Trial 1 | evidenced | valid | completions 1/2")
    agent_row = lines.index("    Agent A | completion recorded")
    next_trial = lines.index("  Trial 2 | started | valid | completions 0/2")
    assert selected_row < agent_row < next_trial
    assert "    observations mechanical 2 | model 1 | human 0" in lines
    assert any(line.startswith("    cache deadline | respond by ") for line in lines)
    assert "selected:" not in frame


def test_lobby_collapse_hides_detail_while_explicit_follow_keeps_its_contract() -> None:
    capabilities = TerminalCapabilities(
        width=120,
        ascii=True,
        motion=False,
        color=False,
    )

    collapsed = render_arity_lobby_frame(_model(), capabilities, expanded=False)
    explicit = render_watch_follow_frame(_model(), capabilities, expanded=True)

    assert "Agent A" not in collapsed
    assert "observations" not in collapsed
    assert "cache deadline" not in collapsed
    assert explicit.startswith("arity watch |")
    assert "selected: Trial 1" in explicit


class _LobbyTerminal:
    def __init__(self) -> None:
        self.frames: list[str] = []

    def stdin_isatty(self) -> bool:
        return True

    def stdout_isatty(self) -> bool:
        return True

    def supports_interactive(self) -> bool:
        return True

    def width(self) -> int:
        return 120

    def setup(self) -> None:
        pass

    def restore(self) -> None:
        pass

    def draw(self, frame: str) -> None:
        self.frames.append(frame)

    def read_key(self, timeout: float) -> str:
        assert timeout > 0
        return "q"


def test_lobby_flag_selects_the_plain_renderer_in_the_real_follow_path() -> None:
    terminal = _LobbyTerminal()
    raw_trial_id = "private-trial-id-must-not-render"

    def load_model(
        _store_spec: object = None,
        *,
        selected_trial_id: str | None,
        projector: WatchProjector,
        **_kwargs: object,
    ) -> WatchViewModel:
        projector._label_registry.assign((raw_trial_id,))
        trial = WatchTrial(
            trial_number=projector._label_registry.number_for(raw_trial_id),
            integrity="valid",
            lifecycle="evidenced",
            detail=_detail(completed=1, cache=True),
            issue=None,
            selected=selected_trial_id == raw_trial_id,
        )
        return WatchViewModel(
            backend="jsonl",
            read_at=100.0,
            trials=(trial,),
            more_trials_omitted=False,
            catalog_integrity="valid",
            selected_trial_number=trial.trial_number if trial.selected else None,
        )

    code = run_watch_command(
        Namespace(
            trial_id=None,
            ascii=True,
            no_motion=True,
            follow=True,
            cache_policy="conservative",
            lobby=True,
        ),
        terminal=terminal,
        model_loader=load_model,
        stdout=io.StringIO(),
        stderr=io.StringIO(),
        monotonic=lambda: 0.0,
        environ={"NO_COLOR": "1"},
    )

    assert code == 0
    assert len(terminal.frames) == 1
    frame = terminal.frames[0]
    assert frame.startswith("arity | jsonl | 1 trial | read ")
    assert "> Trial 1 | evidenced | valid | completions 1/2" in frame
    assert "    Agent A | completion recorded" in frame
    assert "selected:" not in frame
    assert raw_trial_id not in frame
