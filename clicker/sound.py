"""Audio feedback via winsound (stdlib, Windows-only) — no new dependency,
no sound assets. winsound.Beep() is synchronous (blocks the calling thread
for its duration), so these are only ever called from background threads
(the voice-capture worker, run_cycle) — never from the Tk thread, so the UI
never stalls waiting on a beep.
"""

import winsound


def _beep(notes):
    for freq, duration_ms in notes:
        try:
            winsound.Beep(freq, duration_ms)
        except RuntimeError:
            pass  # no beep driver available on this machine — fail silently


def listening_started():
    """Rising two-note chirp — mic just started capturing."""
    _beep([(600, 70), (900, 70)])


def listening_stopped_ok():
    """Single confirmation tone — speech was captured and transcribed."""
    _beep([(1000, 90)])


def listening_stopped_error():
    """Low tone — no speech heard, or transcription failed."""
    _beep([(320, 140)])


def guide_shown():
    """Short, subtle tick — a new instruction just appeared on screen."""
    _beep([(520, 55)])
