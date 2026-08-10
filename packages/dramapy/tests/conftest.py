"""Suite-wide default: turn the assembly-stage voice layer OFF so the run
stays fast (`say` adds ~0.5-1s per dialogue line) and byte-deterministic
across machines. The dedicated voice tests (test_voices.py) and the e2e
speech assertions flip this back on locally; evals/run.py never loads this
file — evals exercise the real `say` path to stay representative.

With voices off, `build_voice_track` returns None and every dialogue shot is
reported as `silent_dialogue` — that is the intended, honest signal (no voice
landed in the episode), so tests that run with voices off must not assert a
clean warning set for dialogue episodes."""

import os

os.environ["VIDEO_VOICES"] = "off"
