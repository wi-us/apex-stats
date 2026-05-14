import { AdminAppNav } from "../../../components/admin/AdminAppNav";
import editorStyles from "../editor-shell.module.css";

export default function AdminDatabasePage() {
  return (
    <div
      className={`paper-broadcast paper-broadcast--theme-dark paper-broadcast--palette-virtus ${editorStyles.shellRoot}`}
    >
      <div className="shell">
        <div className="viewer-rail-stack viewer-rail-stack--left">
          <aside className="rail rail--account">
            <div className={`rail-scroll rail-scroll--account ${editorStyles.railScrollAccount}`}>
              <AdminAppNav active="database" />
            </div>
          </aside>
        </div>
        <div className={`main ${editorStyles.editorMain}`}>
          <iframe
            title="Database workspace"
            src="/workspace"
            className={editorStyles.legacyFrame}
            style={{ background: "rgb(15, 24, 33)" }}
          />
        </div>
      </div>
    </div>
  );
}
