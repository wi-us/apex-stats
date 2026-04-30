from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build


SCOPES = [
    "https://www.googleapis.com/auth/yt-analytics.readonly",
    "https://www.googleapis.com/auth/youtube.readonly",
]


@dataclass
class QueryResult:
    title: str
    columns: list[str]
    rows: list[list[Any]]


class YouTubeStatsCollector:
    def __init__(
        self,
        client_secrets_file: str,
        token_file: str,
        db_path: str,
    ) -> None:
        self.client_secrets_file = Path(client_secrets_file)
        self.token_file = Path(token_file)
        self.db_path = Path(db_path)
        self.youtube_analytics = None
        self.youtube_data = None
        self.channel_id = ""
        self.channel_title = ""
        self.channel_published_at = ""

    def authenticate(self) -> None:
        credentials = self._load_or_create_credentials()
        self.youtube_analytics = build("youtubeAnalytics", "v2", credentials=credentials)
        self.youtube_data = build("youtube", "v3", credentials=credentials)
        self.channel_id, self.channel_title, self.channel_published_at = self._get_channel_info()

    def _load_or_create_credentials(self) -> Credentials:
        credentials = None
        if self.token_file.exists():
            credentials = Credentials.from_authorized_user_file(str(self.token_file), SCOPES)

        if credentials and credentials.valid:
            return credentials

        if credentials and credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
            self.token_file.write_text(credentials.to_json(), encoding="utf-8")
            return credentials

        if not self.client_secrets_file.exists():
            raise FileNotFoundError(
                f"Client secrets file not found: {self.client_secrets_file}. "
                "Create OAuth Desktop credentials in Google Cloud and put JSON here."
            )

        flow = InstalledAppFlow.from_client_secrets_file(str(self.client_secrets_file), SCOPES)
        credentials = flow.run_local_server(port=0)
        self.token_file.write_text(credentials.to_json(), encoding="utf-8")
        return credentials

    def _get_channel_info(self) -> tuple[str, str, str]:
        response = (
            self.youtube_data.channels()
            .list(part="snippet", mine=True, maxResults=1)
            .execute()
        )
        items = response.get("items", [])
        if not items:
            raise RuntimeError("No channel found for authorized account.")

        channel = items[0]
        snippet = channel["snippet"]
        return channel["id"], snippet["title"], snippet["publishedAt"]

    def get_channel_start_date(self) -> str:
        if not self.channel_published_at:
            raise RuntimeError("Channel metadata is not loaded. Call authenticate() first.")
        return self.channel_published_at[:10]

    def collect_deep_analytics(
        self,
        start_date: str,
        end_date: str,
        top_videos_limit: int = 200,
    ) -> dict[str, QueryResult]:
        reports: dict[str, QueryResult] = {}

        base_metrics = ",".join(
            [
                "views",
                "estimatedMinutesWatched",
                "averageViewDuration",
                "averageViewPercentage",
                "likes",
                "comments",
                "shares",
                "subscribersGained",
                "subscribersLost",
            ]
        )

        reports["top_videos"] = self._run_query(
            title="Top videos",
            start_date=start_date,
            end_date=end_date,
            metrics=base_metrics,
            dimensions="video",
            sort="-views",
            max_results=top_videos_limit,
        )

        reports["daily_overview"] = self._run_query(
            title="Daily overview",
            start_date=start_date,
            end_date=end_date,
            metrics=base_metrics,
            dimensions="day",
            sort="day",
            max_results=1000,
        )

        reports["traffic_sources"] = self._run_query(
            title="Traffic sources",
            start_date=start_date,
            end_date=end_date,
            metrics="views,estimatedMinutesWatched,averageViewDuration",
            dimensions="insightTrafficSourceType",
            sort="-views",
            max_results=50,
        )

        reports["geo_countries"] = self._run_query(
            title="Geo countries",
            start_date=start_date,
            end_date=end_date,
            metrics="views,estimatedMinutesWatched,averageViewDuration",
            dimensions="country",
            sort="-views",
            max_results=250,
        )

        reports["devices"] = self._run_query(
            title="Device types",
            start_date=start_date,
            end_date=end_date,
            metrics="views,estimatedMinutesWatched,averageViewDuration",
            dimensions="deviceType",
            sort="-views",
            max_results=20,
        )

        reports["operating_systems"] = self._run_query(
            title="Operating systems",
            start_date=start_date,
            end_date=end_date,
            metrics="views,estimatedMinutesWatched,averageViewDuration",
            dimensions="operatingSystem",
            sort="-views",
            max_results=50,
        )

        reports["platforms"] = self._run_query(
            title="Platform types",
            start_date=start_date,
            end_date=end_date,
            metrics="views,estimatedMinutesWatched,averageViewDuration",
            dimensions="youtubeProduct",
            sort="-views",
            max_results=50,
        )

        reports["player_types"] = self._run_query(
            title="Playback location types",
            start_date=start_date,
            end_date=end_date,
            metrics="views,estimatedMinutesWatched,averageViewDuration",
            dimensions="insightPlaybackLocationType",
            sort="-views",
            max_results=50,
        )

        reports["demographics"] = self._run_query(
            title="Demographics",
            start_date=start_date,
            end_date=end_date,
            metrics="viewerPercentage",
            dimensions="ageGroup,gender",
            sort="ageGroup,gender",
            max_results=200,
        )

        self._enrich_top_videos_with_titles(reports)

        return reports

    def _run_query(
        self,
        title: str,
        start_date: str,
        end_date: str,
        metrics: str,
        dimensions: str,
        sort: str,
        max_results: int,
    ) -> QueryResult:
        response = (
            self.youtube_analytics.reports()
            .query(
                ids="channel==MINE",
                startDate=start_date,
                endDate=end_date,
                metrics=metrics,
                dimensions=dimensions,
                sort=sort,
                maxResults=max_results,
            )
            .execute()
        )

        columns = [item["name"] for item in response.get("columnHeaders", [])]
        rows = response.get("rows", [])
        return QueryResult(title=title, columns=columns, rows=rows)

    def _enrich_top_videos_with_titles(self, reports: dict[str, QueryResult]) -> None:
        top_videos = reports.get("top_videos")
        if not top_videos or not top_videos.rows:
            return

        try:
            video_col_idx = top_videos.columns.index("video")
        except ValueError:
            return

        video_ids = [str(row[video_col_idx]) for row in top_videos.rows if row[video_col_idx]]
        metadata_map = self._fetch_video_metadata(video_ids)

        top_videos.columns = [*top_videos.columns, "videoTitle", "videoType", "durationSeconds"]
        enriched_rows = []
        for row in top_videos.rows:
            video_id = str(row[video_col_idx])
            metadata = metadata_map.get(video_id, {})
            video_title = metadata.get("videoTitle", "")
            video_type = metadata.get("videoType", "")
            duration_seconds = metadata.get("durationSeconds", "")
            enriched_rows.append([*row, video_title, video_type, duration_seconds])
        top_videos.rows = enriched_rows

    def _fetch_video_metadata(self, video_ids: list[str]) -> dict[str, dict[str, str]]:
        unique_ids = list(dict.fromkeys(video_ids))
        if not unique_ids:
            return {}

        result: dict[str, dict[str, str]] = {}
        for i in range(0, len(unique_ids), 50):
            chunk = unique_ids[i : i + 50]
            response = (
                self.youtube_data.videos()
                .list(
                    part="snippet,contentDetails",
                    id=",".join(chunk),
                    maxResults=50,
                )
                .execute()
            )
            for item in response.get("items", []):
                duration_iso = item.get("contentDetails", {}).get("duration", "")
                duration_seconds = self._duration_to_seconds(duration_iso)
                video_type = self._classify_video_type(duration_seconds)
                result[item["id"]] = {
                    "videoTitle": item.get("snippet", {}).get("title", ""),
                    "videoType": video_type,
                    "durationSeconds": str(duration_seconds),
                }
        return result

    def _duration_to_seconds(self, duration_iso: str) -> int:
        if not duration_iso:
            return 0
        pattern = r"^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$"
        match = re.match(pattern, duration_iso)
        if not match:
            return 0
        hours = int(match.group(1) or 0)
        minutes = int(match.group(2) or 0)
        seconds = int(match.group(3) or 0)
        return hours * 3600 + minutes * 60 + seconds

    def _classify_video_type(self, duration_seconds: int) -> str:
        if duration_seconds <= 0:
            return "UNKNOWN"
        return "SHORTS" if duration_seconds <= 60 else "FULL"

    def save_reports_to_sqlite(
        self,
        reports: dict[str, QueryResult],
        start_date: str,
        end_date: str,
    ) -> tuple[Path, int]:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("PRAGMA foreign_keys = ON")
            self._create_meta_tables(conn)

            run_id = self._insert_run(conn, start_date=start_date, end_date=end_date)
            for report_name, report in reports.items():
                table_name = f"report_{self._safe_table_name(report_name)}"
                self._create_report_table(conn, table_name, report.columns)
                self._insert_report_rows(conn, run_id, table_name, report.columns, report.rows)

            conn.commit()
        return self.db_path, run_id

    def _create_meta_tables(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id TEXT NOT NULL,
                channel_title TEXT NOT NULL,
                start_date TEXT NOT NULL,
                end_date TEXT NOT NULL,
                collected_at TEXT NOT NULL
            )
            """
        )

    def _insert_run(self, conn: sqlite3.Connection, start_date: str, end_date: str) -> int:
        cursor = conn.execute(
            """
            INSERT INTO runs (channel_id, channel_title, start_date, end_date, collected_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                self.channel_id,
                self.channel_title,
                start_date,
                end_date,
                datetime.now().isoformat(),
            ),
        )
        return int(cursor.lastrowid)

    def _safe_table_name(self, raw_name: str) -> str:
        normalized = raw_name.strip().lower()
        normalized = re.sub(r"[^a-z0-9_]+", "_", normalized)
        return normalized.strip("_")

    def _safe_column_name(self, raw_name: str) -> str:
        normalized = raw_name.strip()
        normalized = re.sub(r"[^a-zA-Z0-9_]+", "_", normalized)
        if not normalized:
            normalized = "col"
        if normalized[0].isdigit():
            normalized = f"c_{normalized}"
        return normalized

    def _create_report_table(
        self,
        conn: sqlite3.Connection,
        table_name: str,
        columns: list[str],
    ) -> None:
        sql_columns = [self._safe_column_name(column) for column in columns]
        dynamic_columns = ", ".join([f'"{column}" TEXT' for column in sql_columns])
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS "{table_name}" (
                run_id INTEGER NOT NULL,
                row_index INTEGER NOT NULL,
                {dynamic_columns},
                FOREIGN KEY(run_id) REFERENCES runs(id)
            )
            """
        )
        self._ensure_table_columns(conn, table_name, sql_columns)

    def _ensure_table_columns(
        self,
        conn: sqlite3.Connection,
        table_name: str,
        expected_columns: list[str],
    ) -> None:
        existing_cols = {
            row[1]
            for row in conn.execute(f'PRAGMA table_info("{table_name}")').fetchall()
        }
        system_cols = {"run_id", "row_index"}
        for column in expected_columns:
            if column in existing_cols or column in system_cols:
                continue
            conn.execute(f'ALTER TABLE "{table_name}" ADD COLUMN "{column}" TEXT')

    def _insert_report_rows(
        self,
        conn: sqlite3.Connection,
        run_id: int,
        table_name: str,
        columns: list[str],
        rows: list[list[Any]],
    ) -> None:
        sql_columns = [self._safe_column_name(column) for column in columns]
        quoted_columns = ", ".join([f'"{column}"' for column in sql_columns])
        placeholders = ", ".join(["?"] * (2 + len(sql_columns)))

        insert_sql = (
            f'INSERT INTO "{table_name}" (run_id, row_index, {quoted_columns}) '
            f"VALUES ({placeholders})"
        )

        payload = []
        for idx, row in enumerate(rows):
            serialized_row = ["" if value is None else str(value) for value in row]
            payload.append((run_id, idx, *serialized_row))
        if payload:
            conn.executemany(insert_sql, payload)
