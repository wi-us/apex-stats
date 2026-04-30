"""
Модуль для определения команд и их цветов на боковых панелях интерфейса Apex Legends.
"""

import cv2
import numpy as np
import logging
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass
import re

# Опциональный импорт pytesseract
try:
    import pytesseract
    HAS_PYTESSERACT = True
except ImportError:
    HAS_PYTESSERACT = False
    pytesseract = None

logger = logging.getLogger(__name__)


@dataclass
class TeamInfo:
    """Информация о команде."""
    number: str
    name: str
    color_bgr: Tuple[int, int, int]
    color_hsv_range: Tuple[Tuple[int, int, int], Tuple[int, int, int]]
    position: str  # 'left' или 'right'
    rank: Optional[int] = None


class TeamDetector:
    """Класс для детекции команд на боковых панелях."""
    
    def __init__(self):
        """Инициализация детектора команд."""
        # Области для анализа (левая и правая панели)
        self.left_panel_roi = (0, 0, 225, 720)    # x, y, width, height
        self.right_panel_roi = (795, 0, 225, 720)
        
        # Размеры цветовых квадратов команд (примерные)
        self.color_square_min_area = 100
        self.color_square_max_area = 2000
        
        # Параметры для HSV анализа цветов
        self.hsv_tolerance = 20
        
        # Настройки OCR
        self.ocr_config = '--oem 3 --psm 8 -c tessedit_char_whitelist=0123456789#'
        
        # Проверка доступности OCR
        if not HAS_PYTESSERACT:
            logger.warning("Pytesseract не установлен. OCR функции недоступны. Будет использоваться базовое распознавание.")
            
        # Простые шаблоны номеров команд для fallback
        self.team_number_patterns = [
            r'#(\d+)', r'(\d+)', r'Team\s*(\d+)', r'TEAM\s*(\d+)'
        ]
        
        # Кеш найденных команд
        self._teams_cache = {}
        
    def _extract_roi(self, frame: np.ndarray, roi: Tuple[int, int, int, int]) -> np.ndarray:
        """
        Извлечь область интереса из кадра.
        
        Args:
            frame: Исходный кадр
            roi: Область интереса (x, y, width, height)
            
        Returns:
            Извлеченная область
        """
        x, y, w, h = roi
        return frame[y:y+h, x:x+w]
        
    def _find_color_squares(self, roi_image: np.ndarray) -> List[Tuple[int, int, int, int, Tuple[int, int, int]]]:
        """
        Найти цветовые квадраты команд в области.
        
        Args:
            roi_image: Изображение области панели
            
        Returns:
            Список кортежей (x, y, w, h, color_bgr)
        """
        squares = []
        
        try:
            # Преобразование в HSV для лучшего анализа цветов
            hsv = cv2.cvtColor(roi_image, cv2.COLOR_BGR2HSV)
            
            # Размытие для уменьшения шума
            blurred = cv2.GaussianBlur(roi_image, (3, 3), 0)
            
            # Поиск контуров на каждом цветовом канале
            for channel in range(3):
                # Извлечение канала
                channel_img = blurred[:, :, channel]
                
                # Адаптивная бинаризация
                binary = cv2.adaptiveThreshold(
                    channel_img, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, 
                    cv2.THRESH_BINARY, 11, 2
                )
                
                # Морфологические операции для очистки
                kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
                cleaned = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)
                
                # Поиск контуров
                contours, _ = cv2.findContours(cleaned, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                
                for contour in contours:
                    # Фильтрация по площади
                    area = cv2.contourArea(contour)
                    if self.color_square_min_area <= area <= self.color_square_max_area:
                        # Проверка на прямоугольность
                        approx = cv2.approxPolyDP(contour, 0.02 * cv2.arcLength(contour, True), True)
                        
                        if len(approx) >= 4:  # Примерно прямоугольная форма
                            x, y, w, h = cv2.boundingRect(contour)
                            
                            # Проверка пропорций (квадрат или близко к нему)
                            aspect_ratio = w / h
                            if 0.7 <= aspect_ratio <= 1.4:
                                # Извлечение среднего цвета области
                                color_region = roi_image[y:y+h, x:x+w]
                                mean_color = np.mean(color_region, axis=(0, 1)).astype(int)
                                
                                # Проверка, что цвет достаточно насыщенный
                                hsv_color = cv2.cvtColor(np.uint8([[mean_color]]), cv2.COLOR_BGR2HSV)[0][0]
                                if hsv_color[1] > 50:  # Минимальная насыщенность
                                    squares.append((x, y, w, h, tuple(mean_color)))
                                    
            # Удаление дубликатов (объединение близких квадратов)
            unique_squares = []
            for square in squares:
                x, y, w, h, color = square
                is_duplicate = False
                
                for existing in unique_squares:
                    ex_x, ex_y, ex_w, ex_h, ex_color = existing
                    
                    # Проверка перекрытия областей
                    if (abs(x - ex_x) < 20 and abs(y - ex_y) < 20 and 
                        abs(w - ex_w) < 10 and abs(h - ex_h) < 10):
                        is_duplicate = True
                        break
                        
                if not is_duplicate:
                    unique_squares.append(square)
                    
        except Exception as e:
            logger.error(f"Ошибка поиска цветовых квадратов: {e}")
            
        return unique_squares
        
    def _extract_team_number(self, roi_image: np.ndarray, square_box: Tuple[int, int, int, int]) -> Optional[str]:
        """
        Извлечь номер команды из области квадрата.
        
        Args:
            roi_image: Изображение области панели
            square_box: Координаты цветового квадрата (x, y, w, h)
            
        Returns:
            Номер команды или None
        """
        try:
            x, y, w, h = square_box
            
            # Расширение области для захвата текста рядом с квадратом
            text_region = roi_image[max(0, y-5):y+h+5, max(0, x-5):x+w+30]
            
            if HAS_PYTESSERACT:
                # Используем OCR если доступен
                gray = cv2.cvtColor(text_region, cv2.COLOR_BGR2GRAY)
                
                # Увеличение контраста
                enhanced = cv2.convertScaleAbs(gray, alpha=2.0, beta=0)
                
                # Бинаризация
                _, binary = cv2.threshold(enhanced, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
                
                # Морфологическая очистка
                kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
                cleaned = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)
                
                # OCR
                text = pytesseract.image_to_string(cleaned, config=self.ocr_config).strip()
                
                # Поиск номера команды
                number_match = re.search(r'#?(\d+)', text)
                if number_match:
                    return number_match.group(1)
                    
                # Дополнительная попытка
                text2 = pytesseract.image_to_string(gray, config='--oem 3 --psm 6').strip()
                number_match2 = re.search(r'#?(\d+)', text2)
                if number_match2:
                    return number_match2.group(1)
            else:
                # Fallback: простое определение номера по позиции
                # Предполагаем что команды расположены по порядку
                if x < roi_image.shape[1] // 2:  # Левая часть панели
                    if y < 200:
                        return "1"
                    elif y < 400:
                        return "2"
                    elif y < 600:
                        return "3"
                else:  # Правая часть панели
                    if y < 200:
                        return "4"
                    elif y < 400:
                        return "5"
                    elif y < 600:
                        return "6"
                
        except Exception as e:
            logger.debug(f"Ошибка извлечения номера команды: {e}")
            
        return None
        
    def _extract_team_name(self, roi_image: np.ndarray, square_box: Tuple[int, int, int, int]) -> Optional[str]:
        """
        Извлечь название команды из области рядом с квадратом.
        
        Args:
            roi_image: Изображение области панели
            square_box: Координаты цветового квадрата (x, y, w, h)
            
        Returns:
            Название команды или None
        """
        try:
            x, y, w, h = square_box
            
            # Область с названием команды (справа от квадрата)
            name_region = roi_image[y:y+h, x+w+5:x+w+100]
            
            if name_region.size == 0:
                return None
                
            if HAS_PYTESSERACT:
                # Используем OCR если доступен
                gray = cv2.cvtColor(name_region, cv2.COLOR_BGR2GRAY)
                
                # Увеличение размера для лучшего OCR
                scale_factor = 2
                resized = cv2.resize(gray, None, fx=scale_factor, fy=scale_factor, interpolation=cv2.INTER_CUBIC)
                
                # Бинаризация
                _, binary = cv2.threshold(resized, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
                
                # OCR с настройками для текста
                text = pytesseract.image_to_string(binary, config='--oem 3 --psm 8').strip()
                
                # Очистка результата от мусора
                cleaned_text = re.sub(r'[^A-Za-z0-9\s]', '', text).strip()
                
                if len(cleaned_text) >= 2:  # Минимальная длина названия
                    return cleaned_text.upper()
            else:
                # Fallback: генерируем стандартное имя на основе цвета
                x, y, w, h = square_box
                
                # Анализируем цвет квадрата для приблизительного определения
                color_region = roi_image[y:y+h, x:x+w]
                mean_color = np.mean(color_region, axis=(0, 1))
                b, g, r = mean_color
                
                # Простая классификация по доминирующему цвету
                if r > g and r > b:
                    return "RED_TEAM"
                elif g > r and g > b:
                    return "GREEN_TEAM"
                elif b > r and b > g:
                    return "BLUE_TEAM"
                elif r > 150 and g > 150:
                    return "YELLOW_TEAM"
                elif r > 100 and b > 100:
                    return "PURPLE_TEAM"
                elif g > 100 and b > 100:
                    return "CYAN_TEAM"
                else:
                    return "UNKNOWN_TEAM"
                
        except Exception as e:
            logger.debug(f"Ошибка извлечения названия команды: {e}")
            
        return None
        
    def _color_to_hsv_range(self, color_bgr: Tuple[int, int, int]) -> Tuple[Tuple[int, int, int], Tuple[int, int, int]]:
        """
        Преобразовать BGR цвет в диапазон HSV для фильтрации.
        
        Args:
            color_bgr: Цвет в формате BGR
            
        Returns:
            Кортеж (нижний_hsv, верхний_hsv)
        """
        # Преобразование в HSV
        bgr_array = np.uint8([[color_bgr]])
        hsv_color = cv2.cvtColor(bgr_array, cv2.COLOR_BGR2HSV)[0][0]
        h, s, v = hsv_color
        
        # Создание диапазона с учетом толерантности
        lower_hsv = (
            max(0, h - self.hsv_tolerance),
            max(0, s - 50),
            max(0, v - 50)
        )
        upper_hsv = (
            min(179, h + self.hsv_tolerance),
            255,
            255
        )
        
        return (lower_hsv, upper_hsv)
        
    def detect_teams(self, frame: np.ndarray, force_refresh: bool = False) -> Dict[str, TeamInfo]:
        """
        Определить команды на кадре.
        
        Args:
            frame: Кадр видео для анализа
            force_refresh: Принудительно обновить информацию о командах
            
        Returns:
            Словарь команд {номер_команды: TeamInfo}
        """
        if not force_refresh and self._teams_cache:
            return self._teams_cache
            
        teams = {}
        
        try:
            # Анализ левой панели
            left_roi = self._extract_roi(frame, self.left_panel_roi)
            left_squares = self._find_color_squares(left_roi)
            
            for i, (x, y, w, h, color) in enumerate(left_squares):
                team_number = self._extract_team_number(left_roi, (x, y, w, h))
                team_name = self._extract_team_name(left_roi, (x, y, w, h))
                
                if team_number:
                    hsv_range = self._color_to_hsv_range(color)
                    
                    team_info = TeamInfo(
                        number=team_number,
                        name=team_name or f"TEAM_{team_number}",
                        color_bgr=color,
                        color_hsv_range=hsv_range,
                        position="left"
                    )
                    
                    teams[team_number] = team_info
                    logger.info(f"Найдена команда на левой панели: #{team_number} - {team_info.name}")
                    
            # Анализ правой панели
            right_roi = self._extract_roi(frame, self.right_panel_roi)
            right_squares = self._find_color_squares(right_roi)
            
            for i, (x, y, w, h, color) in enumerate(right_squares):
                team_number = self._extract_team_number(right_roi, (x, y, w, h))
                team_name = self._extract_team_name(right_roi, (x, y, w, h))
                
                if team_number and team_number not in teams:  # Избегаем дублирования
                    hsv_range = self._color_to_hsv_range(color)
                    
                    team_info = TeamInfo(
                        number=team_number,
                        name=team_name or f"TEAM_{team_number}",
                        color_bgr=color,
                        color_hsv_range=hsv_range,
                        position="right"
                    )
                    
                    teams[team_number] = team_info
                    logger.info(f"Найдена команда на правой панели: #{team_number} - {team_info.name}")
                    
            # Кеширование результатов
            self._teams_cache = teams
            
            logger.info(f"Всего найдено команд: {len(teams)}")
            
        except Exception as e:
            logger.error(f"Ошибка детекции команд: {e}")
            
        return teams
        
    def get_team_colors_for_tracking(self) -> Dict[str, Tuple[Tuple[int, int, int], Tuple[int, int, int]]]:
        """
        Получить HSV диапазоны цветов команд для трекинга.
        
        Returns:
            Словарь {номер_команды: (lower_hsv, upper_hsv)}
        """
        if not self._teams_cache:
            logger.warning("Команды не определены. Вызовите detect_teams() сначала.")
            return {}
            
        return {
            team_num: team_info.color_hsv_range 
            for team_num, team_info in self._teams_cache.items()
        }
        
    def visualize_detection(self, frame: np.ndarray, save_path: Optional[str] = None) -> np.ndarray:
        """
        Визуализировать найденные команды на кадре.
        
        Args:
            frame: Исходный кадр
            save_path: Путь для сохранения результата
            
        Returns:
            Кадр с визуализацией
        """
        viz_frame = frame.copy()
        
        # Отметка областей панелей
        cv2.rectangle(viz_frame, (0, 0), (225, 720), (255, 255, 0), 2)
        cv2.rectangle(viz_frame, (795, 0), (1020, 720), (255, 255, 0), 2)
        
        # Отметка найденных команд
        for team_num, team_info in self._teams_cache.items():
            color = team_info.color_bgr
            
            # Подпись с информацией о команде
            text = f"#{team_num}: {team_info.name}"
            
            if team_info.position == "left":
                cv2.putText(viz_frame, text, (10, 30 + int(team_num) * 25), 
                           cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
            else:
                cv2.putText(viz_frame, text, (800, 30 + int(team_num) * 25), 
                           cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
                           
        if save_path:
            cv2.imwrite(save_path, viz_frame)
            
        return viz_frame