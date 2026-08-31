"""Presentation contract for the plain-``arity`` terminal lobby."""
from __future__ import annotations

from arity.cache_heat import CacheHeatView
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
