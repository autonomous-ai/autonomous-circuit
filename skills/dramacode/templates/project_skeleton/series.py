"""Series bible — module-level constants, read by every episode.

Editing this file invalidates every rendered episode (the sidecar
fingerprint folds series.py in), so change it deliberately: it is the one
place cast, style, resolution, and pacing live.
"""

from dramalib.bible import Character, Series

SERIES = Series(
    title="Contract Bride of the Chaebol Heir",
    genre="revenge-romance",            # keys dramalib.tropes tables
    style="photoreal-drama",            # or "manhwa", "anime" — provider style preset
    aspect="9:16",
    resolution=(1080, 1920),
    fps=24,
    language="en",
)

# The cast-book skill owns the block between the markers — it regenerates
# the entries (ref_images fill in as reference images land under
# cast/<id>/). Edit cast OUTSIDE a cast-book run only if you must, and
# keep the markers intact.
# CAST-BOOK BEGIN
CAST = [
    Character(
        id="li_wei",
        name="Li Wei",
        look="woman, 28, sharp black bob, gray tailored suit, cold poise "
             "hiding grief",
        voice="f_low_calm",
        ref_images=[],
    ),
    Character(
        id="dorian",
        name="Dorian Cross",
        look="man, 34, black wool coat, silver watch, unreadable half-smile",
        voice="m_deep_cold",
        ref_images=[],
    ),
]
# CAST-BOOK END

# Series-level plan (read by episodes and by you when plotting):
TOTAL_EPISODES = 60
MARKET = "overseas"        # cn | overseas | free — drives the gate plan
FORMAT = "ai-drama"        # keys dramalib.tables.EPISODE_LENGTH_S
