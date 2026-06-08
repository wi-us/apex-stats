import type { ComponentType, ReactNode } from "react";
import {
  LayoutGrid, Pipette, Frame, SlidersHorizontal, Palette, Save, Image as ImageIcon,
  Monitor, Crosshair, Zap, MousePointer2, ShieldCheck, Video, Activity,
  Map as MapIcon, Play,
} from "lucide-react";
import { EditableText } from "./EditableText";
import { SlideCanvas, SlideHeader } from "./SlideCanvas";

type SlideProps = { editing: boolean };

function Block({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div
      className={
        "rounded-2xl border border-border bg-surface/60 backdrop-blur-sm shadow-[0_8px_30px_-12px_rgba(0,0,0,0.6)] " +
        (className ?? "")
      }
    >
      {children}
    </div>
  );
}

function SideNote({ id, Icon, title, body, editing }: {
  id: string; Icon: ComponentType<{ className?: string; strokeWidth?: number }>;
  title: string; body: string; editing: boolean;
}) {
  return (
    <Block className="p-4">
      <div className="mb-2 flex items-center gap-2">
        <Icon className="h-6 w-6 text-cyan" strokeWidth={1.7} />
        <EditableText id={id + ".t"} defaultValue={title} editing={editing}
          className="text-[18px] font-bold text-primary" />
      </div>
      <EditableText id={id + ".d"} defaultValue={body} editing={editing}
        className="text-[14px] leading-snug text-muted-foreground" multiline />
    </Block>
  );
}

/* ────────────────────────── 10. HSV ────────────────────────── */

export function Slide10({ editing }: SlideProps) {
  const rows: [string, string, string, string, string][] = [
    ["#a173e8", "Team 1 (Фиолетовый)", "240 – 290", "60 – 255", "90 – 255"],
    ["#3b82f6", "Team 2 (Синий)",      "200 – 235", "70 – 255", "80 – 255"],
    ["#ef4444", "Team 3 (Красный)",    "0 – 15 / 345 – 360", "80 – 255", "90 – 255"],
    ["#22c55e", "Team 4 (Зелёный)",    "80 – 140", "60 – 255", "80 – 255"],
    ["#f59e0b", "Team 5 (Оранжевый)",  "15 – 35",  "80 – 255", "90 – 255"],
  ];
  return (
    <SlideCanvas>
      <SlideHeader
        titleId="s10.title" titleDefault="HSV — калибровка цвета команд"
        subtitleId="s10.sub" subtitleDefault="Оператор быстро находит цвет команды пипеткой и уточняет диапазон через бинарную HSV-маску."
        editing={editing}
      />
      <div className="mt-12 grid grid-cols-[1fr_2.4fr_1fr] gap-5 px-14">
        <div className="space-y-3">
          <SideNote id="s10.l1" Icon={LayoutGrid} title="Левая панель" body="Выбор режима HSV, список карт, загрузка изображения и настройка диапазонов." editing={editing} />
          <SideNote id="s10.l2" Icon={Pipette}    title="Пипетка и выбор цвета" body="Клик по карте определяет цвет команды и отображает текущий HSV-цвет." editing={editing} />
          <SideNote id="s10.l3" Icon={Frame}      title="Бинарная HSV-маска" body="Белые области — попадания в диапазон, чёрные — фон." editing={editing} />
          <SideNote id="s10.l4" Icon={SlidersHorizontal} title="Диапазоны HUE / SAT / VAL" body="Тонкая настройка трёх каналов для точного выделения нужного цвета." editing={editing} />
        </div>

        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <Block className="p-3">
              <div className="mb-2 flex items-center justify-between">
                <span className="text-[12px] font-bold uppercase tracking-wider text-muted-foreground">Оригинал</span>
                <span className="text-[11px] font-mono text-muted-foreground">e-district</span>
              </div>
              <div className="h-[260px] rounded-md border border-border bg-surface-2/40 hud-grid-bg" />
            </Block>
            <Block className="p-3">
              <div className="mb-2 flex items-center justify-between">
                <span className="text-[12px] font-bold uppercase tracking-wider text-muted-foreground">Маска HSV</span>
                <span className="text-[11px] font-mono text-cyan">live</span>
              </div>
              <div className="h-[260px] rounded-md border border-border bg-foreground/90" />
            </Block>
          </div>

          <Block className="p-4">
            <div className="mb-3 text-[14px] font-bold uppercase tracking-wider text-muted-foreground">Назначенные пороги HSV</div>
            <div className="grid grid-cols-[24px_2fr_1.8fr_1.4fr_1.4fr] gap-2 px-2 pb-2 text-[12px] font-bold uppercase tracking-wider text-muted-foreground">
              <div>Цвет</div><div>Название</div><div>HUE</div><div>Saturation</div><div>Value</div>
            </div>
            {rows.map((r, i) => (
              <div key={i} className="mb-1.5 grid grid-cols-[24px_2fr_1.8fr_1.4fr_1.4fr] items-center gap-2 rounded-md border border-border bg-surface-2/40 px-2 py-2 text-[15px]">
                <span className="h-4 w-4 rounded-full" style={{ background: r[0] }} />
                <EditableText id={`s10.row.${i}.n`} defaultValue={r[1]} editing={editing} />
                <EditableText id={`s10.row.${i}.h`} defaultValue={r[2]} editing={editing} className="font-mono text-cyan" />
                <EditableText id={`s10.row.${i}.s`} defaultValue={r[3]} editing={editing} className="font-mono" />
                <EditableText id={`s10.row.${i}.v`} defaultValue={r[4]} editing={editing} className="font-mono" />
              </div>
            ))}
          </Block>
        </div>

        <div className="space-y-3">
          <SideNote id="s10.r1" Icon={ImageIcon} title="Оригинал" body="Исходный кадр или мини-карта. Используется для выбора цвета пипеткой." editing={editing} />
          <SideNote id="s10.r2" Icon={Frame}     title="Маска HSV" body="Бинарное представление диапазона HSV. Белые — совпадения, чёрные — фон." editing={editing} />
          <SideNote id="s10.r3" Icon={Palette}   title="HSV-настройки" body="Точная настройка HUE / SAT / VAL минимизирует шум выделения." editing={editing} />
          <SideNote id="s10.r4" Icon={Save}      title="Пресеты порогов" body="Сохранённые диапазоны для команд. Быстрый выбор, замена и удаление." editing={editing} />
        </div>
      </div>
    </SlideCanvas>
  );
}

/* ────────────────────────── 11. ZONES ────────────────────────── */

export function Slide11({ editing }: SlideProps) {
  const variables = [
    "mp_zone","t1_leftLeaderboard","t1_leftRankHeader","t1_leftTeamColumn","t1_leftPointsColumn",
    "t1_leftRow_01","t1_leftRow_02","t1_leftRow_03","t1_leftRow_04",
    "t1_rightLeaderboard","t1_rightRankHeader","t1_rightTeamColumn","t1_rightPointsColumn",
    "t1_rightRow_01","t1_rightRow_02","t1_rightRow_03","t1_rightRow_04",
    "leader_bars","match_banner","ring_status_banner",
  ];
  return (
    <SlideCanvas>
      <SlideHeader
        titleId="s11.title" titleDefault="ZONES — зоны HUD"
        subtitleId="s11.sub" subtitleDefault="Оператор быстро определяет области интерфейса трансляции, которые нужно отслеживать на кадре 1920×1080."
        editing={editing}
      />
      <div className="mt-10 grid grid-cols-[1fr_2.6fr_1.2fr] gap-5 px-14">
        <div className="space-y-3">
          <SideNote id="s11.l1" Icon={LayoutGrid} title="Левая панель" body="Режим, список карт, загрузка источников и управление зонами." editing={editing} />
          <SideNote id="s11.l2" Icon={Monitor}    title="Кадр 1920×1080" body="Области HUD определяются на полном кадре трансляции." editing={editing} />
          <SideNote id="s11.l3" Icon={Crosshair}  title="Зоны HUD" body="Области мини-карты, таблиц, баннеров и служебных элементов." editing={editing} />
          <SideNote id="s11.l4" Icon={Zap}        title="Быстрый рабочий процесс" body="Добавьте или измените зоны за секунды — сохраняйте и сразу применяйте в трансляции." editing={editing} />
        </div>

        <Block className="p-4">
          <div className="mb-3 flex items-center justify-between">
            <span className="text-[14px] font-bold uppercase tracking-wider text-muted-foreground">Зоны (только HUD)</span>
            <span className="text-[11px] font-mono text-muted-foreground">Add · Edit · Delete</span>
          </div>
          <div className="relative h-[540px] rounded-md border border-border bg-surface-2/30 hud-grid-bg">
            <div className="absolute left-3 top-3 h-[510px] w-[150px] rounded-md border-2 border-primary/70 bg-primary/10" />
            <div className="absolute right-3 top-3 h-[510px] w-[150px] rounded-md border-2 border-primary/70 bg-primary/10" />
            <div className="absolute left-1/2 top-1/2 h-[360px] w-[360px] -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-cyan/60" />
            <div className="absolute left-[175px] top-3 h-10 w-[180px] rounded-md border-2 border-warning/70 bg-warning/10" />
            <div className="absolute bottom-3 left-1/2 h-9 w-[220px] -translate-x-1/2 rounded-md border-2 border-cyan/70 bg-cyan/10" />
            <div className="absolute left-3 bottom-3 rounded bg-background/80 px-2 py-1 text-[11px] font-mono">1920 × 1080</div>
          </div>
        </Block>

        <Block className="p-3">
          <div className="mb-2 grid grid-cols-[1.4fr_1fr_0.8fr] gap-2 px-2 text-[11px] font-bold uppercase tracking-wider text-muted-foreground">
            <div>Переменная</div><div>Описание</div><div className="text-right">Действие</div>
          </div>
          <div className="max-h-[540px] overflow-hidden">
            {variables.map((v, i) => (
              <div key={i} className="mb-1 grid grid-cols-[1.4fr_1fr_0.8fr] items-center gap-2 rounded-md border border-border bg-surface-2/40 px-2 py-1.5 text-[12px]">
                <span className="font-mono text-cyan">{v}</span>
                <span className="text-muted-foreground">—</span>
                <span className="text-right text-xs font-bold uppercase tracking-wider text-destructive">Удалить</span>
              </div>
            ))}
          </div>
        </Block>
      </div>

      <div className="mx-14 mt-6">
        <Block className="grid grid-cols-[1.4fr_1fr_1fr_1fr] items-center gap-4 px-5 py-4">
          <div className="flex items-center gap-3">
            <Zap className="h-7 w-7 text-primary" />
            <div>
              <EditableText id="s11.fast.t" defaultValue="Скорость и точность" editing={editing}
                className="text-[18px] font-bold text-primary" />
              <EditableText id="s11.fast.d" defaultValue="Зоны HUD настраиваются за секунды и сразу применяются." editing={editing}
                className="text-[13px] text-muted-foreground" multiline />
            </div>
          </div>
          {[
            [MousePointer2, "Визуальный выбор зон прямо на кадре"],
            [Save, "Сохранение набора зон для повторного использования"],
            [ShieldCheck, "Стабильное отслеживание ключевых областей HUD"],
          ].map((row, i) => {
            const Ic = row[0] as ComponentType<{ className?: string; strokeWidth?: number }>;
            return (
              <div key={i} className="flex items-center gap-2 text-[14px]">
                <Ic className="h-5 w-5 text-cyan" strokeWidth={1.8} />
                <span>{row[1] as string}</span>
              </div>
            );
          })}
        </Block>
      </div>
    </SlideCanvas>
  );
}

/* ────────────────────────── 12. CAMERA ────────────────────────── */

export function Slide12({ editing }: SlideProps) {
  return (
    <SlideCanvas>
      <SlideHeader
        titleId="s12.title" titleDefault="CAMERA — калибровка камеры"
        subtitleId="s12.sub" subtitleDefault="Инструмент для калибровки перемещения камеры трансляции: сравнение сайта, видеопотока и графиков скачков."
        editing={editing}
      />
      <div className="mt-10 grid grid-cols-[1fr_3.6fr_1fr] gap-5 px-14">
        <div className="space-y-3">
          <SideNote id="s12.l1" Icon={SlidersHorizontal} title="Левая панель" body="Выбор режима CAMERA и параметры калибровки: турнир, матч, карта и настройки обработки." editing={editing} />
          <SideNote id="s12.l2" Icon={MapIcon}   title="Сайт / карта" body="Эталонная область. Используется для расчёта смещений, масштаба и центра кольца." editing={editing} />
          <SideNote id="s12.l3" Icon={Activity}  title="Графики камеры" body="Сравнение «сырых» данных сайта и видео, анализ скачков, шума и сглаживания." editing={editing} />
        </div>

        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <Block className="p-3">
              <div className="mb-2 text-[12px] font-bold uppercase tracking-wider text-muted-foreground">Сайт / карта (эталон)</div>
              <div className="h-[260px] rounded-md border border-border bg-surface-2/40 hud-grid-bg" />
            </Block>
            <Block className="p-3">
              <div className="mb-2 text-[12px] font-bold uppercase tracking-wider text-muted-foreground">Видео трансляции (источник)</div>
              <div className="h-[260px] rounded-md border border-border bg-surface-2/40 hud-grid-bg" />
            </Block>
          </div>

          <Block className="p-3">
            <div className="flex items-center gap-2 text-[12px] font-mono text-muted-foreground">
              <Play className="h-4 w-4 text-primary" /> 00:00 / 10:25
              <div className="relative ml-3 h-1 flex-1 rounded-full bg-border">
                {[20, 35, 55, 70, 88].map((p, i) => (
                  <div key={i} className="absolute -top-1 h-3 w-3 -translate-x-1/2 rounded-full bg-primary" style={{ left: `${p}%` }} />
                ))}
              </div>
            </div>
          </Block>

          <Block className="p-3">
            <div className="mb-2 flex items-center justify-between">
              <span className="text-[12px] font-bold uppercase tracking-wider text-muted-foreground">Графики камеры: сравнение и анализ скачков</span>
              <div className="flex gap-2 text-xs">
                <span className="rounded bg-primary/15 px-2 py-0.5 text-primary">Step zoom</span>
                <span className="rounded bg-surface-2 px-2 py-0.5 text-muted-foreground">Шум кольца</span>
                <span className="rounded bg-surface-2 px-2 py-0.5 text-muted-foreground">Баланс</span>
              </div>
            </div>
            <div className="space-y-2">
              {[
                ["X: камера raw / см. – центр кольца", "176.0 … 699.0"],
                ["Y: камера raw / см. – центр кольца", "−37.1 … 1119.9"],
                ["Zoom ratio – effective (сайт)", "1.0 … 1.8"],
                ["Радиус кольца – zoomedRadius", "33.1 … 565.6"],
                ["moveDist – jumpScore", "−110.5 … 1951.6"],
              ].map(([lab, range], i) => (
                <div key={i} className="grid grid-cols-[1fr_auto] items-center gap-3">
                  <div className="flex h-[36px] items-center rounded-md border border-border bg-surface-2/30 px-3 text-[13px] text-muted-foreground">
                    <span className="mr-3 truncate">{lab}</span>
                    <svg viewBox="0 0 200 20" className="ml-auto h-5 w-40 text-cyan">
                      <polyline fill="none" stroke="currentColor" strokeWidth="1" points="0,10 20,8 40,12 60,6 80,14 100,4 120,10 140,9 160,11 180,7 200,10" />
                    </svg>
                  </div>
                  <span className="font-mono text-[11px] text-muted-foreground">{range}</span>
                </div>
              ))}
            </div>
          </Block>
        </div>

        <div className="space-y-3">
          <SideNote id="s12.r1" Icon={Video}    title="Видео трансляции" body="Сопоставляемый источник. Сравнивается с данными сайта для выявления смещений." editing={editing} />
          <SideNote id="s12.r2" Icon={Play}     title="Таймлайн" body="Навигация по раундам и времени. Маркеры ключевых событий ускоряют переход." editing={editing} />
          <SideNote id="s12.r3" Icon={Activity} title="Анализ скачков" body="Пороги смещения, шума, устойчивости и пропуска старта кольца." editing={editing} />
        </div>
      </div>
    </SlideCanvas>
  );
}