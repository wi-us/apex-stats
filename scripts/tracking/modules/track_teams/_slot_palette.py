"""Shared HUD VOD slot palette (slot 1..20).

Same hex values as `src/lib/team-colors.ts` and
`scripts/tracking/modules/track_teams/eval/render_live_overlay.py`.
Kept in one place so `track_teams.py` and the eval/render scripts agree.
"""
from __future__ import annotations

SLOT_HEX: list[str] = [
    "#078396", "#1B486A", "#1F55CD", "#452A60", "#6E2C70", "#AD2D78",
    "#AE1C51", "#BF000B", "#C34221", "#791F14", "#9F3A0D", "#764B01",
    "#CE7A12", "#967E01", "#84930A", "#495903", "#719844", "#398935",
    "#2F5B19", "#017557",
]


def slot_color_hex(slot: int) -> str:
    """1-based slot number → hex color (wraps modulo 20)."""
    if slot is None:
        return "#888888"
    return SLOT_HEX[(int(slot) - 1) % len(SLOT_HEX)]


def slot_color_bgr(slot: int) -> tuple[int, int, int]:
    """1-based slot number → OpenCV BGR tuple."""
    h = slot_color_hex(slot).lstrip("#")
    r = int(h[0:2], 16); g = int(h[2:4], 16); b = int(h[4:6], 16)
    return (b, g, r)