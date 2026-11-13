from __future__ import annotations

import hashlib
import json
from typing import Any

# Minecraft-style 8x8 face grids with 8x8 pixel coordinates
# Pixel layers: skin, hair, eyes, accessory (glasses/beard), collar/clothing


PATIENT_AVATAR_PRESETS: dict[str, dict[str, Any]] = {
    "P001": {
        "name": "Clora Christiansen",
        "gender": "female",
        "skin": "#eec5a2",
        "skin_shadow": "#d9ac8b",
        "hair": "#b59b7c",
        "hair_dark": "#9c8163",
        "eye": "#3b82f6",
        "pupil": "#1e3a8a",
        "clothing": "#0d9488",
        "clothing_accent": "#14b8a6",
        "glasses": False,
        "hair_style": "bob",
    },
    "P002": {
        "name": "Eric Ruecker",
        "gender": "male",
        "skin": "#dfaa8b",
        "skin_shadow": "#c48f70",
        "hair": "#4a3221",
        "hair_dark": "#322215",
        "eye": "#15803d",
        "pupil": "#14532d",
        "clothing": "#1e40af",
        "clothing_accent": "#3b82f6",
        "glasses": False,
        "hair_style": "short",
    },
    "P003": {
        "name": "Jarrod O'Keefe",
        "gender": "male",
        "skin": "#eed8c8",
        "skin_shadow": "#d9beaa",
        "hair": "#e2e8f0",
        "hair_dark": "#cbd5e1",
        "eye": "#0284c7",
        "pupil": "#0369a1",
        "clothing": "#475569",
        "clothing_accent": "#64748b",
        "glasses": True,
        "hair_style": "receding",
    },
    "P004": {
        "name": "Genoveva Morar",
        "gender": "female",
        "skin": "#d49e7d",
        "skin_shadow": "#ba8261",
        "hair": "#854d0e",
        "hair_dark": "#713f12",
        "eye": "#78350f",
        "pupil": "#451a03",
        "clothing": "#831843",
        "clothing_accent": "#9d174d",
        "glasses": False,
        "hair_style": "curly",
    },
    "P005": {
        "name": "Angelo Harber",
        "gender": "male",
        "skin": "#c68662",
        "skin_shadow": "#ab6d49",
        "hair": "#18181b",
        "hair_dark": "#09090b",
        "eye": "#854d0e",
        "pupil": "#451a03",
        "clothing": "#365314",
        "clothing_accent": "#4d7c0f",
        "glasses": False,
        "beard": True,
        "hair_style": "short",
    },
    "P006": {
        "name": "Launa Brakus",
        "gender": "female",
        "skin": "#eed3bc",
        "skin_shadow": "#d4b69d",
        "hair": "#78350f",
        "hair_dark": "#451a03",
        "eye": "#059669",
        "pupil": "#065f46",
        "clothing": "#be185d",
        "clothing_accent": "#db2777",
        "glasses": False,
        "hair_style": "long",
    },
    "P007": {
        "name": "Albert Bins",
        "gender": "male",
        "skin": "#d8ab8d",
        "skin_shadow": "#bf8e70",
        "hair": "#a16207",
        "hair_dark": "#854d0e",
        "eye": "#2563eb",
        "pupil": "#1d4ed8",
        "clothing": "#1e293b",
        "clothing_accent": "#334155",
        "glasses": False,
        "hair_style": "short",
    },
    "P008": {
        "name": "Lucie Hackett",
        "gender": "female",
        "skin": "#f8e2cf",
        "skin_shadow": "#dfc7b2",
        "hair": "#ca8a04",
        "hair_dark": "#a16207",
        "eye": "#0284c7",
        "pupil": "#0369a1",
        "clothing": "#059669",
        "clothing_accent": "#10b981",
        "glasses": False,
        "hair_style": "long",
    },
    "P009": {
        "name": "Jarrett Mraz",
        "gender": "male",
        "skin": "#c89474",
        "skin_shadow": "#ad7859",
        "hair": "#52525b",
        "hair_dark": "#3f3f46",
        "eye": "#3b82f6",
        "pupil": "#1d4ed8",
        "clothing": "#1d4ed8",
        "clothing_accent": "#2563eb",
        "glasses": True,
        "hair_style": "buzz",
    },
    "P010": {
        "name": "Asha Raynor",
        "gender": "female",
        "skin": "#8c5638",
        "skin_shadow": "#714227",
        "hair": "#1c1917",
        "hair_dark": "#0c0a09",
        "eye": "#451a03",
        "pupil": "#1c1917",
        "clothing": "#c2410c",
        "clothing_accent": "#ea580c",
        "glasses": False,
        "hair_style": "curly",
    },
}


def generate_minecraft_avatar_svg(patient_id: str, size: int = 64) -> str:
    """Generate a pixel-perfect, crisp Minecraft-style blocky avatar SVG.

    Uses an 8x8 pixel grid with 3D blocky border shading for an authentic
    Minecraft block character face.
    """
    preset = PATIENT_AVATAR_PRESETS.get(patient_id)
    if not preset:
        # Deterministic fallback based on hash
        h = int(hashlib.sha256(patient_id.encode("utf-8")).hexdigest()[:8], 16)
        skins = ["#eec5a2", "#dfaa8b", "#eed8c8", "#d49e7d", "#c68662"]
        hairs = ["#4a3221", "#e2e8f0", "#854d0e", "#18181b", "#a16207"]
        eyes = ["#3b82f6", "#15803d", "#0284c7", "#78350f", "#854d0e"]
        cloths = ["#0d9488", "#1e40af", "#475569", "#831843", "#365314"]
        preset = {
            "name": f"Patient {patient_id}",
            "skin": skins[h % len(skins)],
            "skin_shadow": "#c48f70",
            "hair": hairs[(h // 5) % len(hairs)],
            "hair_dark": "#322215",
            "eye": eyes[(h // 25) % len(eyes)],
            "pupil": "#0f172a",
            "clothing": cloths[(h // 125) % len(cloths)],
            "clothing_accent": cloths[(h // 125) % len(cloths)],
            "glasses": bool((h // 625) % 2 == 0),
            "hair_style": "short",
        }

    # Base grid 8x8 (0 to 7)
    # Row 0: Hair
    # Row 1: Hair + forehead
    # Row 2: Eyes & Nose
    # Row 3: Cheeks & Mouth
    # Row 4: Chin / Jaw
    # Row 5: Neck
    # Rows 6,7: Shoulders / Clothing collar

    pixels: list[tuple[int, int, str]] = []

    # Fill base skin
    skin = preset["skin"]
    skin_s = preset["skin_shadow"]
    for y in range(0, 6):
        for x in range(0, 8):
            # shadow at edges for blocky 3D cube depth
            if x == 0 or x == 7 or y == 5:
                pixels.append((x, y, skin_s))
            else:
                pixels.append((x, y, skin))

    # Hair layers
    hair = preset["hair"]
    hair_d = preset["hair_dark"]

    # Top hair (Row 0)
    for x in range(0, 8):
        pixels.append((x, 0, hair_d if x in (0, 7) else hair))

    # Forehead / bangs (Row 1)
    style = preset.get("hair_style", "short")
    if style == "receding":
        pixels.append((0, 1, hair))
        pixels.append((1, 1, hair))
        pixels.append((6, 1, hair))
        pixels.append((7, 1, hair))
    elif style == "bob" or style == "curly":
        for x in range(0, 8):
            pixels.append((x, 1, hair if x % 2 == 0 else hair_d))
        pixels.append((0, 2, hair_d))
        pixels.append((7, 2, hair_d))
        pixels.append((0, 3, hair_d))
        pixels.append((7, 3, hair_d))
    elif style == "long":
        for x in range(0, 8):
            pixels.append((x, 1, hair))
        for y in range(2, 6):
            pixels.append((0, y, hair_d))
            pixels.append((7, y, hair_d))
    else:  # short / buzz
        for x in range(0, 8):
            pixels.append((x, 1, hair if x in (0, 1, 6, 7) else hair_d))
        pixels.append((0, 2, hair_d))
        pixels.append((7, 2, hair_d))

    # Eyes at row 2:
    # Left eye: x=1,2. White at x=1, Iris at x=2
    # Right eye: x=5,6. Iris at x=5, White at x=6
    pixels.append((1, 2, "#ffffff"))
    pixels.append((2, 2, preset["eye"]))
    pixels.append((5, 2, preset["eye"]))
    pixels.append((6, 2, "#ffffff"))

    # Pupils (subtle dark highlight)
    pixels.append((2, 3, preset.get("pupil", "#0f172a")))
    pixels.append((5, 3, preset.get("pupil", "#0f172a")))

    # Nose at row 3 (x=3,4)
    pixels.append((3, 3, skin_s))
    pixels.append((4, 3, skin_s))

    # Mouth at row 4 (x=3,4)
    mouth_color = "#b97b66" if "female" in preset.get("gender", "") else "#a3634e"
    pixels.append((3, 4, mouth_color))
    pixels.append((4, 4, mouth_color))

    # Beard if present
    if preset.get("beard"):
        pixels.append((2, 4, hair_d))
        pixels.append((5, 4, hair_d))
        for x in range(1, 7):
            pixels.append((x, 5, hair_d))

    # Glasses if present
    if preset.get("glasses"):
        g_frame = "#334155"
        # Frames around eyes
        for gx in (1, 2, 5, 6):
            pixels.append((gx, 2, "#e0f2fe"))  # tinted lens
        # Bridge
        pixels.append((3, 2, g_frame))
        pixels.append((4, 2, g_frame))
        # Outer rims
        pixels.append((0, 2, g_frame))
        pixels.append((7, 2, g_frame))

    # Clothing / Collar (Rows 6, 7)
    cloth = preset["clothing"]
    cloth_acc = preset["clothing_accent"]
    for y in (6, 7):
        for x in range(0, 8):
            if y == 6 and x in (3, 4):
                pixels.append((x, y, skin_s))  # neck/collar opening
            elif x in (0, 7) or y == 7:
                pixels.append((x, y, cloth))
            else:
                pixels.append((x, y, cloth_acc))

    # Compile SVG rects
    # Each pixel in 8x8 grid is 1x1 in viewBox="0 0 8 8"
    pixel_map = {(x, y): color for x, y, color in pixels}

    rects = []
    for (x, y), color in sorted(pixel_map.items()):
        rects.append(f'<rect x="{x}" y="{y}" width="1" height="1" fill="{color}" shape-rendering="crispEdges"/>')

    svg_content = "\n  ".join(rects)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 8 8" width="{size}" height="{size}" '
        f'style="image-rendering:pixelated;border-radius:6px;box-shadow:0 1px 3px rgba(0,0,0,0.2);background:#0f172a;" '
        f'role="img" aria-label="Minecraft avatar for {patient_id}">'
        f"\n  {svg_content}\n</svg>"
    )
