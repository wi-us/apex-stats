"""
Конфигурация параметров для системы отслеживания команд на карте Apex Legends.
"""

import os

# Пути к файлам
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VIDEO_PATH = os.path.join(BASE_DIR, "ffmpeg_downloader", "my_match.mp4")
MAP_PATH = os.path.join(BASE_DIR, "maps", "mp_storm_point.png")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
OUTPUT_PATH = os.path.join(OUTPUT_DIR, "team_trajectories.png")

# Параметры видео (1920x1080)
# Левая панель: 0-420px, Карта: 420-1480px, Правая панель: 1480-1920px
LEFT_PANEL_ROI = (0, 0, 420, 1080)  # Левая панель с командами
MAP_ROI = (420, 0, 1080, 1080)  # Центральная область карты (x, y, width, height)
RIGHT_PANEL_ROI = (1480, 0, 420, 1080)  # Правая панель с командами

# Параметры отслеживания
OBSERVATION_TIME = 10  # секунд для определения начальной точки
STOP_THRESHOLD_TIME = 40  # секунд для регистрации остановки
STOP_THRESHOLD_DISTANCE = 10  # пикселей - максимальное расстояние для остановки
ROI_SEARCH_SIZE = 200  # размер области поиска вокруг последней позиции
MAX_JUMP_DISTANCE = 50  # максимальное расстояние скачка между кадрами (пикселей) - увеличено с 20 до 50

# Параметры детекции стрелочек
MIN_ARROW_AREA = 8  # минимальная площадь контура стрелочки (уменьшено для захвата маленьких стрелочек)
MAX_ARROW_AREA = 250  # максимальная площадь контура стрелочки (увеличено)
MIN_MOVEMENT_DISTANCE = 5  # минимальное расстояние для регистрации движения (пикселей)

# Параметры цветовой фильтрации HSV
HSV_TOLERANCE = 25  # толерантность для HSV диапазона
SATURATION_MIN = 50  # минимальная насыщенность для фильтрации
VALUE_MIN = 50  # минимальная яркость для фильтрации

# Параметры морфологических операций
MORPH_KERNEL_SIZE = (5, 5)  # размер ядра для морфологических операций (из HSV Tuner)
BLUR_KERNEL_SIZE = 5  # размер ядра для размытия

# Тестовые команды
TEST_TEAMS = ["FLCN", "FNC", "CRT"]

# Параметры визуализации
TRAJECTORY_LINE_THICKNESS = 2  # толщина линии траектории
START_MARKER_SIZE = 10  # размер квадратного маркера начальной точки
STOP_MARKER_RADIUS = 15  # радиус круглого маркера остановки
FONT_SCALE = 0.6  # размер шрифта для меток
FONT_THICKNESS = 2  # толщина шрифта

# Параметры обработки видео
VIDEO_FPS = 30  # предполагаемый FPS видео (будет определен автоматически)
FRAME_SKIP = 5  # обрабатывать каждый N-й кадр (1 = все кадры)
MAX_FRAMES_TO_PROCESS = None  # Максимальное количество кадров для обработки (None = все)

# Логирование
LOG_LEVEL = "INFO"  # уровень логирования: DEBUG, INFO, WARNING, ERROR
LOG_INTERVAL = 100  # интервал логирования прогресса (каждые N кадров)
