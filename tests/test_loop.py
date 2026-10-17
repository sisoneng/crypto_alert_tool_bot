import threading

from sec_sentinel.loop import MAX_BACKOFF_SECONDS, next_delay, run_loop, wait_or_stop


class FakeStop(threading.Event):
    """Records each wait and stops after `cycles` waits, advancing the fake clock."""

    def __init__(self, clock, cycles):
        super().__init__()
        self.clock = clock
        self.cycles = cycles
        self.waits = []

    def wait(self, timeout=None):
        self.waits.append(timeout)
        self.clock.t += timeout
        if len(self.waits) >= self.cycles:
            self.set()
        return self.is_set()


def scripted_cycle(outcomes, clock, duration=5.0):
    """Each call takes `duration` seconds; True succeeds, False raises."""
    calls = iter(outcomes)

    def cycle():
        clock.t += duration
        if not next(calls):
            raise RuntimeError("SEC down")

    return cycle


def test_next_delay_backs_off_and_caps():
    assert [next_delay(60, n) for n in range(5)] == [60, 120, 240, 480, 900]
    assert next_delay(60, 50) == MAX_BACKOFF_SECONDS


def test_interval_measured_from_cycle_start(clock):
    stop = FakeStop(clock, cycles=2)
    run_loop(scripted_cycle([True, True], clock), interval=60, stop=stop, notify=lambda t: None, clock=clock, wait=stop.wait)
    assert stop.waits == [55.0, 55.0]  # 60s interval minus 5s cycle


def test_failures_back_off_notify_once_and_recover(clock):
    notes = []
    outcomes = [False, False, False, False, True, True]
    stop = FakeStop(clock, cycles=len(outcomes))
    run_loop(scripted_cycle(outcomes, clock), interval=60, stop=stop, notify=notes.append, clock=clock, wait=stop.wait)
    assert stop.waits == [115.0, 235.0, 475.0, 895.0, 55.0, 55.0]
    assert len(notes) == 2
    assert notes[0].startswith("⚠️ sec-sentinel: 3 poll cycles failed in a row\nRuntimeError: SEC down")
    assert notes[1] == "✅ sec-sentinel recovered after 4 failed cycles"


def test_short_failure_streak_is_silent(clock):
    notes = []
    stop = FakeStop(clock, cycles=3)
    run_loop(scripted_cycle([False, False, True], clock), interval=60, stop=stop, notify=notes.append, clock=clock, wait=stop.wait)
    assert notes == []


def test_notifier_failure_does_not_break_loop(clock):
    def broken_notify(text):
        raise RuntimeError("telegram down")

    stop = FakeStop(clock, cycles=4)
    run_loop(scripted_cycle([False] * 4, clock), interval=60, stop=stop, notify=broken_notify, clock=clock, wait=stop.wait)
    assert len(stop.waits) == 4


def test_stop_before_start_runs_nothing(clock):
    calls = []
    stop = threading.Event()
    stop.set()
    run_loop(lambda: calls.append(1), interval=60, stop=stop, notify=lambda t: None, clock=clock)
    assert calls == []


class SlicedEvent(threading.Event):
    def __init__(self, clock, stop_after=None):
        super().__init__()
        self.clock = clock
        self.slices = []
        self.stop_after = stop_after

    def wait(self, timeout=None):
        self.slices.append(timeout)
        self.clock.t += timeout
        if self.stop_after is not None and len(self.slices) >= self.stop_after:
            self.set()
        return self.is_set()


def test_wait_or_stop_uses_short_slices(clock):
    event = SlicedEvent(clock)
    wait_or_stop(event, 3.5, clock=clock)
    assert event.slices == [1.0, 1.0, 1.0, 0.5]


def test_wait_or_stop_returns_soon_after_stop(clock):
    event = SlicedEvent(clock, stop_after=2)
    wait_or_stop(event, 60, clock=clock)
    assert event.slices == [1.0, 1.0]
