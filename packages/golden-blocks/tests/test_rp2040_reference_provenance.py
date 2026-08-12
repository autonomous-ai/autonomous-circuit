from __future__ import annotations

import hashlib
from pathlib import Path


BLOCK_DIR = (
    Path(__file__).resolve().parents[1] / "blocks" / "rp2040-core"
)
REPO_ROOT = Path(__file__).resolve().parents[3]
LICENSE_SHA256 = "b7d06548be326cac80f52434578cc6a5e7c32e555619ee4aac3e2a03490a25a6"
ARCHIVE_SHA256 = "8fdae5c1d3d8e58f43a45cd604ce9836b1ad4649f11eca4a9bea97eec6c2093a"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_raspberry_pi_minimal_reference_and_license_are_pinned() -> None:
    license_path = BLOCK_DIR / "RASPBERRY_PI_MINIMAL_LICENSE.txt"
    record_path = BLOCK_DIR / "RASPBERRY_PI_MINIMAL_REFERENCE.md"

    assert _sha256(license_path) == LICENSE_SHA256
    record = record_path.read_text(encoding="utf-8")
    assert "https://datasheets.raspberrypi.com/rp2040/Minimal-KiCAD.zip" in record
    assert ARCHIVE_SHA256 in record
    assert LICENSE_SHA256 in record
    assert "b01f852b57e955edeb4001c02fb3a204bd7309a19d8185a6312598f977d656cc" in record
    assert "Copyright (c) 2026 Raspberry Pi Ltd" in license_path.read_text(
        encoding="utf-8"
    )
    assert "permission notice shall be included" in license_path.read_text(
        encoding="utf-8"
    )
    notice = (REPO_ROOT / "NOTICE").read_text(encoding="utf-8")
    assert "Copyright (c) 2026 Raspberry Pi Ltd" in notice
    assert "RASPBERRY_PI_MINIMAL_LICENSE.txt" in notice
