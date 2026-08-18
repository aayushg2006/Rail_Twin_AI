/**
 * Vasai Road Junction — operational topology.
 *
 * Geometry is a schematic of the station diagram: along-track axis is X
 * (left = toward Churchgate / Mumbai, right = toward Dahanu Road / Surat),
 * cross-track axis is Y (top = WEST side, bottom = EAST side), matching the
 * station plan rotated a quarter turn so it reads on a control-room display.
 *
 * PF1-PF5 are Western line faces. PF6/PF7 are the eastern island working the
 * long-distance expresses and the Vasai Road - Diva branch.
 */
import type {
  Junction,
  Platform,
  Point,
  RailRoute,
  Resource,
  SignalPost,
  StationTick,
  Track,
} from "@/domain/types";
import { crossingDistance } from "./geometry";

export const MAP_W = 1620;
export const MAP_H = 940;

export const STATION_X0 = 480;
export const STATION_X1 = 1080;
export const PLATFORM_X0 = 505;
export const PLATFORM_X1 = 1055;
/** Nominal stopping point of a train on any platform. */
export const STOP_X = 790;

export const LINE_Y = {
  WSD: 210,
  WSU: 283,
  WFD: 353,
  WFU: 426,
  THD: 496,
  THU: 545,
  EX1: 620,
  EX2: 690,
  FRC: 735,
} as const;

const R = (x: number, y: number): Point => ({ x, y });

/** Shared branch geometry from the turnout out toward Diva (double line). */
const BRANCH_OUT: Point[] = [R(400, 780), R(330, 830), R(250, 862), R(20, 880)];
const BRANCH_IN: Point[] = [R(20, 898), R(250, 880), R(340, 846), R(380, 800)];
/** Approach from the eastern platform lines down into the branch turnout. */
const EX1_TO_JB: Point[] = [R(430, 620), R(410, 680), R(400, 730), R(400, 780)];
const JB_TO_EX2: Point[] = [R(380, 800), R(378, 740), R(392, 706), R(430, 690)];
const CHORD_TO_JB: Point[] = [R(1600, LINE_Y.FRC), R(520, LINE_Y.FRC), R(450, 752), R(410, 770), R(400, 780)];

export const tracks: Track[] = [
  {
    id: "T-WSD",
    name: "Western Slow DN",
    kind: "WESTERN_SLOW",
    direction: "DOWN",
    path: [R(1600, LINE_Y.WSD), R(20, LINE_Y.WSD)],
  },
  {
    id: "T-WSU",
    name: "Western Slow UP",
    kind: "WESTERN_SLOW",
    direction: "UP",
    path: [R(20, LINE_Y.WSU), R(1600, LINE_Y.WSU)],
  },
  {
    id: "T-WFD",
    name: "Western Fast DN",
    kind: "WESTERN_FAST",
    direction: "DOWN",
    path: [R(1600, LINE_Y.WFD), R(20, LINE_Y.WFD)],
  },
  {
    id: "T-WFU",
    name: "Western Fast UP",
    kind: "WESTERN_FAST",
    direction: "UP",
    path: [R(20, LINE_Y.WFU), R(1600, LINE_Y.WFU)],
  },
  {
    id: "T-THD",
    name: "Through DN",
    kind: "WESTERN_FAST",
    direction: "DOWN",
    through: true,
    path: [R(1600, LINE_Y.THD), R(20, LINE_Y.THD)],
  },
  {
    id: "T-THU",
    name: "Through UP",
    kind: "WESTERN_FAST",
    direction: "UP",
    through: true,
    path: [R(20, LINE_Y.THU), R(1600, LINE_Y.THU)],
  },
  {
    id: "T-EX1",
    name: "East Line 1 (Diva bound)",
    kind: "DIVA_BRANCH",
    direction: "BRANCH_OUT",
    through: true,
    path: [R(1600, LINE_Y.EX1), ...EX1_TO_JB],
  },
  {
    id: "T-EX2",
    name: "East Line 2 (Vasai bound)",
    kind: "DIVA_BRANCH",
    direction: "BRANCH_IN",
    through: true,
    path: [...JB_TO_EX2, R(1600, LINE_Y.EX2)],
  },
  {
    id: "T-BR",
    name: "Vasai Road - Diva branch (Diva bound)",
    kind: "DIVA_BRANCH",
    direction: "BRANCH_OUT",
    through: true,
    path: BRANCH_OUT,
  },
  {
    id: "T-BR2",
    name: "Vasai Road - Diva branch (Vasai bound)",
    kind: "DIVA_BRANCH",
    direction: "BRANCH_IN",
    through: true,
    path: BRANCH_IN,
  },
  {
    id: "T-FRC",
    name: "Goods chord",
    kind: "FREIGHT_CHORD",
    direction: "BRANCH_OUT",
    path: CHORD_TO_JB,
  },
  {
    id: "T-YD1",
    name: "North yard road 1",
    kind: "YARD",
    direction: "UP",
    path: [R(1160, 110), R(1500, 110)],
  },
  {
    id: "T-YD2",
    name: "North yard road 2",
    kind: "YARD",
    direction: "UP",
    path: [R(1160, 140), R(1500, 140)],
  },
  {
    id: "T-YDN",
    name: "Yard neck",
    kind: "YARD",
    direction: "UP",
    path: [R(1160, 110), R(1120, 150), R(1120, 200), R(1140, LINE_Y.WSD)],
  },
  {
    id: "T-GY1",
    name: "Goods yard road 1",
    kind: "YARD",
    direction: "BRANCH_OUT",
    path: [R(600, 800), R(848, 800)],
  },
  {
    id: "T-GY2",
    name: "Goods yard road 2",
    kind: "YARD",
    direction: "BRANCH_OUT",
    path: [R(620, 826), R(848, 826)],
  },
];

export const platforms: Platform[] = [
  {
    id: "PF1",
    label: "PF 1",
    x: PLATFORM_X0,
    y: LINE_Y.WSD - 36,
    w: PLATFORM_X1 - PLATFORM_X0,
    h: 24,
    serves: ["T-WSD"],
    usage: "Slow / Fast DN — Churchgate",
    side: "WEST",
  },
  {
    id: "PF2",
    label: "PF 2",
    x: PLATFORM_X0,
    y: LINE_Y.WSU - 36,
    w: PLATFORM_X1 - PLATFORM_X0,
    h: 24,
    serves: ["T-WSU"],
    usage: "Slow / Fast UP — Virar",
    side: "ISLAND",
  },
  {
    id: "PF3",
    label: "PF 3",
    x: PLATFORM_X0,
    y: LINE_Y.WFD - 36,
    w: PLATFORM_X1 - PLATFORM_X0,
    h: 24,
    serves: ["T-WFD"],
    usage: "Fast DN — Churchgate",
    side: "ISLAND",
  },
  {
    id: "PF4",
    label: "PF 4",
    x: PLATFORM_X0,
    y: LINE_Y.WFU - 36,
    w: PLATFORM_X1 - PLATFORM_X0,
    h: 24,
    serves: ["T-WFU"],
    usage: "Fast UP — Virar / Dahanu",
    side: "ISLAND",
  },
  {
    id: "PF5",
    label: "PF 5",
    x: PLATFORM_X0,
    y: LINE_Y.THD - 36,
    w: PLATFORM_X1 - PLATFORM_X0,
    h: 24,
    serves: ["T-THD"],
    usage: "Slow / Fast DN — through",
    side: "ISLAND",
  },
  {
    id: "PF6",
    label: "PF 6",
    x: PLATFORM_X0,
    y: LINE_Y.EX1 - 36,
    w: PLATFORM_X1 - PLATFORM_X0,
    h: 24,
    serves: ["T-EX1"],
    usage: "Express / MEMU — Diva bound",
    side: "EAST",
  },
  {
    id: "PF7",
    label: "PF 7",
    x: PLATFORM_X0,
    y: LINE_Y.EX2 - 36,
    w: PLATFORM_X1 - PLATFORM_X0,
    h: 24,
    serves: ["T-EX2"],
    usage: "Express / MEMU — Vasai bound",
    side: "EAST",
  },
];

export const junctions: Junction[] = [
  { id: "J1", label: "J1 North crossover", at: R(1120, LINE_Y.WFD) },
  { id: "J2", label: "J2 South crossover", at: R(440, LINE_Y.WFD) },
  { id: "J3", label: "J3 East approach", at: R(1120, LINE_Y.EX1) },
  { id: "JB", label: "JB Diva branch turnout", at: R(400, 780) },
  { id: "JY", label: "JY Yard neck", at: R(1130, 170) },
];

export const resources: Resource[] = [
  {
    id: "JB",
    label: "JB — Diva branch turnout",
    kind: "JUNCTION",
    at: R(400, 780),
    radius: 40,
    headwaySec: 180,
    capacity: 1,
  },
  {
    id: "J3",
    label: "J3 — East approach",
    kind: "JUNCTION",
    at: R(1120, LINE_Y.EX1),
    radius: 40,
    headwaySec: 120,
    capacity: 1,
  },
  {
    id: "J1",
    label: "J1 — North crossover",
    kind: "JUNCTION",
    at: R(1120, LINE_Y.WFD),
    radius: 34,
    headwaySec: 120,
    capacity: 1,
  },
  {
    id: "J2",
    label: "J2 — South crossover",
    kind: "JUNCTION",
    at: R(440, LINE_Y.WFD),
    radius: 34,
    headwaySec: 120,
    capacity: 1,
  },
  {
    id: "BLK-DV1",
    label: "Branch block BR-1 (Diva bound)",
    kind: "BLOCK",
    at: R(250, 862),
    radius: 10,
    headwaySec: 180,
    capacity: 1,
  },
  {
    id: "BLK-DV2",
    label: "Branch block BR-2 (Vasai bound)",
    kind: "BLOCK",
    at: R(250, 880),
    radius: 10,
    headwaySec: 180,
    capacity: 1,
  },
  {
    id: "BLK-N1",
    label: "Block N1 — Vasai / Nalla Sopara",
    kind: "BLOCK",
    at: R(1240, LINE_Y.WFD),
    radius: 40,
    headwaySec: 90,
    capacity: 1,
  },
  ...platforms.map<Resource>((p) => ({
    id: p.id,
    label: `${p.label} — platform road`,
    kind: "PLATFORM" as const,
    at: R(STOP_X, p.y + 36),
    radius: 34,
    headwaySec: 90,
    capacity: 1,
  })),
];

export const signals: SignalPost[] = [
  { id: "S-N1", at: R(1105, LINE_Y.WSD - 14), facing: 180, aspect: "GREEN" },
  { id: "S-N2", at: R(1105, LINE_Y.WFD - 14), facing: 180, aspect: "GREEN" },
  { id: "S-N3", at: R(1105, LINE_Y.EX1 - 14), facing: 180, aspect: "YELLOW" },
  { id: "S-S1", at: R(462, LINE_Y.WSU - 14), facing: 0, aspect: "GREEN" },
  { id: "S-S2", at: R(462, LINE_Y.WFU - 14), facing: 0, aspect: "GREEN" },
  { id: "S-S3", at: R(462, LINE_Y.THD - 14), facing: 180, aspect: "GREEN" },
  { id: "S-B1", at: R(414, 742), facing: 110, aspect: "YELLOW" },
  { id: "S-B2", at: R(352, 812), facing: 145, aspect: "RED" },
  { id: "S-F1", at: R(640, LINE_Y.FRC - 14), facing: 180, aspect: "GREEN" },
];

export const stationTicks: StationTick[] = [
  { name: "Nalla Sopara", at: R(1245, 0), km: 5.1, corridor: "NORTH" },
  { name: "Virar", at: R(1420, 0), km: 12.4, corridor: "NORTH" },
  { name: "Naigaon", at: R(300, 0), km: 5.6, corridor: "SOUTH" },
  { name: "Bhayandar", at: R(140, 0), km: 13.2, corridor: "SOUTH" },
  { name: "Juchandra", at: R(340, 825), km: 6.0, corridor: "DIVA" },
  { name: "Kaman Road", at: R(288, 848), km: 11.4, corridor: "DIVA" },
  { name: "Kharbao", at: R(232, 865), km: 17.2, corridor: "DIVA" },
  { name: "Bhiwandi Road", at: R(176, 869), km: 23.5, corridor: "DIVA" },
  { name: "Kopar", at: R(120, 873), km: 31.0, corridor: "DIVA" },
  { name: "Diva Jn", at: R(64, 877), km: 36.8, corridor: "DIVA" },
];

/** Foot over bridges, as in the station plan (drawn across the platforms). */
export const footBridges = [
  { id: "FOB-1", x: 560, y0: 168, y1: 700 },
  { id: "FOB-2", x: 700, y0: 240, y1: 700 },
  { id: "FOB-3", x: 840, y0: 168, y1: 700 },
  { id: "FOB-4", x: 980, y0: 310, y1: 700 },
];

export type AmenityKind =
  | "TICKET"
  | "RESERVATION"
  | "POLICE"
  | "WATER"
  | "TOILET"
  | "LIFT"
  | "ESCALATOR";

export const amenities: { id: string; kind: AmenityKind; at: Point }[] = [
  { id: "A1", kind: "WATER", at: R(524, 146) },
  { id: "A2", kind: "TOILET", at: R(586, 146) },
  { id: "A3", kind: "TICKET", at: R(652, 146) },
  { id: "A4", kind: "RESERVATION", at: R(736, 146) },
  { id: "A5", kind: "POLICE", at: R(836, 146) },
  { id: "A6", kind: "WATER", at: R(902, 146) },
  { id: "A7", kind: "TICKET", at: R(964, 146) },
  { id: "A8", kind: "TICKET", at: R(556, 722) },
  { id: "A9", kind: "WATER", at: R(660, 722) },
  { id: "A10", kind: "WATER", at: R(860, 722) },
  { id: "A11", kind: "TOILET", at: R(962, 722) },
  { id: "L1", kind: "LIFT", at: R(560, 262) },
  { id: "L2", kind: "LIFT", at: R(560, 470) },
  { id: "L3", kind: "LIFT", at: R(980, 470) },
  { id: "E1", kind: "ESCALATOR", at: R(700, 332) },
  { id: "E2", kind: "ESCALATOR", at: R(840, 404) },
  { id: "E3", kind: "ESCALATOR", at: R(840, 600) },
  { id: "E4", kind: "ESCALATOR", at: R(980, 600) },
];

function stopAt(path: Point[], y: number): number {
  return crossingDistance(path, R(STOP_X, y), 40) ?? 0;
}

function route(
  id: string,
  label: string,
  path: Point[],
  trackIds: string[],
  corridorFrom: RailRoute["corridorFrom"],
  corridorTo: RailRoute["corridorTo"],
  stops: { platformId: string; y: number; dwellSec: number }[],
): RailRoute {
  return {
    id,
    label,
    path,
    tracks: trackIds,
    corridorFrom,
    corridorTo,
    stops: stops.map((st) => ({
      platformId: st.platformId,
      dwellSec: st.dwellSec,
      s: stopAt(path, st.y),
    })),
  };
}

export const routes: RailRoute[] = [
  route(
    "RT-EXP-DIVA",
    "Surat / Virar → PF 6 → Diva branch",
    [R(1600, LINE_Y.EX1), ...EX1_TO_JB, ...BRANCH_OUT.slice(1)],
    ["T-EX1", "T-BR"],
    "NORTH",
    "DIVA",
    [{ platformId: "PF6", y: LINE_Y.EX1, dwellSec: 60 }],
  ),
  route(
    "RT-FRT-DIVA",
    "North goods chord → Diva branch",
    [...CHORD_TO_JB, ...BRANCH_OUT.slice(1)],
    ["T-FRC", "T-BR"],
    "NORTH",
    "DIVA",
    [],
  ),
  route(
    "RT-MEMU-IN",
    "Diva branch → PF 7 → Virar",
    [...BRANCH_IN, ...JB_TO_EX2.slice(1), R(1600, LINE_Y.EX2)],
    ["T-BR2", "T-EX2"],
    "DIVA",
    "NORTH",
    [{ platformId: "PF7", y: LINE_Y.EX2, dwellSec: 90 }],
  ),
  route(
    "RT-SLOW-DN",
    "Virar → PF 1 → Churchgate (slow)",
    [R(1600, LINE_Y.WSD), R(20, LINE_Y.WSD)],
    ["T-WSD"],
    "NORTH",
    "SOUTH",
    [{ platformId: "PF1", y: LINE_Y.WSD, dwellSec: 40 }],
  ),
  route(
    "RT-SLOW-UP",
    "Churchgate → PF 2 → Virar (slow)",
    [R(20, LINE_Y.WSU), R(1600, LINE_Y.WSU)],
    ["T-WSU"],
    "SOUTH",
    "NORTH",
    [{ platformId: "PF2", y: LINE_Y.WSU, dwellSec: 40 }],
  ),
  route(
    "RT-FAST-DN",
    "Dahanu → PF 3 → Churchgate (fast)",
    [R(1600, LINE_Y.WFD), R(20, LINE_Y.WFD)],
    ["T-WFD"],
    "NORTH",
    "SOUTH",
    [{ platformId: "PF3", y: LINE_Y.WFD, dwellSec: 35 }],
  ),
  route(
    "RT-FAST-UP",
    "Churchgate → PF 4 → Dahanu (fast)",
    [R(20, LINE_Y.WFU), R(1600, LINE_Y.WFU)],
    ["T-WFU"],
    "SOUTH",
    "NORTH",
    [{ platformId: "PF4", y: LINE_Y.WFU, dwellSec: 35 }],
  ),
  route(
    "RT-THRU-DN",
    "Surat → PF 5 → Mumbai Central (through)",
    [R(1600, LINE_Y.THD), R(20, LINE_Y.THD)],
    ["T-THD"],
    "NORTH",
    "SOUTH",
    [{ platformId: "PF5", y: LINE_Y.THD, dwellSec: 60 }],
  ),
  route(
    "RT-THRU-UP",
    "Mumbai → through UP → Surat",
    [R(20, LINE_Y.THU), R(1600, LINE_Y.THU)],
    ["T-THU"],
    "SOUTH",
    "NORTH",
    [],
  ),
  route(
    "RT-SHUNT",
    "North yard shunt move",
    [R(1500, 110), R(1160, 110), R(1120, 150), R(1120, 200), R(1140, LINE_Y.WSD), R(1320, LINE_Y.WSD)],
    ["T-YD1", "T-YDN"],
    "STATION",
    "STATION",
    [],
  ),
  // Feasible alternative: goods runs into the Vasai Road goods loop on the
  // Diva side, clears the turnout, then follows the branch.
  route(
    "RT-FRT-LOOP",
    "Goods chord → goods loop → Diva branch",
    [
      R(1600, LINE_Y.FRC),
      R(880, LINE_Y.FRC),
      R(848, 766),
      R(848, 800),
      R(600, 800),
      R(520, 792),
      R(440, 784),
      R(400, 780),
      ...BRANCH_OUT.slice(1),
    ],
    ["T-FRC", "T-GY1", "T-BR"],
    "NORTH",
    "DIVA",
    // Reception into the loop and restart against the branch signal.
    [{ platformId: "GY1", y: 800, dwellSec: 150 }],
  ),
];

export const routeById = Object.fromEntries(routes.map((r) => [r.id, r])) as Record<
  string,
  RailRoute
>;
export const platformById = Object.fromEntries(platforms.map((p) => [p.id, p])) as Record<
  string,
  Platform
>;
export const resourceById = Object.fromEntries(resources.map((r) => [r.id, r])) as Record<
  string,
  Resource
>;

/** Resources each route consumes, with the distance along the route. */
export interface RouteResourceUse {
  resourceId: string;
  s: number;
}

export const routeResourceUse: Record<string, RouteResourceUse[]> = Object.fromEntries(
  routes.map((r) => [
    r.id,
    resources
      .map((res) => {
        const s = crossingDistance(r.path, res.at, res.radius);
        return s === null ? null : { resourceId: res.id, s };
      })
      .filter((v): v is RouteResourceUse => v !== null)
      .sort((a, b) => a.s - b.s),
  ]),
);
