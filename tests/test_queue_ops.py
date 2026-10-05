import wavelink

import queue_ops


def make_track(n: int) -> wavelink.Playable:
    return wavelink.Playable({
        "encoded": f"enc{n}",
        "info": {
            "identifier": f"id{n}", "isSeekable": True, "author": "Artist", "length": 180_000,
            "isStream": False, "position": 0, "title": f"Song {n}", "uri": f"https://example.com/{n}",
            "sourceName": "youtube",
        },
        "pluginInfo": {}, "userData": {},
    })


def titles(q):
    return [t.title for t in q]


def filled(n=5):
    q = wavelink.Queue()
    for i in range(1, n + 1):
        q.put(make_track(i))
    return q


def test_remove_at():
    q = filled()
    t = queue_ops.remove_at(q, 2)
    assert t.title == "Song 2"
    assert titles(q) == ["Song 1", "Song 3", "Song 4", "Song 5"]


def test_move_forward_and_backward():
    q = filled()
    queue_ops.move(q, 4, 1)
    assert titles(q) == ["Song 4", "Song 1", "Song 2", "Song 3", "Song 5"]
    queue_ops.move(q, 1, 5)
    assert titles(q) == ["Song 1", "Song 2", "Song 3", "Song 5", "Song 4"]


def test_skip_to_moves_skipped_into_history():
    q = filled()
    assert queue_ops.skip_to(q, 3) == 2
    assert titles(q) == ["Song 3", "Song 4", "Song 5"]
    assert titles(q.history) == ["Song 1", "Song 2"]


def test_skip_to_first_is_noop():
    q = filled()
    assert queue_ops.skip_to(q, 1) == 0
    assert len(q) == 5


def test_pop_previous_when_current_is_last_in_history():
    q = wavelink.Queue()
    for i in (1, 2, 3):
        q.history.put(make_track(i))
    current = make_track(3)
    prev = queue_ops.pop_previous(q.history, current)
    assert prev.title == "Song 2"
    assert titles(q.history) == ["Song 1"]


def test_pop_previous_nothing_before_current():
    q = wavelink.Queue()
    q.history.put(make_track(1))
    assert queue_ops.pop_previous(q.history, make_track(1)) is None
    assert titles(q.history) == ["Song 1"]  # untouched


def test_pop_previous_when_nothing_is_playing():
    q = wavelink.Queue()
    for i in (1, 2):
        q.history.put(make_track(i))
    prev = queue_ops.pop_previous(q.history, None)
    assert prev.title == "Song 2"
    assert titles(q.history) == ["Song 1"]


def test_loop_modes_exist():
    q = wavelink.Queue()
    for mode in (wavelink.QueueMode.normal, wavelink.QueueMode.loop, wavelink.QueueMode.loop_all):
        q.mode = mode
        assert q.mode is mode
