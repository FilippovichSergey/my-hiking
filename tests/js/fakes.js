// Падмена MapLibre для тэстаў інтэрфейсу: карта без малявання, якая памятае, што ёй перадалі.

class FakeSource {
  constructor(id) { this.id = id; this.data = null; }
  setData(data) { this.data = data; }
  setTiles() {}
  getClusterExpansionZoom() { return Promise.resolve(10); }
}

export class FakeMap {
  static last = null;
  constructor(opts) {
    FakeMap.last = this;
    this.handlers = {};
    this.layout = {}; // уласцівасці слаёў, зададзеныя праз setLayoutProperty: { слой: { уласцівасць: значэнне } }
    this.sources = Object.fromEntries(Object.keys(opts.style.sources).map((id) => [id, new FakeSource(id)]));
    setTimeout(() => this.fire("styledata"), 0);
  }
  on(type, layerOrFn, fn) { (this.handlers[type] ||= []).push(fn || layerOrFn); }
  off(type, fn) { this.handlers[type] = (this.handlers[type] || []).filter((f) => f !== fn); }
  fire(type) { for (const f of [...(this.handlers[type] || [])]) f({}); }
  getSource(id) { return this.sources[id]; }
  getCanvas() { return { style: {} }; }
  getBearing() { return 0; }
  getZoom() { return this.zoom ?? 12; }
  /** Пункт на экране: пікселі, як у сапраўднай карты з пліткамі 256 px (маштаб 12 — каля 29 px на 0,01°). */
  project([lng, lat]) {
    const perDegree = (256 * 2 ** this.getZoom()) / 360;
    return { x: lng * perDegree, y: -lat * perDegree };
  }
  addControl() {} setPaintProperty() {} setFeatureState() {}
  setLayoutProperty(id, prop, value) { (this.layout[id] ||= {})[prop] = value; }
  fitBounds(...args) { (this.fits ||= []).push(args); }
  flyTo() {} easeTo() {} resize() {} setTerrain() {} setSky() {}
}

export const maplibregl = {
  Map: FakeMap,
  NavigationControl: class {},
  ScaleControl: class {},
  Popup: class {
    static content = null; // змесціва апошняй паказанай падказкі
    static open = null;    // падказка, якая цяпер на карце
    setLngLat(at) { this.at = at; return this; }
    setDOMContent(node) { this.content = node; maplibregl.Popup.content = node; return this; }
    addTo() { maplibregl.Popup.open = this; return this; }
    remove() { if (maplibregl.Popup.open === this) maplibregl.Popup.open = null; }
  },
  Marker: class {
    static all = []; // значкі (крыніцы вады, месцы, фота), якія цяпер стаяць на карце
    constructor({ element }) { this.element = element; }
    setLngLat(at) { this.at = at; return this; }
    addTo() { maplibregl.Marker.all.push(this); return this; }
    remove() { maplibregl.Marker.all = maplibregl.Marker.all.filter((m) => m !== this); }
  },
  LngLatBounds: class { extend() { return this; } },
};
