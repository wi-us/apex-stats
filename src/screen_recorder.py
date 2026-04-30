import cv2
import numpy as np
import time
import logging
import threading
from datetime import datetime
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from typing import Optional, Dict

# Пробуем импортировать mss, если не получается - используем PIL
try:
    import mss
    HAS_MSS = True
except ImportError:
    HAS_MSS = False
    from PIL import ImageGrab

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class ScreenRecorder:
    """
    Класс для записи экрана с использованием OpenCV.
    Поддерживает mss (быстрый) или PIL ImageGrab (запасной вариант).
    Оптимизирован для захвата области видео плеера в браузере.
    """
    
    def __init__(self, fps=30):
        """
        Args:
            fps: Частота кадров для записи (по умолчанию 30)
        """
        self.fps = fps
        self.is_recording = False
        self.monitor_thread = None
        self.driver = None
        
        if HAS_MSS:
            self.sct = mss.mss()
            self.capture_method = 'mss'
            logger.info("Используется mss для захвата экрана (быстрый режим)")
        else:
            self.sct = None
            self.capture_method = 'pil'
            logger.info("Используется PIL ImageGrab для захвата экрана")
        
    def get_video_element_bounds(self, driver: WebDriver) -> Optional[Dict]:
        """
        Получение координат видео элемента на экране через Selenium.
        
        Args:
            driver: Selenium WebDriver instance
            
        Returns:
            Dict с координатами {'x', 'y', 'width', 'height'} или None
        """
        if not driver:
            logger.error("WebDriver не инициализирован")
            return None
            
        try:
            # Поиск видео элемента (включая Shadow DOM)
            bounds = driver.execute_script("""
                function findVideo() {
                    // Поиск в обычном DOM
                    let video = document.querySelector('video');
                    if (video) return video;
                    
                    // Поиск в Shadow DOM
                    let allElements = document.querySelectorAll('*');
                    for (let el of allElements) {
                        if (el.shadowRoot) {
                            let shadowVideo = el.shadowRoot.querySelector('video');
                            if (shadowVideo) return shadowVideo;
                        }
                    }
                    return null;
                }
                
                let video = findVideo();
                if (!video) return null;
                
                let rect = video.getBoundingClientRect();
                return {
                    x: Math.round(rect.left + window.screenX),
                    y: Math.round(rect.top + window.screenY),
                    width: Math.round(rect.width),
                    height: Math.round(rect.height)
                };
            """)
            
            if bounds and bounds.get('width', 0) > 0 and bounds.get('height', 0) > 0:
                logger.info(f"Координаты видео элемента: x={bounds['x']}, y={bounds['y']}, "
                           f"width={bounds['width']}, height={bounds['height']}")
                return bounds
            else:
                logger.warning("Видео элемент найден, но имеет нулевой размер")
                return None
                
        except Exception as e:
            logger.error(f"Ошибка получения координат видео: {e}")
            return None
    
    def ensure_video_playing(self, driver: WebDriver) -> bool:
        """
        Убедиться что видео воспроизводится (не на паузе).
        
        Args:
            driver: Selenium WebDriver instance
            
        Returns:
            True если видео играет, False иначе
        """
        try:
            result = driver.execute_script("""
                function findVideo() {
                    let video = document.querySelector('video');
                    if (video) return video;
                    
                    let allElements = document.querySelectorAll('*');
                    for (let el of allElements) {
                        if (el.shadowRoot) {
                            let shadowVideo = el.shadowRoot.querySelector('video');
                            if (shadowVideo) return shadowVideo;
                        }
                    }
                    return null;
                }
                
                let video = findVideo();
                if (!video) return {found: false};
                
                // Проверяем состояние воспроизведения
                let isPaused = video.paused;
                let currentTime = video.currentTime;
                
                // Если на паузе - пытаемся запустить
                if (isPaused) {
                    try {
                        // Кликаем на видео для активации
                        video.click();
                        
                        // Ищем кнопку Play внутри плеера
                        let playBtn = document.querySelector('button[aria-label*="Play" i]') ||
                                     document.querySelector('button[data-testid*="play" i]') ||
                                     document.querySelector('.play-button');
                        
                        if (playBtn) {
                            playBtn.click();
                        }
                        
                        // Запускаем воспроизведение программно
                        video.play();
                        
                        return {found: true, wasPaused: true, playing: true, currentTime: currentTime, clickedPlay: !!playBtn};
                    } catch (e) {
                        return {found: true, wasPaused: true, playing: false, error: e.message};
                    }
                }
                
                return {found: true, wasPaused: false, playing: true, currentTime: currentTime};
            """)
            
            if result.get('found'):
                current_time = result.get('currentTime', 0)
                if result.get('wasPaused'):
                    logger.info(f"Видео было на паузе (время: {current_time:.1f}с), запущено воспроизведение")
                else:
                    logger.debug(f"Видео воспроизводится (время: {current_time:.1f}с)")
                return result.get('playing', False)
            else:
                logger.warning("Видео элемент не найден")
                return False
                
        except Exception as e:
            logger.error(f"Ошибка запуска видео: {e}")
            return False
    
    def close_event_panel(self, driver: WebDriver) -> bool:
        """
        Закрыть панель Events если она открыта.
        Проверяет aria-selected="true" и кликает для закрытия.
        
        Args:
            driver: Selenium WebDriver instance
            
        Returns:
            True если панель закрыта или не была открыта
        """
        try:
            result = driver.execute_script("""
                let eventBtn = document.querySelector('button[aria-label*="Event" i]');
                if (!eventBtn) return {found: false};
                
                // Проверяем aria-selected (должно быть false для закрытой панели)
                let isSelected = eventBtn.getAttribute('aria-selected') === 'true';
                
                if (isSelected) {
                    eventBtn.click();
                    return {found: true, wasClosed: true, ariaSelected: 'true->false'};
                }
                
                return {found: true, wasClosed: false, ariaSelected: eventBtn.getAttribute('aria-selected')};
            """)
            
            if result.get('wasClosed'):
                logger.info(f"Event панель была открыта (aria-selected=true) и закрыта")
                time.sleep(2)
            else:
                logger.debug(f"Event панель уже закрыта (aria-selected={result.get('ariaSelected')})")
            
            return True
            
        except Exception as e:
            logger.warning(f"Ошибка закрытия Event панели: {e}")
            return False
    
    def click_fullscreen(self, driver: WebDriver, retry_count=3) -> bool:
        """
        Кликнуть на кнопку Fullscreen для развертывания видео.
        Поддерживает поиск в обычном DOM, Shadow DOM и внутри iframe.
        
        Args:
            driver: Selenium WebDriver instance
            retry_count: Количество попыток поиска кнопки
            
        Returns:
            True если клик успешен
        """
        for attempt in range(retry_count):
            try:
                result = driver.execute_script("""
                    function findFullscreenButton() {
                        // Ищем в обычном DOM
                        let selectors = [
                            '[data-testid="fullscreen-control-button"]',
                            '[data-testid="fullscreen-icon"]',
                            'button[aria-label="Fullscreen"]',
                            'button[aria-label*="fullscreen" i]',
                            'button[data-testid*="fullscreen" i]',
                            '.fullscreen-button',
                            'button[title*="fullscreen" i]',
                            'div[data-testid="fullscreen-icon"]'
                        ];
                        
                        for (let selector of selectors) {
                            let btn = document.querySelector(selector);
                            if (btn) return {btn: btn, location: 'normal DOM', selector: selector};
                        }
                        
                        // Ищем в Shadow DOM
                        let allElements = document.querySelectorAll('*');
                        for (let el of allElements) {
                            if (el.shadowRoot) {
                                for (let selector of selectors) {
                                    let btn = el.shadowRoot.querySelector(selector);
                                    if (btn) return {btn: btn, location: 'shadow DOM', selector: selector};
                                }
                            }
                        }
                        
                        // Ищем внутри iframe
                        let iframes = document.querySelectorAll('iframe');
                        for (let iframe of iframes) {
                            try {
                                let iframeDoc = iframe.contentDocument || iframe.contentWindow.document;
                                for (let selector of selectors) {
                                    let btn = iframeDoc.querySelector(selector);
                                    if (btn) return {btn: btn, location: 'iframe', selector: selector};
                                }
                            } catch (e) {
                                // Cross-origin iframe, пропускаем
                            }
                        }
                        
                        return null;
                    }
                    
                    let result = findFullscreenButton();
                    if (result) {
                        result.btn.click();
                        return {found: true, clicked: true, location: result.location, selector: result.selector};
                    }
                    
                    return {found: false};
                """)
                
                if result.get('clicked'):
                    logger.info(f"Кнопка Fullscreen нажата (найдена в {result.get('location')}, селектор: {result.get('selector')})")
                    time.sleep(3)
                    return True
                else:
                    if attempt < retry_count - 1:
                        logger.debug(f"Попытка {attempt + 1}/{retry_count}: Кнопка Fullscreen не найдена, повтор через 2 сек...")
                        time.sleep(2)
                    else:
                        logger.warning("Кнопка Fullscreen не найдена после всех попыток")
                        return False
                    
            except Exception as e:
                if attempt < retry_count - 1:
                    logger.debug(f"Попытка {attempt + 1}/{retry_count}: Ошибка клика на Fullscreen: {e}, повтор...")
                    time.sleep(2)
                else:
                    logger.warning(f"Ошибка клика на Fullscreen после всех попыток: {e}")
                    return False
        
        return False
    
    def click_video_center_to_toggle_play(self, driver: WebDriver) -> bool:
        """
        Кликает по центру видео области для переключения Play/Pause.
        Использует JavaScript для точного клика по координатам.
        
        Args:
            driver: Selenium WebDriver instance
            
        Returns:
            True если клик выполнен
        """
        try:
            result = driver.execute_script("""
                function findVideo() {
                    let video = document.querySelector('video');
                    if (video) return video;
                    
                    let allElements = document.querySelectorAll('*');
                    for (let el of allElements) {
                        if (el.shadowRoot) {
                            let shadowVideo = el.shadowRoot.querySelector('video');
                            if (shadowVideo) return shadowVideo;
                        }
                    }
                    return null;
                }
                
                let video = findVideo();
                if (!video) return {found: false};
                
                // Получаем размеры видео контейнера
                let rect = video.getBoundingClientRect();
                
                // Кликаем по центру видео
                let centerX = rect.left + rect.width / 2;
                let centerY = rect.top + rect.height / 2;
                
                // Создаем и отправляем клик
                let clickEvent = new MouseEvent('click', {
                    view: window,
                    bubbles: true,
                    cancelable: true,
                    clientX: centerX,
                    clientY: centerY
                });
                
                video.dispatchEvent(clickEvent);
                
                // Также пробуем программно запустить
                let wasPaused = video.paused;
                try {
                    if (wasPaused) {
                        video.play();
                    }
                } catch (e) {}
                
                return {
                    found: true, 
                    clicked: true, 
                    wasPaused: wasPaused,
                    currentTime: video.currentTime,
                    readyState: video.readyState
                };
            """)
            
            if result.get('clicked'):
                logger.info(f"Клик по центру видео (было на паузе: {result.get('wasPaused')}, "
                           f"время: {result.get('currentTime', 0):.1f}с, readyState: {result.get('readyState')})")
                return True
            return False
            
        except Exception as e:
            logger.debug(f"Ошибка клика по центру видео: {e}")
            return False
    
    def activate_player_controls_and_play(self, driver: WebDriver) -> bool:
        """
        Активирует контролы плеера через движение мыши и кликает Play + Fullscreen.
        Контролы появляются в data-testid="player-controls" при наведении на видео.
        
        Args:
            driver: Selenium WebDriver instance
            
        Returns:
            True если успешно запущено видео
        """
        try:
            logger.info("Активация контролов плеера через движение мыши...")
            
            # Используем JavaScript для имитации событий мыши над видео
            # Это более надежно чем ActionChains для элементов в Shadow DOM
            mouse_result = driver.execute_script("""
                function findVideo() {
                    let video = document.querySelector('video');
                    if (video) return video;
                    
                    let allElements = document.querySelectorAll('*');
                    for (let el of allElements) {
                        if (el.shadowRoot) {
                            let shadowVideo = el.shadowRoot.querySelector('video');
                            if (shadowVideo) return shadowVideo;
                        }
                    }
                    return null;
                }
                
                let video = findVideo();
                if (!video) return {videoFound: false};
                
                // Имитируем движение мыши над видео
                let rect = video.getBoundingClientRect();
                let centerX = rect.left + rect.width / 2;
                let centerY = rect.top + rect.height / 2;
                
                // Создаем события мыши
                let events = ['mouseover', 'mouseenter', 'mousemove'];
                for (let eventType of events) {
                    let event = new MouseEvent(eventType, {
                        view: window,
                        bubbles: true,
                        cancelable: true,
                        clientX: centerX,
                        clientY: centerY
                    });
                    video.dispatchEvent(event);
                }
                
                return {videoFound: true, mouseEventsDispatched: true};
            """)
            
            if not mouse_result.get('videoFound'):
                logger.warning("Видео элемент не найден для активации контролов")
                return False
            
            logger.info("События мыши отправлены, ожидание появления контролов...")
            time.sleep(1)
            
            # Ждем немного для появления контролов
            time.sleep(1)
            
            # Теперь ищем и кликаем на Play внутри player-controls
            logger.info("Поиск кнопки Play в контролах плеера...")
            result = driver.execute_script("""
                // Ждем появления контролов
                let controls = document.querySelector('[data-testid="player-controls"]');
                if (!controls) {
                    // Пробуем найти по классу
                    controls = document.querySelector('.player-controls');
                }
                
                if (!controls) {
                    return {controlsFound: false};
                }
                
                // Ищем кнопку Play
                let playBtn = controls.querySelector('button[aria-label="Play"]') ||
                             controls.querySelector('[data-testid="play-control-button"]') ||
                             controls.querySelector('button[data-testid*="play" i]');
                
                if (playBtn) {
                    playBtn.click();
                    return {controlsFound: true, playClicked: true};
                }
                
                return {controlsFound: true, playClicked: false, controlsHTML: controls.innerHTML.substring(0, 200)};
            """)
            
            if result.get('playClicked'):
                logger.info("Кнопка Play нажата через контролы плеера")
                time.sleep(2)
                
                # Теперь кликаем Fullscreen
                logger.info("Поиск кнопки Fullscreen в контролах плеера...")
                fs_result = driver.execute_script("""
                    let controls = document.querySelector('[data-testid="player-controls"]');
                    if (!controls) return {found: false};
                    
                    let fsBtn = controls.querySelector('[data-testid="fullscreen-control-button"]') ||
                               controls.querySelector('button[aria-label="Fullscreen"]') ||
                               controls.querySelector('[data-testid="fullscreen-icon"]');
                    
                    if (fsBtn) {
                        fsBtn.click();
                        return {found: true, clicked: true};
                    }
                    
                    return {found: false};
                """)
                
                if fs_result.get('clicked'):
                    logger.info("Кнопка Fullscreen нажата через контролы плеера")
                    time.sleep(2)
                
                return True
            else:
                logger.warning(f"Контролы плеера: найдены={result.get('controlsFound')}, Play не найден")
                return False
                
        except Exception as e:
            logger.error(f"Ошибка активации контролов плеера: {e}")
            return False
    
    def click_play_button(self, driver: WebDriver) -> bool:
        """
        Найти и кликнуть на кнопку Play в интерфейсе плеера.
        
        Args:
            driver: Selenium WebDriver instance
            
        Returns:
            True если клик успешен
        """
        try:
            result = driver.execute_script("""
                // Ищем кнопку Play по разным селекторам
                let selectors = [
                    'button[aria-label*="Play" i]',
                    'button[data-testid*="play" i]',
                    '.play-button',
                    'button[title*="Play" i]',
                    '[data-testid="play-control-button"]'
                ];
                
                for (let selector of selectors) {
                    let btn = document.querySelector(selector);
                    if (btn && btn.offsetParent !== null) {
                        btn.click();
                        return {found: true, clicked: true, selector: selector};
                    }
                }
                
                // Ищем в Shadow DOM
                let allElements = document.querySelectorAll('*');
                for (let el of allElements) {
                    if (el.shadowRoot) {
                        for (let selector of selectors) {
                            let btn = el.shadowRoot.querySelector(selector);
                            if (btn && btn.offsetParent !== null) {
                                btn.click();
                                return {found: true, clicked: true, selector: selector, location: 'shadow'};
                            }
                        }
                    }
                }
                
                return {found: false};
            """)
            
            if result.get('clicked'):
                logger.info(f"Кнопка Play нажата (селектор: {result.get('selector')})")
                return True
            return False
            
        except Exception as e:
            logger.debug(f"Ошибка клика на Play: {e}")
            return False
    
    def click_video_to_play(self, driver: WebDriver) -> bool:
        """
        Кликает по центру видео элемента для запуска воспроизведения.
        Это стандартный способ запуска видео в большинстве плееров.
        
        Args:
            driver: Selenium WebDriver instance
            
        Returns:
            True если клик выполнен
        """
        try:
            result = driver.execute_script("""
                function findVideo() {
                    let video = document.querySelector('video');
                    if (video) return video;
                    
                    let allElements = document.querySelectorAll('*');
                    for (let el of allElements) {
                        if (el.shadowRoot) {
                            let shadowVideo = el.shadowRoot.querySelector('video');
                            if (shadowVideo) return shadowVideo;
                        }
                    }
                    return null;
                }
                
                let video = findVideo();
                if (!video) return {found: false};
                
                // Кликаем по центру видео
                let rect = video.getBoundingClientRect();
                let centerX = rect.left + rect.width / 2;
                let centerY = rect.top + rect.height / 2;
                
                let clickEvent = new MouseEvent('click', {
                    view: window,
                    bubbles: true,
                    cancelable: true,
                    clientX: centerX,
                    clientY: centerY
                });
                
                video.dispatchEvent(clickEvent);
                
                return {found: true, clicked: true, paused: video.paused};
            """)
            
            if result.get('clicked'):
                logger.info(f"Клик по видео выполнен (было на паузе: {result.get('paused')})")
                return True
            return False
            
        except Exception as e:
            logger.debug(f"Ошибка клика по видео: {e}")
            return False
    
    def _monitor_video_playback(self, check_interval=15):
        """
        Фоновый мониторинг видео: проверяет каждые N секунд что видео играет и Event закрыт.
        Запускается в отдельном потоке.
        
        Args:
            check_interval: Интервал проверки в секундах
        """
        logger.info(f"Запущен фоновый мониторинг видео (проверка каждые {check_interval}с)")
        
        while self.is_recording:
            try:
                time.sleep(check_interval)
                
                if not self.is_recording:
                    break
                
                # Проверяем и закрываем Event панель
                self.close_event_panel(self.driver)
                
                # Проверяем и запускаем видео
                playing = self.ensure_video_playing(self.driver)
                
                # Если видео не запустилось программно, кликаем по центру видео
                if not playing:
                    logger.info("Видео на паузе, выполняем клик по центру...")
                    self.click_video_center_to_toggle_play(self.driver)
                    time.sleep(1)
                    # Проверяем снова
                    self.ensure_video_playing(self.driver)
                
            except Exception as e:
                logger.warning(f"Ошибка в фоновом мониторинге: {e}")
        
        logger.info("Фоновый мониторинг видео остановлен")
    
    def wait_for_video_element(self, driver: WebDriver, timeout_seconds=60, check_interval=2) -> Optional[Dict]:
        """
        Активное ожидание появления и инициализации видео элемента.
        
        Args:
            driver: Selenium WebDriver instance
            timeout_seconds: Максимальное время ожидания
            check_interval: Интервал проверки в секундах
            
        Returns:
            Dict с координатами или None
        """
        logger.info(f"Ожидание видео элемента (до {timeout_seconds}с)...")
        
        # Сначала закрываем Event панель если открыта
        self.close_event_panel(driver)
        
        # Запускаем видео если на паузе
        self.ensure_video_playing(driver)
        
        start_time = time.time()
        checks = 0
        
        while time.time() - start_time < timeout_seconds:
            checks += 1
            
            # Периодически проверяем Event панель
            if checks % 5 == 0:
                self.close_event_panel(driver)
                self.ensure_video_playing(driver)
            
            bounds = self.get_video_element_bounds(driver)
            
            if bounds:
                logger.info(f"Видео элемент найден после {checks} проверок ({int(time.time() - start_time)}с)")
                return bounds
            
            if checks % 5 == 0:
                logger.debug(f"Проверка #{checks}: видео элемент не найден...")
            
            time.sleep(check_interval)
        
        logger.error(f"Видео элемент не найден за {timeout_seconds}с")
        return None
    
    def start_recording(self, region: Dict, duration_minutes: int, output_file: str) -> bool:
        """
        Запись указанной области экрана в видео файл.
        
        Args:
            region: Dict с координатами {'x', 'y', 'width', 'height'}
            duration_minutes: Длительность записи в минутах
            output_file: Путь к выходному файлу
            
        Returns:
            True если запись успешна, False иначе
        """
        try:
            # Подготовка региона для mss
            monitor = {
                "top": region['y'],
                "left": region['x'],
                "width": region['width'],
                "height": region['height']
            }
            
            # Настройка VideoWriter
            fourcc = cv2.VideoWriter_fourcc(*'mp4v')
            out = cv2.VideoWriter(
                output_file,
                fourcc,
                self.fps,
                (region['width'], region['height'])
            )
            
            if not out.isOpened():
                logger.error("Не удалось открыть VideoWriter")
                return False
            
            # Сохранение первого кадра для проверки
            if self.capture_method == 'mss':
                first_frame = self.sct.grab(monitor)
                first_frame_np = np.array(first_frame)
                first_frame_bgr = cv2.cvtColor(first_frame_np, cv2.COLOR_BGRA2BGR)
            else:
                # PIL ImageGrab
                bbox = (region['x'], region['y'], 
                       region['x'] + region['width'], 
                       region['y'] + region['height'])
                first_frame_pil = ImageGrab.grab(bbox=bbox)
                first_frame_np = np.array(first_frame_pil)
                first_frame_bgr = cv2.cvtColor(first_frame_np, cv2.COLOR_RGB2BGR)
            
            cv2.imwrite("first_frame_preview.png", first_frame_bgr)
            logger.info(f"Первый кадр сохранен в first_frame_preview.png")
            
            # Расчет параметров записи
            total_frames = duration_minutes * 60 * self.fps
            frame_delay = 1.0 / self.fps
            
            logger.info(f"Начало записи: {duration_minutes} минут, {self.fps} FPS")
            logger.info(f"Всего кадров: {total_frames}, размер: {region['width']}x{region['height']}")
            logger.info(f"Выходной файл: {output_file}")
            
            self.is_recording = True
            
            # Запускаем фоновый мониторинг видео (проверка каждые 15 секунд)
            if self.driver:
                self.monitor_thread = threading.Thread(
                    target=self._monitor_video_playback,
                    args=(15,),
                    daemon=True
                )
                self.monitor_thread.start()
                logger.info("Фоновый мониторинг видео запущен (проверка каждые 15с)")
            
            start_time = time.time()
            frames_captured = 0
            last_progress_log = 0
            
            # Основной цикл записи
            while frames_captured < total_frames and self.is_recording:
                frame_start = time.time()
                
                # Захват кадра
                if self.capture_method == 'mss':
                    screenshot = self.sct.grab(monitor)
                    frame = np.array(screenshot)
                    frame_bgr = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
                else:
                    # PIL ImageGrab
                    bbox = (region['x'], region['y'], 
                           region['x'] + region['width'], 
                           region['y'] + region['height'])
                    screenshot = ImageGrab.grab(bbox=bbox)
                    frame = np.array(screenshot)
                    frame_bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
                
                # Запись кадра
                out.write(frame_bgr)
                frames_captured += 1
                
                # Логирование прогресса каждую минуту
                elapsed_minutes = (time.time() - start_time) / 60
                if int(elapsed_minutes) > last_progress_log:
                    last_progress_log = int(elapsed_minutes)
                    progress_percent = (frames_captured / total_frames) * 100
                    logger.info(f"Прогресс: {last_progress_log}/{duration_minutes} минут "
                               f"({progress_percent:.1f}%, кадров: {frames_captured}/{total_frames})")
                
                # Контроль частоты кадров
                frame_time = time.time() - frame_start
                sleep_time = frame_delay - frame_time
                if sleep_time > 0:
                    time.sleep(sleep_time)
                elif frames_captured % 100 == 0:
                    logger.debug(f"Предупреждение: обработка кадра заняла {frame_time:.3f}с (целевое: {frame_delay:.3f}с)")
            
            # Завершение записи
            out.release()
            elapsed_time = time.time() - start_time
            actual_fps = frames_captured / elapsed_time if elapsed_time > 0 else 0
            
            logger.info(f"Запись завершена!")
            logger.info(f"  - Записано кадров: {frames_captured}/{total_frames}")
            logger.info(f"  - Время записи: {elapsed_time/60:.1f} минут")
            logger.info(f"  - Средний FPS: {actual_fps:.1f}")
            logger.info(f"  - Файл: {output_file}")
            
            # Проверка размера файла
            import os
            if os.path.exists(output_file):
                file_size_mb = os.path.getsize(output_file) / (1024 * 1024)
                logger.info(f"  - Размер файла: {file_size_mb:.1f} MB")
            
            return True
            
        except Exception as e:
            logger.error(f"Ошибка записи экрана: {e}")
            self.is_recording = False
            return False
        finally:
            if 'out' in locals():
                out.release()
    
    def stop_recording(self):
        """Остановка текущей записи"""
        self.is_recording = False
        logger.info("Запись остановлена пользователем")
    
    def __del__(self):
        """Очистка ресурсов"""
        try:
            if hasattr(self, 'sct') and self.sct:
                self.sct.close()
        except:
            pass
