import { BadRequestException, Injectable, NotFoundException } from "@nestjs/common";
import Database = require("better-sqlite3");
import { execFileSync } from "node:child_process";
import * as fs from "node:fs";
import * as path from "node:path";
import { loadRuntimePaths } from "../../core/runtime-paths";

type SqliteRow = Record<string, unknown>;

function quoteIdentifier(value: string): string {
  return `"${value.replace(/"/g, "\"\"")}"`;
}

function looksLikeBinary(data: Buffer): boolean {
  const sample = data.subarray(0, 512);
  for (const byte of sample) {
    if (byte === 0) return true;
  }
  return false;
}

function serializeCellValue(value: unknown): unknown {
  if (Buffer.isBuffer(value)) {
    return `[BLOB ${value.length} bytes]`;
  }
  return value;
}

function isVideoFileName(name: string): boolean {
  const lower = name.toLowerCase();
  return lower.endsWith(".mp4") || lower.endsWith(".mkv") || lower.endsWith(".webm") || lower.endsWith(".mov");
}

@Injectable()
export class WorkspaceService {
  private readonly projectRoot = path.resolve(__dirname, "../../../../..");
  private readonly runtimePaths = loadRuntimePaths(this.projectRoot);
  private readonly durationCache = new Map<string, number | null>();

  private resolveSafePath(relOrAbs?: string): string {
    const base = relOrAbs?.trim() ? relOrAbs.trim() : ".";
    const resolved = path.resolve(this.projectRoot, base);
    const rootWithSep = this.projectRoot.endsWith(path.sep)
      ? this.projectRoot
      : `${this.projectRoot}${path.sep}`;
    if (resolved !== this.projectRoot && !resolved.startsWith(rootWithSep)) {
      throw new BadRequestException("Path must stay inside project root.");
    }
    return resolved;
  }

  private resolveExistingDbPath(dbPath: string): string {
    const resolved = this.resolveSafePath(dbPath);
    if (path.extname(resolved).toLowerCase() !== ".sqlite") {
      throw new BadRequestException("Only .sqlite files are supported.");
    }
    if (!fs.existsSync(resolved)) {
      throw new NotFoundException("Database file not found.");
    }
    return resolved;
  }

  private toRelativePath(absPath: string): string {
    const rel = path.relative(this.projectRoot, absPath);
    return rel === "" ? "." : rel.split(path.sep).join("/");
  }

  listDatabases(): string[] {
    const known = new Set<string>();
    for (const dbPath of this.runtimePaths.databases.tournaments) {
      if (fs.existsSync(dbPath)) known.add(this.toRelativePath(dbPath));
    }
    if (fs.existsSync(this.runtimePaths.databases.mapStartDetection)) {
      known.add(this.toRelativePath(this.runtimePaths.databases.mapStartDetection));
    }

    const scanRoots = ["output", "tools/algs-collector"];
    for (const root of scanRoots) {
      const absRoot = this.resolveSafePath(root);
      if (!fs.existsSync(absRoot)) continue;
      const stack: string[] = [absRoot];
      while (stack.length) {
        const dir = stack.pop() as string;
        const entries = fs.readdirSync(dir, { withFileTypes: true });
        for (const entry of entries) {
          const full = path.join(dir, entry.name);
          if (entry.isDirectory()) {
            stack.push(full);
            continue;
          }
          if (entry.isFile() && entry.name.toLowerCase().endsWith(".sqlite")) {
            known.add(this.toRelativePath(full));
          }
        }
      }
    }

    return [...known].sort((a, b) => a.localeCompare(b));
  }

  listTables(dbPath: string): string[] {
    const absPath = this.resolveExistingDbPath(dbPath);
    const db = new Database(absPath, { readonly: true, fileMustExist: true });
    try {
      const rows = db
        .prepare(
          "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
        .all() as Array<{ name: string }>;
      return rows.map((row) => row.name);
    } finally {
      db.close();
    }
  }

  getTableRows(dbPath: string, tableName: string, limit = 100, offset = 0) {
    const absPath = this.resolveExistingDbPath(dbPath);
    const safeTable = quoteIdentifier(tableName);
    const db = new Database(absPath, { readonly: true, fileMustExist: true });
    try {
      const columns = db
        .prepare(`PRAGMA table_info(${safeTable})`)
        .all() as Array<{ name: string; type: string; notnull: number; pk: number }>;
      if (columns.length === 0) {
        throw new NotFoundException(`Table not found: ${tableName}`);
      }
      const totalRaw = db.prepare(`SELECT COUNT(*) as total FROM ${safeTable}`).get() as {
        total: number;
      };
      const rawRows = db
        .prepare(`SELECT rowid as __rowid__, * FROM ${safeTable} LIMIT ? OFFSET ?`)
        .all(limit, offset) as SqliteRow[];
      const rows = rawRows.map((row) => {
        const normalized: SqliteRow = {};
        for (const [key, value] of Object.entries(row)) {
          normalized[key] = serializeCellValue(value);
        }
        return normalized;
      });
      return {
        columns: columns.map((col) => ({
          name: col.name,
          type: col.type,
          nullable: col.notnull === 0,
          primaryKey: col.pk > 0,
        })),
        rows,
        total: totalRaw.total,
        limit,
        offset,
      };
    } finally {
      db.close();
    }
  }

  updateRow(dbPath: string, tableName: string, rowId: number, values: Record<string, unknown>) {
    const absPath = this.resolveExistingDbPath(dbPath);
    const safeTable = quoteIdentifier(tableName);
    const entries = Object.entries(values).filter(([key]) => key !== "__rowid__");
    if (!entries.length) {
      throw new BadRequestException("No editable values were provided.");
    }
    const db = new Database(absPath, { fileMustExist: true });
    try {
      const setClause = entries
        .map(([key]) => `${quoteIdentifier(key)} = ?`)
        .join(", ");
      const params = entries.map(([, value]) => value);
      const result = db
        .prepare(`UPDATE ${safeTable} SET ${setClause} WHERE rowid = ?`)
        .run(...params, rowId);
      if (result.changes === 0) {
        throw new NotFoundException("Row not found.");
      }
      return { changes: result.changes };
    } finally {
      db.close();
    }
  }

  insertRow(dbPath: string, tableName: string, values: Record<string, unknown>) {
    const absPath = this.resolveExistingDbPath(dbPath);
    const safeTable = quoteIdentifier(tableName);
    const entries = Object.entries(values).filter(([key]) => key !== "__rowid__");
    const db = new Database(absPath, { fileMustExist: true });
    try {
      let result;
      if (!entries.length) {
        result = db.prepare(`INSERT INTO ${safeTable} DEFAULT VALUES`).run();
      } else {
        const columnSql = entries.map(([key]) => quoteIdentifier(key)).join(", ");
        const placeholderSql = entries.map(() => "?").join(", ");
        const params = entries.map(([, value]) => value);
        result = db
          .prepare(`INSERT INTO ${safeTable} (${columnSql}) VALUES (${placeholderSql})`)
          .run(...params);
      }
      return { changes: result.changes, lastInsertRowid: Number(result.lastInsertRowid) };
    } finally {
      db.close();
    }
  }

  deleteRow(dbPath: string, tableName: string, rowId: number) {
    const absPath = this.resolveExistingDbPath(dbPath);
    const safeTable = quoteIdentifier(tableName);
    const db = new Database(absPath, { fileMustExist: true });
    try {
      const result = db.prepare(`DELETE FROM ${safeTable} WHERE rowid = ?`).run(rowId);
      if (result.changes === 0) {
        throw new NotFoundException("Row not found.");
      }
      return { changes: result.changes };
    } finally {
      db.close();
    }
  }

  listDirectory(relPath?: string) {
    const absPath = this.resolveSafePath(relPath);
    if (!fs.existsSync(absPath)) {
      throw new NotFoundException("Path does not exist.");
    }
    const st = fs.statSync(absPath);
    if (!st.isDirectory()) {
      throw new BadRequestException("Path must be a directory.");
    }
    const items = fs
      .readdirSync(absPath, { withFileTypes: true })
      .map((entry) => {
        const full = path.join(absPath, entry.name);
        const est = fs.statSync(full);
        return {
          name: entry.name,
          path: this.toRelativePath(full),
          type: entry.isDirectory() ? "dir" : "file",
          size: est.size,
          modifiedAt: est.mtime.toISOString(),
          durationSec: entry.isFile() && isVideoFileName(entry.name) ? this.getVideoDurationSec(full) : null,
        };
      })
      .sort((a, b) => {
        if (a.type !== b.type) return a.type === "dir" ? -1 : 1;
        return a.name.localeCompare(b.name);
      });
    return {
      currentPath: this.toRelativePath(absPath),
      parentPath: absPath === this.projectRoot ? null : this.toRelativePath(path.dirname(absPath)),
      items,
    };
  }

  private getVideoDurationSec(absPath: string): number | null {
    const key = `${absPath}:${fs.statSync(absPath).mtimeMs}`;
    if (this.durationCache.has(key)) return this.durationCache.get(key) ?? null;
    try {
      const raw = execFileSync(
        "ffprobe",
        ["-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", absPath],
        { encoding: "utf-8", timeout: 5000, windowsHide: true }
      );
      const sec = Number(String(raw).trim());
      const value = Number.isFinite(sec) && sec > 0 ? sec : null;
      this.durationCache.set(key, value);
      return value;
    } catch {
      this.durationCache.set(key, null);
      return null;
    }
  }

  readTextFile(filePath: string) {
    const absPath = this.resolveSafePath(filePath);
    if (!fs.existsSync(absPath)) {
      throw new NotFoundException("File does not exist.");
    }
    const st = fs.statSync(absPath);
    if (!st.isFile()) {
      throw new BadRequestException("Path must be a file.");
    }
    if (st.size > 2 * 1024 * 1024) {
      throw new BadRequestException("File is too large for inline editor (max 2MB).");
    }
    const data = fs.readFileSync(absPath);
    if (looksLikeBinary(data)) {
      throw new BadRequestException("Binary file is not editable in text mode.");
    }
    return {
      path: this.toRelativePath(absPath),
      content: data.toString("utf-8"),
      size: st.size,
      modifiedAt: st.mtime.toISOString(),
    };
  }

  writeTextFile(filePath: string, content: string) {
    const absPath = this.resolveSafePath(filePath);
    const dir = path.dirname(absPath);
    if (!fs.existsSync(dir)) {
      fs.mkdirSync(dir, { recursive: true });
    }
    fs.writeFileSync(absPath, content, "utf-8");
    return { path: this.toRelativePath(absPath), size: Buffer.byteLength(content, "utf-8") };
  }

  /** Абсолютный путь к каталогу с сырыми записями (см. `runtime_paths.json` → media.recordsDir). */
  getMediaRecordsAbsPath(): string {
    return this.runtimePaths.media.recordsDir;
  }

  /** Относительный путь от корня проекта для абсолютного пути внутри репозитория. */
  fileAbsoluteToProjectRelative(absFilePath: string): string {
    const abs = path.resolve(absFilePath);
    const root = path.resolve(this.projectRoot);
    const rootWithSep = root.endsWith(path.sep) ? root : `${root}${path.sep}`;
    if (abs !== root && !abs.startsWith(rootWithSep)) {
      throw new BadRequestException("File path must stay inside project root.");
    }
    const rel = path.relative(root, abs);
    return rel.split(path.sep).join("/");
  }

  private segmentManifestsRelDir(): string {
    return "output/management_segment_requests";
  }

  listSegmentManifestSummaries(): Array<Record<string, unknown> & { manifestPath: string }> {
    const absDir = this.resolveSafePath(this.segmentManifestsRelDir());
    if (!fs.existsSync(absDir)) {
      return [];
    }
    const names = fs.readdirSync(absDir).filter((n) => n.toLowerCase().endsWith(".json"));
    const out: Array<Record<string, unknown> & { manifestPath: string }> = [];
    for (const name of names) {
      const full = path.join(absDir, name);
      try {
        const raw = fs.readFileSync(full, "utf-8");
        const data = JSON.parse(raw) as Record<string, unknown>;
        out.push({ ...data, manifestPath: this.toRelativePath(full) });
      } catch {
        /* skip broken files */
      }
    }
    out.sort((a, b) => String(b.createdAt ?? "").localeCompare(String(a.createdAt ?? "")));
    return out;
  }

  createSegmentManifest(params: {
    tournamentId: string;
    videoRelativePath: string;
    startSec: number;
    endSec: number;
    segmentDurationSec: number;
  }) {
    const tournamentId = params.tournamentId?.trim();
    if (!tournamentId) {
      throw new BadRequestException("tournamentId is required.");
    }
    const videoRel = params.videoRelativePath?.trim().replace(/\\/g, "/");
    if (!videoRel) {
      throw new BadRequestException("videoRelativePath is required.");
    }
    const startSec = Number(params.startSec);
    const endSec = Number(params.endSec);
    const segmentDurationSec = Number(params.segmentDurationSec);
    if (![startSec, endSec, segmentDurationSec].every((n) => Number.isFinite(n))) {
      throw new BadRequestException("startSec, endSec and segmentDurationSec must be finite numbers.");
    }
    if (startSec < 0 || endSec < 0 || segmentDurationSec <= 0) {
      throw new BadRequestException("Invalid timing values.");
    }
    if (endSec <= startSec) {
      throw new BadRequestException("endSec must be greater than startSec.");
    }

    const absVideo = this.resolveSafePath(videoRel);
    if (!fs.existsSync(absVideo)) {
      throw new NotFoundException("Video file not found.");
    }
    if (!fs.statSync(absVideo).isFile()) {
      throw new BadRequestException("videoRelativePath must point to a file.");
    }

    const manifest = {
      version: 1,
      createdAt: new Date().toISOString(),
      tournamentId,
      videoRelativePath: this.toRelativePath(absVideo),
      startSec,
      endSec,
      segmentDurationSec,
    };

    const absDir = this.resolveSafePath(this.segmentManifestsRelDir());
    fs.mkdirSync(absDir, { recursive: true });
    const stem = path.basename(absVideo).replace(/\.[^.]+$/, "") || "video";
    const safeStem = stem.replace(/[^\w.\-]+/g, "_").slice(0, 120);
    const fileName = `seg_${Date.now()}_${safeStem}.json`;
    const absManifest = path.join(absDir, fileName);
    fs.writeFileSync(absManifest, `${JSON.stringify(manifest, null, 2)}\n`, "utf-8");
    return {
      manifestPath: this.toRelativePath(absManifest),
      manifest,
    };
  }

  createDirectory(dirPath: string) {
    const absPath = this.resolveSafePath(dirPath);
    fs.mkdirSync(absPath, { recursive: true });
    return { path: this.toRelativePath(absPath) };
  }

  movePath(fromPath: string, toPath: string) {
    const fromAbs = this.resolveSafePath(fromPath);
    const toAbs = this.resolveSafePath(toPath);
    if (!fs.existsSync(fromAbs)) {
      throw new NotFoundException("Source path does not exist.");
    }
    const targetDir = path.dirname(toAbs);
    if (!fs.existsSync(targetDir)) {
      fs.mkdirSync(targetDir, { recursive: true });
    }
    fs.renameSync(fromAbs, toAbs);
    return {
      from: this.toRelativePath(fromAbs),
      to: this.toRelativePath(toAbs),
    };
  }

  deletePath(targetPath: string, recursive = false) {
    const absPath = this.resolveSafePath(targetPath);
    if (!fs.existsSync(absPath)) {
      throw new NotFoundException("Path does not exist.");
    }
    const st = fs.statSync(absPath);
    if (st.isDirectory()) {
      fs.rmSync(absPath, { recursive, force: false });
      return { deleted: this.toRelativePath(absPath), type: "dir" };
    }
    fs.unlinkSync(absPath);
    return { deleted: this.toRelativePath(absPath), type: "file" };
  }
}

