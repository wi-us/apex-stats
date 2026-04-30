"""
Модуль для подключения к Chrome через debugging port.
Обеспечивает стабильное подключение с retry логикой.
"""

import time
import json
import logging
from pathlib import Path
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

logger = logging.getLogger(__name__)


class ChromeDebugger:
    """
    Класс для подключения к Chrome через remote debugging port.
    """
    
    def __init__(self, debug_port=9222):
        self.debug_port = debug_port
        self.driver = None
        
    def connect(self, max_retries=10, retry_delay=2):
        """
        Подключается к Chrome с retry логикой.
        
        Args:
            max_retries: Максимальное количество попыток
            retry_delay: Задержка между попытками в секундах
            
        Returns:
            True если подключение успешно
        """
        logger.info(f"Подключение к Chrome (debugging port {self.debug_port})...")
        
        for attempt in range(max_retries):
            try:
                options = Options()
                options.debugger_address = f"127.0.0.1:{self.debug_port}"
                
                self.driver = webdriver.Chrome(options=options)
                
                logger.info(f"[OK] Подключено к Chrome!")
                logger.info(f"URL: {self.driver.current_url}")
                logger.info(f"Title: {self.driver.title}")
                
                return True
                
            except Exception as e:
                if attempt < max_retries - 1:
                    logger.info(f"Попытка {attempt + 1}/{max_retries} не удалась, повтор через {retry_delay}с...")
                    time.sleep(retry_delay)
                else:
                    logger.error(f"Не удалось подключиться к Chrome после {max_retries} попыток")
                    logger.error(f"Ошибка: {e}")
                    logger.error("")
                    logger.error("Убедитесь что Chrome запущен с debugging:")
                    logger.error('  chrome.exe --remote-debugging-port=9222')
                    return False
        
        return False
    
    def load_cookies_from_file(self, cookies_file="faceit_session_cookies.json"):
        """
        Загружает cookies из файла в браузер.
        
        Args:
            cookies_file: Путь к файлу с cookies
            
        Returns:
            True если cookies загружены
        """
        if not self.driver:
            logger.error("Driver не инициализирован")
            return False
        
        cookies_path = Path(cookies_file)
        if not cookies_path.exists():
            logger.warning(f"Файл cookies не найден: {cookies_file}")
            return False
        
        logger.info(f"Загрузка cookies из {cookies_file}...")
        
        try:
            with open(cookies_path, 'r') as f:
                data = json.load(f)
            
            # Поддержка двух форматов: массив или объект с ключом "cookies"
            if isinstance(data, dict) and 'cookies' in data:
                cookies = data['cookies']
            elif isinstance(data, list):
                cookies = data
            else:
                logger.error(f"Неверный формат файла cookies")
                return False
            
            # Переходим на домен для загрузки cookies
            current_url = self.driver.current_url
            if 'faceit.com' not in current_url:
                logger.info("Переход на faceit.com для загрузки cookies...")
                self.driver.get("https://www.faceit.com")
                time.sleep(2)
            
            # Загружаем обычные cookies
            loaded_count = 0
            host_cookies = []
            
            for cookie in cookies:
                try:
                    # __Host- cookies требуют CDP
                    if cookie['name'].startswith('__Host-'):
                        host_cookies.append(cookie)
                        continue
                    
                    cookie_dict = {
                        'name': cookie['name'],
                        'value': cookie['value'],
                        'domain': cookie.get('domain', '.faceit.com'),
                        'path': cookie.get('path', '/'),
                    }
                    
                    if 'expiry' in cookie:
                        cookie_dict['expiry'] = cookie['expiry']
                    if 'secure' in cookie:
                        cookie_dict['secure'] = cookie['secure']
                    if 'httpOnly' in cookie:
                        cookie_dict['httpOnly'] = cookie['httpOnly']
                    
                    self.driver.add_cookie(cookie_dict)
                    loaded_count += 1
                    
                except Exception as e:
                    logger.debug(f"Пропущен cookie {cookie['name']}: {e}")
                    continue
            
            logger.info(f"Загружено обычных cookies: {loaded_count}/{len(cookies)}")
            
            # Загружаем __Host- cookies через CDP
            if host_cookies:
                logger.info(f"Загрузка {len(host_cookies)} __Host- cookies через CDP...")
                for cookie in host_cookies:
                    try:
                        self.driver.execute_cdp_cmd('Network.setCookie', {
                            'name': cookie['name'],
                            'value': cookie['value'],
                            'domain': cookie.get('domain', 'www.faceit.com'),
                            'path': cookie.get('path', '/'),
                            'secure': cookie.get('secure', True),
                            'httpOnly': cookie.get('httpOnly', True),
                            'sameSite': cookie.get('sameSite', 'None')
                        })
                        logger.debug(f"  [OK] {cookie['name']}")
                    except Exception as e:
                        logger.warning(f"  [!] {cookie['name']}: {e}")
            
            logger.info("[OK] Cookies загружены")
            return True
            
        except Exception as e:
            logger.error(f"Ошибка загрузки cookies: {e}")
            return False
    
    def get_cookies_dict(self):
        """
        Возвращает cookies в виде словаря для HTTP запросов.
        
        Returns:
            Dict с cookies
        """
        if not self.driver:
            return {}
        
        try:
            selenium_cookies = self.driver.get_cookies()
            return {cookie['name']: cookie['value'] for cookie in selenium_cookies}
        except Exception as e:
            logger.error(f"Ошибка получения cookies: {e}")
            return {}
    
    def navigate_to_match(self, match_url, wait_time=10):
        """
        Переходит на страницу матча и ждет загрузки.
        
        Args:
            match_url: URL страницы матча
            wait_time: Время ожидания загрузки
            
        Returns:
            True если успешно
        """
        if not self.driver:
            logger.error("Driver не инициализирован")
            return False
        
        logger.info(f"Переход на страницу матча...")
        logger.info(f"URL: {match_url}")
        
        try:
            self.driver.get(match_url)
            time.sleep(wait_time)
            
            logger.info(f"[OK] Страница загружена: {self.driver.title}")
            return True
            
        except Exception as e:
            logger.error(f"Ошибка навигации: {e}")
            return False
    
    def setup_match_interface(self):
        """
        Настраивает интерфейс матча: открывает Map, закрывает Event.
        
        Returns:
            True если успешно
        """
        if not self.driver:
            logger.error("Driver не инициализирован")
            return False
        
        logger.info("Настройка интерфейса (Map/Event)...")
        
        try:
            result = self.driver.execute_script("""
                function setupInterface() {
                    let results = {map: null, event: null};
                    
                    // Открываем Map
                    let mapBtn = document.querySelector('button[aria-label="Map"]');
                    if (mapBtn) {
                        let isSelected = mapBtn.getAttribute('aria-selected') === 'true';
                        if (!isSelected) {
                            mapBtn.click();
                            results.map = 'opened';
                        } else {
                            results.map = 'already_open';
                        }
                    } else {
                        results.map = 'not_found';
                    }
                    
                    // Закрываем Event
                    let eventBtn = document.querySelector('button[aria-label="Event"]');
                    if (eventBtn) {
                        let isSelected = eventBtn.getAttribute('aria-selected') === 'true';
                        if (isSelected) {
                            eventBtn.click();
                            results.event = 'closed';
                        } else {
                            results.event = 'already_closed';
                        }
                    } else {
                        results.event = 'not_found';
                    }
                    
                    return results;
                }
                
                return setupInterface();
            """)
            
            logger.info(f"  Map: {result.get('map', 'unknown')}")
            logger.info(f"  Event: {result.get('event', 'unknown')}")
            
            return True
            
        except Exception as e:
            logger.error(f"Ошибка настройки интерфейса: {e}")
            return False
    
    def wait_for_video_playing(self, timeout=60, check_interval=1):
        """
        Ждет пока видео начнет воспроизводиться.
        
        Args:
            timeout: Максимальное время ожидания в секундах
            check_interval: Интервал проверки в секундах
            
        Returns:
            True если видео запустилось
        """
        if not self.driver:
            logger.error("Driver не инициализирован")
            return False
        
        logger.info("")
        logger.info("=" * 60)
        logger.info(">>> ЗАПУСТИТЕ ВИДЕО В БРАУЗЕРЕ ПРЯМО СЕЙЧАС! <<<")
        logger.info("=" * 60)
        logger.info("")
        logger.info(f"Ожидание запуска видео (таймаут: {timeout}с)...")
        logger.info("")
        
        start_time = time.time()
        last_log = 0
        
        while time.time() - start_time < timeout:
            try:
                result = self.driver.execute_script("""
                    let video = document.querySelector('video');
                    if (!video) {
                        let allElements = document.querySelectorAll('*');
                        for (let el of allElements) {
                            if (el.shadowRoot) {
                                video = el.shadowRoot.querySelector('video');
                                if (video) break;
                            }
                        }
                    }
                    
                    if (!video) return {found: false};
                    
                    return {
                        found: true,
                        paused: video.paused,
                        currentTime: video.currentTime,
                        readyState: video.readyState
                    };
                """)
                
                if result.get('found'):
                    current_time = result.get('currentTime', 0)
                    is_paused = result.get('paused', True)
                    
                    # Видео запустилось!
                    if current_time > 0 and not is_paused:
                        logger.info(f"[OK] Видео воспроизводится! (currentTime={current_time:.1f}s)")
                        return True
                    
                    # Логируем каждые 5 секунд
                    elapsed = int(time.time() - start_time)
                    if elapsed - last_log >= 5:
                        logger.info(f"[{elapsed:02d}с] Ожидание... (currentTime={current_time:.1f}s, paused={is_paused})")
                        last_log = elapsed
                
                time.sleep(check_interval)
                
            except Exception as e:
                logger.debug(f"Ошибка проверки видео: {e}")
                time.sleep(check_interval * 2)
        
        logger.warning(f"[!] Таймаут {timeout}с, видео не запустилось")
        logger.warning("Продолжаем перехват в любом случае...")
        return False
    
    def close(self):
        """
        Закрывает соединение с браузером (не закрывает сам браузер).
        """
        if self.driver:
            try:
                self.driver.quit()
            except:
                pass
            self.driver = None
