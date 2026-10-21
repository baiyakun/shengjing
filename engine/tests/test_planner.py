import pytest

from shengjing_engine.core.planner import (
    WORKER_OVERHEAD_GB,
    Action,
    InsufficientMemory,
    MemoryPlanner,
)


def make(budget: float = 20.0) -> MemoryPlanner:
    p = MemoryPlanner(budget_gb=budget)
    p.register_worker("speech")
    p.register_worker("llm", single_model=True)
    p.register_worker("analysis")
    return p


def run(p: MemoryPlanner, worker: str, model: str, mem: float) -> list[Action]:
    actions = p.plan(worker, model, mem)
    for a in actions:
        p.apply(a, mem if a.kind == "load" else 0.0)
    return actions


def test_cold_start_starts_worker_then_loads():
    p = make()
    assert run(p, "speech", "tts", 7) == [
        Action("start_worker", "speech"),
        Action("load", "speech", "tts"),
    ]
    assert p.is_loaded("speech", "tts")
    assert run(p, "speech", "tts", 7) == []


def test_unloads_least_recently_used_in_same_worker():
    p = make(budget=15 + WORKER_OVERHEAD_GB)
    run(p, "speech", "a", 7)
    run(p, "speech", "b", 7)
    p.touch("a")  # a 最近用过，b 应该先被卸载
    actions = run(p, "speech", "c", 7)
    assert actions == [Action("unload", "speech", "b"), Action("load", "speech", "c")]


def test_stops_whole_worker_when_victim_is_its_last_model():
    p = make(budget=12)
    run(p, "llm", "qwen", 8)
    actions = run(p, "speech", "tts", 7)
    assert actions[0] == Action("stop_worker", "llm")
    assert actions[-1] == Action("load", "speech", "tts")
    assert not p.workers["llm"].running


def test_single_model_worker_restarts_to_switch_model():
    p = make(budget=40)
    run(p, "llm", "small", 4)
    actions = run(p, "llm", "big", 20)
    assert actions == [
        Action("stop_worker", "llm"),
        Action("start_worker", "llm"),
        Action("load", "llm", "big"),
    ]


def test_pinned_models_are_never_evicted():
    p = make(budget=12)
    run(p, "speech", "tts", 7)
    p.pin("tts")
    with pytest.raises(InsufficientMemory) as err:
        p.plan("llm", "qwen", 8)
    assert "内存不足" in str(err.value)
    p.unpin("tts")
    assert p.plan("llm", "qwen", 8)[0] == Action("stop_worker", "speech")


def test_model_larger_than_budget_fails_fast():
    p = make(budget=16)
    with pytest.raises(InsufficientMemory):
        p.plan("speech", "drama3", 46)


def test_unexpected_exit_clears_state():
    p = make()
    run(p, "speech", "tts", 7)
    p.mark_stopped("speech")
    assert p.used_gb() == 0
    assert run(p, "speech", "tts", 7)[0] == Action("start_worker", "speech")
