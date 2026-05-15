"""
Совместимый адаптер для конфигов цветов команд.

Основные данные перенесены в `tracking_settings.py`.
Оставлено для обратной совместимости существующих импортов.
"""

from .tracking_settings import (
    MAP_TEAM_COLORS,
    get_all_teams_for_map,
    get_team_config,
)
