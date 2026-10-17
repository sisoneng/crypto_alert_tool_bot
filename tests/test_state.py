import json

from sec_sentinel.state import MAX_SEEN, State, load_state, save_state


def test_missing_file_is_empty(tmp_path):
    state = load_state(tmp_path / "seen.json")
    assert state.seen == [] and state.armed == []


def test_round_trip(tmp_path):
    path = tmp_path / "state" / "seen.json"
    state = State()
    state.add("a")
    state.add("b")
    state.add("a")
    state.heartbeat_day, state.scanned, state.alerts = "2026-10-17", 5, 2
    save_state(path, state)
    loaded = load_state(path)
    assert loaded.seen == ["a", "b"]
    assert loaded.has("b") and not loaded.has("c")
    assert (loaded.heartbeat_day, loaded.scanned, loaded.alerts) == ("2026-10-17", 5, 2)


def test_prunes_oldest(tmp_path):
    path = tmp_path / "seen.json"
    state = State()
    for i in range(MAX_SEEN + 10):
        state.add(f"k{i}")
    save_state(path, state)
    loaded = load_state(path)
    assert len(loaded.seen) == MAX_SEEN
    assert loaded.seen[0] == "k10"
    assert loaded.seen[-1] == f"k{MAX_SEEN + 9}"
    assert not state.has("k0")


def test_atomic_write_leaves_no_temp_files(tmp_path):
    path = tmp_path / "seen.json"
    save_state(path, State(seen=["x"]))
    save_state(path, State(seen=["x", "y"]))
    assert [p.name for p in tmp_path.iterdir()] == ["seen.json"]
    assert json.loads(path.read_text())["seen"] == ["x", "y"]
