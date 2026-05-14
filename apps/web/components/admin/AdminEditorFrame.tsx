"use client";

import { useSearchParams } from "next/navigation";
import { useCallback, useEffect, useRef } from "react";
import editorStyles from "../../app/admin/editor-shell.module.css";

const LEGACY_FRAME_SRC = "/admin/legacy-editor.html?embed=1";
export const ADMIN_LEGACY_SET_TAB = "apex-admin-set-tab";

function normalizeEditorTab(raw: string | null): "hsv" | "zones" | "poly" {
  if (raw === "zones" || raw === "poly") return raw;
  return "hsv";
}

export function AdminEditorFrame() {
  const searchParams = useSearchParams();
  const tab = normalizeEditorTab(searchParams.get("tab"));
  const iframeRef = useRef<HTMLIFrameElement | null>(null);
  const legacyReadyRef = useRef(false);
  const tabRef = useRef(tab);
  tabRef.current = tab;

  const postTab = useCallback((t: "hsv" | "zones" | "poly") => {
    const win = iframeRef.current?.contentWindow;
    if (!win) return;
    win.postMessage({ type: ADMIN_LEGACY_SET_TAB, tab: t }, window.location.origin);
  }, []);

  useEffect(() => {
    const el = iframeRef.current;
    if (!el) return;
    const onLoad = () => {
      legacyReadyRef.current = true;
      postTab(tabRef.current);
    };
    el.addEventListener("load", onLoad);
    try {
      if (el.contentDocument?.readyState === "complete") onLoad();
    } catch {
      /* ignore */
    }
    return () => {
      el.removeEventListener("load", onLoad);
      legacyReadyRef.current = false;
    };
  }, [postTab]);

  useEffect(() => {
    if (!legacyReadyRef.current) return;
    postTab(tab);
  }, [tab, postTab]);

  return (
    <div className={`paper-broadcast paper-broadcast--theme-dark paper-broadcast--palette-virtus ${editorStyles.shellRoot}`}>
      <div className={`shell ${editorStyles.shellEmbed}`}>
        <iframe
          ref={iframeRef}
          title="Редактор карт, HSV, зон и полигонов"
          className={editorStyles.legacyFrameFull}
          src={LEGACY_FRAME_SRC}
        />
      </div>
    </div>
  );
}
