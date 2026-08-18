import type { Point } from "@/domain/types";

/** Map scale: metres represented by one map unit. Used for all speed maths. */
export const METRES_PER_UNIT = 10;

export function kmhToUnitsPerSec(kmh: number): number {
  return (kmh * 1000) / 3600 / METRES_PER_UNIT;
}

export function unitsToKm(units: number): number {
  return (units * METRES_PER_UNIT) / 1000;
}

export function dist(a: Point, b: Point): number {
  return Math.hypot(b.x - a.x, b.y - a.y);
}

export function pathLength(path: Point[]): number {
  let total = 0;
  for (let i = 1; i < path.length; i++) total += dist(path[i - 1]!, path[i]!);
  return total;
}

export function cumulative(path: Point[]): number[] {
  const acc = [0];
  for (let i = 1; i < path.length; i++) acc.push(acc[i - 1]! + dist(path[i - 1]!, path[i]!));
  return acc;
}

export function pointAt(path: Point[], s: number): Point {
  if (path.length === 0) return { x: 0, y: 0 };
  const acc = cumulative(path);
  const total = acc[acc.length - 1]!;
  const d = Math.max(0, Math.min(total, s));
  for (let i = 1; i < path.length; i++) {
    if (d <= acc[i]!) {
      const prev = path[i - 1]!;
      const next = path[i]!;
      const seg = acc[i]! - acc[i - 1]! || 1;
      const t = (d - acc[i - 1]!) / seg;
      return {
        x: prev.x + (next.x - prev.x) * t,
        y: prev.y + (next.y - prev.y) * t,
      };
    }
  }
  return path[path.length - 1]!;
}

/** Heading in degrees at distance s along the path (0 = +x). */
export function headingAt(path: Point[], s: number): number {
  const a = pointAt(path, Math.max(0, s - 4));
  const b = pointAt(path, s + 4);
  return (Math.atan2(b.y - a.y, b.x - a.x) * 180) / Math.PI;
}

/** Slice of the path between two distances, as a renderable polyline. */
export function pathSlice(path: Point[], from: number, to: number): Point[] {
  const acc = cumulative(path);
  const total = acc[acc.length - 1]!;
  const a = Math.max(0, Math.min(total, from));
  const b = Math.max(0, Math.min(total, to));
  if (b <= a) return [pointAt(path, a)];
  const out: Point[] = [pointAt(path, a)];
  for (let i = 0; i < path.length; i++) {
    if (acc[i]! > a && acc[i]! < b) out.push(path[i]!);
  }
  out.push(pointAt(path, b));
  return out;
}

export function toPolyline(points: Point[]): string {
  return points.map((p) => `${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" ");
}

/**
 * Distance along `path` at which it comes closest to `target`, or null when it
 * never enters `radius`. Used to derive which resources a route consumes.
 */
export function crossingDistance(path: Point[], target: Point, radius: number): number | null {
  const acc = cumulative(path);
  let best: { d: number; s: number } | null = null;
  for (let i = 1; i < path.length; i++) {
    const a = path[i - 1]!;
    const b = path[i]!;
    const vx = b.x - a.x;
    const vy = b.y - a.y;
    const len2 = vx * vx + vy * vy;
    const t =
      len2 === 0
        ? 0
        : Math.max(0, Math.min(1, ((target.x - a.x) * vx + (target.y - a.y) * vy) / len2));
    const px = a.x + vx * t;
    const py = a.y + vy * t;
    const d = Math.hypot(target.x - px, target.y - py);
    if (!best || d < best.d) best = { d, s: acc[i - 1]! + Math.sqrt(len2) * t };
  }
  if (!best || best.d > radius) return null;
  return best.s;
}
