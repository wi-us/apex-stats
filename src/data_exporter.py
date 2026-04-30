"""
Модуль для экспорта данных отслеживания в различные форматы.
"""

import json
import csv
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime
import numpy as np

from src.arrow_tracker import TrackingResult
from src.team_detector import TeamInfo

logger = logging.getLogger(__name__)


class DataExportError(Exception):
    """Исключение при ошибках экспорта данных."""
    pass


class DataExporter:
    """Класс для экспорта результатов трекинга в различные форматы."""
    
    def __init__(self):
        """Инициализация экспортера данных."""
        self.output_dir = Path("data/outputs")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
    def _prepare_metadata(self, url: Optional[str] = None, **kwargs) -> Dict[str, Any]:
        """
        Подготовить метаданные для экспорта.
        
        Args:
            url: URL исходного видео
            **kwargs: Дополнительные параметры
            
        Returns:
            Словарь с метаданными
        """
        metadata = {
            "export_timestamp": datetime.now().isoformat(),
            "format_version": "1.0",
            "source_url": url,
            "processing_info": {
                "tool": "Apex Stats Parser",
                "version": "1.0.0"
            }
        }
        
        # Добавление дополнительных параметров
        metadata.update(kwargs)
        
        # Извлечение информации из URL если возможно
        if url and "faceit.com" in url:
            try:
                if "/matches/" in url:
                    parts = url.split("/matches/")[1].split("/")
                    if len(parts) >= 2:
                        metadata["match_id"] = parts[0]
                        metadata["tournament"] = parts[1].split("?")[0]
                        
                if "map=" in url:
                    map_param = url.split("map=")[1].split("&")[0]
                    metadata["map"] = int(map_param)
                    
                if "pov=" in url:
                    pov_param = url.split("pov=")[1].split("&")[0]
                    metadata["pov"] = pov_param
                    
            except Exception as e:
                logger.debug(f"Не удалось извлечь метаданные из URL: {e}")
                
        return metadata
        
    def _serialize_teams(self, teams: Dict[str, TeamInfo]) -> Dict[str, Dict[str, Any]]:
        """
        Сериализовать информацию о командах.
        
        Args:
            teams: Словарь команд
            
        Returns:
            Сериализованные данные команд
        """
        serialized = {}
        
        for team_id, team_info in teams.items():
            serialized[team_id] = {
                "name": team_info.name,
                "color_bgr": list(team_info.color_bgr),
                "color_hex": f"#{team_info.color_bgr[2]:02x}{team_info.color_bgr[1]:02x}{team_info.color_bgr[0]:02x}",
                "position": team_info.position,
                "rank": team_info.rank
            }
            
        return serialized
        
    def _calculate_statistics(self, tracking_data: List[TrackingResult]) -> Dict[str, Any]:
        """
        Вычислить статистику отслеживания.
        
        Args:
            tracking_data: Данные отслеживания
            
        Returns:
            Словарь со статистикой
        """
        if not tracking_data:
            return {}
            
        stats = {
            "total_frames": len(tracking_data),
            "duration_seconds": 0,
            "teams_tracked": set(),
            "team_statistics": {}
        }
        
        # Анализ каждого кадра
        for result in tracking_data:
            # Обновление продолжительности
            stats["duration_seconds"] = max(stats["duration_seconds"], result.timestamp)
            
            # Сбор информации о командах
            for team_id, position in result.team_positions.items():
                stats["teams_tracked"].add(team_id)
                
                if team_id not in stats["team_statistics"]:
                    stats["team_statistics"][team_id] = {
                        "frames_tracked": 0,
                        "avg_confidence": 0.0,
                        "positions": [],
                        "min_confidence": 1.0,
                        "max_confidence": 0.0
                    }
                    
                team_stats = stats["team_statistics"][team_id]
                team_stats["frames_tracked"] += 1
                
                confidence = position.get("confidence", 0.0)
                team_stats["positions"].append((position["x"], position["y"]))
                team_stats["min_confidence"] = min(team_stats["min_confidence"], confidence)
                team_stats["max_confidence"] = max(team_stats["max_confidence"], confidence)
                
        # Вычисление средних значений
        stats["teams_tracked"] = list(stats["teams_tracked"])
        
        for team_id, team_stats in stats["team_statistics"].items():
            # Средняя уверенность
            total_confidence = 0
            confidence_count = 0
            
            for result in tracking_data:
                if team_id in result.team_positions:
                    total_confidence += result.team_positions[team_id].get("confidence", 0.0)
                    confidence_count += 1
                    
            if confidence_count > 0:
                team_stats["avg_confidence"] = total_confidence / confidence_count
                
            # Процент успешного трекинга
            team_stats["tracking_success_rate"] = (team_stats["frames_tracked"] / 
                                                  len(tracking_data)) * 100
                                                  
            # Средняя позиция
            if team_stats["positions"]:
                positions = np.array(team_stats["positions"])
                team_stats["avg_position"] = {
                    "x": float(np.mean(positions[:, 0])),
                    "y": float(np.mean(positions[:, 1]))
                }
                team_stats["position_std"] = {
                    "x": float(np.std(positions[:, 0])),
                    "y": float(np.std(positions[:, 1]))
                }
                
            # Удаляем сырые позиции для уменьшения размера файла
            del team_stats["positions"]
            
        return stats
        
    def export_to_json(self, tracking_data: List[TrackingResult], 
                      output_path: Optional[str] = None,
                      teams: Optional[Dict[str, TeamInfo]] = None,
                      **metadata_kwargs) -> Path:
        """
        Экспортировать данные в JSON формат.
        
        Args:
            tracking_data: Данные отслеживания
            output_path: Путь для сохранения (опционально)
            teams: Информация о командах
            **metadata_kwargs: Дополнительные метаданные
            
        Returns:
            Путь к сохраненному файлу
        """
        try:
            logger.info("Экспорт данных в JSON формат")
            
            if not tracking_data:
                raise DataExportError("Нет данных для экспорта")
                
            # Определение пути вывода
            if not output_path:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                output_path = self.output_dir / f"apex_tracking_{timestamp}.json"
            else:
                output_path = Path(output_path)
                
            # Создание директории если нужно
            output_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Подготовка данных для экспорта
            export_data = {
                "metadata": self._prepare_metadata(**metadata_kwargs),
                "teams": self._serialize_teams(teams) if teams else {},
                "statistics": self._calculate_statistics(tracking_data),
                "tracking_data": []
            }
            
            # Сериализация данных трекинга
            for result in tracking_data:
                frame_data = {
                    "frame_number": result.frame_number,
                    "timestamp": round(result.timestamp, 3),
                    "team_positions": {}
                }
                
                for team_id, position in result.team_positions.items():
                    frame_data["team_positions"][team_id] = {
                        "x": round(position["x"], 1),
                        "y": round(position["y"], 1),
                        "confidence": round(position["confidence"], 3)
                    }
                    
                export_data["tracking_data"].append(frame_data)
                
            # Сохранение в файл
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(export_data, f, indent=2, ensure_ascii=False)
                
            file_size = output_path.stat().st_size / (1024 * 1024)  # MB
            logger.info(f"Данные экспортированы в {output_path} ({file_size:.1f} MB)")
            logger.info(f"Экспортировано кадров: {len(tracking_data)}")
            logger.info(f"Команд отслежено: {len(export_data['statistics'].get('teams_tracked', []))}")
            
            return output_path
            
        except Exception as e:
            logger.error(f"Ошибка экспорта в JSON: {e}")
            raise DataExportError(f"Не удалось экспортировать в JSON: {e}")
            
    def export_to_csv(self, tracking_data: List[TrackingResult], 
                     output_path: Optional[str] = None) -> Path:
        """
        Экспортировать данные в CSV формат.
        
        Args:
            tracking_data: Данные отслеживания
            output_path: Путь для сохранения
            
        Returns:
            Путь к сохраненному файлу
        """
        try:
            logger.info("Экспорт данных в CSV формат")
            
            if not tracking_data:
                raise DataExportError("Нет данных для экспорта")
                
            # Определение пути вывода
            if not output_path:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                output_path = self.output_dir / f"apex_tracking_{timestamp}.csv"
            else:
                output_path = Path(output_path)
                
            output_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Подготовка данных для CSV (плоская структура)
            rows = []
            
            for result in tracking_data:
                for team_id, position in result.team_positions.items():
                    rows.append({
                        "frame_number": result.frame_number,
                        "timestamp": round(result.timestamp, 3),
                        "team_id": team_id,
                        "x": round(position["x"], 1),
                        "y": round(position["y"], 1),
                        "confidence": round(position["confidence"], 3)
                    })
                    
            # Сохранение в CSV
            if rows:
                fieldnames = ["frame_number", "timestamp", "team_id", "x", "y", "confidence"]
                
                with open(output_path, 'w', newline='', encoding='utf-8') as csvfile:
                    writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                    writer.writeheader()
                    writer.writerows(rows)
                    
            logger.info(f"CSV данные экспортированы в {output_path}")
            logger.info(f"Записей: {len(rows)}")
            
            return output_path
            
        except Exception as e:
            logger.error(f"Ошибка экспорта в CSV: {e}")
            raise DataExportError(f"Не удалось экспортировать в CSV: {e}")
            
    def export_summary_report(self, tracking_data: List[TrackingResult],
                            teams: Optional[Dict[str, TeamInfo]] = None,
                            output_path: Optional[str] = None) -> Path:
        """
        Создать сводный отчет анализа.
        
        Args:
            tracking_data: Данные отслеживания
            teams: Информация о командах
            output_path: Путь для сохранения
            
        Returns:
            Путь к сохраненному отчету
        """
        try:
            logger.info("Создание сводного отчета")
            
            if not output_path:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                output_path = self.output_dir / f"apex_report_{timestamp}.txt"
            else:
                output_path = Path(output_path)
                
            output_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Получение статистики
            stats = self._calculate_statistics(tracking_data)
            
            # Создание отчета
            report_lines = [
                "=== APEX LEGENDS TRACKING ANALYSIS REPORT ===",
                f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                "",
                "OVERVIEW:",
                f"  Total frames processed: {stats.get('total_frames', 0)}",
                f"  Duration: {stats.get('duration_seconds', 0):.1f} seconds",
                f"  Teams detected: {len(stats.get('teams_tracked', []))}",
                "",
                "TEAM ANALYSIS:",
            ]
            
            # Анализ по командам
            for team_id in stats.get('teams_tracked', []):
                team_stats = stats['team_statistics'].get(team_id, {})
                team_info = teams.get(team_id) if teams else None
                
                report_lines.extend([
                    f"  Team #{team_id}:",
                    f"    Name: {team_info.name if team_info else 'Unknown'}",
                    f"    Frames tracked: {team_stats.get('frames_tracked', 0)}",
                    f"    Success rate: {team_stats.get('tracking_success_rate', 0):.1f}%",
                    f"    Avg confidence: {team_stats.get('avg_confidence', 0):.3f}",
                    f"    Confidence range: {team_stats.get('min_confidence', 0):.3f} - {team_stats.get('max_confidence', 0):.3f}",
                ])
                
                avg_pos = team_stats.get('avg_position', {})
                if avg_pos:
                    report_lines.append(f"    Avg position: ({avg_pos.get('x', 0):.1f}, {avg_pos.get('y', 0):.1f})")
                    
                report_lines.append("")
                
            # Сохранение отчета
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write('\n'.join(report_lines))
                
            logger.info(f"Сводный отчет сохранен: {output_path}")
            return output_path
            
        except Exception as e:
            logger.error(f"Ошибка создания отчета: {e}")
            raise DataExportError(f"Не удалось создать отчет: {e}")
            
    def export_data(self, tracking_data: List[TrackingResult], 
                   output_path: str,
                   teams: Optional[Dict[str, TeamInfo]] = None,
                   format_type: str = "auto",
                   **metadata_kwargs) -> Path:
        """
        Универсальный метод экспорта данных.
        
        Args:
            tracking_data: Данные отслеживания
            output_path: Путь для сохранения
            teams: Информация о командах
            format_type: Тип формата ('json', 'csv', 'report', 'auto')
            **metadata_kwargs: Дополнительные метаданные
            
        Returns:
            Путь к сохраненному файлу
        """
        output_path = Path(output_path)
        
        # Автоматическое определение формата по расширению
        if format_type == "auto":
            extension = output_path.suffix.lower()
            format_map = {
                '.json': 'json',
                '.csv': 'csv',
                '.txt': 'report',
                '.md': 'report'
            }
            format_type = format_map.get(extension, 'json')
            
        # Экспорт в соответствующий формат
        if format_type == "json":
            return self.export_to_json(tracking_data, output_path, teams, **metadata_kwargs)
        elif format_type == "csv":
            return self.export_to_csv(tracking_data, output_path)
        elif format_type == "report":
            return self.export_summary_report(tracking_data, teams, output_path)
        else:
            raise DataExportError(f"Неподдерживаемый формат: {format_type}")
            
    def batch_export(self, tracking_data: List[TrackingResult],
                    base_name: str,
                    teams: Optional[Dict[str, TeamInfo]] = None,
                    formats: Optional[List[str]] = None,
                    **metadata_kwargs) -> List[Path]:
        """
        Экспорт в несколько форматов одновременно.
        
        Args:
            tracking_data: Данные отслеживания
            base_name: Базовое имя файлов
            teams: Информация о командах
            formats: Список форматов для экспорта
            **metadata_kwargs: Дополнительные метаданные
            
        Returns:
            Список путей к сохраненным файлам
        """
        if formats is None:
            formats = ["json", "csv", "report"]
            
        exported_files = []
        
        for format_type in formats:
            try:
                if format_type == "json":
                    path = self.output_dir / f"{base_name}.json"
                    exported_files.append(self.export_to_json(tracking_data, path, teams, **metadata_kwargs))
                elif format_type == "csv":
                    path = self.output_dir / f"{base_name}.csv"
                    exported_files.append(self.export_to_csv(tracking_data, path))
                elif format_type == "report":
                    path = self.output_dir / f"{base_name}_report.txt"
                    exported_files.append(self.export_summary_report(tracking_data, teams, path))
                    
            except Exception as e:
                logger.error(f"Ошибка экспорта в формат {format_type}: {e}")
                
        logger.info(f"Batch экспорт завершен. Сохранено файлов: {len(exported_files)}")
        return exported_files