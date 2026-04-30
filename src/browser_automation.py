"""
Модуль браузерной автоматизации для обхода модальных окон FACEIT и правильного выбора стрима.
"""

import logging
import time
from typing import Optional, Dict, List, Any
from pathlib import Path
import re

# Опциональный импорт зависимостей браузерной автоматизации
try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.chrome.options import Options
    from selenium.common.exceptions import (
        TimeoutException, NoSuchElementException, ElementClickInterceptedException,
        StaleElementReferenceException, WebDriverException
    )
    from webdriver_manager.chrome import ChromeDriverManager
    HAS_SELENIUM = True
except ImportError:
    HAS_SELENIUM = False
    webdriver = None
    
from config.faceit_config import FaceitConfig

logger = logging.getLogger(__name__)


class BrowserAutomationError(Exception):
    """Исключение при ошибках браузерной автоматизации."""
    pass


class FaceitBrowserAutomator:
    """Класс для автоматизации браузера при работе с FACEIT."""
    
    def __init__(self, config: FaceitConfig, headless: bool = True):
        """
        Инициализация автоматизатора браузера.
        
        Args:
            config: Конфигурация FACEIT с cookie
            headless: Запуск в headless режиме
        """
        if not HAS_SELENIUM:
            raise BrowserAutomationError(
                "Selenium не установлен. Установите зависимости: pip install -r requirements-browser.txt"
            )
            
        self.config = config
        self.headless = headless
        self.driver: Optional[webdriver.Chrome] = None
        self.wait_timeout = 30
        self.retry_attempts = 3
        
        # Селекторы элементов
        self.selectors = {
            'modal_dialog': 'div[role="dialog"]',
            'modal_close': 'button[aria-label="Close"], button[data-testid="close"], .modal-close, [class*="close"]',
            'broadcast_offline': '[class*="offline"], [class*="Offline"]',
            'map_button': 'button[aria-label="Map"]',
            'event_button': 'button[aria-label="Event"]',
            'video_element': 'video, iframe[src*="stream"], iframe[src*="video"]',
            'stream_container': '[class*="stream"], [class*="player"], [class*="video"]'
        }
        
        # Дополнительные селекторы для кнопок (если основные не работают)
        self.alternative_selectors = {
            'map_buttons': [
                'button[aria-label="Map"]',
                'button[title="Map"]', 
                'button:contains("Map")',
                '[data-testid*="map"]',
                '[class*="map"][role="button"]',
                'button[data-label="Map"]',
                'div[aria-label="Map"]',
                '[aria-label*="map" i]',  # case insensitive
                'button[data-cy="map"]',
                '.map-button',
                '[data-name="map"]'
            ],
            'event_buttons': [
                'button[aria-label="Event"]',
                'button[title="Event"]',
                'button:contains("Event")', 
                '[data-testid*="event"]',
                '[class*="event"][role="button"]',
                'button[data-label="Event"]',
                'div[aria-label="Event"]',
                '[aria-label*="event" i]',  # case insensitive
                'button[data-cy="event"]',
                '.event-button',
                '[data-name="event"]'
            ]
        }
        
    def setup_browser(self) -> bool:
        """
        Настроить браузер с cookie и заголовками.
        
        Returns:
            True если браузер успешно настроен
        """
        try:
            logger.info("Настройка браузера Chrome...")
            
            # Настройки Chrome
            chrome_options = Options()
            
            if self.headless:
                chrome_options.add_argument('--headless=new')
                
            # Дополнительные опции для стабильности
            chrome_options.add_argument('--no-sandbox')
            chrome_options.add_argument('--disable-dev-shm-usage')
            chrome_options.add_argument('--disable-gpu')
            chrome_options.add_argument('--window-size=1920,1080')
            chrome_options.add_argument('--disable-blink-features=AutomationControlled')
            chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
            chrome_options.add_experimental_option('useAutomationExtension', False)
            
            # User-Agent
            chrome_options.add_argument(f'--user-agent={self.config.headers.get("User-Agent", "")}')
            
            # Создание драйвера
            service = Service(ChromeDriverManager().install())
            self.driver = webdriver.Chrome(service=service, options=chrome_options)
            
            # Убираем флаг автоматизации
            self.driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            
            logger.info("Браузер успешно запущен")
            return True
            
        except Exception as e:
            logger.error(f"Ошибка настройки браузера: {e}")
            return False
            
    def set_cookies(self, url: str) -> bool:
        """
        Установить cookie для домена FACEIT.
        
        Args:
            url: URL для перехода перед установкой cookie
            
        Returns:
            True если cookie успешно установлены
        """
        if not self.driver:
            return False
            
        try:
            logger.info("Установка cookie...")
            
            # Переходим на главную страницу FACEIT для установки cookie
            self.driver.get("https://www.faceit.com")
            time.sleep(2)
            
            # Устанавливаем cookie
            for name, value in self.config.cookies.items():
                if value:  # Пропускаем пустые cookie
                    try:
                        self.driver.add_cookie({
                            'name': name,
                            'value': value,
                            'domain': '.faceit.com'
                        })
                        logger.debug(f"Установлен cookie: {name}")
                    except Exception as e:
                        logger.warning(f"Не удалось установить cookie {name}: {e}")
                        
            logger.info("Cookie установлены")
            return True
            
        except Exception as e:
            logger.error(f"Ошибка установки cookie: {e}")
            return False
            
    def wait_for_cloudflare_check(self) -> bool:
        """
        Ожидание завершения проверки Cloudflare.
        
        Returns:
            True если проверка завершена успешно
        """
        if not self.driver:
            return False
            
        max_attempts = 30  # 30 попыток по 2 секунды = 1 минута
        
        for attempt in range(max_attempts):
            try:
                # Проверяем заголовок страницы
                title = self.driver.title
                current_url = self.driver.current_url
                
                logger.info(f"Проверка Cloudflare (попытка {attempt + 1}): title='{title}', url='{current_url[:100]}...'")
                
                # Если заголовок изменился с "Ждем пятнадцать", значит проверка завершена
                if "ждем" not in title.lower() and "проверка" not in title.lower():
                    # Проверяем, что URL не содержит параметры Cloudflare challenge
                    if "__cf_chl_tk" not in current_url:
                        logger.info("Проверка Cloudflare завершена успешно")
                        return True
                        
                # Ждем еще немного
                time.sleep(2)
                
            except Exception as e:
                logger.debug(f"Ошибка при проверке статуса Cloudflare: {e}")
                time.sleep(2)
                
        logger.warning("Проверка Cloudflare не завершилась за отведенное время")
        return False

    def navigate_to_match(self, url: str) -> bool:
        """
        Перейти на страницу матча.
        
        Args:
            url: URL страницы матча
            
        Returns:
            True если переход успешен
        """
        if not self.driver:
            return False
            
        try:
            logger.info(f"Переход на страницу матча: {url}")
            self.driver.get(url)
            
            # Ждем загрузки страницы
            WebDriverWait(self.driver, self.wait_timeout).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )
            
            time.sleep(3)  # Дополнительная пауза для загрузки JavaScript
            
            # Проверяем, не попали ли мы на страницу проверки Cloudflare
            title = self.driver.title
            if "ждем" in title.lower() or "проверка" in title.lower() or "__cf_chl_tk" in self.driver.current_url:
                logger.info("Обнаружена проверка Cloudflare, ожидаем завершения...")
                if not self.wait_for_cloudflare_check():
                    logger.error("Не удалось дождаться завершения проверки Cloudflare")
                    return False
                    
            logger.info("Страница загружена")
            return True
            
        except TimeoutException:
            logger.error("Таймаут загрузки страницы")
            return False
        except Exception as e:
            logger.error(f"Ошибка перехода на страницу: {e}")
            return False
            
    def handle_modal_dialogs(self) -> bool:
        """
        Обработать модальные окна (закрыть их).
        
        Returns:
            True если модальные окна обработаны
        """
        if not self.driver:
            return False
            
        try:
            # Ищем модальные окна
            modals_found = False
            
            # Поиск различных типов модальных окон
            modal_selectors = [
                self.selectors['modal_dialog'],
                '[class*="modal"]', 
                '[class*="Modal"]',
                '[class*="dialog"]', 
                '[class*="Dialog"]',
                '[class*="overlay"]',
                '[class*="Overlay"]'
            ]
            
            for selector in modal_selectors:
                try:
                    modals = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    for modal in modals:
                        if modal.is_displayed():
                            logger.info(f"Найдено модальное окно: {selector}")
                            
                            # Попытка закрыть модальное окно
                            if self._close_modal(modal):
                                modals_found = True
                                time.sleep(1)
                                
                except Exception as e:
                    logger.debug(f"Ошибка поиска модального окна {selector}: {e}")
                    
            if modals_found:
                logger.info("Модальные окна закрыты")
                time.sleep(2)  # Пауза после закрытия модальных окон
                
            return True
            
        except Exception as e:
            logger.error(f"Ошибка обработки модальных окон: {e}")
            return False
            
    def _close_modal(self, modal_element) -> bool:
        """
        Закрыть конкретное модальное окно.
        
        Args:
            modal_element: Элемент модального окна
            
        Returns:
            True если окно закрыто
        """
        try:
            # Поиск кнопки закрытия в модальном окне
            close_selectors = [
                'button[aria-label="Close"]',
                'button[data-testid="close"]',
                'button[class*="close"]',
                '.close',
                '[class*="Close"]',
                'button:contains("✕")',
                'button:contains("×")',
                '[aria-label*="close"]'
            ]
            
            for selector in close_selectors:
                try:
                    close_buttons = modal_element.find_elements(By.CSS_SELECTOR, selector)
                    for button in close_buttons:
                        if button.is_displayed() and button.is_enabled():
                            button.click()
                            logger.info(f"Кликнули кнопку закрытия: {selector}")
                            return True
                except Exception:
                    continue
                    
            # Если кнопка не найдена, пробуем кликнуть по фону (overlay)
            try:
                # Клик в углу модального окна (обычно закрывает модальное окно)
                self.driver.execute_script("arguments[0].click();", modal_element)
                logger.info("Закрыли модальное окно кликом по элементу")
                return True
            except Exception:
                pass
                
            # Попытка нажать Escape
            try:
                from selenium.webdriver.common.keys import Keys
                modal_element.send_keys(Keys.ESCAPE)
                logger.info("Закрыли модальное окно клавишей Escape")
                return True
            except Exception:
                pass
                
            return False
            
        except Exception as e:
            logger.debug(f"Ошибка закрытия модального окна: {e}")
            return False
            
    def click_map_button(self) -> bool:
        """
        Кликнуть на кнопку Map.
        
        Returns:
            True если клик успешен
        """
        return self._click_button_with_retry(
            self.selectors['map_button'], 
            "Map",
            alternative_selectors=self.alternative_selectors['map_buttons']
        )
        
    def click_event_button(self) -> bool:
        """
        Кликнуть на кнопку Event.
        
        Returns:
            True если клик успешен
        """
        return self._click_button_with_retry(
            self.selectors['event_button'],
            "Event", 
            alternative_selectors=self.alternative_selectors['event_buttons']
        )
        
    def _click_button_with_retry(self, selector: str, button_name: str, 
                                alternative_selectors: Optional[List[str]] = None) -> bool:
        """
        Кликнуть на кнопку с повторными попытками.
        
        Args:
            selector: CSS селектор кнопки
            button_name: Название кнопки для логирования
            alternative_selectors: Альтернативные селекторы
            
        Returns:
            True если клик успешен
        """
        if not self.driver:
            return False
            
        selectors_to_try = [selector]
        if alternative_selectors:
            selectors_to_try.extend(alternative_selectors)
            
        for attempt in range(self.retry_attempts):
            for sel in selectors_to_try:
                try:
                    logger.info(f"Поиск кнопки {button_name} (попытка {attempt + 1}): {sel}")
                    
                    # Обрабатываем :contains() селекторы через XPath
                    if ':contains(' in sel:
                        # Преобразуем button:contains("Text") в XPath
                        if sel.startswith('button:contains('):
                            text = sel.split(':contains(')[1].rstrip(')')
                            text = text.strip('"').strip("'")
                            xpath = f"//button[contains(text(), '{text}')]"
                            elements = self.driver.find_elements(By.XPATH, xpath)
                        else:
                            continue  # Пропускаем сложные :contains селекторы
                    else:
                        # Обычные CSS селекторы
                        elements = self.driver.find_elements(By.CSS_SELECTOR, sel)
                    
                    # Ищем кликабельный элемент
                    for element in elements:
                        try:
                            if element.is_displayed() and element.is_enabled():
                                # Скролл к элементу
                                self.driver.execute_script("arguments[0].scrollIntoView(true);", element)
                                time.sleep(1)
                                
                                # Клик
                                element.click()
                                logger.info(f"Успешно кликнули кнопку {button_name} с селектором: {sel}")
                                time.sleep(2)  # Пауза после клика
                                return True
                        except (ElementClickInterceptedException, StaleElementReferenceException):
                            # Попытка клика через JavaScript
                            try:
                                self.driver.execute_script("arguments[0].click();", element)
                                logger.info(f"Кликнули кнопку {button_name} через JavaScript с селектором: {sel}")
                                time.sleep(2)
                                return True
                            except Exception:
                                continue
                        except Exception:
                            continue
                    
                except Exception as e:
                    logger.debug(f"Ошибка поиска кнопки {button_name} с селектором {sel}: {e}")
                    continue
                    
            time.sleep(2)  # Пауза между попытками
            
        logger.error(f"Не удалось кликнуть кнопку {button_name} ни с одним из селекторов")
        return False
        
    def extract_video_urls(self) -> List[str]:
        """
        Извлечь URL видеостримов со страницы.
        
        Returns:
            Список найденных URL видео
        """
        if not self.driver:
            return []
            
        video_urls = []
        
        try:
            logger.info("Извлечение URL видеостримов...")
            
            # Ждем появления видео элементов
            time.sleep(5)
            
            # Поиск видео элементов
            video_selectors = [
                'video',
                'iframe[src*="stream"]',
                'iframe[src*="video"]', 
                'iframe[src*="player"]',
                '[src*=".m3u8"]',
                '[data-src*="stream"]'
            ]
            
            for selector in video_selectors:
                try:
                    elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    for element in elements:
                        # Получаем URL из различных атрибутов
                        url_attrs = ['src', 'data-src', 'data-video-src']
                        for attr in url_attrs:
                            url = element.get_attribute(attr)
                            if url and (url.endswith('.m3u8') or 'stream' in url.lower()):
                                video_urls.append(url)
                                logger.info(f"Найден видео URL: {url}")
                                
                except Exception as e:
                    logger.debug(f"Ошибка поиска видео в {selector}: {e}")
                    
            # Поиск URL в JavaScript переменных
            js_urls = self._extract_urls_from_javascript()
            video_urls.extend(js_urls)
            
            # Поиск в network запросах (через логи)
            network_urls = self._extract_urls_from_network()
            video_urls.extend(network_urls)
            
            # Удаление дубликатов
            video_urls = list(set(video_urls))
            
            logger.info(f"Найдено {len(video_urls)} уникальных видео URL")
            return video_urls
            
        except Exception as e:
            logger.error(f"Ошибка извлечения видео URL: {e}")
            return []
            
    def _extract_urls_from_javascript(self) -> List[str]:
        """Извлечь URL из JavaScript переменных на странице."""
        urls = []
        
        try:
            # Выполняем JavaScript для поиска URL
            js_script = """
            var urls = [];
            
            // Поиск в глобальных переменных
            if (window.videoUrl) urls.push(window.videoUrl);
            if (window.streamUrl) urls.push(window.streamUrl);
            if (window.manifestUrl) urls.push(window.manifestUrl);
            
            // Поиск в тексте страницы
            var pageText = document.documentElement.innerText || document.documentElement.textContent || '';
            var urlRegex = /https?:\/\/[^\\s"']+\\.m3u8[^\\s"']*/g;
            var matches = pageText.match(urlRegex) || [];
            urls = urls.concat(matches);
            
            return urls;
            """
            
            js_urls = self.driver.execute_script(js_script)
            if js_urls:
                urls.extend(js_urls)
                logger.info(f"Найдено {len(js_urls)} URL через JavaScript")
                
        except Exception as e:
            logger.debug(f"Ошибка извлечения URL из JavaScript: {e}")
            
        return urls
        
    def _extract_urls_from_network(self) -> List[str]:
        """Извлечь URL из сетевых запросов браузера."""
        urls = []
        
        try:
            # Получаем логи сетевых запросов
            logs = self.driver.get_log('performance')
            
            for log in logs:
                message = log.get('message', {})
                if isinstance(message, str):
                    import json
                    try:
                        message = json.loads(message)
                    except:
                        continue
                        
                method = message.get('method', '')
                params = message.get('params', {})
                
                if method == 'Network.responseReceived':
                    response = params.get('response', {})
                    url = response.get('url', '')
                    
                    if url and (url.endswith('.m3u8') or 'stream' in url.lower()):
                        urls.append(url)
                        
        except Exception as e:
            logger.debug(f"Ошибка извлечения URL из network логов: {e}")
            
        return urls
        
    def debug_page_content(self) -> Dict[str, Any]:
        """
        Отладочная функция для анализа содержимого страницы.
        
        Returns:
            Информация о найденных элементах на странице
        """
        if not self.driver:
            return {}
            
        try:
            # Получаем основную информацию о странице
            debug_info = {
                'page_title': self.driver.title,
                'current_url': self.driver.current_url,
                'page_source_length': len(self.driver.page_source),
                'buttons_found': [],
                'aria_labels_found': [],
                'video_elements': [],
                'modal_elements': []
            }
            
            # Поиск всех кнопок
            buttons = self.driver.find_elements(By.TAG_NAME, 'button')
            for button in buttons[:20]:  # Ограничиваем до 20 кнопок
                try:
                    aria_label = button.get_attribute('aria-label')
                    title = button.get_attribute('title')  
                    text = button.text[:50]  # Первые 50 символов
                    if aria_label or title or text:
                        debug_info['buttons_found'].append({
                            'aria_label': aria_label,
                            'title': title,
                            'text': text,
                            'visible': button.is_displayed()
                        })
                except Exception:
                    continue
                    
            # Поиск всех элементов с aria-label
            all_elements = self.driver.find_elements(By.XPATH, '//*[@aria-label]')
            for element in all_elements[:30]:  # Ограничиваем до 30 элементов
                try:
                    aria_label = element.get_attribute('aria-label')
                    tag_name = element.tag_name
                    if aria_label:
                        debug_info['aria_labels_found'].append({
                            'tag': tag_name,
                            'aria_label': aria_label,
                            'visible': element.is_displayed()
                        })
                except Exception:
                    continue
                    
            # Поиск видео элементов
            video_selectors = ['video', 'iframe[src*="stream"]', 'iframe[src*="video"]']
            for selector in video_selectors:
                try:
                    elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    for element in elements:
                        src = element.get_attribute('src')
                        if src:
                            debug_info['video_elements'].append({
                                'tag': element.tag_name,
                                'src': src[:100],  # Первые 100 символов
                                'visible': element.is_displayed()
                            })
                except Exception:
                    continue
                    
            # Поиск модальных окон
            modal_selectors = ['div[role="dialog"]', '[class*="modal"]', '[class*="Modal"]']
            for selector in modal_selectors:
                try:
                    elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    for element in elements:
                        if element.is_displayed():
                            debug_info['modal_elements'].append({
                                'selector': selector,
                                'text': element.text[:100],  # Первые 100 символов
                                'class': element.get_attribute('class')
                            })
                except Exception:
                    continue
                    
            return debug_info
            
        except Exception as e:
            logger.error(f"Ошибка отладки страницы: {e}")
            return {}

    def get_video_stream_url(self, match_url: str) -> Optional[str]:
        """
        Получить URL видеострима через полную браузерную автоматизацию.
        
        Args:
            match_url: URL страницы матча
            
        Returns:
            URL видеострима или None
        """
        try:
            logger.info("Запуск браузерной автоматизации для получения видео URL")
            
            # Настройка браузера
            if not self.setup_browser():
                raise BrowserAutomationError("Не удалось настроить браузер")
                
            # Установка cookie
            if not self.set_cookies(match_url):
                raise BrowserAutomationError("Не удалось установить cookie")
                
            # Переход на страницу матча
            if not self.navigate_to_match(match_url):
                raise BrowserAutomationError("Не удалось перейти на страницу матча")
                
            # Обработка модальных окон
            self.handle_modal_dialogs()
            
            # ОТЛАДКА: Анализируем содержимое страницы
            logger.info("=== ОТЛАДКА СОДЕРЖИМОГО СТРАНИЦЫ ===")
            debug_info = self.debug_page_content()
            
            logger.info(f"Заголовок страницы: {debug_info.get('page_title', 'Неизвестно')}")
            logger.info(f"Текущий URL: {debug_info.get('current_url', 'Неизвестно')}")
            logger.info(f"Размер HTML: {debug_info.get('page_source_length', 0)} символов")
            
            # Проверяем, на какой странице мы находимся
            title = debug_info.get('page_title', '').lower()
            url = debug_info.get('current_url', '')
            
            if "ждем" in title or "проверка" in title or "__cf_chl_tk" in url:
                logger.warning("ВНИМАНИЕ: МЫ НА СТРАНИЦЕ ПРОВЕРКИ CLOUDFLARE!")
                logger.info("Это объясняет отсутствие кнопок Map/Event")
                logger.info("Попробуем подождать завершения проверки...")
            elif "faceit" in url and "watch" in url:
                logger.info("SUCCESS: Мы на правильной странице FACEIT")
            else:
                logger.warning("UNKNOWN: Неизвестная страница")
            
            # Выводим информацию о найденных кнопках
            buttons = debug_info.get('buttons_found', [])
            logger.info(f"Найдено кнопок: {len(buttons)}")
            for i, button in enumerate(buttons[:10]):  # Показываем первые 10
                logger.info(f"  Кнопка {i+1}: aria-label='{button.get('aria_label')}', title='{button.get('title')}', text='{button.get('text')}', visible={button.get('visible')}")
                
            # Выводим информацию об aria-label элементах
            aria_elements = debug_info.get('aria_labels_found', [])
            logger.info(f"Элементов с aria-label: {len(aria_elements)}")
            for i, element in enumerate(aria_elements[:10]):  # Показываем первые 10
                logger.info(f"  Элемент {i+1}: tag='{element.get('tag')}', aria-label='{element.get('aria_label')}', visible={element.get('visible')}")
                
            # Выводим информацию о модальных окнах
            modals = debug_info.get('modal_elements', [])
            if modals:
                logger.info(f"Найдены модальные окна: {len(modals)}")
                for modal in modals:
                    logger.info(f"  Модальное окно: {modal.get('selector')}, text='{modal.get('text')}', class='{modal.get('class')}'")
            else:
                logger.info("Модальные окна не найдены")
                
            logger.info("=== КОНЕЦ ОТЛАДКИ ===")
            
            # Клик на кнопку Map
            if self.click_map_button():
                time.sleep(3)  # Пауза для загрузки стрима
                
            # Клик на кнопку Event
            if self.click_event_button():
                time.sleep(3)  # Пауза для закрытия ненужного стрима
                
            # Извлечение URL видео
            video_urls = self.extract_video_urls()
            
            if video_urls:
                # Возвращаем первый найденный URL
                best_url = video_urls[0]
                logger.info(f"Получен видео URL: {best_url}")
                return best_url
            else:
                logger.warning("Видео URL не найдены")
                return None
                
        except Exception as e:
            logger.error(f"Ошибка браузерной автоматизации: {e}")
            return None
        finally:
            self.cleanup()
            
    def cleanup(self):
        """Очистка ресурсов браузера."""
        if self.driver:
            try:
                self.driver.quit()
                logger.info("Браузер закрыт")
            except Exception as e:
                logger.debug(f"Ошибка закрытия браузера: {e}")
            finally:
                self.driver = None