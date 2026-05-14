import { AdminAppNav } from "../../../components/admin/AdminAppNav";
import { AdminManagementModule } from "../../../components/admin/AdminManagementModule";
import editorStyles from "../editor-shell.module.css";

export default function AdminManagementPage() {
  return (
    <div
      className={`paper-broadcast paper-broadcast--theme-dark paper-broadcast--palette-virtus ${editorStyles.shellRoot}`}
    >
      <div className="shell">
        <div className="viewer-rail-stack viewer-rail-stack--left">
          <aside className="rail rail--account">
            <div className={`rail-scroll rail-scroll--account ${editorStyles.railScrollAccount}`}>
              <AdminAppNav active="management" />
            </div>
          </aside>
        </div>
        <div className={`main ${editorStyles.editorMain}`}>
          <AdminManagementModule />
        </div>
      </div>
    </div>
  );
}
