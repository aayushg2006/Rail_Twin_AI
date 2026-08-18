import type { TrainType } from "@/domain/types";

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

export const trainTypeLabel: Record<TrainType, string> = {
  EXPRESS: "EXP",
  PASSENGER: "PASS",
  LOCAL: "LOC",
  MEMU: "MEMU",
  FREIGHT: "GOODS",
  SHUNT: "SHUNT",
};

export function conflictKindLabel(kind: string): string {
  return kind
    .split("_")
    .map((w) => w.charAt(0) + w.slice(1).toLowerCase())
    .join(" ");
}
