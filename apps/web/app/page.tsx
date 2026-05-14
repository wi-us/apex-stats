"use client";

import { DM_Sans } from "next/font/google";
import { useCallback, useEffect, useState } from "react";
import { BroadcastViewer } from "../components/broadcast/BroadcastViewer";
import { useMatchViewerState } from "../lib/useMatchViewerState";

const dmSans = DM_Sans({
  subsets: ["latin", "latin-ext"],
  weight: ["400", "600", "700"],
  display: "swap",
});

const STORAGE_BRIGHTNESS = "apex-broadcast-theme-brightness";
const STORAGE_PALETTE = "apex-broadcast-theme-palette";

export default function HomePage() {
  const vm = useMatchViewerState();
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

  return (
    <div
      className={`paper-broadcast paper-broadcast--theme-${themeBrightness} paper-broadcast--palette-${themePalette} ${dmSans.className}`}
    >
      <BroadcastViewer
        vm={vm}
        showCameraTimelineCharts={false}
        showCalibrationPanel={false}
        showFragmentRoundPicker={false}
        themeBrightness={themeBrightness}
        themePalette={themePalette}
        onThemeBrightnessChange={onThemeBrightnessChange}
        onThemePaletteChange={onThemePaletteChange}
      />
    </div>
  );
}
