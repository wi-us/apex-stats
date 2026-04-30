"""
Конфигурация для работы с FACEIT API и видео трансляциями.
"""

import os
from typing import Dict, Optional


class FaceitConfig:
    """Конфигурация для авторизации и работы с FACEIT."""
    
    def __init__(self):
        """Инициализация конфигурации FACEIT."""
        # Базовые URL
        self.base_url = "https://www.faceit.com"
        self.api_base = "https://api.faceit.com"
        
        # Cookie для авторизации (должны быть настроены пользователем)
        self.cookies = {
            # Основные cookie авторизации (обновленные названия с префиксами)
            "__Host-AuthSession": os.getenv("FACEIT_AUTH_SESSION", ""),
            "__Host-FaceitGatewayAuthorization": os.getenv("FACEIT_GATEWAY_AUTH", ""),
            "__Secure-FaceitBrowserId": os.getenv("FACEIT_BROWSER_ID", ""),
            
            # Cloudflare cookie для обхода защиты
            "__cf_bm": os.getenv("FACEIT_CF_BM", ""),
            "cf_clearance": os.getenv("FACEIT_CF_CLEARANCE", ""),
            "_cfuvid": os.getenv("FACEIT_CFUVID", ""),
            
            # Сессионные и дополнительные cookie
            "anon": os.getenv("FACEIT_ANON", ""),
            "asifcit": os.getenv("FACEIT_ASIFCIT", ""),
            "page_category": os.getenv("FACEIT_PAGE_CATEGORY", ""),
            "SHARED_WEB_NEXT_FACEIT_CONNECT_LOCALE_KEY": os.getenv("FACEIT_LOCALE", ""),
        }
        
        # Заголовки запросов
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        }
        
        # Параметры для видео
        self.video_quality = "720p"  # Качество видео по умолчанию
        self.chunk_size = 8192      # Размер блока при скачивании
        
    def set_cookie(self, name: str, value: str) -> None:
        """
        Установить значение cookie.
        
        Args:
            name: Имя cookie
            value: Значение cookie
        """
        self.cookies[name] = value
        
    def set_cookies_from_dict(self, cookies_dict: Dict[str, str]) -> None:
        """
        Установить несколько cookie из словаря.
        
        Args:
            cookies_dict: Словарь с cookie
        """
        self.cookies.update(cookies_dict)
        
    def validate_cookies(self) -> bool:
        """
        Проверить, что все необходимые cookie установлены.
        
        Returns:
            True если все cookie присутствуют, False иначе
        """
        required_cookies = [
            "__Host-AuthSession",
            "__Host-FaceitGatewayAuthorization", 
            "__Secure-FaceitBrowserId"
        ]
        
        # Дополнительные полезные cookie (не обязательные, но важные для обхода защиты)
        optional_cookies = ["__cf_bm", "cf_clearance", "_cfuvid", "anon", "asifcit", "page_category"]
        
        # Проверка обязательных cookie
        for cookie_name in required_cookies:
            if not self.cookies.get(cookie_name):
                return False
        
        # Предупреждение об отсутствующих опциональных cookie
        missing_optional = []
        for cookie_name in optional_cookies:
            if not self.cookies.get(cookie_name):
                missing_optional.append(cookie_name)
                
        if missing_optional:
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(f"Отсутствуют опциональные cookie: {missing_optional}")
                
        return True
        
    def get_match_url(self, match_id: str, tournament_name: str, 
                      map_number: int = 1, pov: Optional[str] = None) -> str:
        """
        Построить URL для доступа к видео матча.
        
        Args:
            match_id: ID матча
            tournament_name: Название турнира
            map_number: Номер карты (1-9)
            pov: POV константа (например S35) - опциональный
            
        Returns:
            Полный URL для доступа к видео
        """
        base_match_url = f"{self.base_url}/en/watch/matches/{match_id}/{tournament_name}"
        
        # Строим параметры URL
        params = []
        if map_number:
            params.append(f"map={map_number}")
        if pov:
            params.append(f"pov={pov}")
            
        if params:
            return f"{base_match_url}?{'&'.join(params)}"
        else:
            return base_match_url
            
    def get_match_url_without_pov(self, match_id: str, tournament_name: str, 
                                 map_number: int = 1) -> str:
        """
        Построить URL без проблемного параметра pov.
        
        Args:
            match_id: ID матча
            tournament_name: Название турнира
            map_number: Номер карты (1-9)
            
        Returns:
            URL без pov параметра
        """
        base_match_url = f"{self.base_url}/en/watch/matches/{match_id}/{tournament_name}"
        if map_number:
            return f"{base_match_url}?map={map_number}"
        else:
            return base_match_url
        
    def parse_match_url(self, url: str) -> Optional[Dict[str, str]]:
        """
        Извлечь компоненты из URL матча.
        
        Args:
            url: URL матча FACEIT
            
        Returns:
            Словарь с компонентами URL или None при ошибке парсинга
        """
        try:
            # Парсинг URL типа: https://www.faceit.com/en/watch/matches/695bfe6943ae2ce034795ad1/ALGS-2026-Championship-Match?map=1&pov=S35
            
            if "faceit.com/en/watch/matches/" not in url:
                return None
                
            # Извлекаем часть после matches/
            parts = url.split("faceit.com/en/watch/matches/")[1]
            
            # Разделяем на path и query
            if "?" in parts:
                path_part, query_part = parts.split("?", 1)
            else:
                path_part = parts
                query_part = ""
                
            # Извлекаем match_id и tournament_name
            path_components = path_part.split("/")
            if len(path_components) < 2:
                return None
                
            match_id = path_components[0]
            tournament_name = path_components[1]
            
            # Парсим query параметры
            map_number = "1"
            pov = None  # Теперь pov опциональный
            
            if query_part:
                for param in query_part.split("&"):
                    if "=" in param:
                        key, value = param.split("=", 1)
                        if key == "map":
                            map_number = value
                        elif key == "pov":
                            pov = value
                            
            return {
                "match_id": match_id,
                "tournament_name": tournament_name,
                "map": map_number,
                "pov": pov
            }
            
        except Exception:
            return None