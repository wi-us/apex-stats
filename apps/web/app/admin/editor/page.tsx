import { Suspense } from "react";
import { AdminEditorFrame } from "../../../components/admin/AdminEditorFrame";
import editorStyles from "../editor-shell.module.css";

function EditorFallback() {
  return (
    <div
      className={`paper-broadcast paper-broadcast--theme-dark paper-broadcast--palette-virtus ${editorStyles.shellRoot}`}
    >
      <div className={`shell ${editorStyles.shellEmbed}`}>
        <div
          className={editorStyles.legacyFrameFull}
          style={{
            background: "color-mix(in srgb, var(--surface) 92%, var(--bg))",
            minHeight: "100vh",
          }}
          aria-hidden
        />
      </div>
    </div>
  );
}

export default function AdminEditorPage() {
  return (
    <Suspense fallback={<EditorFallback />}>
      <AdminEditorFrame />
    </Suspense>
  );
}
