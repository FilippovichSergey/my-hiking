// Карта MapLibre: падкладкі (тапа / спадарожнік / схема), 3D-рэльеф, маркеры і трэкі паходаў.
import { cssVar, h } from "./dom.js";
import { dateRange, pick, t } from "./i18n.js";

const OSM = '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>';
const BASES = {
  topo: {
    tiles: ["a", "b", "c"].map((s) => `https://${s}.tile.opentopomap.org/{z}/{x}/{y}.png`),
    maxzoom: 17,
    attribution: `${OSM}, SRTM | © <a href="https://opentopomap.org">OpenTopoMap</a> (CC-BY-SA)`,
  },
  sat: {
    tiles: ["https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"],
    maxzoom: 19,
    attribution: "© Esri, Maxar, Earthstar Geographics",
  },
  plain: {
    tiles: (dark) => ["a", "b", "c", "d"].map((s) =>
      `https://${s}.basemaps.cartocdn.com/${dark ? "dark_all" : "rastertiles/voyager"}/{z}/{x}/{y}@2x.png`),
    maxzoom: 20,
    attribution: `${OSM} © <a href="https://carto.com/attributions">CARTO</a>`,
  },
};
const DEM = {
  type: "raster-dem",
  tiles: ["https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"],
  tileSize: 256,
  maxzoom: 15,
  encoding: "terrarium",
  attribution: '<a href="https://registry.opendata.aws/terrain-tiles/">Terrain Tiles</a>',
};
const EMPTY = { type: "FeatureCollection", features: [] };
const CLUSTER_COLOR = "#c2410c";

function fitPadding() {
  return innerWidth > 760 ? { top: 50, bottom: 50, left: 50, right: 150 } : 36;
}

function isDark() {
  return document.documentElement.classList.contains("theme-dark") ||
    (!document.documentElement.classList.contains("theme-light") &&
      matchMedia("(prefers-color-scheme: dark)").matches);
}

function store(key, value) {
  try {
    if (value === undefined) return localStorage.getItem(key);
    localStorage.setItem(key, value);
  } catch { return null; }
}

export function createMap(el, { onSelect }) {
  let base = store("map-base") || "topo";
  if (!BASES[base]) base = "topo";
  let terrainOn = false;
  let hoveredSlug = null;
  let selectedSlug = null;
  let hikesBySlug = new Map();
  let selection = null; // { hike, track, day }

  const map = new maplibregl.Map({
    container: el,
    attributionControl: { compact: true },
    maxPitch: 75,
    center: [43.4, 42.2],
    zoom: 6.5,
    style: {
      version: 8,
      glyphs: "https://fonts.openmaptiles.org/{fontstack}/{range}.pbf",
      sources: {
        topo: { type: "raster", tiles: BASES.topo.tiles, tileSize: 256, maxzoom: BASES.topo.maxzoom, attribution: BASES.topo.attribution },
        sat: { type: "raster", tiles: BASES.sat.tiles, tileSize: 256, maxzoom: BASES.sat.maxzoom, attribution: BASES.sat.attribution },
        plain: { type: "raster", tiles: BASES.plain.tiles(isDark()), tileSize: 256, maxzoom: BASES.plain.maxzoom, attribution: BASES.plain.attribution },
        dem: DEM,
        shade: DEM,
        hikes: { type: "geojson", data: EMPTY, cluster: true, clusterRadius: 36, clusterMaxZoom: 11 },
        lines: { type: "geojson", data: EMPTY, promoteId: "slug" },
        selected: { type: "geojson", data: EMPTY },
        hover: { type: "geojson", data: EMPTY },
      },
      layers: [
        { id: "topo", type: "raster", source: "topo", layout: { visibility: base === "topo" ? "visible" : "none" } },
        { id: "sat", type: "raster", source: "sat", layout: { visibility: base === "sat" ? "visible" : "none" } },
        { id: "plain", type: "raster", source: "plain", layout: { visibility: base === "plain" ? "visible" : "none" } },
        {
          id: "shade", type: "hillshade", source: "shade",
          layout: { visibility: base === "plain" ? "visible" : "none" },
          paint: { "hillshade-exaggeration": 0.3, "hillshade-shadow-color": "#473B24" },
        },
        {
          id: "lines-casing", type: "line", source: "lines", minzoom: 8,
          layout: { "line-join": "round", "line-cap": "round" },
          paint: { "line-color": "#ffffff", "line-width": 5, "line-opacity": 0.8 },
        },
        {
          id: "lines", type: "line", source: "lines", minzoom: 8,
          layout: { "line-join": "round", "line-cap": "round" },
          paint: {
            "line-color": "#eb6834",
            "line-width": ["case", ["boolean", ["feature-state", "hover"], false], 4.5, 2.5],
          },
        },
        {
          id: "sel-casing", type: "line", source: "selected",
          layout: { "line-join": "round", "line-cap": "round" },
          paint: { "line-color": "#ffffff", "line-width": 7, "line-opacity": ["case", ["get", "dim"], 0.4, 0.95] },
        },
        {
          id: "sel", type: "line", source: "selected",
          layout: { "line-join": "round", "line-cap": "round" },
          paint: { "line-color": ["get", "color"], "line-width": 4, "line-opacity": ["case", ["get", "dim"], 0.4, 1] },
        },
        {
          id: "clusters", type: "circle", source: "hikes", filter: ["has", "point_count"],
          paint: {
            "circle-color": CLUSTER_COLOR,
            "circle-radius": ["step", ["get", "point_count"], 14, 5, 17, 15, 21],
            "circle-stroke-color": "#ffffff",
            "circle-stroke-width": 2,
          },
        },
        {
          id: "cluster-count", type: "symbol", source: "hikes", filter: ["has", "point_count"],
          layout: { "text-field": ["get", "point_count_abbreviated"], "text-font": ["Open Sans Bold"], "text-size": 12 },
          paint: { "text-color": "#ffffff" },
        },
        {
          id: "points", type: "circle", source: "hikes", filter: ["!", ["has", "point_count"]],
          paint: {
            "circle-color": ["case", ["get", "selected"], "#2a78d6", "#eb6834"],
            "circle-radius": ["case", ["get", "selected"], 8, 6.5],
            "circle-stroke-color": "#ffffff",
            "circle-stroke-width": 2,
          },
        },
        {
          id: "hover-pt", type: "circle", source: "hover",
          paint: { "circle-radius": 6, "circle-color": ["get", "color"], "circle-stroke-color": "#ffffff", "circle-stroke-width": 2.5 },
        },
      ],
    },
  });

  // Кантэйнер можа быць нулявога памеру пры стварэнні (схаваная ўкладка, мабільная раскладка).
  new ResizeObserver(() => map.resize()).observe(el);

  map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), "top-right");
  map.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-left");

  // --- Пераключальнік падкладак і 3D ---
  const btns = {};
  const ctl = h("div", { class: "maplibregl-ctrl map-ctl" });
  for (const key of Object.keys(BASES)) {
    btns[key] = h("button", { type: "button", onclick: () => setBase(key) });
    ctl.append(btns[key]);
  }
  const btn3d = h("button", { type: "button", onclick: () => setTerrain(!terrainOn) });
  ctl.append(btn3d);
  map.addControl({ onAdd: () => ctl, onRemove: () => ctl.remove() }, "top-right");

  function updateControls() {
    for (const [key, b] of Object.entries(btns)) {
      b.textContent = t("layers")[key];
      b.setAttribute("aria-pressed", String(key === base));
    }
    btn3d.textContent = t("terrain3d");
    btn3d.setAttribute("aria-pressed", String(terrainOn));
  }

  function setBase(key) {
    base = key;
    store("map-base", key);
    for (const id of ["topo", "sat", "plain"]) map.setLayoutProperty(id, "visibility", id === key ? "visible" : "none");
    map.setLayoutProperty("shade", "visibility", key === "plain" ? "visible" : "none");
    updateControls();
  }

  function setTerrain(on) {
    terrainOn = on;
    if (on) {
      map.setTerrain({ source: "dem", exaggeration: 1.4 });
      try {
        map.setSky({
          "sky-color": "#7fb2e5", "horizon-color": "#dbe7f3", "fog-color": "#eef2f6",
          "sky-horizon-blend": 0.6, "horizon-fog-blend": 0.6, "fog-ground-blend": 0.7,
        });
      } catch {}
    } else {
      map.setTerrain(null);
    }
    if (selection?.track) {
      api.select(selection.hike, selection.track, selection.day, true); // перапісаць трэк з нахілам / без
    } else {
      map.easeTo({ pitch: on ? 62 : 0, bearing: on ? map.getBearing() : 0, duration: 800 });
    }
    updateControls();
  }

  // --- Узаемадзеянне з маркерамі і лініямі ---
  const popup = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 12 });
  function showPopup(slug, lngLat) {
    const hike = hikesBySlug.get(slug);
    if (!hike) return;
    popup.setLngLat(lngLat).setDOMContent(h("div", null,
      h("div", { class: "popup-title" }, pick(hike.title)),
      h("div", { class: "popup-meta" }, dateRange(hike.date, hike.end)),
    )).addTo(map);
  }

  for (const layer of ["points", "lines"]) {
    map.on("mouseenter", layer, (e) => {
      map.getCanvas().style.cursor = "pointer";
      const slug = e.features[0].properties.slug;
      highlight(slug);
      showPopup(slug, layer === "points" ? e.features[0].geometry.coordinates : e.lngLat);
    });
    map.on("mouseleave", layer, () => {
      map.getCanvas().style.cursor = "";
      highlight(null);
      popup.remove();
    });
    map.on("click", layer, (e) => {
      popup.remove();
      onSelect(e.features[0].properties.slug);
    });
  }
  map.on("mouseenter", "clusters", () => { map.getCanvas().style.cursor = "pointer"; });
  map.on("mouseleave", "clusters", () => { map.getCanvas().style.cursor = ""; });
  map.on("click", "clusters", async (e) => {
    const f = e.features[0];
    const zoom = await map.getSource("hikes").getClusterExpansionZoom(f.properties.cluster_id);
    map.easeTo({ center: f.geometry.coordinates, zoom });
  });

  function highlight(slug) {
    if (hoveredSlug) map.setFeatureState({ source: "lines", id: hoveredSlug }, { hover: false });
    hoveredSlug = slug;
    if (slug && hikesBySlug.has(slug)) map.setFeatureState({ source: "lines", id: slug }, { hover: true });
  }

  // --- Публічнае API ---
  const api = {
    updateControls,

    setHikes(hikes) {
      hikesBySlug = new Map(hikes.map((hk) => [hk.slug, hk]));
      map.getSource("hikes").setData({
        type: "FeatureCollection",
        features: hikes.filter((hk) => hk.point).map((hk) => ({
          type: "Feature",
          properties: { slug: hk.slug, selected: hk.slug === selectedSlug },
          geometry: { type: "Point", coordinates: hk.point },
        })),
      });
      map.getSource("lines").setData({
        type: "FeatureCollection",
        features: hikes.filter((hk) => hk.lines.length && hk.slug !== selectedSlug).map((hk) => ({
          type: "Feature",
          properties: { slug: hk.slug },
          geometry: { type: "MultiLineString", coordinates: hk.lines },
        })),
      });
    },

    fitHikes(hikes) {
      const pts = hikes.flatMap((hk) => (hk.bbox ? [[hk.bbox[0], hk.bbox[1]], [hk.bbox[2], hk.bbox[3]]] : hk.point ? [hk.point] : []));
      if (!pts.length) return;
      const b = new maplibregl.LngLatBounds(pts[0], pts[0]);
      pts.forEach((p) => b.extend(p));
      map.fitBounds(b, { padding: fitPadding(), maxZoom: 12, duration: 800 });
    },

    /** hike, track: {days:[{coords}]} | null, day: індэкс дня або null (усе дні) */
    select(hike, track, day, fit = true) {
      selectedSlug = hike.slug;
      selection = { hike, track, day };
      map.setPaintProperty("lines", "line-opacity", 0.45);
      map.setPaintProperty("lines-casing", "line-opacity", 0.35);
      const features = (track?.days || []).map((d, i) => ({
        type: "Feature",
        properties: { color: cssVar(`--series-${(i % 8) + 1}`), dim: day != null && day !== i },
        geometry: { type: "MultiLineString", coordinates: d.segs.map((seg) => seg.map((c) => [c[0], c[1]])) },
      }));
      map.getSource("selected").setData({ type: "FeatureCollection", features });
      if (!fit) return;
      const coords = (day != null ? [track.days[day]] : track?.days || []).flatMap((d) => d.segs.flat());
      if (coords.length) {
        const b = new maplibregl.LngLatBounds([coords[0][0], coords[0][1]], [coords[0][0], coords[0][1]]);
        coords.forEach((c) => b.extend([c[0], c[1]]));
        map.fitBounds(b, { padding: fitPadding(), maxZoom: 15, duration: 900, pitch: terrainOn ? 62 : 0 });
      } else if (hike.point) {
        map.flyTo({ center: hike.point, zoom: 13, duration: 900 });
      }
    },

    clearSelection() {
      selectedSlug = null;
      selection = null;
      map.setPaintProperty("lines", "line-opacity", 1);
      map.setPaintProperty("lines-casing", "line-opacity", 0.8);
      map.getSource("selected").setData(EMPTY);
      map.getSource("hover").setData(EMPTY);
    },

    setHoverPoint(p, color) {
      map.getSource("hover").setData(p ? {
        type: "FeatureCollection",
        features: [{ type: "Feature", properties: { color }, geometry: { type: "Point", coordinates: [p.lng, p.lat] } }],
      } : EMPTY);
    },

    highlight,

    refreshTheme() {
      map.getSource("plain")?.setTiles(BASES.plain.tiles(isDark()));
      if (selection) this.select(selection.hike, selection.track, selection.day, false);
    },

    resize() { map.resize(); },
  };

  // Пакуль стыль не загружаны, крыніц даных яшчэ няма: выклікі чакаюць у чарзе, панэль не блакуецца.
  // (Не чакаем падзеі load — яна прыходзіць толькі пасля ўсіх плітак, а OpenTopoMap бывае павольны.)
  let ready = false;
  const queue = [];
  for (const name of ["setHikes", "fitHikes", "select", "clearSelection", "setHoverPoint", "highlight", "refreshTheme"]) {
    const fn = api[name];
    api[name] = (...args) => (ready ? fn.apply(api, args) : queue.push(() => fn.apply(api, args)));
  }
  const onStyle = () => {
    if (ready || !map.getSource("hikes")) return;
    ready = true;
    map.off("styledata", onStyle);
    queue.splice(0).forEach((call) => call());
  };
  map.on("styledata", onStyle);
  return api;
}
