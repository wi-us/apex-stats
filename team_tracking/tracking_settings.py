"""
Единый центр настроек трекинга:
- цвета команд по картам;
- параметры обработки по картам (например FRAME_SKIP).
"""

# Карта: Storm Point
STORM_POINT_TEAMS = {
    "TEAM_1": {
        "name": "Команда №1",
        "color_bgr": (4, 131, 148),
        "display_color_bgr": (150, 131, 7),  # Team palette (screenshot)
        "hsv_range": ((80, 170, 120), (95, 255, 255)),
        "morph_kernel_size": 5,
        "min_area": 8,
        "max_area": 250,
        "outlier_threshold_ratio": 0.04,  # 4% = 8px (очень строгий)
        "description": "Супер!",
    },
    "TEAM_2": {
        "name": "Команда №2",
        "color_bgr": (26, 71, 104),
        "display_color_bgr": (106, 72, 27),  # Team palette (screenshot)
        "hsv_range": ((100, 170, 80), (113, 236, 153)),
        "morph_kernel_size": 5,
        "min_area": 15,
        "max_area": 250,
        "description": "Супер!",
    },
    "TEAM_3": {
        "name": "Команда №3",
        "color_bgr": (31, 84, 204),
        "display_color_bgr": (205, 85, 31),  # Team palette (screenshot)
        "hsv_range": ((96, 160, 161), (116, 219, 217)),
        "morph_kernel_size": 5,
        "min_area": 8,
        "max_area": 250,
        "description": "Супер!",
    },
    "TEAM_4": {
        "name": "Команда №4",
        "color_bgr": (67, 42, 96),
        "display_color_bgr": (96, 42, 69),  # Team palette (screenshot)
        "hsv_range": ((118, 139, 73), (141, 173, 88)),
        "morph_kernel_size": 5,
        "min_area": 8,
        "max_area": 250,
        "description": "Работает корректно, но слишком строго",
    },
    "TEAM_5": {
        "name": "Команда 5",
        "color_bgr": (109, 44, 111),
        "display_color_bgr": (112, 44, 110),  # Team palette (screenshot)
        "hsv_range": ((141, 160, 80), (162, 201, 104)),
        "morph_kernel_size": 5,
        "min_area": 8,
        "max_area": 250,
        "description": "Работает корректно, но слишком строго",
    },
    "TEAM_6": {
        "name": "Команда №6",
        "color_bgr": (173, 45, 120),
        "display_color_bgr": (120, 45, 173),  # Team palette (screenshot)
        "hsv_range": ((155, 201, 113), (179, 222, 149)),
        "morph_kernel_size": 5,
        "min_area": 8,
        "max_area": 250,
        "description": "Немного скачет т.к. цвета пограничные, но ок",
    },
    "TEAM_7": {
        "name": "Команда №7",
        "color_bgr": (176, 28, 80),
        "display_color_bgr": (81, 28, 174),  # Team palette (screenshot)
        "hsv_range": ((164, 236, 139), (179, 253, 151)),
        "morph_kernel_size": 5,
        "min_area": 8,
        "max_area": 250,
        "description": "Немного скачет т.к. цвета пограничные, но ок. Очень строго",
    },
    "TEAM_8": {
        "name": "Команда №8",
        "color_bgr": (194, 0, 10),
        "display_color_bgr": (11, 0, 191),  # Team palette (screenshot)
        "hsv_range": ((175, 246, 149), (179, 255, 170)),
        "morph_kernel_size": 5,
        "min_area": 8,
        "max_area": 250,
        "description": "Строго, но ок",
    },
    "TEAM_9": {
        "name": "Команда №9",
        "color_bgr": (196, 67, 31),
        "display_color_bgr": (33, 66, 195),  # Team palette (screenshot)
        "hsv_range": ((0, 193, 151), (5, 217, 182)),
        "morph_kernel_size": 5,
        "min_area": 8,
        "max_area": 250,
        "description": "Строго, но ок",
    },
    "TEAM_10": {
        "name": "Команда №10",
        "color_bgr": (196, 29, 18),
        "display_color_bgr": (20, 31, 121),  # Team palette (screenshot)
        "hsv_range": ((0, 201, 94), (179, 226, 101)),
        "morph_kernel_size": 10,
        "min_area": 8,
        "max_area": 250,
        "description": "Супер! Но может потеряться в красной зоне",
    },
    "TEAM_11": {
        "name": "Команда №11",
        "color_bgr": (159, 59, 13),
        "display_color_bgr": (13, 58, 159),  # Team palette (screenshot)
        "hsv_range": ((4, 220, 125), (10, 241, 135)),
        "morph_kernel_size": 6,
        "min_area": 8,
        "max_area": 250,
        "description": "Супер!",
    },
    "TEAM_12": {
        "name": "Команда №12",
        "color_bgr": (119, 75, 1),
        "display_color_bgr": (1, 75, 118),  # Team palette (screenshot)
        "hsv_range": ((18, 241, 97), (21, 255, 101)),
        "morph_kernel_size": 5,
        "min_area": 8,
        "max_area": 250,
        "description": "Супер! Но слишком строго",
    },
    "TEAM_13": {
        "name": "Команда №13",
        "color_bgr": (203, 121, 19),
        "display_color_bgr": (18, 122, 206),  # Team palette (screenshot)
        "hsv_range": ((13, 208, 168), (17, 245, 179)),
        "morph_kernel_size": 5,
        "min_area": 8,
        "max_area": 250,
        "description": "Супер!",
    },
    "TEAM_14": {
        "name": "Команда №14",
        "color_bgr": (150, 125, 0),
        "display_color_bgr": (1, 126, 150),  # Team palette (screenshot)
        "hsv_range": ((22, 201, 110), (28, 255, 168)),
        "morph_kernel_size": 5,
        "min_area": 8,
        "max_area": 250,
        "outlier_threshold_ratio": 0.25,  # 25% = 50px (очень мягко из-за множественных детекций)
        "description": "Супер!",
    },
    "TEAM_15": {
        "name": "Команда №15",
        "color_bgr": (132, 147, 9),
        "display_color_bgr": (10, 147, 132),  # Team palette (screenshot)
        "hsv_range": ((29, 173, 118), (37, 239, 158)),
        "morph_kernel_size": 5,
        "min_area": 8,
        "max_area": 250,
        "description": "Супер!",
    },
    "TEAM_16": {
        "name": "Команда №16",
        "color_bgr": (74, 88, 3),
        "display_color_bgr": (3, 89, 73),  # Team palette (screenshot)
        "hsv_range": ((26, 210, 0), (40, 250, 95)),
        "morph_kernel_size": 5,
        "min_area": 8,
        "max_area": 250,
        "description": "Супер!",
    },
    "TEAM_17": {
        "name": "Команда №17",
        "color_bgr": (111, 151, 66),
        "display_color_bgr": (68, 152, 113),  # Team palette (screenshot)
        "hsv_range": ((32, 127, 128), (47, 153, 151)),
        "morph_kernel_size": 5,
        "min_area": 8,
        "max_area": 250,
        "description": "Супер!",
    },
    "TEAM_18": {
        "name": "Команда №18",
        "color_bgr": (57, 137, 53),
        "display_color_bgr": (53, 137, 57),  # Team palette (screenshot)
        "hsv_range": ((55, 128, 92), (61, 180, 139)),
        "morph_kernel_size": 5,
        "min_area": 8,
        "max_area": 250,
        "description": "Супер!",
    },
    "TEAM_19": {
        "name": "Команда №19",
        "color_bgr": (47, 89, 25),
        "display_color_bgr": (25, 91, 47),  # Team palette (screenshot)
        "hsv_range": ((46, 158, 76), (55, 203, 111)),
        "morph_kernel_size": 5,
        "min_area": 8,
        "max_area": 250,
        "description": "Супер!",
    },
    "TEAM_20": {
        "name": "Команда №20",
        "color_bgr": (0, 117, 87),
        "display_color_bgr": (87, 117, 1),  # Team palette (screenshot)
        "hsv_range": ((69, 147, 104), (80, 255, 118)),
        "morph_kernel_size": 5,
        "min_area": 8,
        "max_area": 250,
        "description": "Супер!",
    },
}

# Настройки цветов по картам
MAP_TEAM_COLORS = {
    "mp_storm_point": STORM_POINT_TEAMS,
}

# Дополнительные настройки по картам
MAP_RUNTIME_SETTINGS = {
    "mp_storm_point": {
        "frame_skip": 8,
        "round_windows": {
            "round1": {"start_sec": 0, "end_sec": 375},
            "round2": {"start_sec": 375, "end_sec": 600},
        },
    },
}


def normalize_map_name(map_name: str) -> str:
    """Привести имя карты к формату mp_*."""
    return map_name if map_name.startswith("mp_") else f"mp_{map_name}"


def get_team_config(team_number: int, map_name: str = "storm_point"):
    """Получить конфигурацию команды для указанной карты."""
    map_key = normalize_map_name(map_name)
    teams = MAP_TEAM_COLORS.get(map_key, MAP_TEAM_COLORS.get("mp_storm_point", {}))
    return teams.get(f"TEAM_{team_number}")


def get_all_teams_for_map(map_name: str):
    """Получить все команды для указанной карты."""
    map_key = normalize_map_name(map_name)
    return MAP_TEAM_COLORS.get(map_key, MAP_TEAM_COLORS.get("mp_storm_point", {}))


def get_frame_skip(map_name: str = "storm_point", default: int = 5) -> int:
    """Получить FRAME_SKIP для указанной карты."""
    map_key = normalize_map_name(map_name)
    if map_key in MAP_RUNTIME_SETTINGS:
        return MAP_RUNTIME_SETTINGS[map_key].get("frame_skip", default)
    return MAP_RUNTIME_SETTINGS.get("mp_storm_point", {}).get("frame_skip", default)


def get_round_windows(map_name: str = "storm_point"):
    """Получить временные окна для кругов (round1/round2)."""
    map_key = normalize_map_name(map_name)
    windows = MAP_RUNTIME_SETTINGS.get(map_key, {}).get("round_windows")
    if not windows:
        windows = MAP_RUNTIME_SETTINGS.get("mp_storm_point", {}).get("round_windows")
    if windows:
        return windows
    return {
        "round1": {"start_sec": 0, "end_sec": 375},
        "round2": {"start_sec": 375, "end_sec": 600},
    }
