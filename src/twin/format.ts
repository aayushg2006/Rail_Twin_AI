export function mmss(totalSec: number): string {
  const s = Math.max(0, Math.round(totalSec));
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${String(m).padStart(2, "0")}:${String(r).padStart(2, "0")}`;
}

export function signedMin(sec: number): string {
  const m = sec / 60;
  const sign = m > 0.05 ? "+" : m < -0.05 ? "" : "";
  return `${sign}${m.toFixed(1)} min`;
}

export function clockOf(epochMs: number): string {
  const d = new Date(epochMs);
  return d.toLocaleTimeString("en-GB", { hour12: false, timeZone: "Asia/Kolkata" });
}

export function clockShort(epochMs: number): string {
  const d = new Date(epochMs);
  return d.toLocaleTimeString("en-GB", {
    hour12: false,
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "Asia/Kolkata",
  });
}

export function dateOf(epochMs: number): string {
  return new Date(epochMs).toLocaleDateString("en-GB", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    timeZone: "Asia/Kolkata",
  });
}

export const trainTypeLabel: Record<string, string> = {
  EXPRESS: "EXP",
  PASSENGER: "PASS",
  LOCAL: "LOCAL",
  MEMU: "MEMU",
  FREIGHT: "GOODS",
  SHUNT: "SHUNT",
};

/** Seconds -> "4.2 min late" / "1.1 min early" / "on time". */
export function lateness(sec: number): string {
  const m = sec / 60;
  if (Math.abs(m) < 0.5) return "on time";
  return m > 0 ? `${m.toFixed(1)} min late` : `${Math.abs(m).toFixed(1)} min early`;
}

/** Seconds until something happens, in words a controller would use. */
export function countdown(sec: number): string {
  const s = Math.max(0, Math.round(sec));
  if (s < 60) return `in ${s}s`;
  const m = Math.floor(s / 60);
  const r = s % 60;
  return r === 0 ? `in ${m} min` : `in ${m} min ${r}s`;
}

export function minutes(sec: number): string {
  return `${(sec / 60).toFixed(1)} min`;
}

export function compactNumber(n: number): string {
  if (Math.abs(n) >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return n.toFixed(0);
}

export function conflictKindLabel(kind: string): string {
  return kind
    .split("_")
    .map((w) => w.charAt(0) + w.slice(1).toLowerCase())
    .join(" ");
}
