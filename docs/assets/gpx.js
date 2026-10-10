// GPX 1.1 з трэку, які ўжо ёсць у даных сайта (data/tracks/*.json): пункты [даўгата, шырата, вышыня, км].
// Часу ў пунктах няма: на сайце захоўваецца толькі геаметрыя трэку.

const ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&apos;" };
const esc = (text) => String(text).replace(/[&<>"']/g, (c) => ESCAPES[c]);

/** tracks: [{ name, segs: [[[lng, lat, alt, km], …], …] }] — адзін <trk> на дзень, адзін <trkseg> на адрэзак. */
export function gpxText({ name, link, creator, tracks }) {
  const out = [
    '<?xml version="1.0" encoding="UTF-8"?>',
    `<gpx version="1.1" creator="${esc(creator)}" xmlns="http://www.topografix.com/GPX/1/1">`,
    `  <metadata><name>${esc(name)}</name>${link ? `<link href="${esc(link)}"/>` : ""}</metadata>`,
  ];
  for (const trk of tracks) {
    out.push("  <trk>", `    <name>${esc(trk.name)}</name>`);
    for (const seg of trk.segs) {
      out.push("    <trkseg>");
      for (const [lng, lat, alt] of seg) {
        out.push(`      <trkpt lat="${lat}" lon="${lng}">${alt == null ? "" : `<ele>${alt}</ele>`}</trkpt>`);
      }
      out.push("    </trkseg>");
    }
    out.push("  </trk>");
  }
  out.push("</gpx>", "");
  return out.join("\n");
}
