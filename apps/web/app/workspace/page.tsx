"use client";

import { DM_Sans } from "next/font/google";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { API_URL } from "../../lib/api";
import styles from "./workspace.module.css";

const dmSans = DM_Sans({
  subsets: ["latin", "latin-ext"],
  weight: ["400", "600", "700"],
  display: "swap",
});

type DbColumn = {
  name: string;
  type: string;
  nullable: boolean;
  primaryKey: boolean;
};

type DbRowsResponse = {
  columns: DbColumn[];
  rows: Array<Record<string, unknown>>;
  total: number;
  limit: number;
  offset: number;
};

type FsItem = {
  name: string;
  path: string;
  type: "dir" | "file";
  size: number;
  modifiedAt: string;
};

type FsListResponse = {
  currentPath: string;
  parentPath: string | null;
  items: FsItem[];
};

type FsReadResponse = {
  path: string;
  content: string;
  size: number;
  modifiedAt: string;
};

async function apiCall<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    cache: "no-store",
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `Request failed with status ${response.status}`);
  }
  return response.json() as Promise<T>;
}

function parseInputValue(raw: string, original: unknown): unknown {
  if (raw === "") return null;
  if (typeof original === "number") {
    const num = Number(raw);
    return Number.isFinite(num) ? num : raw;
  }
  if (typeof original === "boolean") {
    if (raw === "true") return true;
    if (raw === "false") return false;
  }
  return raw;
}

function parseForInsert(raw: string): unknown {
  const trimmed = raw.trim();
  if (trimmed === "") return null;
  if (trimmed === "true") return true;
  if (trimmed === "false") return false;
  const numeric = Number(trimmed);
  if (Number.isFinite(numeric) && /^-?\d+(\.\d+)?$/.test(trimmed)) {
    return numeric;
  }
  return raw;
}

export default function WorkspacePage() {
  const [tab, setTab] = useState<"db" | "files">("db");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string>("");

  const [databases, setDatabases] = useState<string[]>([]);
  const [selectedDb, setSelectedDb] = useState("");
  const [tables, setTables] = useState<string[]>([]);
  const [selectedTable, setSelectedTable] = useState("");
  const [dbRows, setDbRows] = useState<DbRowsResponse | null>(null);
  const [editedRows, setEditedRows] = useState<Record<string, Record<string, string>>>({});
  const [newRowValues, setNewRowValues] = useState<Record<string, string>>({});

  const [fsState, setFsState] = useState<FsListResponse | null>(null);
  const [selectedFilePath, setSelectedFilePath] = useState("");
  const [selectedFileContent, setSelectedFileContent] = useState("");
  const [newDirName, setNewDirName] = useState("");
  const [moveTargetPath, setMoveTargetPath] = useState("");

  const editableColumns = useMemo(
    () =>
      (dbRows?.columns ?? []).filter(
        (col) => col.name !== "__rowid__" && !col.type.toUpperCase().includes("BLOB")
      ),
    [dbRows]
  );

  const refreshDatabases = async () => {
    const list = await apiCall<string[]>("/workspace/databases");
    setDatabases(list);
    if (!selectedDb && list.length > 0) {
      setSelectedDb(list[0]);
    }
  };

  const refreshTables = async (dbPath: string) => {
    if (!dbPath) return;
    const list = await apiCall<string[]>(
      `/workspace/databases/tables?dbPath=${encodeURIComponent(dbPath)}`
    );
    setTables(list);
    if (!list.includes(selectedTable)) {
      setSelectedTable(list[0] ?? "");
    }
  };

  const refreshRows = async (dbPath: string, tableName: string) => {
    if (!dbPath || !tableName) return;
    const data = await apiCall<DbRowsResponse>(
      `/workspace/databases/rows?dbPath=${encodeURIComponent(dbPath)}&table=${encodeURIComponent(tableName)}&limit=100&offset=0`
    );
    setDbRows(data);
    setEditedRows({});
    const seeded: Record<string, string> = {};
    for (const column of data.columns) {
      seeded[column.name] = "";
    }
    setNewRowValues(seeded);
  };

  const refreshFiles = async (dirPath?: string) => {
    const query = dirPath ? `?path=${encodeURIComponent(dirPath)}` : "";
    const data = await apiCall<FsListResponse>(`/workspace/files${query}`);
    setFsState(data);
  };

  useEffect(() => {
    void refreshDatabases().catch((error) => setMessage(String(error)));
    void refreshFiles(".").catch((error) => setMessage(String(error)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!selectedDb) return;
    void refreshTables(selectedDb).catch((error) => setMessage(String(error)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedDb]);

  useEffect(() => {
    if (!selectedDb || !selectedTable) return;
    void refreshRows(selectedDb, selectedTable).catch((error) => setMessage(String(error)));
  }, [selectedDb, selectedTable]);

  const updateMessage = (text: string) => {
    setMessage(text);
    window.setTimeout(() => setMessage(""), 2500);
  };

  const handleSaveRow = async (row: Record<string, unknown>) => {
    const rowId = Number(row.__rowid__);
    const edited = editedRows[String(rowId)];
    if (!edited || !Object.keys(edited).length) return;
    const payload: Record<string, unknown> = {};
    for (const [key, val] of Object.entries(edited)) {
      payload[key] = parseInputValue(val, row[key]);
    }
    setBusy(true);
    try {
      await apiCall("/workspace/databases/rows", {
        method: "PUT",
        body: JSON.stringify({
          dbPath: selectedDb,
          table: selectedTable,
          rowId,
          values: payload,
        }),
      });
      await refreshRows(selectedDb, selectedTable);
      updateMessage("Строка сохранена");
    } catch (error) {
      setMessage(String(error));
    } finally {
      setBusy(false);
    }
  };

  const handleDeleteRow = async (row: Record<string, unknown>) => {
    const rowId = Number(row.__rowid__);
    if (!Number.isFinite(rowId)) return;
    setBusy(true);
    try {
      await apiCall("/workspace/databases/rows", {
        method: "DELETE",
        body: JSON.stringify({
          dbPath: selectedDb,
          table: selectedTable,
          rowId,
        }),
      });
      await refreshRows(selectedDb, selectedTable);
      updateMessage("Строка удалена");
    } catch (error) {
      setMessage(String(error));
    } finally {
      setBusy(false);
    }
  };

  const handleInsertRow = async () => {
    const payload: Record<string, unknown> = {};
    for (const column of editableColumns) {
      payload[column.name] = parseForInsert(newRowValues[column.name] ?? "");
    }
    setBusy(true);
    try {
      await apiCall("/workspace/databases/rows", {
        method: "POST",
        body: JSON.stringify({
          dbPath: selectedDb,
          table: selectedTable,
          values: payload,
        }),
      });
      await refreshRows(selectedDb, selectedTable);
      updateMessage("Строка добавлена");
    } catch (error) {
      setMessage(String(error));
    } finally {
      setBusy(false);
    }
  };

  const handleOpenFile = async (filePath: string) => {
    setBusy(true);
    try {
      const data = await apiCall<FsReadResponse>(
        `/workspace/files/read?path=${encodeURIComponent(filePath)}`
      );
      setSelectedFilePath(data.path);
      setSelectedFileContent(data.content);
      setMoveTargetPath(data.path);
    } catch (error) {
      setMessage(String(error));
    } finally {
      setBusy(false);
    }
  };

  const handleSaveFile = async () => {
    if (!selectedFilePath) return;
    setBusy(true);
    try {
      await apiCall("/workspace/files/write", {
        method: "PUT",
        body: JSON.stringify({
          path: selectedFilePath,
          content: selectedFileContent,
        }),
      });
      await refreshFiles(fsState?.currentPath ?? ".");
      updateMessage("Файл сохранен");
    } catch (error) {
      setMessage(String(error));
    } finally {
      setBusy(false);
    }
  };

  const handleCreateDir = async () => {
    const current = fsState?.currentPath ?? ".";
    const name = newDirName.trim();
    if (!name) return;
    setBusy(true);
    try {
      await apiCall("/workspace/files/mkdir", {
        method: "POST",
        body: JSON.stringify({ path: `${current}/${name}` }),
      });
      setNewDirName("");
      await refreshFiles(current);
      updateMessage("Папка создана");
    } catch (error) {
      setMessage(String(error));
    } finally {
      setBusy(false);
    }
  };

  const handleMovePath = async (fromPath: string) => {
    const toPath = moveTargetPath.trim();
    if (!toPath) return;
    setBusy(true);
    try {
      await apiCall("/workspace/files/move", {
        method: "POST",
        body: JSON.stringify({ from: fromPath, to: toPath }),
      });
      await refreshFiles(fsState?.currentPath ?? ".");
      setSelectedFilePath(toPath);
      updateMessage("Путь перемещен");
    } catch (error) {
      setMessage(String(error));
    } finally {
      setBusy(false);
    }
  };

  const handleDeletePath = async (targetPath: string, isDir: boolean) => {
    setBusy(true);
    try {
      await apiCall("/workspace/files", {
        method: "DELETE",
        body: JSON.stringify({ path: targetPath, recursive: isDir }),
      });
      await refreshFiles(fsState?.currentPath ?? ".");
      if (targetPath === selectedFilePath) {
        setSelectedFilePath("");
        setSelectedFileContent("");
      }
      updateMessage("Удалено");
    } catch (error) {
      setMessage(String(error));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className={`paper-broadcast paper-broadcast--theme-dark paper-broadcast--palette-virtus ${dmSans.className}`}
    >
      <div className={styles.page}>
      <header className={styles.header}>
        <div>
          <h1 className={styles.title}>Workspace Manager</h1>
          <p className={styles.subtitle}>
            Редактирование SQLite таблиц и управление файлами в одном интерфейсе.
          </p>
        </div>
        <Link href="/" className="rail-action-btn">
          На сайт
        </Link>
      </header>

      <div className={styles.tabs}>
        <button
          type="button"
          className={`${styles.tabBtn} ${tab === "db" ? styles.tabBtnActive : ""}`}
          onClick={() => setTab("db")}
        >
          База данных
        </button>
        <button
          type="button"
          className={`${styles.tabBtn} ${tab === "files" ? styles.tabBtnActive : ""}`}
          onClick={() => setTab("files")}
        >
          Файловый менеджер
        </button>
      </div>

      {message ? <p className={styles.message}>{message}</p> : null}

      {tab === "db" ? (
        <section className={styles.grid}>
          <aside className="panel">
            <label htmlFor="db-select">SQLite файл</label>
            <select
              id="db-select"
              value={selectedDb}
              onChange={(event) => setSelectedDb(event.target.value)}
            >
              {databases.map((dbPath) => (
                <option key={dbPath} value={dbPath}>
                  {dbPath}
                </option>
              ))}
            </select>
            <label htmlFor="table-select">Таблица</label>
            <select
              id="table-select"
              value={selectedTable}
              onChange={(event) => setSelectedTable(event.target.value)}
            >
              {tables.map((table) => (
                <option key={table} value={table}>
                  {table}
                </option>
              ))}
            </select>
            <button
              type="button"
              className={styles.actionBtn}
              onClick={() => refreshRows(selectedDb, selectedTable)}
              disabled={!selectedDb || !selectedTable || busy}
            >
              Обновить таблицу
            </button>
          </aside>

          <div className="panel">
            <div className={styles.tableWrap}>
              <table className={styles.table}>
                <thead>
                  <tr>
                    <th>rowid</th>
                    {editableColumns.map((column) => (
                      <th key={column.name}>{column.name}</th>
                    ))}
                    <th>Действия</th>
                  </tr>
                </thead>
                <tbody>
                  {(dbRows?.rows ?? []).map((row) => {
                    const rowId = String(row.__rowid__ ?? "");
                    return (
                      <tr key={rowId}>
                        <td>{rowId}</td>
                        {editableColumns.map((column) => {
                          const current = editedRows[rowId]?.[column.name];
                          const displayValue =
                            current ?? (row[column.name] === null ? "" : String(row[column.name] ?? ""));
                          return (
                            <td key={column.name}>
                              <input
                                value={displayValue}
                                onChange={(event) =>
                                  setEditedRows((prev) => ({
                                    ...prev,
                                    [rowId]: {
                                      ...(prev[rowId] ?? {}),
                                      [column.name]: event.target.value,
                                    },
                                  }))
                                }
                              />
                            </td>
                          );
                        })}
                        <td className={styles.rowActions}>
                          <button
                            type="button"
                            className={styles.actionBtn}
                            onClick={() => handleSaveRow(row)}
                            disabled={busy}
                          >
                            Сохранить
                          </button>
                          <button
                            type="button"
                            className={styles.dangerBtn}
                            onClick={() => handleDeleteRow(row)}
                            disabled={busy}
                          >
                            Удалить
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                  {editableColumns.length > 0 ? (
                    <tr className={styles.newRow}>
                      <td>new</td>
                      {editableColumns.map((column) => (
                        <td key={column.name}>
                          <input
                            value={newRowValues[column.name] ?? ""}
                            onChange={(event) =>
                              setNewRowValues((prev) => ({
                                ...prev,
                                [column.name]: event.target.value,
                              }))
                            }
                          />
                        </td>
                      ))}
                      <td>
                        <button
                          type="button"
                          className={styles.actionBtn}
                          onClick={handleInsertRow}
                          disabled={busy}
                        >
                          Добавить
                        </button>
                      </td>
                    </tr>
                  ) : null}
                </tbody>
              </table>
            </div>
            {dbRows ? (
              <p className={styles.meta}>Записей: {dbRows.total}. Показаны первые {dbRows.rows.length}.</p>
            ) : null}
          </div>
        </section>
      ) : (
        <section className={styles.grid}>
          <aside className="panel">
            <div className={styles.fileToolbar}>
              <button
                type="button"
                className={styles.actionBtn}
                onClick={() => refreshFiles(fsState?.parentPath ?? ".")}
                disabled={!fsState?.parentPath || busy}
              >
                Вверх
              </button>
              <button
                type="button"
                className={styles.actionBtn}
                onClick={() => refreshFiles(fsState?.currentPath ?? ".")}
                disabled={busy}
              >
                Обновить
              </button>
            </div>
            <p className={styles.metaPath}>{fsState?.currentPath ?? "."}</p>
            <div className={styles.fileList}>
              {(fsState?.items ?? []).map((item) => (
                <div key={item.path} className={styles.fileItem}>
                  <button
                    type="button"
                    className={styles.fileBtn}
                    onClick={() => {
                      if (item.type === "dir") {
                        void refreshFiles(item.path);
                        return;
                      }
                      void handleOpenFile(item.path);
                    }}
                  >
                    {item.type === "dir" ? "📁" : "📄"} {item.name}
                  </button>
                  <button
                    type="button"
                    className={styles.dangerBtn}
                    onClick={() => handleDeletePath(item.path, item.type === "dir")}
                    disabled={busy}
                  >
                    ✕
                  </button>
                </div>
              ))}
            </div>
            <div className={styles.inlineForm}>
              <input
                value={newDirName}
                onChange={(event) => setNewDirName(event.target.value)}
                placeholder="Новая папка"
              />
              <button type="button" className={styles.actionBtn} onClick={handleCreateDir} disabled={busy}>
                Создать
              </button>
            </div>
          </aside>

          <div className="panel">
            <label htmlFor="file-path">Путь</label>
            <input
              id="file-path"
              value={moveTargetPath}
              onChange={(event) => setMoveTargetPath(event.target.value)}
              placeholder="output/example.txt"
            />
            <div className={styles.fileToolbar}>
              <button
                type="button"
                className={styles.actionBtn}
                onClick={() => handleMovePath(selectedFilePath)}
                disabled={!selectedFilePath || busy}
              >
                Переместить
              </button>
              <button
                type="button"
                className={styles.actionBtn}
                onClick={handleSaveFile}
                disabled={!selectedFilePath || busy}
              >
                Сохранить файл
              </button>
            </div>
            <textarea
              className={styles.editor}
              value={selectedFileContent}
              onChange={(event) => setSelectedFileContent(event.target.value)}
              placeholder="Откройте файл из списка слева"
            />
          </div>
        </section>
      )}
      </div>
    </div>
  );
}

