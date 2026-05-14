"use client";

import type { CSSProperties, ReactNode, RefObject } from "react";
import { Fragment, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { API_URL } from "../../lib/api";
import { formatMapLabel, formatMmSs } from "../../lib/match-viewer-utils";
import type { MatchViewerState } from "../../lib/useMatchViewerState";
import { ROUND_2_ENABLED, type RingTimelineMarker } from "../../lib/useMatchViewerState";
import type { Team } from "../../lib/types";
import {
  MapPlayer,
  RING_CAMERA_NOISE_SLIDER_MAX,
  RING_CAMERA_NOISE_SLIDER_MAX_HEAVY,
} from "../map-player";
import { CameraTimelineCharts } from "./CameraTimelineCharts";

function teamAccentCss(team: Team): string {
  const [b, g, r] = team.colorBgr;
  return `rgb(${r},${g},${b})`;
}

/** Индекс слота TEAM_1 … TEAM_20; иначе null. */
function parseTeamSlotIndex(teamId: string): number | null {
  const m = /^TEAM_(\d+)$/i.exec(teamId.trim());
  if (!m) return null;
  return Number(m[1]);
}

/** Колонки ушей: слоты TEAM_1–TEAM_10 слева, TEAM_11–TEAM_20 справа, порядок по номеру. */
function teamsForEarSlotRange(teams: Team[], lo: number, hi: number): Team[] {
  return teams
    .filter((t) => {
      const n = parseTeamSlotIndex(t.id);
      return n != null && n >= lo && n <= hi;
    })
    .sort((a, b) => parseTeamSlotIndex(a.id)! - parseTeamSlotIndex(b.id)!);
}

function teamFsMonogram(team: Team): string {
  const t = team.name.trim();
  if (!t) {
    const id = team.id.trim();
    return id ? id.charAt(0).toUpperCase() : "?";
  }
  const cp = t.codePointAt(0);
  if (cp === undefined) return "?";
  return String.fromCodePoint(cp).toUpperCase();
}

function MapFsTeamChipLogo({ team }: { team: Team }) {
  const [imgFailed, setImgFailed] = useState(false);
  const src = `${API_URL}/teams/${encodeURIComponent(team.id)}/logo.png`;
  const onErr = useCallback(() => setImgFailed(true), []);
  if (!imgFailed) {
    return <img className="map-fs-team-chip-img" src={src} alt="" loading="lazy" decoding="async" onError={onErr} />;
  }
  return (
    <span className="map-fs-team-chip-mono" aria-hidden>
      {teamFsMonogram(team)}
    </span>
  );
}

function MapFullscreenTeamEarColumn({
  teams,
  side,
  eliminationByTeamId,
  eliminationAtSecByTeamId,
  timeCursor,
  selectedTeamIds,
  toggleTeam,
  mapWrapFullscreen,
}: {
  teams: Team[];
  side: "left" | "right";
  eliminationByTeamId: Map<string, boolean>;
  eliminationAtSecByTeamId: Map<string, number | undefined>;
  timeCursor: number;
  selectedTeamIds: string[];
  toggleTeam: (id: string) => void;
  mapWrapFullscreen: boolean;
}) {
  if (teams.length === 0) return null;
  const ariaLabel = side === "left" ? "Слоты TEAM_1–TEAM_10 (экран)" : "Слоты TEAM_11–TEAM_20 (экран)";
  return (
    <aside
      className={`map-fs-team-ear map-fs-team-ear--${side}`}
      role="group"
      aria-label={ariaLabel}
      aria-hidden={!mapWrapFullscreen}
    >
      {teams.map((team) => {
        const on = selectedTeamIds.includes(team.id);
        const eliminatedFlag = eliminationByTeamId.get(team.id) === true;
        const elimAt = eliminationAtSecByTeamId.get(team.id);
        const showEliminated =
          eliminatedFlag &&
          (elimAt === undefined || !Number.isFinite(elimAt) || timeCursor >= elimAt);
        const elimHint =
          eliminatedFlag && elimAt != null && Number.isFinite(elimAt)
            ? ` · ELIM с ${formatMmSs(elimAt)}`
            : "";
        return (
          <button
            key={team.id}
            type="button"
            className={`map-fs-team-chip${on ? " is-on" : " is-off"}${showEliminated ? " is-eliminated" : ""}`}
            aria-pressed={on}
            style={{ "--bc": teamAccentCss(team) } as CSSProperties}
            title={`${team.name}: ${on ? "скрыть" : "показать"} на карте${elimHint}`}
            onClick={() => toggleTeam(team.id)}
          >
            <span className="map-fs-team-chip-accent" aria-hidden />
            <span className="map-fs-team-chip-logo">
              <MapFsTeamChipLogo team={team} />
            </span>
          </button>
        );
      })}
    </aside>
  );
}

function BroadcastMapTransportBar({
  className,
  style,
  ariaHidden,
  isPlaying,
  setIsPlaying,
  timeCursor,
  setTimeCursor,
  roundRange,
  ringTimelineMarkers,
  timelineRingStackMax,
  playbackSpeed,
  setPlaybackSpeed,
  playerRef,
  onFullscreenClick,
  fullscreenActive,
}: {
  className?: string;
  style?: CSSProperties;
  ariaHidden?: boolean;
  isPlaying: boolean;
  setIsPlaying: MatchViewerState["setIsPlaying"];
  timeCursor: number;
  setTimeCursor: MatchViewerState["setTimeCursor"];
  roundRange: MatchViewerState["roundRange"];
  ringTimelineMarkers: RingTimelineMarker[];
  timelineRingStackMax: number;
  playbackSpeed: number;
  setPlaybackSpeed: MatchViewerState["setPlaybackSpeed"];
  playerRef: RefObject<HTMLDivElement | null>;
  /** Режим студии и др.: своя логика вместо Element.requestFullscreen */
  onFullscreenClick?: () => void;
  /** Для подписи/стиля кнопки (например режим студии открыт) */
  fullscreenActive?: boolean;
}) {
  return (
    <div
      className={["panel", "transport-bar", className].filter(Boolean).join(" ")}
      style={style}
      aria-hidden={ariaHidden}
    >
      <div className="transport-lead">
        <button
          type="button"
          className={`play-toggle${isPlaying ? " is-playing" : ""}`}
          aria-pressed={isPlaying}
          aria-label={isPlaying ? "Пауза" : "Пуск"}
          onClick={() => setIsPlaying((p) => !p)}
        >
          <img className="icon-play" src="/icon-play.png" width={22} height={22} alt="" style={{ display: isPlaying ? "none" : "block" }} />
          <img className="icon-pause" src="/icon-pause.png" width={22} height={22} alt="" style={{ display: isPlaying ? "block" : "none" }} />
        </button>
        <div className="transport-time">
          <span>{formatMmSs(timeCursor)}</span>
          <span className="transport-time-sep" aria-hidden>
            /
          </span>
          <span>{formatMmSs(roundRange.endSec)}</span>
        </div>
      </div>
      <div className="timeline-wrap">
        <div className="timeline-rings-ticks">
          <div
            className="timeline-markers-band"
            style={{ minHeight: ringTimelineMarkers.length === 0 ? 20 : 22 + timelineRingStackMax * 12 }}
            aria-hidden
          >
            {ringTimelineMarkers.length === 0 ? (
              <span className="timeline-rings-empty">Нет событий колец в окне</span>
            ) : (
              <>
                {ringTimelineMarkers.map((m) => (
                  <span
                    key={m.id}
                    className="timeline-ring-marker-label"
                    style={{
                      left: `${Math.min(100, Math.max(0, m.pct))}%`,
                      bottom: `${10 + m.stack * 12}px`,
                      transform: "translateX(-50%)",
                    }}
                    title={`${formatMmSs(m.sec)} · ${m.label}`}
                  >
                    {m.label}
                  </span>
                ))}
                {ringTimelineMarkers.map((m) => (
                  <span
                    key={`tick-${m.id}`}
                    className="timeline-ring-marker-tick"
                    style={{
                      left: `${Math.min(100, Math.max(0, m.pct))}%`,
                      transform: "translateX(-50%)",
                    }}
                  />
                ))}
              </>
            )}
          </div>
        </div>
        <div className="timeline-track-wrap">
          <input
            type="range"
            className="timeline"
            min={roundRange.startSec}
            max={roundRange.endSec}
            step={1}
            value={timeCursor}
            aria-label="Позиция на таймлайне"
            onChange={(e) => setTimeCursor(Number(e.target.value))}
          />
        </div>
      </div>
      <div className="speed-select-wrap">
        <select
          className="speed-select"
          aria-label="Скорость воспроизведения"
          value={String(playbackSpeed)}
          onChange={(e) => setPlaybackSpeed(Number(e.target.value))}
        >
          <option value="0.25">0.25×</option>
          <option value="0.5">0.5×</option>
          <option value="1">1×</option>
          <option value="1.5">1.5×</option>
          <option value="2">2×</option>
          <option value="3">3×</option>
        </select>
      </div>
      <button
        type="button"
        className={`btn-fs${fullscreenActive ? " is-active" : ""}`}
        aria-label={fullscreenActive ? "Выйти из режима студии" : "Режим студии — на весь экран"}
        title={fullscreenActive ? "Выйти из режима студии" : "Режим студии — на весь экран"}
        aria-pressed={fullscreenActive === true ? true : undefined}
        onClick={() => {
          if (onFullscreenClick) {
            onFullscreenClick();
            return;
          }
          if (!playerRef.current) return;
          if (document.fullscreenElement) void document.exitFullscreen();
          else void playerRef.current.requestFullscreen();
        }}
      >
        <svg
          className="btn-fs-icon"
          width={22}
          height={22}
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth={2}
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden
        >
          <path d="M8 3H5a2 2 0 0 0-2 2v3m18 0V5a2 2 0 0 0-2-2h-3m0 18h3a2 2 0 0 0 2-2v-3M3 16v3a2 2 0 0 0 2 2h3" />
        </svg>
      </button>
    </div>
  );
}

function ParamHint({ text }: { text: string }) {
  return (
    <span className="param-hint" title={text} aria-label={text}>
      ⓘ
    </span>
  );
}

function RailIconMenu() {
  return (
    <svg width={20} height={20} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden>
      <path d="M4 6h16M4 12h16M4 18h10" />
    </svg>
  );
}

function RailIconCatalog() {
  return (
    <svg width={20} height={20} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinejoin="round" aria-hidden>
      <path d="M12 2 2 7l10 5 10-5-10-5z" />
      <path d="m2 17 10 5 10-5" />
      <path d="m2 12 10 5 10-5" />
    </svg>
  );
}

function RailIconSliders() {
  return (
    <svg width={20} height={20} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden>
      <path d="M4 21v-7" />
      <path d="M4 10V3" />
      <path d="M12 21v-9" />
      <path d="M12 8V3" />
      <path d="M20 21v-5" />
      <path d="M20 12V3" />
      <path d="M2 14h4" />
      <path d="M10 8h4" />
      <path d="M18 16h4" />
    </svg>
  );
}

function RailIconSun() {
  return (
    <svg width={18} height={18} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden>
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2m0 16v2M4.93 4.93l1.41 1.41m11.32 11.32l1.41 1.41M2 12h2m16 0h2M6.34 17.66l-1.41 1.41m13.08-13.08l-1.41 1.41" />
    </svg>
  );
}

function RailIconMoon() {
  return (
    <svg width={18} height={18} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
    </svg>
  );
}

const VIRTUS_LOGO_SRC = "https://virtus.pro/img/logo.svg";
const STANDARD_THEME_PREVIEW_SRC = "/theme-brand-standard.png";
const THEME_APPEARANCE_ICON_SRC = "/assets/icon-theme-appearance.png";

function RailThemeControls({
  themeBrightness,
  themePalette,
  onThemeBrightnessChange,
  onThemePaletteChange,
}: {
  themeBrightness: "dark" | "light";
  themePalette: "virtus" | "standard";
  onThemeBrightnessChange: (v: "dark" | "light") => void;
  onThemePaletteChange: (v: "virtus" | "standard") => void;
}) {
  return (
    <div className="rail-theme-block">
      <div className="rail-theme-label">Яркость</div>
      <div
        className="mode-segment theme-brightness-segment"
        role="tablist"
        aria-label="Светлая или тёмная тема"
        data-theme-brightness-active={themeBrightness}
      >
        <button
          type="button"
          role="tab"
          aria-selected={themeBrightness === "light"}
          className={`theme-brightness-btn hsv-src-btn${themeBrightness === "light" ? " on" : ""}`}
          title="Светлая тема"
          onClick={() => onThemeBrightnessChange("light")}
        >
          <RailIconSun />
          <span className="sr-only">Светлая</span>
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={themeBrightness === "dark"}
          className={`theme-brightness-btn hsv-src-btn${themeBrightness === "dark" ? " on" : ""}`}
          title="Тёмная тема"
          onClick={() => onThemeBrightnessChange("dark")}
        >
          <RailIconMoon />
          <span className="sr-only">Тёмная</span>
        </button>
      </div>
      <div className="rail-theme-label">Оформление</div>
      <div className="theme-palette-row" role="group" aria-label="Палитра оформления">
        <button
          type="button"
          className={`theme-palette-btn${themePalette === "virtus" ? " is-active" : ""}`}
          title="Virtus.pro"
          aria-pressed={themePalette === "virtus"}
          onClick={() => onThemePaletteChange("virtus")}
        >
          <img src={VIRTUS_LOGO_SRC} alt="" width={48} height={36} decoding="async" />
        </button>
        <button
          type="button"
          className={`theme-palette-btn theme-palette-btn--standard${themePalette === "standard" ? " is-active" : ""}`}
          title="Стандартная"
          aria-pressed={themePalette === "standard"}
          onClick={() => onThemePaletteChange("standard")}
        >
          <img
            className="theme-palette-standard-preview"
            src={STANDARD_THEME_PREVIEW_SRC}
            alt=""
            width={48}
            height={36}
            decoding="async"
          />
        </button>
      </div>
    </div>
  );
}

function RailAccountThemeRow({
  children,
  onOpenTheme,
  showThemeButton,
}: {
  children: ReactNode;
  onOpenTheme: () => void;
  showThemeButton: boolean;
}) {
  return (
    <div className="rail-account-top-row">
      <div className="rail-account-top-row__grow">{children}</div>
      {showThemeButton ? (
        <button
          type="button"
          className="rail-theme-appearance-btn"
          onClick={onOpenTheme}
          aria-haspopup="dialog"
          aria-label="Оформление интерфейса"
          title="Оформление"
        >
          <img src={THEME_APPEARANCE_ICON_SRC} alt="" width={36} height={36} decoding="async" />
        </button>
      ) : null}
    </div>
  );
}

function ThemeAppearanceModal({
  open,
  onClose,
  themeBrightness,
  themePalette,
  onThemeBrightnessChange,
  onThemePaletteChange,
}: {
  open: boolean;
  onClose: () => void;
  themeBrightness: "dark" | "light";
  themePalette: "virtus" | "standard";
  onThemeBrightnessChange: (v: "dark" | "light") => void;
  onThemePaletteChange: (v: "virtus" | "standard") => void;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className="theme-appearance-modal-root" role="presentation">
      <button
        type="button"
        className="theme-appearance-modal-backdrop"
        aria-label="Закрыть"
        onClick={onClose}
      />
      <div
        className="theme-appearance-modal-panel panel"
        role="dialog"
        aria-modal="true"
        aria-labelledby="theme-appearance-modal-title"
      >
        <div className="theme-appearance-modal-header">
          <h3 id="theme-appearance-modal-title">Тема оформления</h3>
          <button type="button" className="rail-flyout-close" aria-label="Закрыть" onClick={onClose}>
            ×
          </button>
        </div>
        <RailThemeControls
          themeBrightness={themeBrightness}
          themePalette={themePalette}
          onThemeBrightnessChange={onThemeBrightnessChange}
          onThemePaletteChange={onThemePaletteChange}
        />
      </div>
    </div>
  );
}

function TeamColumn({
  teams,
  eliminationByTeamId,
  eliminationAtSecByTeamId,
  timeCursor,
  selectedTeamIds,
  toggleTeam,
  ariaLabel,
  className,
}: {
  teams: Team[];
  eliminationByTeamId: Map<string, boolean>;
  eliminationAtSecByTeamId: Map<string, number | undefined>;
  timeCursor: number;
  selectedTeamIds: string[];
  toggleTeam: (id: string) => void;
  ariaLabel: string;
  className: string;
}) {
  return (
    <div className={className} role="group" aria-label={ariaLabel}>
      {teams.map((team) => {
        const on = selectedTeamIds.includes(team.id);
        const eliminatedFlag = eliminationByTeamId.get(team.id) === true;
        const elimAt = eliminationAtSecByTeamId.get(team.id);
        const showEliminated =
          eliminatedFlag &&
          (elimAt === undefined || !Number.isFinite(elimAt) || timeCursor >= elimAt);
        const elimHint =
          eliminatedFlag && elimAt != null && Number.isFinite(elimAt)
            ? ` · ELIM с ${formatMmSs(elimAt)}`
            : "";
        return (
          <button
            key={team.id}
            type="button"
            className={`broadcast-team-card${on ? " is-on" : " is-off"}${showEliminated ? " is-eliminated" : ""}`}
            aria-pressed={on}
            style={{ "--bc": teamAccentCss(team) } as CSSProperties}
            title={`${on ? "Скрыть" : "Показать"} «${team.name}» на карте${elimHint}`}
            onClick={() => toggleTeam(team.id)}
          >
            <span className="broadcast-team-accent-bar" aria-hidden />
            <span className="broadcast-team-body">
              <span className="broadcast-team-brand">
                <span className="broadcast-team-logo broadcast-team-logo--placeholder" aria-hidden />
                <span className="broadcast-team-name">{team.name}</span>
              </span>
              {showEliminated ? (
                <span className="broadcast-team-elim-ribbon" aria-label="Eliminated">
                  <span className="broadcast-team-elim-text">ELIM</span>
                </span>
              ) : null}
            </span>
          </button>
        );
      })}
    </div>
  );
}

export function BroadcastViewer({
  vm,
  showTeams = true,
  settingsInLeftRail = false,
  showCameraTimelineCharts = true,
  showCalibrationPanel = true,
  showFragmentRoundPicker = true,
  rightMediaPane,
  cameraStudioLayout = false,
  themeBrightness,
  themePalette,
  onThemeBrightnessChange,
  onThemePaletteChange,
  railAccountExtras,
  railAccountHomeHref = "/admin",
  railAccountHomeLabel = "В админку",
}: {
  vm: MatchViewerState;
  showTeams?: boolean;
  settingsInLeftRail?: boolean;
  showCameraTimelineCharts?: boolean;
  showCalibrationPanel?: boolean;
  showFragmentRoundPicker?: boolean;
  rightMediaPane?: ReactNode;
  /** Режим CAMERA: отдельный полноэкран «студии», не браузерный fullscreen карты как на главной */
  cameraStudioLayout?: boolean;
  themeBrightness?: "dark" | "light";
  themePalette?: "virtus" | "standard";
  onThemeBrightnessChange?: (v: "dark" | "light") => void;
  onThemePaletteChange?: (v: "virtus" | "standard") => void;
  /** Доп. блок в левом `rail--account` (напр. переключатель режима админки) — перед блоком темы */
  railAccountExtras?: ReactNode;
  /** Ссылка из карточки аккаунта / меню (по умолчанию в админку) */
  railAccountHomeHref?: string;
  railAccountHomeLabel?: string;
}) {
  const {
    tournaments,
    matches,
    maps,
    teams,
    roundRings,
    cameraTracks,
    selectedTournamentId,
    setSelectedTournamentId,
    selectedMatchId,
    setSelectedMatchId,
    selectedMapId,
    setSelectedMapId,
    selectedTeamIds,
    selectedRound,
    setSelectedRound,
    timeCursor,
    setTimeCursor,
    isPlaying,
    setIsPlaying,
    playbackSpeed,
    setPlaybackSpeed,
    enableStopGrouping,
    setEnableStopGrouping,
    stopRadiusPx,
    setStopRadiusPx,
    stopMinDurationSec,
    setStopMinDurationSec,
    smoothWindow,
    setSmoothWindow,
    camEmaK100Permille,
    setCamEmaK100Permille,
    camEmaSpanCenti,
    setCamEmaSpanCenti,
    camEmaK200Permille,
    setCamEmaK200Permille,
    antiLatchEnabled,
    setAntiLatchEnabled,
    antiLatchTailDistancePx,
    setAntiLatchTailDistancePx,
    antiLatchTailFrames,
    setAntiLatchTailFrames,
    antiLatchRingQuietPx,
    setAntiLatchRingQuietPx,
    antiLatchSnapPercent,
    setAntiLatchSnapPercent,
    antiLatchZoomGapMilli,
    setAntiLatchZoomGapMilli,
    antiLatchZoomTailFrames,
    setAntiLatchZoomTailFrames,
    antiLatchZoomQuietMilli,
    setAntiLatchZoomQuietMilli,
    antiLatchZoomSnapPercent,
    setAntiLatchZoomSnapPercent,
    preJumpLockEnabled,
    setPreJumpLockEnabled,
    preJumpMaxDriftPermille,
    setPreJumpMaxDriftPermille,
    preJumpMaxZoomPermille,
    setPreJumpMaxZoomPermille,
    preJumpUnlockMinJumpScore,
    setPreJumpUnlockMinJumpScore,
    preJumpUnlockShiftPx,
    setPreJumpUnlockShiftPx,
    preJumpUnlockZoomMilli,
    setPreJumpUnlockZoomMilli,
    preJumpUnlockFrames,
    setPreJumpUnlockFrames,
    pathJumpThresholdPercent,
    setPathJumpThresholdPercent,
    trackStrokePx,
    setTrackStrokePx,
    ringShadePercent,
    setRingShadePercent,
    showCameraDebugHud,
    setShowCameraDebugHud,
    applyCameraShiftToTracks,
    setApplyCameraShiftToTracks,
    cameraShiftZoomStrengthPercent,
    setCameraShiftZoomStrengthPercent,
    ringCameraNoiseByRing,
    setRingCameraNoiseByRing,
    ringNoiseSliderMaxByRing,
    playerRef,
    roundWindows,
    roundRange,
    visibleTracks,
    selectedMap,
    ringRoundLabel,
    estimatedGameTimeLabel,
    ringPhaseShortLabel,
    ringPhaseLabel,
    cameraSmoothingTuning,
    toggleTeam,
    calibrationPayload,
    eliminationByTeamId,
    eliminationAtSecByTeamId,
    ringTimelineMarkers,
  } = vm;

  const [railDrawer, setRailDrawer] = useState<null | "menu" | "catalog" | "calibrate">(null);
  const [compactRailMq, setCompactRailMq] = useState(false);
  const [browserMapFullscreen, setBrowserMapFullscreen] = useState(false);
  const [cameraStudioOpen, setCameraStudioOpen] = useState(false);
  const [studioFlyout, setStudioFlyout] = useState<null | "tools" | "teams" | "overlays">(null);
  const [studioOverlays, setStudioOverlays] = useState({
    graph: true,
    rings: true,
    hud: true,
    teamTracks: true,
  });
  const [mapOverlaySettingsOpen, setMapOverlaySettingsOpen] = useState(false);
  const [themeAppearanceModalOpen, setThemeAppearanceModalOpen] = useState(false);
  const mapOverlaySettingsBtnRef = useRef<HTMLButtonElement>(null);
  const mapOverlaySettingsPanelRef = useRef<HTMLDivElement>(null);
  const shellRef = useRef<HTMLDivElement>(null);
  const mapStagePanelRef = useRef<HTMLElement>(null);

  const mapWrapFullscreen = cameraStudioLayout ? false : browserMapFullscreen;
  const cameraStudioStageOpen = cameraStudioLayout && cameraStudioOpen;
  const showCameraChartsBlock =
    showCameraTimelineCharts && (!cameraStudioStageOpen || studioOverlays.graph);

  useEffect(() => {
    if (cameraStudioLayout) {
      setBrowserMapFullscreen(false);
      return;
    }
    const onFs = () => {
      setBrowserMapFullscreen(Boolean(playerRef.current && document.fullscreenElement === playerRef.current));
    };
    document.addEventListener("fullscreenchange", onFs);
    onFs();
    return () => document.removeEventListener("fullscreenchange", onFs);
  }, [playerRef, cameraStudioLayout]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const mq = window.matchMedia("(max-width: 1280px) and (min-width: 901px)");
    const apply = () => setCompactRailMq(mq.matches);
    apply();
    mq.addEventListener("change", apply);
    return () => mq.removeEventListener("change", apply);
  }, []);

  const iconRailMode = Boolean(showTeams && rightMediaPane == null && compactRailMq);

  useEffect(() => {
    if (!cameraStudioLayout) {
      setCameraStudioOpen(false);
      setStudioFlyout(null);
    }
  }, [cameraStudioLayout]);

  useLayoutEffect(() => {
    const shell = shellRef.current;
    const stage = mapStagePanelRef.current;
    if (!shell || !stage || typeof ResizeObserver === "undefined") return;
    const apply = () => {
      const h = Math.round(stage.getBoundingClientRect().height);
      shell.style.setProperty("--paper-broadcast-work-main-h", `${h}px`);
    };
    apply();
    const ro = new ResizeObserver(apply);
    ro.observe(stage);
    window.addEventListener("resize", apply);
    return () => {
      ro.disconnect();
      window.removeEventListener("resize", apply);
    };
  }, [cameraStudioLayout, showCameraChartsBlock, showTeams, cameraStudioStageOpen, iconRailMode]);

  useEffect(() => {
    if (!cameraStudioStageOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      setStudioFlyout((f) => {
        if (f) return null;
        setCameraStudioOpen(false);
        return null;
      });
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [cameraStudioStageOpen]);

  useEffect(() => {
    if (!railDrawer) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setRailDrawer(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [railDrawer]);

  useEffect(() => {
    const el = playerRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [playerRef]);

  useEffect(() => {
    if (!mapOverlaySettingsOpen) return;
    const onDocDown = (e: MouseEvent) => {
      const t = e.target as Node;
      if (mapOverlaySettingsPanelRef.current?.contains(t)) return;
      if (mapOverlaySettingsBtnRef.current?.contains(t)) return;
      setMapOverlaySettingsOpen(false);
    };
    document.addEventListener("mousedown", onDocDown);
    return () => document.removeEventListener("mousedown", onDocDown);
  }, [mapOverlaySettingsOpen]);

  useEffect(() => {
    if (!mapOverlaySettingsOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setMapOverlaySettingsOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [mapOverlaySettingsOpen]);

  const earSlotLeftTeams = useMemo(() => teamsForEarSlotRange(teams, 1, 10), [teams]);
  const earSlotRightTeams = useMemo(() => teamsForEarSlotRange(teams, 11, 20), [teams]);

  const themeControlsReady =
    themeBrightness != null &&
    themePalette != null &&
    onThemeBrightnessChange != null &&
    onThemePaletteChange != null;

  const ringPillClosing = ringPhaseLabel.includes("CLOSING");

  const timelineRingStackMax =
    ringTimelineMarkers.length === 0 ? 0 : Math.max(0, ...ringTimelineMarkers.map((m) => m.stack));

  const mapOverlayGatesViewport = cameraStudioLayout;
  const mapPlayerRings = mapOverlayGatesViewport && !studioOverlays.rings ? [] : roundRings;
  const mapPlayerTracks = mapOverlayGatesViewport && !studioOverlays.teamTracks ? [] : visibleTracks;
  const mapPlayerShowHud = !mapOverlayGatesViewport || studioOverlays.hud;

  const applyCameraGraphTuningPreset = (presetId: string) => {
    if (presetId === "step-zoom") {
      setRingCameraNoiseByRing({ 1: 120, 2: 120, 3: 55, 4: 45, 5: 35, 6: 30 });
      setCamEmaK100Permille(32);
      setCamEmaSpanCenti(45);
      setCamEmaK200Permille(36);
      setAntiLatchTailDistancePx(28);
      setAntiLatchTailFrames(8);
      setAntiLatchRingQuietPx(2.2);
      setAntiLatchSnapPercent(45);
      setAntiLatchZoomGapMilli(25);
      setAntiLatchZoomTailFrames(6);
      setAntiLatchZoomQuietMilli(10);
      setAntiLatchZoomSnapPercent(55);
      setPreJumpUnlockMinJumpScore(140);
      setPreJumpUnlockShiftPx(18);
      setPreJumpUnlockZoomMilli(18);
      setPreJumpUnlockFrames(1);
    } else if (presetId === "ring-noise") {
      setRingCameraNoiseByRing({ 1: 200, 2: 200, 3: 90, 4: 70, 5: 55, 6: 45 });
      setCamEmaK100Permille(14);
      setCamEmaSpanCenti(80);
      setCamEmaK200Permille(16);
      setAntiLatchTailDistancePx(38);
      setAntiLatchTailFrames(22);
      setAntiLatchRingQuietPx(2.0);
      setAntiLatchSnapPercent(28);
      setAntiLatchZoomGapMilli(45);
      setAntiLatchZoomTailFrames(18);
      setAntiLatchZoomQuietMilli(14);
      setAntiLatchZoomSnapPercent(32);
      setPreJumpUnlockMinJumpScore(220);
      setPreJumpUnlockShiftPx(30);
      setPreJumpUnlockZoomMilli(32);
      setPreJumpUnlockFrames(3);
    } else if (presetId === "balanced") {
      setRingCameraNoiseByRing({ 1: 180, 2: 180, 3: 70, 4: 55, 5: 40, 6: 35 });
      setCamEmaK100Permille(20);
      setCamEmaSpanCenti(58);
      setCamEmaK200Permille(25);
      setAntiLatchTailDistancePx(34);
      setAntiLatchTailFrames(16);
      setAntiLatchRingQuietPx(2.6);
      setAntiLatchSnapPercent(32);
      setAntiLatchZoomGapMilli(35);
      setAntiLatchZoomTailFrames(12);
      setAntiLatchZoomQuietMilli(12);
      setAntiLatchZoomSnapPercent(38);
      setPreJumpUnlockMinJumpScore(180);
      setPreJumpUnlockShiftPx(24);
      setPreJumpUnlockZoomMilli(25);
      setPreJumpUnlockFrames(1);
    } else if (presetId === "very-sensitive") {
      setRingCameraNoiseByRing({ 1: 80, 2: 80, 3: 35, 4: 30, 5: 25, 6: 20 });
      setCamEmaK100Permille(48);
      setCamEmaSpanCenti(30);
      setCamEmaK200Permille(55);
      setAntiLatchTailDistancePx(20);
      setAntiLatchTailFrames(5);
      setAntiLatchRingQuietPx(3.5);
      setAntiLatchSnapPercent(60);
      setAntiLatchZoomGapMilli(14);
      setAntiLatchZoomTailFrames(4);
      setAntiLatchZoomQuietMilli(8);
      setAntiLatchZoomSnapPercent(70);
      setPreJumpUnlockMinJumpScore(100);
      setPreJumpUnlockShiftPx(12);
      setPreJumpUnlockZoomMilli(10);
      setPreJumpUnlockFrames(1);
    }
  };

  const calibrationAside = (
    <>
      <details className="filter-spoiler">
        <summary>Камера: шум по кольцам</summary>
        <div className="filter-spoiler-body">
          <p className="paper-muted-p">
            0 — резко; 100 — сильное сглаживание; кольца 1–2 до {RING_CAMERA_NOISE_SLIDER_MAX_HEAVY}. Только сайт.
          </p>
          <div className="paper-calib-box calibration-grid-2">
            {[1, 2, 3, 4, 5, 6].map((ringNo) => (
              <label key={ringNo} htmlFor={`paper-ring-noise-${ringNo}`} style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                <span className="slider-caption">
                  Кольцо {ringNo}: {ringCameraNoiseByRing[ringNo] ?? 35}
                  {ringNo <= 2 ? ` / ${RING_CAMERA_NOISE_SLIDER_MAX_HEAVY}` : ` / ${RING_CAMERA_NOISE_SLIDER_MAX}`}
                  <ParamHint text="Подавление микродерганий камеры в пределах текущего кольца. Увеличивайте, когда видно шум/дрожь; уменьшайте, если камера начинает запаздывать." />
                </span>
                <div className="timeline-track-wrap">
                  <input
                    id={`paper-ring-noise-${ringNo}`}
                    type="range"
                    className="timeline"
                    min={0}
                    max={ringNoiseSliderMaxByRing[ringNo] ?? RING_CAMERA_NOISE_SLIDER_MAX}
                    step={1}
                    value={ringCameraNoiseByRing[ringNo] ?? 35}
                    onChange={(e) => {
                      const v = Number(e.target.value);
                      setRingCameraNoiseByRing((prev) => ({ ...prev, [ringNo]: v }));
                    }}
                  />
                </div>
              </label>
            ))}
          </div>
        </div>
      </details>

      <details className="filter-spoiler">
        <summary>Камера: EMA</summary>
        <div className="filter-spoiler-body">
          <p className="paper-muted-p">Тонкая подстройка сглаживания; JSON внизу для передачи в анализ.</p>
          <div className="paper-calib-box calibration-grid-2">
            <label htmlFor="paper-cam-k100">
              <span className="slider-caption">
                k при 100%: {(camEmaK100Permille / 1000).toFixed(3)} (×1000: {camEmaK100Permille})
                <ParamHint text="Базовая скорость реакции сглаженной камеры при высоком уровне сглаживания. Выше — быстрее реакция и больше шума; ниже — плавнее, но больше лаг." />
              </span>
              <div className="timeline-track-wrap">
                <input
                  id="paper-cam-k100"
                  type="range"
                  className="timeline"
                  min={5}
                  max={80}
                  step={1}
                  value={camEmaK100Permille}
                  onChange={(e) => setCamEmaK100Permille(Number(e.target.value))}
                />
              </div>
            </label>
            <label htmlFor="paper-cam-span">
              <span className="slider-caption">
                Разброс 0→100: {(camEmaSpanCenti / 100).toFixed(2)}
                <ParamHint text="Насколько сильно отличаются режимы слабого и сильного сглаживания. Повышайте, когда нужно резче разделить 0 и 100 по поведению." />
              </span>
              <div className="timeline-track-wrap">
                <input
                  id="paper-cam-span"
                  type="range"
                  className="timeline"
                  min={15}
                  max={120}
                  step={1}
                  value={camEmaSpanCenti}
                  onChange={(e) => setCamEmaSpanCenti(Number(e.target.value))}
                />
              </div>
            </label>
            <label htmlFor="paper-cam-k200" className="calibration-grid-2-full">
              <span className="slider-caption">
                k при 200%: {(camEmaK200Permille / 10000).toFixed(4)} (×10⁴: {camEmaK200Permille})
                <ParamHint text="Крайне медленный режим для тяжелых шумов (обычно кольца 1–2). Ставьте ниже, если остаются длинные паразитные хвосты." />
              </span>
              <div className="timeline-track-wrap">
                <input
                  id="paper-cam-k200"
                  type="range"
                  className="timeline"
                  min={5}
                  max={80}
                  step={1}
                  value={camEmaK200Permille}
                  onChange={(e) => setCamEmaK200Permille(Number(e.target.value))}
                />
              </div>
            </label>
          </div>
        </div>
      </details>

      <details className="filter-spoiler">
        <summary>Анти-латч длинного хвоста</summary>
        <div className="filter-spoiler-body">
          <p className="paper-muted-p">
            Режет длинные ложные скачки: если камера долго уезжает от центра кольца при слабом движении кольца, фильтр
            мягко возвращает её обратно.
          </p>
          <div className="paper-calib-box calibration-grid-2">
            <label className="paper-calib-row calibration-grid-2-full">
              <input type="checkbox" checked={antiLatchEnabled} onChange={(e) => setAntiLatchEnabled(e.target.checked)} />
              <span style={{ fontSize: 13 }}>
                Включить anti-latch
                <ParamHint text="Защита от длинных ложных хвостов: принудительно возвращает камеру к адекватной траектории, если хвост держится долго." />
              </span>
            </label>
            <label htmlFor="paper-tail-shift">
              <span className="slider-caption">
                Порог хвоста XY: {antiLatchTailDistancePx}px
                <ParamHint text="Минимальное рассогласование камеры и центра кольца по X/Y, после которого хвост считается подозрительным." />
              </span>
              <div className="timeline-track-wrap">
                <input
                  id="paper-tail-shift"
                  type="range"
                  className="timeline"
                  min={10}
                  max={80}
                  step={1}
                  value={antiLatchTailDistancePx}
                  onChange={(e) => setAntiLatchTailDistancePx(Number(e.target.value))}
                />
              </div>
            </label>
            <label htmlFor="paper-tail-frames">
              <span className="slider-caption">
                Длительность хвоста XY (кадры): {antiLatchTailFrames}
                <ParamHint text="Сколько кадров подряд должен держаться XY-хвост перед коррекцией. Больше — меньше ложных срабатываний, но медленнее реакция." />
              </span>
              <div className="timeline-track-wrap">
                <input
                  id="paper-tail-frames"
                  type="range"
                  className="timeline"
                  min={4}
                  max={45}
                  step={1}
                  value={antiLatchTailFrames}
                  onChange={(e) => setAntiLatchTailFrames(Number(e.target.value))}
                />
              </div>
            </label>
            <label htmlFor="paper-tail-quiet">
              <span className="slider-caption">
                Тихое движение кольца: {antiLatchRingQuietPx.toFixed(1)}px
                <ParamHint text="Максимальное движение центра кольца, которое считаем «почти неподвижным». Чем меньше, тем строже условие анти-латча." />
              </span>
              <div className="timeline-track-wrap">
                <input
                  id="paper-tail-quiet"
                  type="range"
                  className="timeline"
                  min={5}
                  max={80}
                  step={1}
                  value={Math.round(antiLatchRingQuietPx * 10)}
                  onChange={(e) => setAntiLatchRingQuietPx(Number(e.target.value) / 10)}
                />
              </div>
            </label>
            <label htmlFor="paper-tail-snap">
              <span className="slider-caption">
                Сила возврата XY: {antiLatchSnapPercent}%
                <ParamHint text="Насколько агрессивно подтягивать камеру обратно к центру кольца при срабатывании XY анти-латча." />
              </span>
              <div className="timeline-track-wrap">
                <input
                  id="paper-tail-snap"
                  type="range"
                  className="timeline"
                  min={5}
                  max={100}
                  step={1}
                  value={antiLatchSnapPercent}
                  onChange={(e) => setAntiLatchSnapPercent(Number(e.target.value))}
                />
              </div>
            </label>
            <label htmlFor="paper-tail-z-gap">
              <span className="slider-caption">
                Порог хвоста Z: {(antiLatchZoomGapMilli / 1000).toFixed(3)}
                <ParamHint text="Минимальное рассогласование сглаженного zoom и raw zoom, чтобы считать его подозрительным хвостом по масштабу." />
              </span>
              <div className="timeline-track-wrap">
                <input
                  id="paper-tail-z-gap"
                  type="range"
                  className="timeline"
                  min={10}
                  max={180}
                  step={1}
                  value={antiLatchZoomGapMilli}
                  onChange={(e) => setAntiLatchZoomGapMilli(Number(e.target.value))}
                />
              </div>
            </label>
            <label htmlFor="paper-tail-z-frames">
              <span className="slider-caption">
                Длительность хвоста Z (кадры): {antiLatchZoomTailFrames}
                <ParamHint text="Сколько кадров подряд должен держаться zoom-хвост перед возвратом масштаба." />
              </span>
              <div className="timeline-track-wrap">
                <input
                  id="paper-tail-z-frames"
                  type="range"
                  className="timeline"
                  min={3}
                  max={45}
                  step={1}
                  value={antiLatchZoomTailFrames}
                  onChange={(e) => setAntiLatchZoomTailFrames(Number(e.target.value))}
                />
              </div>
            </label>
            <label htmlFor="paper-tail-z-quiet">
              <span className="slider-caption">
                Тихий raw zoom: {(antiLatchZoomQuietMilli / 1000).toFixed(3)}
                <ParamHint text="Если raw zoom меняется не сильнее этого порога, считаем что реального движения по zoom нет и хвост скорее ложный." />
              </span>
              <div className="timeline-track-wrap">
                <input
                  id="paper-tail-z-quiet"
                  type="range"
                  className="timeline"
                  min={2}
                  max={80}
                  step={1}
                  value={antiLatchZoomQuietMilli}
                  onChange={(e) => setAntiLatchZoomQuietMilli(Number(e.target.value))}
                />
              </div>
            </label>
            <label htmlFor="paper-tail-z-snap">
              <span className="slider-caption">
                Сила возврата Z: {antiLatchZoomSnapPercent}%
                <ParamHint text="Насколько быстро возвращать сглаженный zoom к raw zoom при срабатывании zoom анти-латча." />
              </span>
              <div className="timeline-track-wrap">
                <input
                  id="paper-tail-z-snap"
                  type="range"
                  className="timeline"
                  min={5}
                  max={100}
                  step={1}
                  value={antiLatchZoomSnapPercent}
                  onChange={(e) => setAntiLatchZoomSnapPercent(Number(e.target.value))}
                />
              </div>
            </label>
          </div>
        </div>
      </details>

      <details className="filter-spoiler">
        <summary>Жесткий lock до первого реального скачка</summary>
        <div className="filter-spoiler-body">
          <p className="paper-muted-p">
            Держит камеру в коридоре до подтвержденного реального jump-события. Подходит, когда до ~8:30 должны быть почти
            ровные x/y/zoom.
          </p>
          <div className="paper-calib-box calibration-grid-2">
            <label className="paper-calib-row calibration-grid-2-full">
              <input type="checkbox" checked={preJumpLockEnabled} onChange={(e) => setPreJumpLockEnabled(e.target.checked)} />
              <span style={{ fontSize: 13 }}>
                Включить pre-jump lock
                <ParamHint text="Пока реальный скачок не подтвержден, камера удерживается в узком коридоре по X/Y и zoom." />
              </span>
            </label>
            <label htmlFor="paper-prejump-drift">
              <span className="slider-caption">
                Коридор XY до unlock: {(preJumpMaxDriftPermille / 10).toFixed(1)}%
                <ParamHint text="Максимально допустимое отклонение X/Y до первого реального скачка. Для цели 0–2% ставьте 2.0%." />
              </span>
              <div className="timeline-track-wrap">
                <input
                  id="paper-prejump-drift"
                  type="range"
                  className="timeline"
                  min={5}
                  max={50}
                  step={1}
                  value={preJumpMaxDriftPermille}
                  onChange={(e) => setPreJumpMaxDriftPermille(Number(e.target.value))}
                />
              </div>
            </label>
            <label htmlFor="paper-prejump-zoom">
              <span className="slider-caption">
                Коридор zoom до unlock: {(preJumpMaxZoomPermille / 10).toFixed(1)}%
                <ParamHint text="Максимальное отклонение zoom до первого реального скачка. Для цели 0–2% ставьте 2.0%." />
              </span>
              <div className="timeline-track-wrap">
                <input
                  id="paper-prejump-zoom"
                  type="range"
                  className="timeline"
                  min={5}
                  max={50}
                  step={1}
                  value={preJumpMaxZoomPermille}
                  onChange={(e) => setPreJumpMaxZoomPermille(Number(e.target.value))}
                />
              </div>
            </label>
            <label htmlFor="paper-prejump-score">
              <span className="slider-caption">
                Unlock min jumpScore: {preJumpUnlockMinJumpScore}
                <ParamHint text="Чем выше порог, тем сложнее снять lock (меньше ложных unlock, но риск пропустить реальный ранний скачок)." />
              </span>
              <div className="timeline-track-wrap">
                <input
                  id="paper-prejump-score"
                  type="range"
                  className="timeline"
                  min={80}
                  max={600}
                  step={5}
                  value={preJumpUnlockMinJumpScore}
                  onChange={(e) => setPreJumpUnlockMinJumpScore(Number(e.target.value))}
                />
              </div>
            </label>
            <label htmlFor="paper-prejump-shift">
              <span className="slider-caption">
                Unlock shift px: {preJumpUnlockShiftPx}
                <ParamHint text="Минимальный raw-сдвиг камеры для снятия lock. Поднимайте, если lock снимается слишком рано." />
              </span>
              <div className="timeline-track-wrap">
                <input
                  id="paper-prejump-shift"
                  type="range"
                  className="timeline"
                  min={10}
                  max={120}
                  step={1}
                  value={preJumpUnlockShiftPx}
                  onChange={(e) => setPreJumpUnlockShiftPx(Number(e.target.value))}
                />
              </div>
            </label>
            <label htmlFor="paper-prejump-z">
              <span className="slider-caption">
                Unlock zoom %: {(preJumpUnlockZoomMilli / 10).toFixed(1)}%
                <ParamHint text="Минимальное изменение raw zoom для снятия lock. Используйте вместе с jumpScore и shift." />
              </span>
              <div className="timeline-track-wrap">
                <input
                  id="paper-prejump-z"
                  type="range"
                  className="timeline"
                  min={5}
                  max={1000}
                  step={1}
                  value={preJumpUnlockZoomMilli}
                  onChange={(e) => setPreJumpUnlockZoomMilli(Number(e.target.value))}
                />
              </div>
            </label>
            <label htmlFor="paper-prejump-frames">
              <span className="slider-caption">
                Unlock устойчивость (кадры): {preJumpUnlockFrames}
                <ParamHint text="Сколько кадров подряд должны выполняться условия unlock. Больше — стабильнее, но позже unlock." />
              </span>
              <div className="timeline-track-wrap">
                <input
                  id="paper-prejump-frames"
                  type="range"
                  className="timeline"
                  min={1}
                  max={12}
                  step={1}
                  value={preJumpUnlockFrames}
                  onChange={(e) => setPreJumpUnlockFrames(Number(e.target.value))}
                />
              </div>
            </label>
          </div>
        </div>
      </details>

      <details className="filter-spoiler">
        <summary>Карта: трассы и кольцо</summary>
        <div className="filter-spoiler-body">
          <div className="paper-calib-box calibration-grid-2">
            <label htmlFor="paper-path-jump">
              <span className="slider-caption">
                Разрыв трассы: {pathJumpThresholdPercent}%
                <ParamHint text="Порог для разрыва линии трека команды на визуализации. Полезно, чтобы не рисовать ложные «телепорты»." />
              </span>
              <div className="timeline-track-wrap">
                <input
                  id="paper-path-jump"
                  type="range"
                  className="timeline"
                  min={5}
                  max={45}
                  step={1}
                  value={pathJumpThresholdPercent}
                  onChange={(e) => setPathJumpThresholdPercent(Number(e.target.value))}
                />
              </div>
            </label>
            <label htmlFor="paper-track-stroke">
              <span className="slider-caption">
                Толщина линии: {trackStrokePx}px
                <ParamHint text="Чисто визуальный параметр толщины траектории команд на карте." />
              </span>
              <div className="timeline-track-wrap">
                <input
                  id="paper-track-stroke"
                  type="range"
                  className="timeline"
                  min={1}
                  max={12}
                  step={1}
                  value={trackStrokePx}
                  onChange={(e) => setTrackStrokePx(Number(e.target.value))}
                />
              </div>
            </label>
            <label htmlFor="paper-ring-shade">
              <span className="slider-caption">
                Красная зона: {ringShadePercent}%
                <ParamHint text="Прозрачность/интенсивность затемнения зоны вне безопасного круга. На расчет камеры не влияет." />
              </span>
              <div className="timeline-track-wrap">
                <input
                  id="paper-ring-shade"
                  type="range"
                  className="timeline"
                  min={30}
                  max={250}
                  step={5}
                  value={ringShadePercent}
                  onChange={(e) => setRingShadePercent(Number(e.target.value))}
                />
              </div>
            </label>
            <label className="paper-calib-row calibration-grid-2-full" style={{ alignSelf: "end" }}>
              <input
                type="checkbox"
                checked={showCameraDebugHud}
                onChange={(e) => setShowCameraDebugHud(e.target.checked)}
              />
              <span style={{ fontSize: 13 }}>
                Отладка камеры
                <ParamHint text="Показывает служебные подписи камеры (zoom/size/anti-latch flags). Включайте для диагностики, выключайте для чистого вида." />
              </span>
            </label>
            <label className="paper-calib-row calibration-grid-2-full" style={{ alignSelf: "end" }}>
              <input
                type="checkbox"
                checked={applyCameraShiftToTracks}
                onChange={(e) => setApplyCameraShiftToTracks(e.target.checked)}
              />
              <span style={{ fontSize: 13 }}>
                Смещение камеры для треков
                <ParamHint text="Сдвигает/масштабирует точки команд по движению и zoom камеры. Полезно для выравнивания траекторий после резких camera-shift." />
              </span>
            </label>
            <label htmlFor="paper-camera-shift-zoom-strength">
              <span className="slider-caption">
                Сила zoom-компенсации треков: {cameraShiftZoomStrengthPercent}%
                <ParamHint text="Site-side усиление inverse zoom для траекторий команд. Подбирается без переанализа команд: больше значение сильнее стягивает треки к центру после приближения камеры." />
              </span>
              <div className="timeline-track-wrap">
                <input
                  id="paper-camera-shift-zoom-strength"
                  type="range"
                  className="timeline"
                  min={70}
                  max={1000}
                  step={5}
                  value={cameraShiftZoomStrengthPercent}
                  onChange={(e) => setCameraShiftZoomStrengthPercent(Number(e.target.value))}
                />
              </div>
            </label>
          </div>

          <details className="filter-spoiler calib-spoiler-nested">
            <summary>Остановки · сглаживание</summary>
            <div className="filter-spoiler-body paper-calib-box">
              <label className="paper-calib-row">
                <input
                  type="checkbox"
                  checked={enableStopGrouping}
                  onChange={(e) => setEnableStopGrouping(e.target.checked)}
                />
                <span>
                  Stop-маркеры
                  <ParamHint text="Группирует плотные участки траектории в маркеры остановок. Удобно для анализа статичных фаз." />
                </span>
              </label>
              <label htmlFor="paper-stop-r">
                Радиус stop (px): {stopRadiusPx}
                <ParamHint text="Максимальный размер области, в которой точки считаются одной остановкой." />
              </label>
              <div className="timeline-track-wrap">
                <input
                  id="paper-stop-r"
                  type="range"
                  className="timeline"
                  min={4}
                  max={200}
                  step={1}
                  value={stopRadiusPx}
                  onChange={(e) => setStopRadiusPx(Number(e.target.value))}
                />
              </div>
              <label htmlFor="paper-stop-dur">
                Мин. длительность stop (c): {stopMinDurationSec}
                <ParamHint text="Минимальное время, чтобы кластер считался реальной остановкой, а не шумом." />
              </label>
              <div className="timeline-track-wrap">
                <input
                  id="paper-stop-dur"
                  type="range"
                  className="timeline"
                  min={5}
                  max={400}
                  step={1}
                  value={stopMinDurationSec}
                  onChange={(e) => setStopMinDurationSec(Number(e.target.value))}
                />
              </div>
              <label htmlFor="paper-smooth-w">
                Окно сглаживания: {smoothWindow}
                <ParamHint text="Скользящее окно сглаживания командных треков. Больше — плавнее, но выше риск потери мелких маневров." />
              </label>
              <div className="timeline-track-wrap">
                <input
                  id="paper-smooth-w"
                  type="range"
                  className="timeline"
                  min={1}
                  max={80}
                  step={1}
                  value={smoothWindow}
                  onChange={(e) => setSmoothWindow(Number(e.target.value))}
                />
              </div>
              <label style={{ marginTop: 8, fontSize: 13 }}>Payload</label>
              <textarea readOnly className="paper-calib-output" value={JSON.stringify(calibrationPayload, null, 2)} />
            </div>
          </details>
        </div>
      </details>
    </>
  );

  const tournamentCatalogBody = (
          <div className="tournament-picker" style={{ marginTop: 0 }}>
            <span className="picker-label">Sidebar - Турнир и карта</span>
            <div className="paper-field">
              <label htmlFor="paper-tournament">Турнир</label>
              <select
                id="paper-tournament"
                value={selectedTournamentId}
                onChange={(e) => setSelectedTournamentId(e.target.value)}
              >
                {tournaments.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name} ({item.season})
                  </option>
                ))}
              </select>
            </div>
            <div className="paper-field">
              <label htmlFor="paper-match">Матч</label>
              <select id="paper-match" value={selectedMatchId} onChange={(e) => setSelectedMatchId(e.target.value)}>
                {matches.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.title}
                  </option>
                ))}
              </select>
            </div>
            <div className="paper-field">
              <label htmlFor="paper-map">Карта</label>
              <select id="paper-map" value={selectedMapId} onChange={(e) => setSelectedMapId(e.target.value)}>
                {maps.map((item) => (
                  <option key={item.id} value={item.id}>
                    {formatMapLabel(item)}
                  </option>
                ))}
              </select>
            </div>
            {showFragmentRoundPicker ? (
            <div className="paper-field">
              <label>Фрагмент</label>
              <div className="paper-round-row">
                <button
                  type="button"
                  className={`paper-round-btn${selectedRound === "round1" ? " is-active" : ""}`}
                  onClick={() => {
                    setSelectedRound("round1");
                    setTimeCursor(roundWindows.round1.startSec);
                    setIsPlaying(false);
                  }}
                >
                  Круг 1
                </button>
                <button
                  type="button"
                  className={`paper-round-btn${selectedRound === "all" ? " is-active" : ""}`}
                  onClick={() => {
                    const allStart = selectedMap?.workFragmentStartSec ?? roundWindows.round1.startSec;
                    setSelectedRound("all");
                    setTimeCursor(allStart);
                    setIsPlaying(false);
                  }}
                >
                  All
                </button>
                <button
                  type="button"
                  className={`paper-round-btn${selectedRound === "round2" ? " is-active" : ""}`}
                  disabled={!ROUND_2_ENABLED}
                  onClick={() => {
                    if (!ROUND_2_ENABLED) return;
                    setSelectedRound("round2");
                    setTimeCursor(roundWindows.round2.startSec);
                    setIsPlaying(false);
                  }}
                >
                  Круг 2
                </button>
              </div>
            </div>
            ) : null}
          </div>
  );

  return (
    <Fragment>
      <div
        ref={shellRef}
        className={`shell${iconRailMode ? " shell--rail-icon-mode" : ""}${cameraStudioStageOpen ? " shell--camera-studio-open" : ""}`}
      >
      {iconRailMode ? (
        <div
          className="viewer-rail-stack viewer-rail-stack--left viewer-rail-stack--icon-mode"
          aria-label="Навигация и каталог"
        >
          <div className="rail-icon-toolbar" role="toolbar" aria-label="Быстрые действия">
            <button
              type="button"
              className={`rail-icon-btn${railDrawer === "menu" ? " is-active" : ""}`}
              aria-pressed={railDrawer === "menu"}
              aria-expanded={railDrawer === "menu"}
              aria-controls="rail-flyout-panel"
              title="Меню"
              onClick={() => setRailDrawer((d) => (d === "menu" ? null : "menu"))}
            >
              <RailIconMenu />
              <span className="sr-only">Меню и ссылки</span>
            </button>
            <button
              type="button"
              className={`rail-icon-btn${railDrawer === "catalog" ? " is-active" : ""}`}
              aria-pressed={railDrawer === "catalog"}
              aria-expanded={railDrawer === "catalog"}
              aria-controls="rail-flyout-panel"
              title="Турнир и карта"
              onClick={() => setRailDrawer((d) => (d === "catalog" ? null : "catalog"))}
            >
              <RailIconCatalog />
              <span className="sr-only">Турнир, матч и карта</span>
            </button>
            {settingsInLeftRail && showCalibrationPanel ? (
              <button
                type="button"
                className={`rail-icon-btn${railDrawer === "calibrate" ? " is-active" : ""}`}
                aria-pressed={railDrawer === "calibrate"}
                aria-expanded={railDrawer === "calibrate"}
                aria-controls="rail-flyout-panel"
                title="Калибровка"
                onClick={() => setRailDrawer((d) => (d === "calibrate" ? null : "calibrate"))}
              >
                <RailIconSliders />
                <span className="sr-only">Калибровка отображения</span>
              </button>
            ) : null}
          </div>
          {railDrawer ? (
            <>
              <button
                type="button"
                className="rail-flyout-backdrop"
                aria-label="Закрыть панель"
                onClick={() => setRailDrawer(null)}
              />
              <div
                id="rail-flyout-panel"
                className={`rail-flyout-panel${railDrawer === "calibrate" ? " rail-flyout-panel--calibration" : ""}`}
                role="dialog"
                aria-modal="true"
                aria-labelledby="rail-flyout-title"
              >
                <div className="rail-flyout-header">
                  <span id="rail-flyout-title">
                    {railDrawer === "menu" ? "Меню" : railDrawer === "catalog" ? "Турнир и карта" : "Калибровка"}
                  </span>
                  <button
                    type="button"
                    className="rail-flyout-close"
                    aria-label="Закрыть"
                    onClick={() => setRailDrawer(null)}
                  >
                    ×
                  </button>
                </div>
                <div
                  className={`rail-flyout-body${railDrawer === "calibrate" ? " rail-flyout-body--calibration-scroll" : ""}`}
                >
                  {railDrawer === "menu" ? (
                    <>
                      <nav className="rail-flyout-nav" aria-label="Разделы сайта">
                        <RailAccountThemeRow
                          showThemeButton={themeControlsReady}
                          onOpenTheme={() => {
                            setThemeAppearanceModalOpen(true);
                            setRailDrawer(null);
                          }}
                        >
                          <Link href={railAccountHomeHref} className="rail-action-btn">
                            {railAccountHomeLabel}
                          </Link>
                        </RailAccountThemeRow>
                      </nav>
                    </>
                  ) : null}
                  {railDrawer === "catalog" ? tournamentCatalogBody : null}
                  {railDrawer === "calibrate" ? calibrationAside : null}
                </div>
              </div>
            </>
          ) : null}
        </div>
      ) : (
        <div className="viewer-rail-stack viewer-rail-stack--left" aria-label="Профиль, калибровка и каталог">
          <aside className="rail rail--account">
            <RailAccountThemeRow
              showThemeButton={themeControlsReady}
              onOpenTheme={() => setThemeAppearanceModalOpen(true)}
            >
              <Link href={railAccountHomeHref} className="rail-user-card">
                {railAccountHomeLabel}
              </Link>
            </RailAccountThemeRow>
            {railAccountExtras ?? null}
          </aside>

          <aside className="rail rail--tools rail--tools-compact" aria-label="Турнир и карта">
            {tournamentCatalogBody}
          </aside>

          {settingsInLeftRail && showCalibrationPanel ? (
            <aside className="rail rail--tools" aria-label="Калибровка отображения">
              {calibrationAside}
            </aside>
          ) : null}
        </div>
      )}

      <div className="main">
        {cameraStudioStageOpen ? (
          <>
            <div className="camera-studio-toolbar" role="toolbar" aria-label="Режим студии">
              <button
                type="button"
                className={`camera-studio-tool-btn${studioFlyout === "tools" ? " is-active" : ""}`}
                aria-pressed={studioFlyout === "tools"}
                onClick={() => setStudioFlyout((f) => (f === "tools" ? null : "tools"))}
              >
                Инструменты
              </button>
              <button
                type="button"
                className={`camera-studio-tool-btn${studioFlyout === "teams" ? " is-active" : ""}`}
                aria-pressed={studioFlyout === "teams"}
                onClick={() => setStudioFlyout((f) => (f === "teams" ? null : "teams"))}
              >
                Команды
              </button>
              <button
                type="button"
                className={`camera-studio-tool-btn${studioFlyout === "overlays" ? " is-active" : ""}`}
                aria-pressed={studioFlyout === "overlays"}
                onClick={() => setStudioFlyout((f) => (f === "overlays" ? null : "overlays"))}
              >
                Оверлеи
              </button>
            </div>
            {studioFlyout ? (
              <>
                <button
                  type="button"
                  className="camera-studio-flyout-backdrop"
                  aria-label="Закрыть панель"
                  onClick={() => setStudioFlyout(null)}
                />
                <div
                  className="camera-studio-flyout-panel"
                  role="dialog"
                  aria-modal="true"
                  aria-label={
                    studioFlyout === "tools" ? "Инструменты" : studioFlyout === "teams" ? "Команды" : "Оверлеи"
                  }
                >
                  <div className="camera-studio-flyout-header">
                    <span>
                      {studioFlyout === "tools"
                        ? "Инструменты"
                        : studioFlyout === "teams"
                          ? "Команды"
                          : "Оверлеи"}
                    </span>
                    <button
                      type="button"
                      className="rail-flyout-close"
                      aria-label="Закрыть"
                      onClick={() => setStudioFlyout(null)}
                    >
                      ×
                    </button>
                  </div>
                  <div className="camera-studio-flyout-body rail-flyout-body--calibration-scroll">
                    {studioFlyout === "tools" ? (
                      <>
                        <nav className="rail-flyout-nav" aria-label="Разделы сайта">
                          <RailAccountThemeRow
                            showThemeButton={themeControlsReady}
                            onOpenTheme={() => {
                              setThemeAppearanceModalOpen(true);
                              setStudioFlyout(null);
                            }}
                          >
                            <Link href={railAccountHomeHref} className="rail-action-btn">
                              {railAccountHomeLabel}
                            </Link>
                          </RailAccountThemeRow>
                        </nav>
                        {showCalibrationPanel ? <div className="camera-studio-tools-calib">{calibrationAside}</div> : null}
                      </>
                    ) : null}
                    {studioFlyout === "teams" ? (
                      <div className="camera-studio-teams-grid">
                        <TeamColumn
                          teams={earSlotLeftTeams}
                          eliminationByTeamId={eliminationByTeamId}
                          eliminationAtSecByTeamId={eliminationAtSecByTeamId}
                          timeCursor={timeCursor}
                          selectedTeamIds={selectedTeamIds}
                          toggleTeam={toggleTeam}
                          ariaLabel="Команды TEAM_1–TEAM_10"
                          className="broadcast-teams-col broadcast-teams-col--left"
                        />
                        <TeamColumn
                          teams={earSlotRightTeams}
                          eliminationByTeamId={eliminationByTeamId}
                          eliminationAtSecByTeamId={eliminationAtSecByTeamId}
                          timeCursor={timeCursor}
                          selectedTeamIds={selectedTeamIds}
                          toggleTeam={toggleTeam}
                          ariaLabel="Команды TEAM_11–TEAM_20"
                          className="broadcast-teams-col broadcast-teams-col--right"
                        />
                      </div>
                    ) : null}
                    {studioFlyout === "overlays" ? (
                      <div className="camera-studio-overlay-toggles">
                        <label className="camera-studio-overlay-row">
                          <input
                            type="checkbox"
                            checked={studioOverlays.graph}
                            onChange={(e) => setStudioOverlays((s) => ({ ...s, graph: e.target.checked }))}
                          />
                          График камеры
                        </label>
                        <label className="camera-studio-overlay-row">
                          <input
                            type="checkbox"
                            checked={studioOverlays.rings}
                            onChange={(e) => setStudioOverlays((s) => ({ ...s, rings: e.target.checked }))}
                          />
                          Кольца
                        </label>
                        <label className="camera-studio-overlay-row">
                          <input
                            type="checkbox"
                            checked={studioOverlays.hud}
                            onChange={(e) => setStudioOverlays((s) => ({ ...s, hud: e.target.checked }))}
                          />
                          Таймер и фаза кольца
                        </label>
                        <label className="camera-studio-overlay-row">
                          <input
                            type="checkbox"
                            checked={studioOverlays.teamTracks}
                            onChange={(e) => setStudioOverlays((s) => ({ ...s, teamTracks: e.target.checked }))}
                          />
                          Треки команд
                        </label>
                        <label className="camera-studio-overlay-row">
                          <input
                            type="checkbox"
                            checked={showCameraDebugHud}
                            onChange={(e) => setShowCameraDebugHud(e.target.checked)}
                          />
                          ROI камеры и отладка
                        </label>
                        <p className="camera-studio-overlay-hint">
                          Зелёная рамка — окно рендера, оранжевое кольцо — центр кольца зоны, фиолетовое — зум камеры. Тот же флажок, что «Отладка камеры» в
                          калибровке.
                        </p>
                      </div>
                    ) : null}
                  </div>
                </div>
              </>
            ) : null}
          </>
        ) : null}
        <section
          ref={mapStagePanelRef}
          className={`map-stage panel${showCameraChartsBlock ? " map-stage--with-camera-charts" : ""}`}
        >
          <div className={`map-stage-teams-row${showTeams ? "" : " map-stage-teams-row--map-only"}`}>
            {showTeams ? (
              <TeamColumn
                teams={earSlotLeftTeams}
                eliminationByTeamId={eliminationByTeamId}
                eliminationAtSecByTeamId={eliminationAtSecByTeamId}
                timeCursor={timeCursor}
                selectedTeamIds={selectedTeamIds}
                toggleTeam={toggleTeam}
                ariaLabel="Команды TEAM_1–TEAM_10 слева от карты"
                className="broadcast-teams-col broadcast-teams-col--left"
              />
            ) : null}
            <div className="map-stage-center">
              <div className="map-wrap has-game-map" ref={playerRef}>
                <div className="map-fs-main">
                  <MapFullscreenTeamEarColumn
                    teams={earSlotLeftTeams}
                    side="left"
                    eliminationByTeamId={eliminationByTeamId}
                    eliminationAtSecByTeamId={eliminationAtSecByTeamId}
                    timeCursor={timeCursor}
                    selectedTeamIds={selectedTeamIds}
                    toggleTeam={toggleTeam}
                    mapWrapFullscreen={mapWrapFullscreen}
                  />
                  <div className="map-fs-map-column">
                    <div className="map-broadcast-canvas-host">
                      <div className="map">
                        <MapPlayer
                          tracks={mapPlayerTracks}
                          rings={mapPlayerRings}
                          cameraTracks={cameraTracks}
                          currentTimeSec={timeCursor}
                          teams={teams}
                          backgroundSrc={selectedMap?.backgroundUrl ? `${API_URL}${selectedMap.backgroundUrl}` : undefined}
                          observerRoi={selectedMap?.observerRoi}
                          renderSettings={{
                            enableStopGrouping,
                            stopRadiusPx,
                            stopMinDurationSec,
                            smoothWindow,
                            pathJumpThresholdRatio: pathJumpThresholdPercent / 100,
                            trackStrokePx,
                            ringShadeAlphaScale: ringShadePercent / 100,
                            showCameraDebugHud,
                            applyCameraShiftToTracks,
                            cameraShiftZoomStrength: cameraShiftZoomStrengthPercent / 100,
                          }}
                          ringCameraNoiseByRing={ringCameraNoiseByRing}
                          ringNoiseSliderMaxByRing={ringNoiseSliderMaxByRing}
                          cameraSmoothingTuning={cameraSmoothingTuning}
                        />
                      </div>
                    </div>
                    {mapPlayerShowHud ? (
                    <div className="map-overlay-hud" aria-live="polite">
                      <div className="map-overlay-inner">
                        <div className="paper-timer">{estimatedGameTimeLabel}</div>
                        <div className={`ring-pill${ringPillClosing ? " ring-closing" : ""}`}>
                          Round <b id="ring-round">{ringRoundLabel}</b> · <span id="ring-phase">{ringPhaseShortLabel}</span>
                        </div>
                      </div>
                    </div>
                    ) : null}
                  </div>
                  <MapFullscreenTeamEarColumn
                    teams={earSlotRightTeams}
                    side="right"
                    eliminationByTeamId={eliminationByTeamId}
                    eliminationAtSecByTeamId={eliminationAtSecByTeamId}
                    timeCursor={timeCursor}
                    selectedTeamIds={selectedTeamIds}
                    toggleTeam={toggleTeam}
                    mapWrapFullscreen={mapWrapFullscreen}
                  />
                </div>
                <BroadcastMapTransportBar
                  className="transport-bar--map-fs"
                  ariaHidden={cameraStudioLayout ? true : !mapWrapFullscreen}
                  isPlaying={isPlaying}
                  setIsPlaying={setIsPlaying}
                  timeCursor={timeCursor}
                  setTimeCursor={setTimeCursor}
                  roundRange={roundRange}
                  ringTimelineMarkers={ringTimelineMarkers}
                  timelineRingStackMax={timelineRingStackMax}
                  playbackSpeed={playbackSpeed}
                  setPlaybackSpeed={setPlaybackSpeed}
                  playerRef={playerRef}
                  style={{ boxShadow: "0 8px 24px rgba(0,0,0,.08)" }}
                />
                {cameraStudioLayout ? (
                  <div className="map-overlay-settings-anchor">
                    <button
                      ref={mapOverlaySettingsBtnRef}
                      type="button"
                      className="map-overlay-settings-btn"
                      aria-expanded={mapOverlaySettingsOpen}
                      aria-haspopup="dialog"
                      aria-controls="map-overlay-settings-popover"
                      onClick={() => setMapOverlaySettingsOpen((o) => !o)}
                    >
                      Оверлей
                    </button>
                    {mapOverlaySettingsOpen ? (
                      <div
                        id="map-overlay-settings-popover"
                        ref={mapOverlaySettingsPanelRef}
                        className="map-overlay-settings-panel"
                        role="dialog"
                        aria-label="Настройки оверлея карты"
                        onWheel={(e) => e.stopPropagation()}
                      >
                        <div className="camera-studio-overlay-toggles">
                          <label className="camera-studio-overlay-row">
                            <input
                              type="checkbox"
                              checked={studioOverlays.graph}
                              onChange={(e) => setStudioOverlays((s) => ({ ...s, graph: e.target.checked }))}
                            />
                            График камеры
                          </label>
                          <label className="camera-studio-overlay-row">
                            <input
                              type="checkbox"
                              checked={studioOverlays.rings}
                              onChange={(e) => setStudioOverlays((s) => ({ ...s, rings: e.target.checked }))}
                            />
                            Кольца
                          </label>
                          <label className="camera-studio-overlay-row">
                            <input
                              type="checkbox"
                              checked={studioOverlays.hud}
                              onChange={(e) => setStudioOverlays((s) => ({ ...s, hud: e.target.checked }))}
                            />
                            Таймер и фаза кольца
                          </label>
                          <label className="camera-studio-overlay-row">
                            <input
                              type="checkbox"
                              checked={studioOverlays.teamTracks}
                              onChange={(e) => setStudioOverlays((s) => ({ ...s, teamTracks: e.target.checked }))}
                            />
                            Треки команд
                          </label>
                          <label className="camera-studio-overlay-row">
                            <input
                              type="checkbox"
                              checked={showCameraDebugHud}
                              onChange={(e) => setShowCameraDebugHud(e.target.checked)}
                            />
                            ROI камеры и отладка
                          </label>
                        </div>
                        <p className="camera-studio-overlay-hint">
                          Зелёная рамка — окно рендера; оранжевое и фиолетовое кольца — зона и зум. Дублирует панель «Оверлеи» в
                          полноэкранной студии.
                        </p>
                      </div>
                    ) : null}
                  </div>
                ) : null}
              </div>
            </div>
            {showTeams ? (
              <TeamColumn
                teams={earSlotRightTeams}
                eliminationByTeamId={eliminationByTeamId}
                eliminationAtSecByTeamId={eliminationAtSecByTeamId}
                timeCursor={timeCursor}
                selectedTeamIds={selectedTeamIds}
                toggleTeam={toggleTeam}
                ariaLabel="Команды TEAM_11–TEAM_20 справа от карты"
                className="broadcast-teams-col broadcast-teams-col--right"
              />
            ) : null}
            {!showTeams && rightMediaPane ? <div className="broadcast-media-pane">{rightMediaPane}</div> : null}
          </div>

          <BroadcastMapTransportBar
            ariaHidden={cameraStudioLayout ? false : mapWrapFullscreen}
            isPlaying={isPlaying}
            setIsPlaying={setIsPlaying}
            timeCursor={timeCursor}
            setTimeCursor={setTimeCursor}
            roundRange={roundRange}
            ringTimelineMarkers={ringTimelineMarkers}
            timelineRingStackMax={timelineRingStackMax}
            playbackSpeed={playbackSpeed}
            setPlaybackSpeed={setPlaybackSpeed}
            playerRef={playerRef}
            onFullscreenClick={cameraStudioLayout ? () => setCameraStudioOpen((o) => !o) : undefined}
            fullscreenActive={cameraStudioLayout ? cameraStudioOpen : undefined}
            style={{ boxShadow: "0 8px 24px rgba(0,0,0,.08)" }}
          />

          {showCameraChartsBlock ? (
            <div
              className={
                cameraStudioStageOpen && studioOverlays.graph ? "camera-studio-graph-dock" : "camera-charts-stage"
              }
            >
              <CameraTimelineCharts
                cameraTracks={cameraTracks}
                ringCameraNoiseByRing={ringCameraNoiseByRing}
                ringNoiseSliderMaxByRing={ringNoiseSliderMaxByRing}
                cameraSmoothingTuning={cameraSmoothingTuning}
                currentTimeSec={timeCursor}
                onSeek={setTimeCursor}
                onApplyTuningPreset={applyCameraGraphTuningPreset}
              />
            </div>
          ) : null}
        </section>

        {!settingsInLeftRail && showCalibrationPanel ? (
          <aside className="rail rail--tools rail--tools-near-graphs" aria-label="Калибровка отображения">
            {calibrationAside}
          </aside>
        ) : null}
      </div>
    </div>
    {themeControlsReady ? (
      <ThemeAppearanceModal
        open={themeAppearanceModalOpen}
        onClose={() => setThemeAppearanceModalOpen(false)}
        themeBrightness={themeBrightness}
        themePalette={themePalette}
        onThemeBrightnessChange={onThemeBrightnessChange}
        onThemePaletteChange={onThemePaletteChange}
      />
    ) : null}
    </Fragment>
  );
}
