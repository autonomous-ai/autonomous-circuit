"""Every block id a skill names must be a block that exists.

`circuit-analysis/SKILL.md` told the planner to pick `esp32-s3-core` for
anything wireless. That block has never existed. The planner does what the
skill says, so it briefed a board around a phantom, offered the user "Wi-Fi
control" as a choice, and only fell over later — by which time the user had
already been promised something we cannot build.

The same file also omitted `ws2812-chain`, which does exist, so the planner
could not see a block we ship.

Both failures are the same shape: prose naming blocks, drifting from the
directory that holds them. This pins the prose to the directory.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
BLOCKS_DIR = REPO / "packages" / "golden-blocks" / "blocks"
SKILLS_DIR = REPO / "skills"

#: Ids that name a deliberate absence rather than a block. `blocks.py` keeps
#: the gated entry on purpose — battery charging is a fire risk and the empty
#: slot is the point — so a skill may name it while saying it is unavailable.
ALLOWED_ABSENT = {"lipo-tp4056"}

#: Hyphenated vocabulary that is not a block id at all: power stories, rail
#: names, capability keys. They share the shape and mean something else.
NOT_BLOCKS = {
    "usb-c-5v", "external-dc-lv", "battery-lipo-sealed-block",
    "power-usb", "rail-3v3", "usb-data", "sensor-environment",
    "rgb-pixels", "circuit-brief", "circuit-plan", "circuit-questions",
}

#: Looks like a block id: lowercase words and digits joined by hyphens, and
#: containing at least one hyphen so ordinary prose does not match.
CANDIDATE = re.compile(r"`([a-z0-9]+(?:-[a-z0-9]+)+)`")


def released_blocks() -> set[str]:
    return {d.name for d in BLOCKS_DIR.iterdir() if d.is_dir()}


class SkillBlockReferences(unittest.TestCase):
    def test_no_skill_names_a_block_that_does_not_exist(self):
        real = released_blocks()
        # Only ids that look like ours: anything matching a released block's
        # shape but absent from disk. Keeps the check from arguing with
        # ordinary hyphenated prose or file names.
        known_shapes = {b.rsplit("-", 1)[0] for b in real} | {
            "esp32-s3-core", "esp32-c3-core", "buck-3v3", "motor-drv8833",
        }
        problems = []
        for skill_md in sorted(SKILLS_DIR.glob("*/SKILL.md")):
            text = skill_md.read_text()
            for token in CANDIDATE.findall(text):
                if token in real or token in ALLOWED_ABSENT or token in NOT_BLOCKS:
                    continue
                stem = token.rsplit("-", 1)[0]
                if token in known_shapes or stem in known_shapes:
                    # Names a block-shaped thing that is not on disk. Allowed
                    # only where the text is explicitly about its absence —
                    # checked over a window, because the sentence saying so
                    # often wraps past the line holding the name.
                    lines = text.splitlines()
                    excused = False
                    for i, line in enumerate(lines):
                        if token not in line:
                            continue
                        window = " ".join(lines[max(0, i - 2): i + 3])
                        if re.search(
                            r"never existed|not authored|does not exist|"
                            r"no .{0,20}block exists|is a `?gaps?`? entry",
                            window,
                            re.I,
                        ):
                            excused = True
                            break
                    if not excused:
                        problems.append(
                            f"{skill_md.relative_to(REPO)} names `{token}`, "
                            "which is not in packages/golden-blocks/blocks/"
                        )
        self.assertEqual(problems, [], "\n" + "\n".join(problems))

    def test_the_catalog_table_lists_every_released_block(self):
        """A block we ship but never mention is one the planner cannot pick."""
        text = (SKILLS_DIR / "circuit-analysis" / "SKILL.md").read_text()
        missing = sorted(
            b for b in released_blocks()
            if b != "glue" and f"`{b}`" not in text
        )
        self.assertEqual(
            missing, [],
            f"circuit-analysis/SKILL.md never names: {missing}. "
            "The planner only picks from what this file lists.",
        )


if __name__ == "__main__":
    unittest.main()
