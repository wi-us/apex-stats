import type { ReactNode } from "react";

type ActionBtnProps = { icon: ReactNode; label: string; onClick: () => void };
export function ActionBtn({ icon, label, onClick }: ActionBtnProps) {
  return (
    <button onClick={onClick}
      className="flex flex-col items-center gap-0.5 rounded-sm border border-border bg-surface-2 px-1 py-1.5 text-xs font-semibold uppercase tracking-wider hover:bg-muted">
      {icon}
      {label}
    </button>
  );
}

type FieldProps = { label: string; value: string; onChange: (v: string) => void };
export function Field({ label, value, onChange }: FieldProps) {
  return (
    <label className="mb-2 block">
      <span className="label-eyebrow mb-1 block text-xs">{label}</span>
      <input value={value} onChange={(e) => onChange(e.target.value)}
        className="w-full rounded-sm border border-border bg-background px-2 py-1.5 text-xs outline-none focus:border-primary/60" />
    </label>
  );
}

type NumFieldProps = { label: string; value: number; onChange: (v: number) => void };
export function NumField({ label, value, onChange }: NumFieldProps) {
  return (
    <label className="block">
      <span className="label-eyebrow mb-1 block text-xs">{label}</span>
      <input type="number" value={value} onChange={(e) => onChange(+e.target.value || 0)}
        className="text-mono w-full rounded-sm border border-border bg-background px-2 py-1.5 text-xs tabular-nums outline-none focus:border-primary/60" />
    </label>
  );
}
