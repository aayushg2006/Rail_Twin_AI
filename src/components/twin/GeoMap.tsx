import { useEffect, useMemo, useRef } from "react";
import * as maplibregl from "maplibre-gl";
import type { Map as MapLibreMap } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { useTwin } from "@/twin/store";
import type { Conflict, RailGeometry, TrainSnapshot } from "@/domain/types";

const TRAIN_COLOUR: Record<string, string> = {
  PREMIUM: "#cf8cff",
  SUPERFAST: "#ff7466",
  MAIL_EXPRESS: "#ff7466",
  LOCAL_FAST: "#24d3e8",
  LOCAL_AC: "#24d3e8",
  LOCAL_SLOW: "#63abd2",
  LOCAL_SEMIFAST: "#63abd2",
  PASSENGER: "#63bd8a",
  MEMU: "#63bd8a",
  FREIGHT: "#d0b878",
  FREIGHT_EMPTY: "#d0b878",
  PREMIUM_FREIGHT: "#d0b878",
  SHUNT: "#9299a2",
};

type Feature = {
  type: "Feature";
  geometry: { type: string; coordinates: unknown };
  properties: Record<string, unknown>;
};

const collection = (features: Feature[]) => ({
  type: "FeatureCollection" as const,
  features,
});

function infrastructure(geo: RailGeometry) {
  const ways: Feature[] = geo.ways.map((way) => ({
    type: "Feature",
    geometry: {
      type: "LineString",
      coordinates: way.points.map((point) => [point.lng, point.lat]),
    },
    properties: { id: way.id, kind: way.kind, name: way.name ?? "" },
  }));
  const platforms: Feature[] = geo.platforms.map((platform) => {
    const ring = platform.points.map((point) => [point.lng, point.lat]);
    if (ring.length && (ring[0]![0] !== ring.at(-1)![0]
        || ring[0]![1] !== ring.at(-1)![1])) ring.push([...ring[0]!] as number[]);
    return {
      type: "Feature",
      geometry: { type: "Polygon", coordinates: [ring] },
      properties: { ref: platform.ref ?? "", faces: platform.faces.join(" / ") },
    };
  });
  const platformLabels: Feature[] = geo.platforms.flatMap((platform) => {
    if (!platform.points.length) return [];
    const center = platform.points.reduce(
      (sum, point) => ({ lat: sum.lat + point.lat, lng: sum.lng + point.lng }),
      { lat: 0, lng: 0 },
    );
    center.lat /= platform.points.length;
    center.lng /= platform.points.length;
    return platform.faces.map((face, index) => ({
      type: "Feature" as const,
      geometry: {
        type: "Point",
        coordinates: [center.lng + (index - (platform.faces.length - 1) / 2) * 0.00007,
          center.lat],
      },
      properties: { label: face },
    }));
  });
  const switches: Feature[] = geo.switches.map((item) => ({
    type: "Feature",
    geometry: { type: "Point", coordinates: [item.lng, item.lat] },
    properties: { id: item.id, label: "TURNOUT" },
  }));
  return { ways, platforms, platformLabels, switches };
}

export function GeoMap() {
  const {
    bundle,
    geoProjector,
    activeTrains,
    conflicts,
    selectedConflict,
    selectedTrainId,
    selectConflict,
    selectTrain,
    focusMode,
    layers,
    toggleLayer,
    setMapView,
    horizonOffset,
  } = useTwin();
  const container = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MapLibreMap | null>(null);
  const geo = geoProjector?.geometry ?? null;
  const tileUrl = ((import.meta as { env?: Record<string, string> }).env ?? {})[
    "VITE_OSM_TILE_URL"
  ] ?? "https://tile.openstreetmap.org/{z}/{x}/{y}.png";

  const staticData = useMemo(() => geo ? infrastructure(geo) : null, [geo]);

  useEffect(() => {
    if (!container.current || !geo?.station || mapRef.current) return;
    const map = new maplibregl.Map({
      container: container.current,
      center: [geo.station.lng, geo.station.lat],
      zoom: 14.8,
      attributionControl: false,
      style: {
        version: 8,
        sources: {
          osm: {
            type: "raster",
            tiles: [tileUrl],
            tileSize: 256,
            maxzoom: 19,
            attribution: "© OpenStreetMap contributors",
          },
        },
        layers: [{
          id: "osm",
          type: "raster",
          source: "osm",
          paint: {
            "raster-saturation": -0.78,
            "raster-brightness-max": 0.52,
            "raster-contrast": 0.22,
          },
        }],
      },
    });
    map.addControl(new maplibregl.NavigationControl({ showCompass: true }), "bottom-left");
    map.addControl(new maplibregl.AttributionControl({ compact: false }), "bottom-right");
    mapRef.current = map;
    return () => {
      map.remove();
      mapRef.current = null;
    };
  }, [geo?.station, tileUrl]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !staticData) return;
    const install = () => {
      if (map.getSource("rail-ways")) return;
      map.addSource("rail-ways", { type: "geojson", data: collection(staticData.ways) });
      map.addLayer({
        id: "rail-main", type: "line", source: "rail-ways",
        filter: ["==", ["get", "kind"], "WESTERN_MAIN"],
        paint: { "line-color": "#aeb9c3", "line-width": ["interpolate", ["linear"], ["zoom"], 12, 1.2, 17, 4.2], "line-opacity": 0.92 },
      });
      map.addLayer({
        id: "rail-branch", type: "line", source: "rail-ways",
        filter: ["==", ["get", "kind"], "BRANCH_OR_LINK"],
        paint: { "line-color": "#58bd8c", "line-width": ["interpolate", ["linear"], ["zoom"], 12, 1.2, 17, 3.6] },
      });
      map.addLayer({
        id: "rail-freight", type: "line", source: "rail-ways",
        filter: ["in", ["get", "kind"], ["literal", ["FREIGHT_SIDING", "YARD"]]],
        paint: { "line-color": "#c4a45e", "line-width": 2, "line-dasharray": [3, 2], "line-opacity": 0.88 },
      });
      map.addLayer({
        id: "rail-crossover", type: "line", source: "rail-ways",
        filter: ["==", ["get", "kind"], "CROSSOVER"],
        paint: { "line-color": "#ff9f43", "line-width": 3 },
      });
      map.addSource("rail-platforms", { type: "geojson", data: collection(staticData.platforms) });
      map.addLayer({
        id: "rail-platform-fill", type: "fill", source: "rail-platforms",
        paint: { "fill-color": "#48515a", "fill-opacity": 0.86, "fill-outline-color": "#d3d9de" },
      });
      map.addSource("rail-platform-labels", { type: "geojson", data: collection(staticData.platformLabels) });
      map.addLayer({
        id: "rail-platform-labels", type: "symbol", source: "rail-platform-labels",
        minzoom: 14.3,
        layout: { "text-field": ["get", "label"], "text-size": 11, "text-allow-overlap": false },
        paint: { "text-color": "#ffffff", "text-halo-color": "#14191d", "text-halo-width": 1.5 },
      });
      map.addSource("rail-switches", { type: "geojson", data: collection(staticData.switches) });
      map.addLayer({
        id: "rail-switches", type: "circle", source: "rail-switches", minzoom: 14,
        paint: { "circle-radius": 4, "circle-color": "#ffad42", "circle-stroke-color": "#1a1f23", "circle-stroke-width": 1 },
      });
      for (const id of ["predicted-paths", "conflict-points", "train-points"] as const) {
        map.addSource(id, { type: "geojson", data: collection([]) });
      }
      map.addLayer({
        id: "predicted-paths", type: "line", source: "predicted-paths",
        paint: { "line-color": ["get", "colour"], "line-width": 2, "line-dasharray": [3, 3], "line-opacity": ["get", "opacity"] },
      });
      map.addLayer({
        id: "conflict-halo", type: "circle", source: "conflict-points",
        paint: { "circle-radius": ["case", ["==", ["get", "severity"], "CRITICAL"], 12, 9], "circle-color": ["case", ["==", ["get", "severity"], "CRITICAL"], "#f05d5e", "#f0ad35"], "circle-opacity": ["get", "opacity"], "circle-stroke-width": 2, "circle-stroke-color": "#171b1f" },
      });
      map.addLayer({
        id: "conflict-label", type: "symbol", source: "conflict-points",
        layout: { "text-field": "!", "text-size": 13, "text-allow-overlap": true },
        paint: { "text-color": "#101316" },
      });
      map.addLayer({
        id: "train-points", type: "circle", source: "train-points",
        paint: { "circle-radius": ["case", ["==", ["get", "category"], "FREIGHT"], 7, 5.5], "circle-color": ["get", "colour"], "circle-opacity": ["get", "opacity"], "circle-stroke-width": 1.5, "circle-stroke-color": ["case", ["boolean", ["get", "selected"], false], "#5ad7ff", "#11161a"] },
      });
      map.addLayer({
        id: "train-labels", type: "symbol", source: "train-points", minzoom: 13,
        layout: { "text-field": ["get", "label"], "text-size": 11, "text-offset": [0, -1.35], "text-allow-overlap": false },
        paint: { "text-color": "#eef3f6", "text-halo-color": "#11161a", "text-halo-width": 1.5, "text-opacity": ["get", "opacity"] },
      });
      map.on("click", "train-points", (event) => {
        const id = event.features?.[0]?.properties?.["id"];
        if (typeof id === "string") selectTrain(id);
      });
      map.on("click", "conflict-halo", (event) => {
        const id = event.features?.[0]?.properties?.["id"];
        if (typeof id === "string") selectConflict(id);
      });
      for (const id of ["train-points", "conflict-halo"]) {
        map.on("mouseenter", id, () => { map.getCanvas().style.cursor = "pointer"; });
        map.on("mouseleave", id, () => { map.getCanvas().style.cursor = ""; });
      }
    };
    if (map.loaded()) install();
    else map.once("load", install);
  }, [staticData, selectConflict, selectTrain]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !geoProjector || !bundle) return;
    const update = () => {
      const focusIds = focusMode && selectedConflict
        ? new Set([selectedConflict.trainA, selectedConflict.trainB]) : null;
      const trains: Feature[] = activeTrains.flatMap((train: TrainSnapshot) => {
        if (!train.at) return [];
        const point = geoProjector.lngLat(train.at, train.line);
        return [{
          type: "Feature",
          geometry: { type: "Point", coordinates: [point.lng, point.lat] },
          properties: {
            id: train.trainId, label: train.number, category: train.category,
            colour: TRAIN_COLOUR[train.serviceClass] ?? "#9ba6af",
            opacity: focusIds && !focusIds.has(train.trainId) ? 0.12 : 1,
            selected: selectedTrainId === train.trainId,
            source: train.dataSource ?? "TIMETABLE_ESTIMATE",
          },
        }];
      });
      const conflictFeatures: Feature[] = conflicts.map((conflict: Conflict) => {
        const train = bundle.simState.trains[conflict.trainA];
        const point = geoProjector.lngLat(conflict.at, train?.line);
        return {
          type: "Feature",
          geometry: { type: "Point", coordinates: [point.lng, point.lat] },
          properties: {
            id: conflict.id, severity: conflict.severity,
            opacity: focusMode && selectedConflict?.id !== conflict.id ? 0.08 : 0.95,
            evidence: conflict.evidence ?? "PREDICTED",
          },
        };
      });
      const predicted: Feature[] = Object.entries(bundle.prediction.paths).flatMap(
        ([id, chain]) => {
          const train = bundle.simState.trains[id];
          if (!train || chain.length < 2) return [];
          return [{
            type: "Feature",
            geometry: {
              type: "LineString",
              coordinates: chain.map((at) => {
                const point = geoProjector.lngLat(at, train.line);
                return [point.lng, point.lat];
              }),
            },
            properties: {
              id, colour: TRAIN_COLOUR[train.serviceClass] ?? "#9ba6af",
              opacity: focusIds && !focusIds.has(id) ? 0.05 : 0.55,
            },
          }];
        },
      );
      (map.getSource("train-points") as maplibregl.GeoJSONSource | undefined)
        ?.setData(collection(trains) as never);
      (map.getSource("conflict-points") as maplibregl.GeoJSONSource | undefined)
        ?.setData(collection(conflictFeatures) as never);
      (map.getSource("predicted-paths") as maplibregl.GeoJSONSource | undefined)
        ?.setData(collection(predicted) as never);
    };
    if (map.loaded()) update();
    else map.once("load", update);
  }, [bundle, activeTrains, conflicts, selectedConflict, selectedTrainId,
    focusMode, geoProjector]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map?.loaded()) return;
    const visibility = (value: boolean) => value ? "visible" : "none";
    for (const id of ["rail-main", "rail-branch", "rail-freight", "rail-crossover",
      "rail-platform-fill", "rail-platform-labels", "rail-switches"]) {
      if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", visibility(layers.infrastructure));
    }
    for (const id of ["train-points", "train-labels"]) {
      if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", visibility(layers.live));
    }
    for (const id of ["predicted-paths", "conflict-halo", "conflict-label"]) {
      if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", visibility(layers.predicted));
    }
  }, [layers]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !focusMode || !selectedConflict || !geoProjector || !bundle) return;
    const bounds = new maplibregl.LngLatBounds();
    for (const id of [selectedConflict.trainA, selectedConflict.trainB]) {
      const train = bundle.simState.trains[id];
      if (train?.at) {
        const point = geoProjector.lngLat(train.at, train.line);
        bounds.extend([point.lng, point.lat]);
      }
      for (const at of bundle.prediction.paths[id] ?? []) {
        const point = geoProjector.lngLat(at, train?.line);
        bounds.extend([point.lng, point.lat]);
      }
    }
    const at = geoProjector.lngLat(selectedConflict.at,
      bundle.simState.trains[selectedConflict.trainA]?.line);
    bounds.extend([at.lng, at.lat]);
    if (!bounds.isEmpty()) map.fitBounds(bounds, { padding: 110, maxZoom: 16.8, duration: 650 });
  }, [focusMode, selectedConflict?.id, geoProjector]);

  return (
    <div className="relative h-full w-full overflow-hidden bg-map">
      <div ref={container} className="h-full w-full" aria-label="Interactive OpenStreetMap railway view" />
      <div className="pointer-events-none absolute top-3 left-3 border border-border bg-panel/90 px-2 py-1.5">
        <div className="label-xs">Vasai Road Jn · OpenStreetMap railway topology</div>
        <div className="num text-[10px] text-muted-foreground">
          {horizonOffset > 0 ? "PROJECTED" : "REAL IST CLOCK"}
          {focusMode ? " · CONFLICT FOCUS" : ""}
        </div>
      </div>
      <div className="absolute top-3 right-3 flex items-center gap-1 border border-border bg-panel/90 p-1">
        <button type="button" className="border border-selected px-2 py-1 label-xs text-selected">Real map</button>
        <button type="button" onClick={() => setMapView("SCHEMATIC")} className="border border-border px-2 py-1 label-xs">Schematic</button>
        {(["infrastructure", "live", "predicted"] as const).map((key) => (
          <button key={key} type="button" onClick={() => toggleLayer(key)}
            className={`border px-2 py-1 label-xs ${layers[key] ? "border-selected text-selected" : "border-border text-faint"}`}>
            {key}
          </button>
        ))}
      </div>
      <div className="pointer-events-none absolute bottom-8 left-3 border border-border bg-panel/90 px-2 py-1 label-xs">
        Passenger markers: RailRadar/timetable estimate · Goods: SYNTHETIC FREIGHT
      </div>
    </div>
  );
}
