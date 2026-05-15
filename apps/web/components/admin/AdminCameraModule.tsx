"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { API_URL } from "../../lib/api";
import { AdminAppNav } from "./AdminAppNav";
import { BroadcastViewer } from "../broadcast/BroadcastViewer";
import { useMatchViewerState } from "../../lib/useMatchViewerState";
import styles from "../../app/admin/zoom/zoom.module.css";

const STORAGE_BRIGHTNESS = "apex-broadcast-theme-brightness";
const STORAGE_PALETTE = "apex-broadcast-theme-palette";

export function AdminCameraModule() {
  const vm = useMatchViewerState();
  const wrapperRef = useRef<HTMLDivElement | null>(null);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const applyingFromCursorRef = useRef(false);
  const [videoCandidateIdx, setVideoCandidateIdx] = useState(0);
  const [themeBrightness, setThemeBrightness] = useState<"dark" | "light">("dark");
  const [themePalette, setThemePalette] = useState<"virtus" | "standard">("virtus");

  useEffect(() => {
    try {
      const b = sessionStorage.getItem(STORAGE_BRIGHTNESS);
      const p = sessionStorage.getItem(STORAGE_PALETTE);
      if (b === "light" || b === "dark") setThemeBrightness(b);
      if (p === "standard" || p === "virtus") setThemePalette(p);
    } catch {
      /* ignore */
    }
  }, []);

  const onThemeBrightnessChange = useCallback((v: "dark" | "light") => {
    setThemeBrightness(v);
    try {
      sessionStorage.setItem(STORAGE_BRIGHTNESS, v);
    } catch {
      /* ignore */
    }
  }, []);

  const onThemePaletteChange = useCallback((v: "virtus" | "standard") => {
    setThemePalette(v);
    try {
      sessionStorage.setItem(STORAGE_PALETTE, v);
    } catch {
      /* ignore */
    }
  }, []);

  const mapVideoByApi = vm.selectedMap?.id
    ? `${API_URL}/catalog/maps/${encodeURIComponent(vm.selectedMap.id)}/video`
    : "";

  const videoCandidates = useMemo(() => {
    const raw = vm.selectedMap?.videoUrl;
    if (!raw && !mapVideoByApi) return [] as string[];
    const sanitized = raw ? String(raw).trim().replace(/\\/g, "/") : "";
    const filename = sanitized.split("/").filter(Boolean).pop() ?? "";
    const variants = new Set<string>();
    if (mapVideoByApi) variants.add(mapVideoByApi);
    if (/^https?:\/\//i.test(sanitized)) {
      variants.add(sanitized);
    } else if (sanitized.startsWith("/")) {
      variants.add(sanitized);
      variants.add(`${API_URL}${sanitized}`);
    } else if (sanitized) {
      variants.add(`${API_URL}/${sanitized.replace(/^\/+/, "")}`);
      variants.add(`${API_URL}${sanitized.startsWith("/") ? sanitized : `/${sanitized}`}`);
    }
    if (filename && !/^https?:\/\//i.test(sanitized)) {
      variants.add(`${API_URL}/records/${filename}`);
      variants.add(`${API_URL}/ffmpeg_downloader/records/${filename}`);
      variants.add(`/ffmpeg_downloader/records/${filename}`);
    }
    const list = Array.from(variants);
    // #region agent log
    fetch("http://127.0.0.1:7664/ingest/0aa35fc0-93f5-4a7d-ae43-8a87e2b19087", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Debug-Session-Id": "119440" },
      body: JSON.stringify({
        sessionId: "119440",
        runId: "post-fix",
        hypothesisId: "H1-H2",
        location: "AdminCameraModule.tsx:videoCandidates",
        message: "video_candidates_built",
        data: {
          API_URL,
          mapVideoByApi,
          rawCatalog: vm.selectedMap?.videoUrl ?? null,
          sanitizedLen: sanitized.length,
          isHttpAbs: /^https?:\/\//i.test(sanitized),
          candidates: list,
        },
        timestamp: Date.now(),
      }),
    }).catch(() => {});
    // #endregion
    return list;
  }, [mapVideoByApi, vm.selectedMap?.videoUrl]);
  const videoSrc = videoCandidates[videoCandidateIdx] ?? "";

  const videoBaseSec = vm.selectedMap?.workFragmentStartSec ?? 0;
  const targetVideoSec = Math.max(0, vm.timeCursor - videoBaseSec);

  useEffect(() => {
    setVideoCandidateIdx(0);
  }, [vm.selectedMap?.id]);

  useEffect(() => {
    const video = videoRef.current;
    if (!video || !videoSrc) return;
    const drift = Math.abs(video.currentTime - targetVideoSec);
    if (drift > 0.12) {
      applyingFromCursorRef.current = true;
      video.currentTime = targetVideoSec;
      requestAnimationFrame(() => {
        applyingFromCursorRef.current = false;
      });
    }
  }, [targetVideoSec, videoSrc]);

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    if (!videoSrc) {
      video.pause();
      return;
    }
    if (vm.isPlaying) {
      void video.play().catch(() => {});
    } else {
      video.pause();
    }
  }, [videoSrc, vm.isPlaying]);

  return (
    <div
      ref={wrapperRef}
      className={`${styles.zoomRoot} paper-broadcast paper-broadcast--theme-${themeBrightness} paper-broadcast--palette-${themePalette}`}
    >
      <BroadcastViewer
        vm={vm}
        showTeams={false}
        cameraStudioLayout
        settingsInLeftRail
        showFragmentRoundPicker={false}
        showCameraTimelineCharts
        themeBrightness={themeBrightness}
        themePalette={themePalette}
        onThemeBrightnessChange={onThemeBrightnessChange}
        onThemePaletteChange={onThemePaletteChange}
        railAccountHomeHref="/"
        railAccountHomeLabel="На сайт"
        railAccountExtras={<AdminAppNav active="camera" />}
        rightMediaPane={
          <div className={styles.videoPanel}>
            <div className={styles.videoCrop}>
              <video
                ref={videoRef}
                className={styles.video}
                src={videoSrc || undefined}
                preload="metadata"
                playsInline
                onPlay={() => vm.setIsPlaying(true)}
                onPause={() => vm.setIsPlaying(false)}
                onEnded={() => vm.setIsPlaying(false)}
                onSeeked={(e) => {
                  const sec = Number((e.currentTarget as HTMLVideoElement).currentTime ?? 0);
                  vm.setTimeCursor(videoBaseSec + sec);
                }}
                onTimeUpdate={(e) => {
                  if (vm.isPlaying || applyingFromCursorRef.current) return;
                  const sec = Number((e.currentTarget as HTMLVideoElement).currentTime ?? 0);
                  vm.setTimeCursor(videoBaseSec + sec);
                }}
                onError={(ev) => {
                  const el = ev.currentTarget as HTMLVideoElement;
                  // #region agent log
                  fetch("http://127.0.0.1:7664/ingest/0aa35fc0-93f5-4a7d-ae43-8a87e2b19087", {
                    method: "POST",
                    headers: { "Content-Type": "application/json", "X-Debug-Session-Id": "119440" },
                    body: JSON.stringify({
                      sessionId: "119440",
                      runId: "post-fix",
                      hypothesisId: "H2-H3",
                      location: "AdminCameraModule.tsx:video.onError",
                      message: "video_src_failed",
                      data: {
                        failedSrc: el.currentSrc || videoSrc,
                        candidateIdx: videoCandidateIdx,
                        totalCandidates: videoCandidates.length,
                        mediaError: el.error?.code ?? null,
                        networkState: el.networkState,
                      },
                      timestamp: Date.now(),
                    }),
                  }).catch(() => {});
                  // #endregion
                  setVideoCandidateIdx((prev) => (prev + 1 < videoCandidates.length ? prev + 1 : prev));
                }}
              />
            </div>
          </div>
        }
      />
    </div>
  );
}
