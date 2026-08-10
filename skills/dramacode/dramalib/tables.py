"""Canonical short-drama number tables — the single owner of every number.

Durations in seconds, audio in dB, subtitle limits in characters. Sourced
from ``docs/drama-research-2026-08-09.md`` (红果 official screenwriting
tutorials, 53AI, DataEye, ReelShort practitioner guides). Updating a value
here updates every helper that uses it. Do not transcribe these numbers
into episode files — import the table or call the helper.
"""

from __future__ import annotations


# -- Shot durations by kind (seconds, inclusive craft range) -----------------
#
# The craft numbers from the research doc. NOTE the frozen contract
# (video-interfaces.md §1 + CHANGES 2026-08-09) validates Shot.duration_s in
# [1.5, 15], so 1.5-2s inserts are legal. The skill-level hard cap stays 10s
# (generated video degrades past 10s), so the effective emitted range is
# [1.5, 10].
SHOT_DURATION_S: dict[str, tuple[float, float]] = {
    "establish": (3.0, 4.0),              # establishing / wide; never exceed 5.0
    "action": (2.0, 3.0),                 # action or emotional expression
    "dialogue_per_sentence": (2.0, 3.0),  # per spoken sentence
    "insert": (1.0, 2.0),                 # prop / detail close-up
    "peak_freeze": (3.0, 3.0),            # emotional-peak freeze frame
}

ESTABLISH_ABS_MAX_S = 5.0            # an establishing shot never exceeds this
SHOT_HARD_CAP_S = 10.0               # skill discipline: never generate past 10s
SHOT_CONTRACT_RANGE_S = (1.5, 15.0)  # dramapy validator range (CHANGES 2026-08-09: floor 3.0 -> 1.5)


# -- The episode beat law (the formula every hit follows) --------------------

HOOK_MAX_S = 3.0                     # 0-3s: the hook; 80% of viewers decide in 6s
WORLD_BY_S = 10.0                    # by 10s: conflict + relations + goal all set
FIRST_REVERSAL_BY_S = 30.0           # by 30s: first emotional reversal (爽点)
BEAT_INTERVAL_S = (20.0, 30.0)       # an emotional beat every 20-30s (3-4/min)
CLIFFHANGER_WINDOW_S = (5.0, 10.0)   # final 5-10s: cut at the peak, dead stop

BEAT_LAW = {
    "HOOK_MAX_S": HOOK_MAX_S,
    "WORLD_BY_S": WORLD_BY_S,
    "FIRST_REVERSAL_BY_S": FIRST_REVERSAL_BY_S,
    "BEAT_INTERVAL_S": BEAT_INTERVAL_S,
    "CLIFFHANGER_WINDOW_S": CLIFFHANGER_WINDOW_S,
}

# The three hook types (research: 正面冲突 / 悬念 / 极致反差).
HOOK_TYPES = ("direct_confrontation", "mystery", "extreme_contrast")

RECAP_MAX_S = 10.0                   # recap-as-flashback: 0:00-0:10, optional on
                                     # binge platforms; the template warning fires at 8s


# -- Episode length by format (seconds) ---------------------------------------
#
# THE PLATFORM'S FOCUS IS THE SHORT-DRAMA SERIES (not one-off short films). The
# default unit is a ~50-episode series of 90-120s episodes (Dee, 2026-08-09).
# Honest range from the 13-agent market study: the observed *mode* is 60-100 eps
# at 60-120s; we deliberately target the tighter 50 x 90-120s (~75-100 min total
# runtime — a feature-length story, sliced). "series" is the default format.

EPISODE_LENGTH_S: dict[str, tuple[float, float]] = {
    "series": (90.0, 120.0),         # DEFAULT — short-drama-series episode
    "ai-drama": (45.0, 90.0),        # AI短剧 legacy/short sweet spot
    "manju": (60.0, 180.0),          # 漫剧: 60 eps x 2-3 min
}
DEFAULT_EPISODE_LENGTH_S = (90.0, 120.0)   # the series unit

# The series is the product. ~50 episodes is the target; the observed market
# range is 50-100 (English ReelShort ~70-90, Chinese 80-100+) — 50 is the
# tighter, higher-completion target we build to by default.
DEFAULT_SERIES_EPISODES = 50
SERIES_EPISODES_RANGE = (50, 100)

SHOTS_PER_EPISODE = (10, 18)         # for a 90-120s episode (manju runs 20-30/min)
SCENES_PER_EPISODE = (1, 3)          # few locations — asset-reuse doctrine
LEADS_PER_SERIES = (2, 3)


# -- Paywall gates (卡点) by market --------------------------------------------

PAYWALL_GATES = {
    "cn": [10, 20, 30],              # first gate ~ep10 (hooks loaded into 9-10)
    "overseas_first": (5, 12),       # varies by app — configurable
    "overseas_major": (26, 30),
    "hongguo_free": [],              # 红果/free: no gates; ad-break tolerance instead
}

AD_BREAK_TOLERANCE_S = 15.0          # free-model episodes must survive ~15s ad breaks

# Series pacing (红果 official): golden window / development / escalation.
SERIES_PACING = {
    "golden_window_eps": (1, 10),    # protagonist + core conflict + reason to watch
    "development_eps": (11, 30),
    "escalation_eps": (31, 80),
    "minor_reversal_every_eps": (5, 10),
    "major_reversal_every_eps": (20, 30),
    "core_reversals_per_series": (2, 3),
}


# -- Subtitles (burned in, always) --------------------------------------------

SUBTITLE = {
    "max_chars_zh": 14,              # per line
    "max_chars_en": 42,              # per line (x2 lines max)
    "max_lines": 2,
    "position": "lower-fifth",       # never absolute bottom — platform UI lives there
    "font": "Source Han Sans",
    "fill": "white",
    "stroke_px": 2,                  # dark stroke
}

DIALOGUE_LINE_MAX_ZH = 15            # a single spoken line <= 15 Chinese chars
DIALOGUE_CHARS_PER_EPISODE = (200, 300)  # per 1.5-2 min episode


# -- Audio (4 layers: dialogue / ambient / SFX / BGM) --------------------------

AUDIO = {
    "layers": ("dialogue", "ambient", "sfx", "bgm"),
    "dialogue_over_bed_db": 10.0,    # dialogue +10 dB over the bed
    "bgm_db": -5.0,                  # BGM -5 dB, ducks under dialogue
    "tts_speed_conflict": (1.10, 1.15),
    "tts_speed_tender": (0.85, 0.90),
}


# -- Trope table (genre formulas) ----------------------------------------------
#
# Keys are ascii slugs; ``zh`` carries the Chinese genre name. ``beats`` is the
# canonical beat pattern the genre's hits follow — beat_sheet() cycles it.

TROPE_TABLE: dict[str, dict] = {
    "zhuixu": {                       # 赘婿 — despised son-in-law, hidden power
        "zh": "赘婿",
        "audience": "male",
        "market": "cn",
        "beats": ["humiliation", "concealed_identity", "forced_reveal",
                  "face_slap_cascade"],
    },
    "zhanshen": {                     # 战神 — war god returns
        "zh": "战神",
        "audience": "male",
        "market": "cn",
        "beats": ["triumphant_return", "dismissed_as_nobody", "identity_reveal",
                  "face_slap_cascade"],
    },
    "chongsheng": {                   # 重生 — rebirth with memory
        "zh": "重生",
        "audience": "female",
        "market": "cn",
        "beats": ["death_or_betrayal", "rebirth_with_memory", "preemptive_strike",
                  "reversal"],
    },
    "fuchou": {                       # 复仇 — bullied → awaken → face-slap
        "zh": "复仇",
        "audience": "female",
        "market": "cn",
        "beats": ["suppression", "awakening", "counterstrike", "face_slap_cascade"],
    },
    "bazong": {                       # 霸总 — CEO + Cinderella, 双洁 HE
        "zh": "霸总",
        "audience": "female",
        "market": "cn",
        "beats": ["collision_meeting", "forced_proximity", "misunderstanding",
                  "possessive_rescue"],
    },
    "werewolf": {                     # NA romance staple
        "zh": "狼人",
        "audience": "female",
        "market": "overseas",
        "beats": ["rejected_mate", "hidden_bloodline", "claiming_ceremony",
                  "alpha_reveal"],
    },
    "billionaire": {                  # SEA billionaire / secret heiress
        "zh": "亿万富翁",
        "audience": "female",
        "market": "overseas",
        "beats": ["mistaken_identity", "contract_relationship", "public_humiliation",
                  "wealth_reveal"],
    },
    "revenge": {                      # universal
        "zh": "复仇(海外)",
        "audience": "all",
        "market": "overseas",
        "beats": ["betrayal", "awakening", "systematic_payback",
                  "face_slap_cascade"],
    },
    "mafia": {                        # NA possessive-alpha / dangerous-devotion
        "zh": "黑帮",
        "audience": "female",
        "market": "overseas",
        "beats": ["forced_proximity", "possessive_claiming", "rival_danger",
                  "protective_violence"],
    },
    "riches": {                       # rags-to-riches / 神豪 sudden wealth
        "zh": "逆袭暴富",
        "audience": "all",
        "market": "overseas",
        "beats": ["poverty_humiliation", "windfall", "strategic_rise",
                  "status_reversal"],
    },
    "inlaw": {                        # 婆媳 / daughter-in-law & family drama
        "zh": "婆媳",
        "audience": "female",
        "market": "cn",
        "beats": ["mistreatment", "hidden_worth", "dependence_exposed",
                  "role_reversal"],
    },
    "contract": {                     # fake / contract marriage → real love
        "zh": "契约婚姻",
        "audience": "female",
        "market": "overseas",
        "beats": ["the_deal", "forced_cohabitation", "rules_broken",
                  "real_confession"],
    },
    "flashmarry": {                   # 闪婚豪门 — flash-marry into wealth; the
                                      # OLDER/overlooked self-insert (a 2024 CN
                                      # breakout demographic — dignity restored)
        "zh": "闪婚豪门",
        "audience": "female",
        "market": "cn",
        "beats": ["overlooked_wound", "sudden_marriage", "hidden_wealth_reveal",
                  "dignity_restored"],
    },
}

# Aliases so compound / natural genre strings resolve ("revenge-romance",
# "重生", "ceo", "rebirth"). tropes.trope_for_genre() consults this.
GENRE_ALIASES: dict[str, str] = {
    "赘婿": "zhuixu",
    "战神": "zhanshen", "war-god": "zhanshen", "wargod": "zhanshen",
    "重生": "chongsheng", "rebirth": "chongsheng",
    "复仇": "fuchou",
    "霸总": "bazong", "ceo": "bazong", "cinderella": "bazong",
    "vampire": "werewolf",           # same NA paranormal-romance formula
    "mates": "werewolf", "luna": "werewolf", "alpha": "werewolf",
    "fated": "werewolf",             # "fated-mates" resolves here
    "heiress": "billionaire", "secret-heiress": "billionaire",
    "reborn": "chongsheng", "transmigration": "chongsheng",
    "possessive": "mafia", "don": "mafia", "boss": "mafia",
    "wealth": "riches", "神豪": "riches", "shenhao": "riches",  # rags-to-riches → "riches"
    "family": "inlaw", "婆媳": "inlaw", "inlaws": "inlaw",
    "fake": "contract", "arranged": "contract",   # fake/arranged/contract-marriage
    "闪婚": "flashmarry", "flashmarriage": "flashmarry", "flash-marry": "flashmarry",
}

# The three paywall-hook drivers (borderline is compliance-gated).
PAYWALL_HOOK_DRIVERS = ("climax", "reversal", "borderline")


# -- The emotional core (bond → stakes → gut-punch) ---------------------------
#
# Drama is remembered for its heartbreak, not its plot. Every episode plants a
# BOND early — someone or something the protagonist loves and can lose — then
# threatens it and spends it. The heartbeat has the SAME status as the hook:
# the hook earns the first six seconds, the gut-punch earns the memory.
#
# Genre-agnostic on purpose: revenge, romance, thriller, sci-fi, fantasy and
# slice-of-life all run on the same machine — plant, threaten, pay off.

FEELING_SHIFT_S = (15.0, 25.0)      # the on-screen FEELING should turn this often
                                    # (the emotional layer under the beat law)

# What a bond is made of. Plant ONE of these in the first ~10s so the loss lands
# later — an audience only grieves what it was shown to love.
BOND_TYPES = ("person", "promise", "belonging", "identity", "home", "hope")

# The five gut-punches — the irreversible emotional turns a hit is built around.
# Pick one per episode/series and aim the whole arc at it.
EMOTIONAL_TURNS: dict[str, str] = {
    "betrayal": "someone the protagonist trusted chooses against them",
    "sacrifice": "the protagonist gives up what they love to save another",
    "recognition": "the enemy is revealed as someone once loved, or once ours",
    "irreversible_loss": "the bond is destroyed and cannot be restored",
    "reunion": "a bond thought lost returns — relief that aches",
}

# function (from emotional_arc) → the feeling the frame should carry.
FEELING_BY_FUNCTION: dict[str, str] = {
    "bond": "tenderness",
    "threat": "unease",
    "escalation": "dread",
    "aftermath": "resolve",
}

# gut-punch → the feeling that lands ON the turn.
GUT_PUNCH_FEELING: dict[str, str] = {
    "betrayal": "devastation",
    "sacrifice": "anguish",
    "recognition": "shock",
    "irreversible_loss": "grief",
    "reunion": "aching relief",
}


# -- SFX cue vocabulary (the per-shot ``sfx`` list) ---------------------------
#
# The engine consumes an optional per-shot ``sfx`` list (dramalib.spec.Shot.sfx).
# A peak without its sound is half a peak — a slap without its crack, a reveal
# without its swell. Cue the PEAK and one TEXTURE; keep it to 1-3 cues a shot.
# Keys are moment slugs; values are ready-to-use cue phrases. sfx_for() reads
# this table — don't retype cue strings into episodes.

SFX_CUES: dict[str, tuple[str, ...]] = {
    # -- impact peaks (put one on every reversal) --
    "slap": ("sharp skin-crack slap", "crowd gasp"),
    "glass_shatter": ("glass shatter", "shards skittering across the floor"),
    "door_slam": ("heavy door slam", "lock bolt thunk"),
    "punch": ("dull body impact", "breath knocked out"),
    "gunshot": ("single gunshot crack", "ringing tinnitus tail"),
    "tearing_metal": ("cutting-torch hiss", "metal hull groan"),
    # -- tension textures (build under the escalation) --
    "heartbeat": ("slow heavy heartbeat", "blood-rush in the ears"),
    "clock": ("loud clock tick", "pendulum swing"),
    "rain": ("rain on glass", "distant thunder roll"),
    "wind": ("cold wind gust", "cloth snapping"),
    "fire": ("crackling fire", "timber groan"),
    "water": ("muffled underwater", "bubbles rising"),
    "alarm": ("station alarm klaxon", "red-alert strobe hum"),
    "phone_buzz": ("phone buzzing on a hard surface",),
    "footsteps": ("approaching footsteps on tile",),
    # -- emotional stings (land ON the gut-punch) --
    "reveal": ("low sub-bass swell", "single struck piano note"),
    "betrayal": ("cold string stab", "record-scratch stop"),
    "recognition": ("a held breath", "high sustained violin harmonic"),
    "loss": ("hollow room tone", "a note decaying into silence"),
    "sacrifice": ("swelling strings cut to sudden silence",),
    "reunion": ("warm rising strings", "a caught sob"),
    "lullaby": ("soft hummed lullaby", "music-box tine"),
    "monitor_flatline": ("heart-monitor beep going flat",),
}


# -- Score (BGM) mood + arc ---------------------------------------------------
#
# ``Episode.bgm`` is ONE mood key for the bed; dramapy.audio turns it into a
# generated instrumental score (Lyria) or a synth drone. Two rules the writer
# owns: pick the mood that matches the episode's dominant feeling, and shape it
# — a score is an ARC, not a constant volume. It builds to the climax and DROPS
# OUT for the gut-punch. Silence is the loudest score.

SCORE_MOODS: dict[str, str] = {
    "tense-strings": "suspense / revenge / confrontation — tight tremolo strings",
    "warm-piano": "romance / tenderness / bonding — solo piano, soft pads",
    "low-strings-war-drums": "epic / battle / defiance — cello ostinato + taiko",
    "cold-synth": "sci-fi / dread / isolation — analog drone, sub bass",
    "aching-cello": "grief / loss / sacrifice — solo cello, sparse",
    "bright-pulse": "triumph / comeuppance payoff — driving pulse, rising",
    "music-box": "childhood / memory / the uncanny — music box, reversed reverb",
}

# The score's shape across an episode (phase → intent). Not durations — intent.
SCORE_ARC: dict[str, str] = {
    "open": "state the mood quietly under the hook, never over it",
    "build": "add a layer at each escalation beat",
    "climax": "peak intensity at the reversal / spectacle",
    "gut_punch": "CUT the score — let SFX and one breath carry the heartbreak",
    "tail": "silence into the cliffhanger; no music tail-out",
}


# -- Cinematic vocabulary (for director-grade shot prompts) -------------------
#
# The engine (dramapy.cinematography) already derives a shot-size + camera-move
# from ``shot.kind`` and lens/grade from ``series.style``. This vocab is for the
# WRITER: reach past a bare "close-up" into a specific, MOTIVATED choice inside
# the prompt. The rule that does most of the work: don't shoot every beat the
# same size — vary the scale to make a cut mean something.

SHOT_SCALES = ("ECU", "CU", "MCU", "MS", "WS")   # 大特写/特写/近景/中景/全景
CAMERA_MOVES = (
    "static locked-off", "slow push-in", "slow pull-out", "handheld drift",
    "whip pan", "tracking follow", "crane up", "tilt down", "rack focus",
    "dolly zoom",
)
LIGHTING_KEYS = (
    "hard key + deep shadow", "soft window light", "single practical lamp",
    "rim / silhouette backlight", "neon wash", "firelight flicker",
    "overcast flat", "harsh overhead", "candle warm",
)
