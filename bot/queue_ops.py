"""Small, testable queue operations on top of wavelink.Queue.

All positions are 1-based (as shown to the user in the queue list).
"""

from __future__ import annotations

import wavelink


def remove_at(queue: wavelink.Queue, pos: int) -> wavelink.Playable:
    track = queue[pos - 1]
    queue.delete(pos - 1)
    return track


def move(queue: wavelink.Queue, src: int, dst: int) -> wavelink.Playable:
    track = queue[src - 1]
    queue.delete(src - 1)
    queue.put_at(dst - 1, track)
    return track


def skip_to(queue: wavelink.Queue, pos: int) -> int:
    """Drop everything before position `pos` so it becomes the next track.

    Dropped tracks go to history, so "loop queue" can still replay them later.
    Returns how many tracks were dropped.
    """
    dropped = 0
    for _ in range(pos - 1):
        track = queue[0]
        queue.delete(0)
        if queue.history is not None:
            queue.history.put(track)
        dropped += 1
    return dropped


def pop_previous(history: wavelink.Queue | None, current: wavelink.Playable | None) -> wavelink.Playable | None:
    """Take the track played *before* the current one out of history.

    History normally ends with the current track, so it is removed too (the caller puts the
    current track back at the front of the queue). Returns None if there is nothing before it.
    """
    if history is None:
        return None
    items = list(history)
    cur_idx = None
    if current is not None and items and items[-1].encoded == current.encoded:
        cur_idx = len(items) - 1
    prev_idx = (len(items) - 2) if cur_idx is not None else (len(items) - 1)
    if prev_idx < 0:
        return None
    prev = items[prev_idx]
    if cur_idx is not None:
        history.delete(cur_idx)
    history.delete(prev_idx)
    return prev
