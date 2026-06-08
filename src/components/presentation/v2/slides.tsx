import type { ReactElement, ReactNode } from "react";
import {
  FolderOpen, FileText, CloudUpload, BrainCircuit, Database, Code2, Monitor, User,
  Sliders, Flag, Target, Eye, Users, Crosshair, TrendingUp, Map as MapIcon,
  Trophy, Swords, Filter, Play, BarChart3, Clock, Rocket,
  Bot, Check, X, Server, ShieldCheck, GitBranch,
  Pipette, Palette, Save, LayoutGrid, Frame, Zap, Image as ImageIcon,
  Video, Activity, SlidersHorizontal, MousePointer2, ArrowRight,
} from "lucide-react";
import { E } from "./Editable";
import { V2Canvas } from "./Canvas";

type P = { editing: boolean };

const SERIF = "'Instrument Serif', 'Cormorant Garamond', Georgia, serif";
const INK = "#1a1714";
const PAPER = "#f5f1e8";
const CARD = "#fbf8f1";
const RULE = "#1a1714";
const ACCENT = "#d4541c";   // orange ink
const MUTED = "#6b5d4a";

/* ────────────────── building blocks ────────────────── */

function Page({ num, total, eyebrow, titleId, titleDefault, subId, subDefault, editing, children }: {
  num: string; total: string; eyebrow: string;
  titleId: string; titleDefault: string; subId: string; subDefault: string;
  editing: boolean; children: ReactNode;
}) {
  return (
    <V2Canvas>
      <div className="flex h-full w-full flex-col px-24 pt-14 pb-12">
        <div className="flex items-center justify-between text-[14px] uppercase tracking-[0.3em]" style={{ color: MUTED }}>
          <span>Apex Stats · Концепция платформы</span>
          <span>{num} / {total}</span>
        </div>
        <div className="mt-6 h-px w-full" style={{ background: RULE }} />
        <div className="mt-8 flex items-end gap-6">
          <div className="text-[120px] leading-none" style={{ fontFamily: SERIF, color: ACCENT }}>{num}</div>
          <div className="pb-3">
            <div className="text-[14px] font-semibold uppercase tracking-[0.3em]" style={{ color: ACCENT }}>{eyebrow}</div>
            <E id={titleId} defaultValue={titleDefault} editing={editing} as="h1"
              className="mt-1 text-[68px] leading-[1.05] font-normal"
              />
          </div>
        </div>
        <E id={subId} defaultValue={subDefault} editing={editing} as="p"
          className="mt-4 max-w-[1500px] text-[22px] leading-snug" multiline />
        <div className="mt-8 grid h-px w-24 bg-[#1a1714]" />
        <div className="mt-8 flex-1">{children}</div>
        <div className="mt-6 flex items-center justify-between text-[12px] uppercase tracking-[0.3em]" style={{ color: MUTED }}>
          <span>Apex Legends · Аналитика трансляций</span>
          <span>Издание · {new Date().getFullYear()}</span>
        </div>
      </div>
    </V2Canvas>
  );
}

// Override page heading font on serif globally via inline style is enough; titles use SERIF.

function Card({ children, accent = false }: { children: ReactNode; accent?: boolean }) {
  return (
    <div
      className="rounded-sm p-6"
      style={{
        background: CARD,
        border: `1px solid ${accent ? ACCENT : "#1a17141a"}`,
        boxShadow: "0 1px 0 #1a17140a",
      }}
    >
      {children}
    </div>
  );
}

function Eyebrow({ children, color = ACCENT }: { children: ReactNode; color?: string }) {
  return (
    <div className="text-[11px] font-semibold uppercase tracking-[0.3em]" style={{ color }}>
      {children}
    </div>
  );
}

function H2({ children, id, editing }: { children: string; id?: string; editing?: boolean }) {
  if (id && editing !== undefined) {
    return <E id={id} defaultValue={children} editing={editing} as="h2"
      className="text-[32px] leading-tight font-normal" />;
  }
  return <h2 className="text-[32px] leading-tight font-normal" style={{ fontFamily: SERIF }}>{children}</h2>;
}

function FlowArrow() {
  return <ArrowRight className="h-6 w-6 shrink-0" style={{ color: ACCENT }} />;
}

/* ────────────────── 1. Architecture ────────────────── */

function V2Slide1({ editing }: P) {
  const nodes = [
    { Icon: FolderOpen, t: "VOD / Метаданные", d: "Записи матчей и структура турнира" },
    { Icon: CloudUpload, t: "Сбор данных", d: "Импорт матчей и видео" },
    { Icon: BrainCircuit, t: "Анализ (CV)", d: "Зрение, трекинг команд и зон" },
    { Icon: Database, t: "PostgreSQL", d: "Единый источник данных" },
    { Icon: Code2, t: "API", d: "Доступ к данным и задачам" },
    { Icon: Monitor, t: "Веб-интерфейс", d: "Карта, таймлайн, фильтры" },
    { Icon: User, t: "Аналитик / тренер", d: "Получает инсайты" },
  ];
  return (
    <Page num="01" total="12" eyebrow="Глава I · Архитектура"
      titleId="v2.s1.t" titleDefault="Архитектура верхнего уровня"
      subId="v2.s1.s" subDefault="Как данные из матчей Apex Legends последовательно превращаются в интерактивную аналитику для команды."
      editing={editing}>
      <div className="grid grid-cols-7 gap-4">
        {nodes.map((n, i) => (
          <div key={i} className="flex items-center">
            <Card>
              <n.Icon className="h-9 w-9" style={{ color: ACCENT }} strokeWidth={1.4} />
              <E id={`v2.s1.n${i}.t`} defaultValue={n.t} editing={editing}
                className="mt-3 text-[18px] font-semibold" multiline />
              <E id={`v2.s1.n${i}.d`} defaultValue={n.d} editing={editing}
                className="mt-1 text-[13px]" multiline />
            </Card>
            {i < nodes.length - 1 && <div className="px-1"><FlowArrow /></div>}
          </div>
        ))}
      </div>
      <div className="mt-10 grid grid-cols-3 gap-6">
        {[
          ["Источник правды", "PostgreSQL хранит нормализованные данные турниров и матчей."],
          ["Изоляция сервисов", "Сбор, анализ и доступ разнесены: каждый компонент масштабируется отдельно."],
          ["Один интерфейс", "Веб-дашборд для тренеров и аналитиков объединяет всё в одном экране."],
        ].map(([t, d], i) => (
          <div key={i}>
            <Eyebrow>0{i + 1}</Eyebrow>
            <h3 className="mt-2 text-[22px]" style={{ fontFamily: SERIF }}>{t}</h3>
            <p className="mt-1 text-[15px]" style={{ color: MUTED }}>{d}</p>
          </div>
        ))}
      </div>
    </Page>
  );
}

/* ────────────────── 2. Data flow ────────────────── */

function V2Slide2({ editing }: P) {
  const steps = [
    { Icon: FolderOpen, t: "Исходные данные", d: "VOD, метаданные, карты" },
    { Icon: CloudUpload, t: "Сбор", d: "Импорт и валидация" },
    { Icon: Sliders, t: "Предобработка", d: "Разметка и подготовка" },
    { Icon: Eye, t: "CV-анализ", d: "События и координаты" },
    { Icon: Database, t: "Структурирование", d: "Позиции, зоны, статистика" },
    { Icon: Database, t: "PostgreSQL", d: "Нормализованное хранилище" },
    { Icon: Monitor, t: "Дашборд", d: "Карта, таймлайн, инсайты" },
  ];
  return (
    <Page num="02" total="12" eyebrow="Глава II · Поток данных"
      titleId="v2.s2.t" titleDefault="Диаграмма потока данных"
      subId="v2.s2.s" subDefault="Путь видеоданных от исходного VOD до интерактивной аналитики, читаемой в дашборде."
      editing={editing}>
      <div className="grid grid-cols-[repeat(7,1fr)] items-stretch gap-2">
        {steps.map((s, i) => (
          <div key={i} className="flex items-stretch">
            <Card>
              <div className="text-[12px] font-mono" style={{ color: ACCENT }}>0{i + 1}</div>
              <s.Icon className="mt-2 h-8 w-8" style={{ color: INK }} strokeWidth={1.4} />
              <E id={`v2.s2.s${i}.t`} defaultValue={s.t} editing={editing}
                className="mt-3 text-[18px] font-semibold" />
              <E id={`v2.s2.s${i}.d`} defaultValue={s.d} editing={editing}
                className="mt-1 text-[13px]" multiline />
            </Card>
            {i < steps.length - 1 && <div className="self-center px-0.5"><FlowArrow /></div>}
          </div>
        ))}
      </div>
      <div className="mt-10">
        <Eyebrow>CV-анализ — четыре параллельных задачи</Eyebrow>
        <div className="mt-3 grid grid-cols-4 gap-4">
          {[
            [Flag, "Старт карты", "Определение момента начала раунда"],
            [Target, "Кольца", "Геометрия и тайминги зон"],
            [Eye, "Трекинг обзора", "Куда направлена камера трансляции"],
            [Users, "Трекинг команд", "Позиции по цветовым маркерам"],
          ].map(([Ic, t, d], i) => {
            const I = Ic as any;
            return (
              <div key={i} className="border-l-2 pl-4" style={{ borderColor: ACCENT }}>
                <I className="h-6 w-6" style={{ color: ACCENT }} strokeWidth={1.6} />
                <div className="mt-2 text-[18px] font-semibold">{t as string}</div>
                <div className="mt-1 text-[14px]" style={{ color: MUTED }}>{d as string}</div>
              </div>
            );
          })}
        </div>
      </div>
    </Page>
  );
}

/* ────────────────── 3. CV pipeline ────────────────── */

function V2Slide3({ editing }: P) {
  const steps = [
    { Icon: Play, t: "Видеокадр", d: "Исходный кадр трансляции" },
    { Icon: Crosshair, t: "Миникарта / HUD", d: "Выделение нужной области" },
    { Icon: MapIcon, t: "Регистрация карты", d: "Привязка к игровой карте" },
    { Icon: Target, t: "Детекция кольца", d: "Поиск текущей зоны" },
    { Icon: Users, t: "Маркеры команд", d: "Поиск игроков и сквадов" },
    { Icon: BarChart3, t: "Нормализация координат", d: "Единое пространство" },
    { Icon: TrendingUp, t: "Трекинг и вывод", d: "Траектории и события" },
  ];
  const tech = ["OpenCV", "Сегментация цвета", "Шаблонное сопоставление", "Детекция объектов", "Калман-фильтр", "Проекция карты"];
  return (
    <Page num="03" total="12" eyebrow="Глава III · Зрение"
      titleId="v2.s3.t" titleDefault="Конвейер компьютерного зрения"
      subId="v2.s3.s" subDefault="Семь шагов превращения видеокадра в координаты, события и наглядную аналитику."
      editing={editing}>
      <ol className="grid grid-cols-7 gap-3">
        {steps.map((s, i) => (
          <li key={i} className="relative" style={{ listStyle: "none" }}>
            <div className="text-[40px] leading-none" style={{ fontFamily: SERIF, color: ACCENT }}>{i + 1}</div>
            <s.Icon className="mt-3 h-8 w-8" style={{ color: INK }} strokeWidth={1.4} />
            <E id={`v2.s3.s${i}.t`} defaultValue={s.t} editing={editing}
              className="mt-3 text-[17px] font-semibold" multiline />
            <E id={`v2.s3.s${i}.d`} defaultValue={s.d} editing={editing}
              className="mt-1 text-[13px]" multiline />
          </li>
        ))}
      </ol>
      <div className="mt-12">
        <Eyebrow>Ключевые технологии</Eyebrow>
        <div className="mt-3 flex flex-wrap gap-2">
          {tech.map((t, i) => (
            <span key={i} className="rounded-sm px-3 py-1.5 text-[14px]"
              style={{ background: "#fff", border: `1px solid ${INK}25` }}>{t}</span>
          ))}
        </div>
      </div>
    </Page>
  );
}

/* ────────────────── 4 & 9. User flow ────────────────── */

const FLOW = [
  ["Открыть дашборд", BarChart3],
  ["Выбрать турнир", Trophy],
  ["Выбрать матч", Swords],
  ["Выбрать карту", MapIcon],
  ["Применить фильтры", Filter],
  ["Изучить таймлайн", Play],
  ["Получить инсайты", TrendingUp],
] as const;

function FlowRow({ idPrefix, editing }: { idPrefix: string; editing: boolean }) {
  return (
    <div className="grid grid-cols-7 gap-2">
      {FLOW.map(([label, Ic], i) => (
        <div key={i} className="flex items-center">
          <div className="flex-1">
            <div className="text-[36px] leading-none" style={{ fontFamily: SERIF, color: ACCENT }}>{i + 1}</div>
            <Ic className="mt-2 h-7 w-7" style={{ color: INK }} strokeWidth={1.4} />
            <E id={`${idPrefix}.${i}`} defaultValue={label} editing={editing}
              className="mt-2 text-[15px] font-semibold leading-tight" multiline />
          </div>
          {i < FLOW.length - 1 && <div className="px-1"><FlowArrow /></div>}
        </div>
      ))}
    </div>
  );
}

function V2Slide4({ editing }: P) {
  return (
    <Page num="04" total="12" eyebrow="Глава IV · Сценарий"
      titleId="v2.s4.t" titleDefault="Пользовательский сценарий"
      subId="v2.s4.s" subDefault="Путь аналитика от выбора матча до получения инсайтов в едином интерфейсе."
      editing={editing}>
      <FlowRow idPrefix="v2.s4.f" editing={editing} />
      <div className="mt-10 grid grid-cols-4 gap-4">
        {[
          ["Турнир", "Летний кубок 2024"],
          ["Матч", "Финал · Матч 3"],
          ["Карта", "Шторм-Пойнт"],
          ["Фильтры", "Все команды"],
        ].map(([k, v], i) => (
          <Card key={i}>
            <Eyebrow>{k}</Eyebrow>
            <E id={`v2.s4.h${i}`} defaultValue={v} editing={editing}
              className="mt-2 text-[24px]" />
          </Card>
        ))}
      </div>
      <div className="mt-6 grid grid-cols-[1fr_2fr_1fr] gap-4">
        <Card>
          <Eyebrow>Команды</Eyebrow>
          <ul className="mt-3 space-y-2 text-[15px]">
            {["№1","№5","№8","№12","№17","№20"].map((t, i) => (
              <li key={i} className="flex items-center justify-between border-b pb-1.5" style={{ borderColor: "#1a17141a" }}>
                <span>Команда {t}</span>
                <Eye className="h-4 w-4" style={{ color: MUTED }} />
              </li>
            ))}
          </ul>
        </Card>
        <Card>
          <Eyebrow>Игровая карта</Eyebrow>
          <div className="relative mt-3 h-[260px] rounded-sm" style={{ background: "#ede4d2" }}>
            <div className="absolute inset-0 flex items-center justify-center">
              <div className="h-52 w-52 rounded-full border-2 border-dashed" style={{ borderColor: ACCENT }} />
            </div>
            <div className="absolute left-3 top-3 rounded-sm bg-white px-2 py-0.5 text-[12px] font-mono">08:37 · R1</div>
          </div>
        </Card>
        <Card>
          <Eyebrow>Лидеры матча</Eyebrow>
          <ul className="mt-3 space-y-2 text-[14px]">
            {[["Убийства","Команда №8","27"],["Урон","Команда №1","5 842"],["Дистанция","Команда №17","8.7 км"],["Выживаемость","Команда №5","68%"]].map((r, i) => (
              <li key={i} className="flex items-center justify-between border-b pb-1.5" style={{ borderColor: "#1a17141a" }}>
                <span style={{ color: MUTED }}>{r[0]}</span>
                <span>{r[1]}</span>
                <span className="font-mono" style={{ color: ACCENT }}>{r[2]}</span>
              </li>
            ))}
          </ul>
        </Card>
      </div>
    </Page>
  );
}

function V2Slide9({ editing }: P) {
  const blocks = [
    [Users, "Панель команд", "Списки команд слева и справа. Цветовая привязка к маршрутам."],
    [Filter, "Фильтры матча", "Турнир, матч, карта и команды — всё в боковой панели."],
    [MapIcon, "Игровая карта", "Маршруты, кольцо, тайминги и подписи на одной карте."],
    [Play, "Воспроизведение", "Шкала времени для навигации по раундам и ключевым событиям."],
    [Clock, "Таймер раунда", "Верхний индикатор фазы и оставшегося времени."],
  ] as const;
  return (
    <Page num="09" total="12" eyebrow="Глава IX · Сценарий — детали"
      titleId="v2.s9.t" titleDefault="Пользовательский сценарий — детали"
      subId="v2.s9.s" subDefault="Подробный разбор интерфейса дашборда аналитика."
      editing={editing}>
      <FlowRow idPrefix="v2.s9.f" editing={editing} />
      <div className="mt-10 grid grid-cols-[2.2fr_1fr] gap-5">
        <Card>
          <Eyebrow>Игровая карта</Eyebrow>
          <div className="relative mt-3 h-[420px] rounded-sm" style={{ background: "#ede4d2" }}>
            <div className="absolute inset-0 flex items-center justify-center">
              <div className="h-72 w-72 rounded-full border-2 border-dashed" style={{ borderColor: ACCENT }} />
            </div>
          </div>
        </Card>
        <div className="space-y-3">
          {blocks.map(([Ic, t, d], i) => (
            <Card key={i}>
              <div className="flex gap-3">
                <Ic className="h-7 w-7" style={{ color: ACCENT }} strokeWidth={1.5} />
                <div>
                  <E id={`v2.s9.b${i}.t`} defaultValue={t} editing={editing} className="text-[18px] font-semibold" />
                  <E id={`v2.s9.b${i}.d`} defaultValue={d} editing={editing}
                    className="mt-1 text-[13px]" multiline />
                </div>
              </div>
            </Card>
          ))}
        </div>
      </div>
    </Page>
  );
}

/* ────────────────── 5. Domain model ────────────────── */

function V2Slide5({ editing }: P) {
  const ents = [
    [Trophy, "Турнир", "id · название · сезон · год", "primary"],
    [Swords, "Матч", "id · время_старта · тип · best_of", "primary"],
    [MapIcon, "Карта", "id · название · порядок · длительность", "cyan"],
    [Play, "Событие таймлайна", "id · тип · метка_времени · данные", "primary"],
    [Users, "Команда", "id · название · тег · регион", "primary"],
    [User, "Игрок", "id · имя · роль · team_id", "primary"],
    [MapIcon, "Позиция команды", "id · map_id · team_id · timestamp", "success"],
    [Target, "Кольцо", "id · map_id · номер · полигон", "primary"],
  ] as const;
  return (
    <Page num="05" total="12" eyebrow="Глава V · Модель"
      titleId="v2.s5.t" titleDefault="Предметная модель"
      subId="v2.s5.s" subDefault="Ключевые сущности и связи внутри системы аналитики матчей."
      editing={editing}>
      <div className="grid grid-cols-4 gap-4">
        {ents.map(([Ic, t, fields], i) => (
          <Card key={i}>
            <Ic className="h-7 w-7" style={{ color: ACCENT }} strokeWidth={1.5} />
            <E id={`v2.s5.e${i}.t`} defaultValue={t} editing={editing} className="mt-3 text-[22px] font-semibold" />
            <div className="mt-2 h-px w-10" style={{ background: ACCENT }} />
            <E id={`v2.s5.e${i}.f`} defaultValue={fields} editing={editing}
              className="mt-3 font-mono text-[13px] text-[#6b5d4a]" multiline />
          </Card>
        ))}
      </div>
    </Page>
  );
}

/* ────────────────── 6. ER diagram ────────────────── */

function V2Slide6({ editing }: P) {
  const tables: [string, [string, string, string][]][] = [
    ["tournaments", [["PK","id","integer"],["","name","text"],["","start_date","timestamp"]]],
    ["matches", [["PK","id","integer"],["FK","tournament_id","integer"],["","name","text"]]],
    ["maps", [["PK","id","integer"],["FK","match_id","integer"],["","name","text"]]],
    ["teams", [["PK","id","integer"],["FK","tournament_id","integer"],["","tag","text"]]],
    ["players", [["PK","id","integer"],["FK","team_id","integer"],["","nick","text"]]],
    ["rings", [["PK","id","integer"],["FK","map_id","integer"],["","ring_number","integer"]]],
    ["team_positions", [["PK","id","integer"],["FK","team_id","integer"],["","position","text"]]],
    ["timeline_events", [["PK","id","integer"],["FK","map_id","integer"],["","event_type","text"]]],
    ["analysis_jobs", [["PK","id","integer"],["FK","map_id","integer"],["","status","text"]]],
    ["analysis_outputs", [["PK","id","integer"],["FK","job_id","integer"],["","file_url","text"]]],
  ];
  return (
    <Page num="06" total="12" eyebrow="Глава VI · База данных"
      titleId="v2.s6.t" titleDefault="ER-диаграмма базы данных"
      subId="v2.s6.s" subDefault="Упрощённая структура таблиц и связей для хранения аналитики матчей."
      editing={editing}>
      <div className="grid grid-cols-5 gap-3">
        {tables.map(([name, rows], i) => (
          <Card key={i}>
            <div className="flex items-center gap-2">
              <Database className="h-5 w-5" style={{ color: ACCENT }} strokeWidth={1.6} />
              <span className="text-[16px] font-semibold">{name}</span>
            </div>
            <div className="mt-2 h-px w-full" style={{ background: "#1a17141a" }} />
            <table className="mt-2 w-full text-[12px] font-mono">
              <tbody>
                {rows.map((r, j) => (
                  <tr key={j}>
                    <td className="py-0.5 pr-1">
                      {r[0] && <span className="rounded px-1 text-xs font-bold"
                        style={{ background: r[0] === "PK" ? `${ACCENT}25` : "#1a17141a", color: r[0] === "PK" ? ACCENT : MUTED }}>{r[0]}</span>}
                    </td>
                    <td className="py-0.5">{r[1]}</td>
                    <td className="py-0.5 text-right" style={{ color: MUTED }}>{r[2]}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        ))}
      </div>
      <div className="mt-8 flex items-center gap-6 text-[14px]" style={{ color: MUTED }}>
        <span className="flex items-center gap-2"><span className="rounded px-1.5 text-[11px] font-bold" style={{ background: `${ACCENT}25`, color: ACCENT }}>PK</span> Primary Key</span>
        <span className="flex items-center gap-2"><span className="rounded px-1.5 text-[11px] font-bold" style={{ background: "#1a17141a" }}>FK</span> Foreign Key</span>
      </div>
    </Page>
  );
}

/* ────────────────── 7. Manual vs Auto ────────────────── */

function V2Slide7({ editing }: P) {
  const manual = ["Смотреть весь VOD","Делать заметки","Сохранять скриншоты","Ручная разметка карты","Сложно сравнивать","Тратится много времени"];
  const auto = ["Автоматический детект","Визуальный таймлайн","Треки команд на карте","Быстрые сравнения","Экспорт данных","Экономия времени"];
  return (
    <Page num="07" total="12" eyebrow="Глава VII · Сравнение"
      titleId="v2.s7.t" titleDefault="Ручной vs автоматизированный анализ"
      subId="v2.s7.s" subDefault="Сравнение традиционного разбора матча и автоматизированного подхода Apex Stats."
      editing={editing}>
      <div className="grid grid-cols-2 gap-8">
        <div>
          <Eyebrow color={MUTED}>Подход А</Eyebrow>
          <h3 className="mt-2 text-[36px]" style={{ fontFamily: SERIF, color: MUTED }}>Ручной анализ</h3>
          <ul className="mt-4 space-y-2 text-[18px]">
            {manual.map((m, i) => (
              <li key={i} className="flex items-center gap-3 border-b pb-2" style={{ borderColor: "#1a17141a" }}>
                <X className="h-5 w-5" style={{ color: MUTED }} /> {m}
              </li>
            ))}
          </ul>
          <div className="mt-6 inline-flex items-center gap-3 rounded-sm px-5 py-3" style={{ background: "#1a17140a" }}>
            <Clock className="h-7 w-7" style={{ color: MUTED }} />
            <span className="text-[40px]" style={{ fontFamily: SERIF, color: MUTED }}>3–6 часов</span>
          </div>
        </div>
        <div>
          <Eyebrow>Подход Б · Apex Stats</Eyebrow>
          <h3 className="mt-2 text-[36px]" style={{ fontFamily: SERIF, color: ACCENT }}>Автоматизированный анализ</h3>
          <ul className="mt-4 space-y-2 text-[18px]">
            {auto.map((m, i) => (
              <li key={i} className="flex items-center gap-3 border-b pb-2" style={{ borderColor: "#1a17141a" }}>
                <Check className="h-5 w-5" style={{ color: ACCENT }} /> {m}
              </li>
            ))}
          </ul>
          <div className="mt-6 flex gap-4">
            <div className="inline-flex items-center gap-3 rounded-sm px-5 py-3" style={{ background: `${ACCENT}15` }}>
              <Clock className="h-7 w-7" style={{ color: ACCENT }} />
              <span className="text-[36px]" style={{ fontFamily: SERIF, color: ACCENT }}>20–30 мин</span>
            </div>
            <div className="inline-flex items-center gap-3 rounded-sm px-5 py-3" style={{ background: `${ACCENT}15` }}>
              <Rocket className="h-7 w-7" style={{ color: ACCENT }} />
              <span className="text-[20px] font-semibold">Экономия 80–90% времени</span>
            </div>
          </div>
        </div>
      </div>
    </Page>
  );
}

/* ────────────────── 8. Tech stack ────────────────── */

function V2Slide8({ editing }: P) {
  const cols: [string, any, string[]][] = [
    ["Frontend", Monitor, ["Next.js","React","TypeScript","Tailwind CSS"]],
    ["Backend API", Server, ["NestJS","TypeScript","REST API","WebSocket"]],
    ["Сбор данных", CloudUpload, ["Node.js","TypeScript","Scheduler","BullMQ"]],
    ["Анализ (CV)", Eye, ["Python","OpenCV","NumPy","YOLO"]],
    ["База данных", Database, ["PostgreSQL","PostGIS","Prisma ORM"]],
    ["Инфраструктура", Server, ["Docker","Nginx","PM2","Ubuntu"]],
  ];
  const common: [any, string][] = [
    [ShieldCheck, "Zod (Validation)"],
    [Code2, "TypeScript Types"],
    [Code2, "ESLint / Prettier"],
    [GitBranch, "GitHub Actions (CI/CD)"],
  ];
  return (
    <Page num="08" total="12" eyebrow="Глава VIII · Стек"
      titleId="v2.s8.t" titleDefault="Технологический стек"
      subId="v2.s8.s" subDefault="Ключевые технологии и инструменты, на которых построена платформа Apex Stats."
      editing={editing}>
      <div className="grid grid-cols-6 gap-3">
        {cols.map(([t, Ic, items], i) => (
          <Card key={i}>
            <Ic className="h-7 w-7" style={{ color: ACCENT }} strokeWidth={1.5} />
            <div className="mt-3 text-[20px] font-semibold">{t}</div>
            <div className="mt-2 h-px w-8" style={{ background: ACCENT }} />
            <ul className="mt-3 space-y-1.5 text-[14px]">
              {items.map((it, j) => <li key={j}>· {it}</li>)}
            </ul>
          </Card>
        ))}
      </div>
      <div className="mt-10">
        <Eyebrow>Общие компоненты</Eyebrow>
        <div className="mt-3 grid grid-cols-4 gap-3">
          {common.map(([Ic, t], i) => (
            <Card key={i}>
              <div className="flex items-center gap-3">
                <Ic className="h-6 w-6" style={{ color: ACCENT }} strokeWidth={1.5} />
                <span className="text-[16px] font-semibold">{t}</span>
              </div>
            </Card>
          ))}
        </div>
      </div>
    </Page>
  );
}

/* ────────────────── 10. HSV ────────────────── */

function V2Slide10({ editing }: P) {
  const rows: [string, string, string, string, string][] = [
    ["#a173e8", "Team 1 (Фиолетовый)", "240 – 290", "60 – 255", "90 – 255"],
    ["#3b82f6", "Team 2 (Синий)",      "200 – 235", "70 – 255", "80 – 255"],
    ["#ef4444", "Team 3 (Красный)",    "0 – 15 / 345 – 360", "80 – 255", "90 – 255"],
    ["#22c55e", "Team 4 (Зелёный)",    "80 – 140", "60 – 255", "80 – 255"],
    ["#f59e0b", "Team 5 (Оранжевый)",  "15 – 35",  "80 – 255", "90 – 255"],
  ];
  const notes: [any, string, string][] = [
    [Pipette, "Пипетка", "Клик по карте определяет цвет команды и текущий HSV."],
    [Frame, "Бинарная маска", "Белые области — попадания, чёрные — фон."],
    [SlidersHorizontal, "HUE / SAT / VAL", "Тонкая настройка трёх каналов для точности."],
    [Save, "Пресеты", "Сохранённые диапазоны для каждой команды."],
  ];
  return (
    <Page num="10" total="12" eyebrow="Глава X · HSV"
      titleId="v2.s10.t" titleDefault="HSV — калибровка цвета команд"
      subId="v2.s10.s" subDefault="Оператор находит цвет команды пипеткой и уточняет диапазон через бинарную HSV-маску."
      editing={editing}>
      <div className="grid grid-cols-[2fr_1fr] gap-6">
        <div>
          <div className="grid grid-cols-2 gap-3">
            <Card>
              <Eyebrow>Оригинал</Eyebrow>
              <div className="mt-2 h-[220px] rounded-sm" style={{ background: "#ede4d2" }} />
            </Card>
            <Card>
              <Eyebrow>Маска HSV</Eyebrow>
              <div className="mt-2 h-[220px] rounded-sm" style={{ background: INK }} />
            </Card>
          </div>
          <Card>
            <Eyebrow>Назначенные пороги HSV</Eyebrow>
            <div className="mt-3 grid grid-cols-[24px_2fr_1.8fr_1.4fr_1.4fr] gap-2 pb-2 text-[11px] font-semibold uppercase tracking-[0.2em]" style={{ color: MUTED }}>
              <div>Цвет</div><div>Название</div><div>HUE</div><div>SAT</div><div>VAL</div>
            </div>
            {rows.map((r, i) => (
              <div key={i} className="mb-1 grid grid-cols-[24px_2fr_1.8fr_1.4fr_1.4fr] items-center gap-2 border-b pb-1.5 text-[14px]"
                style={{ borderColor: "#1a17141a" }}>
                <span className="h-4 w-4 rounded-full" style={{ background: r[0] }} />
                <span>{r[1]}</span>
                <span className="font-mono" style={{ color: ACCENT }}>{r[2]}</span>
                <span className="font-mono">{r[3]}</span>
                <span className="font-mono">{r[4]}</span>
              </div>
            ))}
          </Card>
        </div>
        <div className="space-y-3">
          {notes.map(([Ic, t, d], i) => (
            <Card key={i}>
              <div className="flex gap-3">
                <Ic className="h-7 w-7 shrink-0" style={{ color: ACCENT }} strokeWidth={1.5} />
                <div>
                  <E id={`v2.s10.n${i}.t`} defaultValue={t} editing={editing} className="text-[18px] font-semibold" />
                  <E id={`v2.s10.n${i}.d`} defaultValue={d} editing={editing}
                    className="mt-1 text-[13px]" multiline />
                </div>
              </div>
            </Card>
          ))}
        </div>
      </div>
    </Page>
  );
}

/* ────────────────── 11. ZONES ────────────────── */

function V2Slide11({ editing }: P) {
  const vars = ["mp_zone","t1_leftLeaderboard","t1_leftTeamColumn","t1_leftRow_01","t1_leftRow_02",
    "t1_rightLeaderboard","t1_rightTeamColumn","t1_rightRow_01","t1_rightRow_02","leader_bars","match_banner","ring_status_banner"];
  const notes: [any, string, string][] = [
    [Monitor, "Кадр 1920×1080", "Области HUD определяются на полном кадре."],
    [Crosshair, "Зоны HUD", "Мини-карта, таблицы, баннеры, служебные элементы."],
    [MousePointer2, "Визуальный выбор", "Прямо на кадре — клик и drag."],
    [Zap, "Скорость", "Сохраняйте и сразу применяйте в трансляции."],
  ];
  return (
    <Page num="11" total="12" eyebrow="Глава XI · ZONES"
      titleId="v2.s11.t" titleDefault="ZONES — зоны HUD"
      subId="v2.s11.s" subDefault="Оператор быстро определяет области интерфейса трансляции на кадре 1920×1080."
      editing={editing}>
      <div className="grid grid-cols-[2fr_1fr] gap-5">
        <Card>
          <Eyebrow>Зоны (только HUD)</Eyebrow>
          <div className="relative mt-3 h-[460px] rounded-sm" style={{ background: "#ede4d2" }}>
            <div className="absolute left-3 top-3 h-[430px] w-[130px] rounded-sm border-2" style={{ borderColor: ACCENT, background: `${ACCENT}10` }} />
            <div className="absolute right-3 top-3 h-[430px] w-[130px] rounded-sm border-2" style={{ borderColor: ACCENT, background: `${ACCENT}10` }} />
            <div className="absolute left-1/2 top-1/2 h-[300px] w-[300px] -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-dashed" style={{ borderColor: INK }} />
            <div className="absolute left-[150px] top-3 h-9 w-[160px] rounded-sm border-2" style={{ borderColor: INK, background: "#fff" }} />
            <div className="absolute bottom-3 left-1/2 h-8 w-[200px] -translate-x-1/2 rounded-sm border-2" style={{ borderColor: INK, background: "#fff" }} />
            <div className="absolute bottom-3 left-3 rounded-sm bg-white px-2 py-0.5 text-[11px] font-mono">1920 × 1080</div>
          </div>
        </Card>
        <Card>
          <Eyebrow>Переменные</Eyebrow>
          <ul className="mt-3 space-y-1 text-[13px] font-mono" style={{ color: INK }}>
            {vars.map((v, i) => (
              <li key={i} className="flex items-center justify-between border-b pb-1" style={{ borderColor: "#1a17141a" }}>
                <span style={{ color: ACCENT }}>{v}</span>
                <span className="text-xs uppercase tracking-[0.2em]" style={{ color: MUTED }}>Удалить</span>
              </li>
            ))}
          </ul>
        </Card>
      </div>
      <div className="mt-6 grid grid-cols-4 gap-4">
        {notes.map(([Ic, t, d], i) => (
          <div key={i} className="border-l-2 pl-4" style={{ borderColor: ACCENT }}>
            <Ic className="h-6 w-6" style={{ color: ACCENT }} strokeWidth={1.5} />
            <div className="mt-2 text-[18px] font-semibold">{t}</div>
            <div className="mt-1 text-[13px]" style={{ color: MUTED }}>{d}</div>
          </div>
        ))}
      </div>
    </Page>
  );
}

/* ────────────────── 12. CAMERA ────────────────── */

function V2Slide12({ editing }: P) {
  const notes: [any, string, string][] = [
    [SlidersHorizontal, "Параметры калибровки", "Турнир, матч, карта и настройки обработки."],
    [MapIcon, "Сайт / карта", "Эталон для расчёта смещений, масштаба и центра кольца."],
    [Video, "Видео трансляции", "Источник, сравниваемый с данными сайта."],
    [Activity, "Графики камеры", "Анализ скачков, шума и сглаживания."],
  ];
  const series: [string, string][] = [
    ["X: камера raw / см. – центр кольца", "176.0 … 699.0"],
    ["Y: камера raw / см. – центр кольца", "−37.1 … 1119.9"],
    ["Zoom ratio – effective (сайт)", "1.0 … 1.8"],
    ["Радиус кольца – zoomedRadius", "33.1 … 565.6"],
    ["moveDist – jumpScore", "−110.5 … 1951.6"],
  ];
  return (
    <Page num="12" total="12" eyebrow="Глава XII · CAMERA"
      titleId="v2.s12.t" titleDefault="CAMERA — калибровка камеры"
      subId="v2.s12.s" subDefault="Сравнение сайта, видеопотока и графиков скачков для точной калибровки движения камеры."
      editing={editing}>
      <div className="grid grid-cols-[2fr_1fr] gap-5">
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <Card>
              <Eyebrow>Сайт / карта (эталон)</Eyebrow>
              <div className="mt-2 h-[200px] rounded-sm" style={{ background: "#ede4d2" }} />
            </Card>
            <Card>
              <Eyebrow>Видео трансляции (источник)</Eyebrow>
              <div className="mt-2 h-[200px] rounded-sm" style={{ background: "#ede4d2" }} />
            </Card>
          </div>
          <Card>
            <Eyebrow>Графики камеры: сравнение и анализ скачков</Eyebrow>
            <div className="mt-3 space-y-2">
              {series.map(([lab, range], i) => (
                <div key={i} className="grid grid-cols-[1fr_auto] items-center gap-3 border-b pb-2" style={{ borderColor: "#1a17141a" }}>
                  <div className="flex items-center gap-3 text-[13px]">
                    <span style={{ color: MUTED }}>{lab}</span>
                    <svg viewBox="0 0 200 20" className="ml-auto h-5 w-40" style={{ color: ACCENT }}>
                      <polyline fill="none" stroke="currentColor" strokeWidth="1" points="0,10 20,8 40,12 60,6 80,14 100,4 120,10 140,9 160,11 180,7 200,10" />
                    </svg>
                  </div>
                  <span className="font-mono text-[11px]" style={{ color: MUTED }}>{range}</span>
                </div>
              ))}
            </div>
          </Card>
        </div>
        <div className="space-y-3">
          {notes.map(([Ic, t, d], i) => (
            <Card key={i}>
              <div className="flex gap-3">
                <Ic className="h-7 w-7 shrink-0" style={{ color: ACCENT }} strokeWidth={1.5} />
                <div>
                  <E id={`v2.s12.n${i}.t`} defaultValue={t} editing={editing} className="text-[18px] font-semibold" />
                  <E id={`v2.s12.n${i}.d`} defaultValue={d} editing={editing}
                    className="mt-1 text-[13px]" multiline />
                </div>
              </div>
            </Card>
          ))}
        </div>
      </div>
    </Page>
  );
}

/* ────────────────── Registry ────────────────── */

export type V2Slide = { id: string; title: string; subtitle: string; Component: (p: P) => ReactElement };

export const V2_SLIDES: V2Slide[] = [
  { id: "01", title: "Архитектура верхнего уровня", subtitle: "Как данные превращаются в аналитику.", Component: V2Slide1 },
  { id: "02", title: "Поток данных", subtitle: "От VOD до интерактивной аналитики.", Component: V2Slide2 },
  { id: "03", title: "Конвейер компьютерного зрения", subtitle: "Семь шагов CV-обработки кадра.", Component: V2Slide3 },
  { id: "04", title: "Пользовательский сценарий", subtitle: "От матча до инсайтов.", Component: V2Slide4 },
  { id: "05", title: "Предметная модель", subtitle: "Сущности и связи системы.", Component: V2Slide5 },
  { id: "06", title: "ER-диаграмма базы данных", subtitle: "Структура таблиц и связей.", Component: V2Slide6 },
  { id: "07", title: "Ручной vs автоматизированный", subtitle: "Сравнение подходов к разбору матча.", Component: V2Slide7 },
  { id: "08", title: "Технологический стек", subtitle: "Ключевые технологии платформы.", Component: V2Slide8 },
  { id: "09", title: "Сценарий — детали", subtitle: "Подробный разбор дашборда.", Component: V2Slide9 },
  { id: "10", title: "HSV — калибровка цвета", subtitle: "Пипетка и бинарная маска.", Component: V2Slide10 },
  { id: "11", title: "ZONES — зоны HUD", subtitle: "Разметка интерфейса трансляции.", Component: V2Slide11 },
  { id: "12", title: "CAMERA — калибровка камеры", subtitle: "Сравнение сайта и видеопотока.", Component: V2Slide12 },
];