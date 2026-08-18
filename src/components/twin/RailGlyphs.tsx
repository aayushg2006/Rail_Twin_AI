import type { AmenityKind } from "@/twin/topology";

/**
 * Purpose-drawn railway glyphs. No generic icon set — every symbol here is
 * either a signalling object or a station amenity from the station plan.
 */

export function AmenityGlyph({ kind, size = 11 }: { kind: AmenityKind; size?: number }) {
  const s = size;
  const c = s / 2;
  const stroke = "currentColor";
  switch (kind) {
    case "TICKET":
      return (
        <g>
          <rect x={-c} y={-c * 0.7} width={s} height={s * 0.7} fill="none" stroke={stroke} strokeWidth={1} />
          <line x1={-c + 2} y1={0} x2={c - 2} y2={0} stroke={stroke} strokeWidth={1} />
        </g>
      );
    case "RESERVATION":
      return (
        <g>
          <rect x={-c} y={-c * 0.8} width={s} height={s * 0.8} fill="none" stroke={stroke} strokeWidth={1} />
          <line x1={-c + 2} y1={-1.5} x2={c - 2} y2={-1.5} stroke={stroke} strokeWidth={1} />
          <line x1={-c + 2} y1={1.5} x2={1} y2={1.5} stroke={stroke} strokeWidth={1} />
        </g>
      );
    case "POLICE":
      return (
        <g>
          <path d={`M0 ${-c} L${c} ${-c * 0.3} L0 ${c} L${-c} ${-c * 0.3} Z`} fill="none" stroke={stroke} strokeWidth={1} />
        </g>
      );
    case "WATER":
      return (
        <g>
          <path d={`M0 ${-c} C ${c} 0 ${c * 0.6} ${c} 0 ${c} C ${-c * 0.6} ${c} ${-c} 0 0 ${-c} Z`} fill="none" stroke={stroke} strokeWidth={1} />
        </g>
      );
    case "TOILET":
      return (
        <g>
          <circle cx={-2.5} cy={-c * 0.55} r={1.3} fill={stroke} />
          <path d={`M-2.5 ${-c * 0.2} v${s * 0.5}`} stroke={stroke} strokeWidth={1} />
          <circle cx={2.5} cy={-c * 0.55} r={1.3} fill={stroke} />
          <path d={`M2.5 ${-c * 0.2} v${s * 0.5} M0.8 ${c * 0.6} h3.4`} stroke={stroke} strokeWidth={1} fill="none" />
        </g>
      );
    case "LIFT":
      return (
        <g>
          <rect x={-c} y={-c} width={s} height={s} fill="none" stroke={stroke} strokeWidth={1} />
          <path d={`M-2 -1 l2 -2.5 l2 2.5 M-2 1 l2 2.5 l2 -2.5`} fill="none" stroke={stroke} strokeWidth={1} />
        </g>
      );
    case "ESCALATOR":
      return (
        <g>
          <path d={`M${-c} ${c * 0.7} L${-c * 0.1} ${-c * 0.2} L${c} ${-c * 0.2}`} fill="none" stroke={stroke} strokeWidth={1} />
          <path d={`M${-c * 0.4} ${c * 0.7} h${s * 0.5}`} stroke={stroke} strokeWidth={1} />
        </g>
      );
  }
}

export const amenityLabel: Record<AmenityKind, string> = {
  TICKET: "Ticket window",
  RESERVATION: "Reservation office",
  POLICE: "Railway police post",
  WATER: "Drinking water",
  TOILET: "Toilets",
  LIFT: "Lift",
  ESCALATOR: "Escalator",
};

export function SignalGlyph({
  aspect,
  facing,
}: {
  aspect: "GREEN" | "YELLOW" | "RED";
  facing: number;
}) {
  const fill =
    aspect === "GREEN" ? "var(--ok)" : aspect === "YELLOW" ? "var(--warning)" : "var(--critical)";
  return (
    <g transform={`rotate(${facing})`}>
      <line x1={0} y1={0} x2={0} y2={-9} stroke="var(--border-strong)" strokeWidth={1.2} />
      <circle cx={0} cy={-12} r={3.1} fill={fill} stroke="var(--map-deep)" strokeWidth={0.8} />
    </g>
  );
}

export function CompassRose() {
  return (
    <g>
      <circle r={20} fill="none" stroke="var(--border)" strokeWidth={1} />
      <path d="M0 -18 L4 0 L0 18 L-4 0 Z" fill="var(--border-strong)" />
      <path d="M0 -18 L4 0 L-4 0 Z" fill="var(--muted-foreground)" />
      <text y={-24} textAnchor="middle" fontSize={9} fill="var(--muted-foreground)" fontFamily="var(--font-cond)">
        N
      </text>
    </g>
  );
}
