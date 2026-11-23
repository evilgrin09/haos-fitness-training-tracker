"""Tests for the progression suggestions and the new prefill."""

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from custom_components.gym_tracker import logic
from custom_components.gym_tracker.db import GymDatabase, GymError

from .helpers import USER, UserDB

TODAY = date(2026, 11, 23)
WEIGHTED = {"target_sets": 3, "reps_min": 12, "reps_max": 14, "duration_s": None, "weighted": True}
BODYWEIGHT = {**WEIGHTED, "weighted": False, "reps_min": 10, "reps_max": 12}
TIMED = {"target_sets": 3, "reps_min": None, "reps_max": None, "duration_s": 30, "weighted": False}


def point(days_ago: int, *, reps=None, weight=None, rpe=7, sets=3, seconds=None, min_reps=None, min_seconds=None):
    """A completed session of an exercise as the history holds it."""
    return {
        "date": (TODAY - timedelta(days=days_ago)).isoformat(),
        "reps": reps,
        "min_reps": reps if min_reps is None else min_reps,
        "seconds": seconds,
        "min_seconds": seconds if min_seconds is None else min_seconds,
        "weight": weight,
        "rpe": rpe,
        "set_count": sets,
    }


def rule(exercise, history):
    result = logic.recommend(exercise, history, TODAY)
    return result and result["rule"]


# ------------------------------------------------------------------ rules


def test_needs_two_sessions() -> None:
    assert logic.recommend(WEIGHTED, [], TODAY) is None
    assert logic.recommend(WEIGHTED, [point(2, reps=14, weight=60, rpe=5)], TODAY) is None
    assert rule(WEIGHTED, [point(5, reps=12, weight=60), point(2, reps=14, weight=60, rpe=7)]) == "weight"


def test_weight_step() -> None:
    result = logic.recommend(WEIGHTED, [point(5, reps=12, weight=60), point(2, reps=14, weight=63, rpe=8)], TODAY)
    assert result == {
        "rule": "weight",
        "data": {"rpe": 8, "reps": 14, "seconds": None, "weight": 63, "step": 2.5, "suggested_weight": 65.5,
                 "suggested_reps": 12},
    }  # fmt: skip


def test_weight_step_for_light_weights() -> None:
    result = logic.recommend(WEIGHTED, [point(5, reps=12, weight=3), point(2, reps=14, weight=3, rpe=7)], TODAY)
    assert (result["data"]["step"], result["data"]["suggested_weight"]) == (1.0, 4)
    assert logic.weight_step(9.5) == 1.0
    assert logic.weight_step(10) == 2.5


def test_no_weight_step_unless_all_sets_at_top_and_rpe_at_most_8() -> None:
    before = point(5, reps=12, weight=60)
    assert rule(WEIGHTED, [before, point(2, reps=14, weight=63, rpe=9)]) is None
    # One set below the top (e.g. 14/14/13): no weight yet, but more reps.
    assert rule(WEIGHTED, [before, point(2, reps=14, min_reps=13, weight=63, rpe=7)]) is None
    # Fewer sets than planned.
    assert rule(WEIGHTED, [before, point(2, reps=14, weight=63, rpe=7, sets=2)]) is None


def test_double_weight_step() -> None:
    history = [point(5, reps=14, weight=60, rpe=6), point(2, reps=14, weight=60, rpe=5)]
    result = logic.recommend(WEIGHTED, history, TODAY)
    assert (result["rule"], result["data"]["step"], result["data"]["suggested_weight"]) == ("weight_double", 5.0, 65)
    # Only one of both easy: a single step.
    assert rule(WEIGHTED, [point(5, reps=14, weight=60, rpe=7), point(2, reps=14, weight=60, rpe=5)]) == "weight"
    assert rule(WEIGHTED, [point(5, reps=13, weight=60, rpe=5), point(2, reps=14, weight=60, rpe=5)]) == "weight"


def test_more_reps() -> None:
    result = logic.recommend(WEIGHTED, [point(5, reps=12, weight=60), point(2, reps=12, weight=60, rpe=7)], TODAY)
    assert (result["rule"], result["data"]["suggested_reps"]) == ("reps", 13)
    assert rule(WEIGHTED, [point(5, reps=12, weight=60), point(2, reps=12, weight=60, rpe=8)]) is None
    assert rule(BODYWEIGHT, [point(5, reps=10), point(2, reps=11, rpe=6)]) == "reps"
    # At the top without weight: nothing to add.
    assert rule(BODYWEIGHT, [point(5, reps=10), point(2, reps=12, rpe=6)]) is None


def test_more_time() -> None:
    result = logic.recommend(TIMED, [point(5, seconds=30), point(2, seconds=35, rpe=7)], TODAY)
    assert (result["rule"], result["data"]["suggested_seconds"]) == ("time", 40)
    assert rule(TIMED, [point(5, seconds=30), point(2, seconds=30, rpe=8)]) is None
    # One set shorter than the target time.
    assert rule(TIMED, [point(5, seconds=30), point(2, seconds=30, min_seconds=25, rpe=6)]) is None


def test_bodyweight_above_range() -> None:
    result = logic.recommend(BODYWEIGHT, [point(5, reps=12), point(2, reps=15, rpe=9)], TODAY)
    assert (result["rule"], result["data"]["reps_max"]) == ("bodyweight", 12)


def _stalled(exercise, rpe, *, sessions=6, **values):
    return [point(3 * (sessions - number), rpe=rpe, **values) for number in range(sessions)]


def test_stagnation_by_sessions() -> None:
    # Six sessions within 18 days without progress: the session window is reached first.
    history = _stalled(WEIGHTED, 7, reps=13, weight=60)
    stall = logic.stagnation("weight", history, TODAY)
    assert stall == {"sessions": 6, "since": history[0]["date"], "avg_rpe": 7.0}
    # Five sessions are not enough (and the data does not reach back 4 weeks).
    assert logic.stagnation("weight", history[1:], TODAY) is None
    # Any progress in the window: no stagnation.
    better = [*history[:-1], {**history[-1], "reps": 14}]
    assert logic.stagnation("weight", better, TODAY) is None
    heavier = [*history[:-1], {**history[-1], "weight": 62.5, "reps": 12}]
    assert logic.stagnation("weight", heavier, TODAY) is None


def test_stagnation_by_four_weeks() -> None:
    # Once a week: 4 weeks are reached before 6 sessions.
    history = [point(35, reps=13, weight=60), *[point(days, reps=13, weight=60, rpe=6) for days in (28, 21, 14, 7)]]
    stall = logic.stagnation("weight", history, TODAY)
    assert (stall["sessions"], stall["since"]) == (4, "2026-10-26")
    # Data that does not reach back 4 weeks: no time window.
    assert logic.stagnation("weight", history[2:], TODAY) is None
    # Progress within the 4 weeks.
    assert logic.stagnation("weight", [*history[:-1], point(7, reps=14, weight=60)], TODAY) is None


def test_stagnation_with_low_rpe_suggests_to_progress() -> None:
    history = _stalled(WEIGHTED, 7, reps=13, weight=60)
    # Below the top with RPE 7 the reps rule comes first.
    assert rule(WEIGHTED, history) == "reps"
    history = _stalled(WEIGHTED, 8, reps=13, weight=60, sessions=6)
    history = [{**p, "rpe": rpe} for p, rpe in zip(history, (6, 6, 6, 7, 7, 8), strict=True)]
    result = logic.recommend(WEIGHTED, history, TODAY)
    assert (result["rule"], result["data"]["avg_rpe"]) == ("stall_push", 6.7)


def test_stagnation_with_two_sets_suggests_one_set_more() -> None:
    exercise = {**WEIGHTED, "target_sets": 2, "reps_min": 15, "reps_max": 15}
    stalled = _stalled(exercise, 0, reps=14, weight=18, sets=2)
    history = [{**p, "rpe": rpe} for p, rpe in zip(stalled, (6, 6, 7, 7, 7, 8), strict=True)]
    result = logic.recommend(exercise, history, TODAY)
    assert (result["rule"], result["data"]["suggested_sets"]) == ("sets", 3)


def test_stagnation_with_high_rpe_suggests_recovery_instead_of_progress() -> None:
    history = _stalled(WEIGHTED, 9, reps=14, weight=60)
    history = [{**p, "rpe": rpe} for p, rpe in zip(history, (10, 10, 9, 9, 9, 8), strict=True)]
    # The last session alone (all sets at the top, RPE 8) would allow a weight step.
    result = logic.recommend(WEIGHTED, history, TODAY)
    assert result["rule"] == "recovery"
    assert result["data"]["avg_rpe"] == 9.2
    # Just below 9 on average: the weight step applies again.
    history[0]["rpe"] = 8
    assert rule(WEIGHTED, history) == "weight"
    # Between 7 and 9 on average: no stagnation hint at all.
    middle = [{**p, "rpe": 8} for p in _stalled(WEIGHTED, 8, reps=13, weight=60)]
    assert rule(WEIGHTED, middle) is None


def test_apply_recommendation() -> None:
    defaults = {"sets": 3, "reps": 14, "seconds": None, "weight": 63}
    weight = {"rule": "weight", "data": {"suggested_weight": 65.5, "suggested_reps": 12}, "decision": "accepted"}
    assert logic.apply_recommendation(defaults, weight) == {"sets": 3, "reps": 12, "seconds": None, "weight": 65.5}
    assert logic.apply_recommendation(defaults, {**weight, "decision": "declined"}) == defaults
    assert logic.apply_recommendation(defaults, {**weight, "decision": None}) == defaults
    reps = {"rule": "reps", "data": {"suggested_reps": 13}, "decision": "accepted"}
    assert logic.apply_recommendation(defaults, reps)["reps"] == 13
    time = {"rule": "time", "data": {"suggested_seconds": 35}, "decision": "accepted"}
    assert logic.apply_recommendation({**defaults, "seconds": 30}, time)["seconds"] == 35
    assert logic.apply_recommendation(defaults, None) == defaults


# ----------------------------------------------------- stored in sessions

START = datetime(2026, 11, 2, 17, 0, tzinfo=UTC)


@pytest.fixture
def db(tmp_path: Path) -> UserDB:
    """Database with the default plan of one user."""
    database = GymDatabase(tmp_path / "gym.db")
    database.create_user_plan(USER, START, template=True)
    yield UserDB(database)
    database.close()


def _ex(db: UserDB, name: str) -> dict:
    return next(e for e in db.get_plans()[0]["exercises"] if e["name"] == name)


def _item(db: UserDB, session_id: int, name: str) -> dict:
    return next(i for i in db.session_detail(session_id)["items"] if i["exercise"]["name"] == name)


def _train(db: UserDB, day: int, name: str, *, finish: bool = True, **values) -> int:
    when = START + timedelta(days=day)
    session_id = db.start_session(None, when, when.date())
    db.save_entry(session_id, _ex(db, name)["id"], when + timedelta(minutes=5), **values)
    if finish:
        db.finish_session(session_id, when + timedelta(minutes=30))
    else:
        db.cancel_session(session_id, when + timedelta(minutes=30))
    return session_id


def test_prefill_uses_last_recorded_reps_and_seconds(db: UserDB) -> None:
    session_id = db.start_session(None, START, START.date())
    assert _item(db, session_id, "Plank")["defaults"]["seconds"] == 30
    assert _item(db, session_id, "Beinpresse")["defaults"]["reps"] == 12
    db.save_entry(session_id, _ex(db, "Beinpresse")["id"], START, sets=3, reps=13, weight=63, rpe=8)
    db.save_entry(session_id, _ex(db, "Plank")["id"], START, sets=3, seconds=40, rpe=8)
    db.finish_session(session_id, START + timedelta(minutes=30))
    second = db.start_session(None, START + timedelta(days=2), date(2026, 11, 4))
    assert _item(db, second, "Beinpresse")["defaults"] == {"sets": 3, "reps": 13, "seconds": None, "weight": 63}
    assert _item(db, second, "Plank")["defaults"]["seconds"] == 40


def test_rpe_is_required(db: UserDB) -> None:
    session_id = db.start_session(None, START, START.date())
    with pytest.raises(GymError):
        db.save_entry(session_id, _ex(db, "Beinpresse")["id"], START, sets=3, reps=12, weight=63)
    db.save_entry(session_id, _ex(db, "Beinpresse")["id"], START, skipped=True)


def test_suggestion_is_stored_when_the_session_starts(db: UserDB) -> None:
    _train(db, 0, "Beinpresse", sets=3, reps=13, weight=63, rpe=7)
    first = db.start_session(None, START + timedelta(days=1), date(2026, 11, 3))
    assert _item(db, first, "Beinpresse")["recommendation"] is None  # only one session so far
    db.cancel_session(first, START + timedelta(days=1))
    _train(db, 2, "Beinpresse", sets=3, reps=14, weight=63, rpe=7)
    session_id = db.start_session(None, START + timedelta(days=4), date(2026, 11, 6))
    item = _item(db, session_id, "Beinpresse")
    assert item["recommendation"]["rule"] == "weight"
    assert item["recommendation"]["data"]["suggested_weight"] == 65.5
    assert item["recommendation"]["decision"] is None
    assert item["defaults"]["weight"] == 63  # not applied before it is accepted
    assert _item(db, session_id, "Brustpresse")["recommendation"] is None


def test_accept_sets_prefill_for_this_session(db: UserDB) -> None:
    _train(db, 0, "Beinpresse", sets=3, reps=13, weight=63, rpe=7)
    _train(db, 2, "Beinpresse", sets=3, reps=14, weight=63, rpe=7)
    session_id = db.start_session(None, START + timedelta(days=4), date(2026, 11, 6))
    press = _ex(db, "Beinpresse")["id"]
    db.decide_recommendation(session_id, press, "accepted", START + timedelta(days=4))
    item = _item(db, session_id, "Beinpresse")
    assert item["defaults"] == {"sets": 3, "reps": 12, "seconds": None, "weight": 65.5}
    assert item["recommendation"]["decision"] == "accepted"
    assert item["recommendation"]["decided_at"].startswith("2026-11-06")
    # The decision stays visible in the history after the session.
    db.save_entry(session_id, press, START + timedelta(days=4), sets=3, reps=12, weight=65.5, rpe=8)
    db.finish_session(session_id, START + timedelta(days=4, minutes=30))
    assert _item(db, session_id, "Beinpresse")["recommendation"]["decision"] == "accepted"
    with pytest.raises(GymError, check=lambda err: err.code == "not_running"):
        db.decide_recommendation(session_id, press, "declined", START)


def test_declined_suggestion_returns_in_the_next_session_at_the_earliest(db: UserDB) -> None:
    _train(db, 0, "Beinpresse", sets=3, reps=13, weight=63, rpe=7)
    _train(db, 2, "Beinpresse", sets=3, reps=14, weight=63, rpe=7)
    session_id = db.start_session(None, START + timedelta(days=4), date(2026, 11, 6))
    press = _ex(db, "Beinpresse")["id"]
    db.decide_recommendation(session_id, press, "declined", START + timedelta(days=4))
    item = _item(db, session_id, "Beinpresse")
    assert (item["recommendation"]["decision"], item["defaults"]["weight"]) == ("declined", 63)
    db.cancel_session(session_id, START + timedelta(days=4))
    # Next session: suggested again (the data did not change).
    again = db.start_session(None, START + timedelta(days=6), date(2026, 11, 8))
    assert _item(db, again, "Beinpresse")["recommendation"]["decision"] is None


def test_skipped_exercises_and_cancelled_sessions_do_not_count(db: UserDB) -> None:
    _train(db, 0, "Beinpresse", sets=3, reps=13, weight=63, rpe=7)
    _train(db, 2, "Beinpresse", finish=False, sets=3, reps=14, weight=63, rpe=6)  # cancelled
    skipped = db.start_session(None, START + timedelta(days=3), date(2026, 11, 5))
    db.save_entry(skipped, _ex(db, "Beinpresse")["id"], START + timedelta(days=3), skipped=True)
    db.save_entry(skipped, _ex(db, "Plank")["id"], START + timedelta(days=3), sets=3, seconds=30, rpe=6)
    db.finish_session(skipped, START + timedelta(days=3, minutes=30))
    session_id = db.start_session(None, START + timedelta(days=5), date(2026, 11, 7))
    # Only one completed, not skipped session: no suggestion yet.
    assert _item(db, session_id, "Beinpresse")["recommendation"] is None
    # The prefill still takes the weight recorded last (also from the cancelled session).
    assert _item(db, session_id, "Beinpresse")["defaults"]["reps"] == 14


def test_accepting_one_set_more_raises_the_plan_target(db: UserDB) -> None:
    rpes = [6, 6, 7, 7, 7, 8]
    for number, rpe in enumerate(rpes):
        _train(db, number * 3, "Bizeps Curls", sets=2, reps=14, weight=18, rpe=rpe)
    session_id = db.start_session(None, START + timedelta(days=20), date(2026, 11, 22))
    curls = _ex(db, "Bizeps Curls")["id"]
    item = _item(db, session_id, "Bizeps Curls")
    assert (item["recommendation"]["rule"], item["recommendation"]["data"]["suggested_sets"]) == ("sets", 3)
    assert db.session_detail(session_id)["summary"]["stall_hints"][0]["name"] == "Bizeps Curls"
    db.decide_recommendation(session_id, curls, "accepted", START + timedelta(days=20))
    assert _ex(db, "Bizeps Curls")["target_sets"] == 3
    assert _item(db, session_id, "Bizeps Curls")["defaults"]["sets"] == 3


def test_hints_can_only_be_dismissed(db: UserDB) -> None:
    for number in range(6):
        _train(db, number * 3, "Beinpresse", sets=3, reps=13, weight=63, rpe=9)
    session_id = db.start_session(None, START + timedelta(days=20), date(2026, 11, 22))
    press = _ex(db, "Beinpresse")["id"]
    item = _item(db, session_id, "Beinpresse")
    assert item["recommendation"]["rule"] == "recovery"
    hints = db.session_detail(session_id)["summary"]["stall_hints"]
    assert [(h["name"], h["rule"]) for h in hints] == [("Beinpresse", "recovery")]
    with pytest.raises(GymError, check=lambda err: err.code == "invalid_values"):
        db.decide_recommendation(session_id, press, "accepted", START)
    db.decide_recommendation(session_id, press, "declined", START)
    with pytest.raises(GymError, check=lambda err: err.code == "not_found"):
        db.decide_recommendation(session_id, _ex(db, "Brustpresse")["id"], "declined", START)


def test_exercise_statistics_show_stagnation(db: UserDB) -> None:
    for number in range(6):
        _train(db, number * 3, "Beinpresse", sets=3, reps=13, weight=63, rpe=6)
    stats = db.exercise_stats(_ex(db, "Beinpresse")["id"], None, date(2026, 11, 20))
    assert stats["stall"] == {"sessions": 6, "since": "2026-11-02", "avg_rpe": 6.0}
    _train(db, 19, "Beinpresse", sets=3, reps=14, weight=63, rpe=6)
    assert db.exercise_stats(_ex(db, "Beinpresse")["id"], None, date(2026, 11, 22))["stall"] is None


def test_cardio_is_not_part_of_suggestions(db: UserDB) -> None:
    _train(db, 0, "Beinpresse", sets=3, reps=13, weight=63, rpe=7)
    session_id = _train(db, 2, "Beinpresse", sets=3, reps=14, weight=63, rpe=7)
    db.add_cardio(session_id, "warmup", "bike", 60, START + timedelta(days=2))
    new = db.start_session(None, START + timedelta(days=4), date(2026, 11, 6))
    assert {i["exercise"]["name"] for i in db.session_detail(new)["items"] if i["recommendation"]} == {"Beinpresse"}
