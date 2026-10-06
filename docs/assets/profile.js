// Профіль вышыні: SVG-плошча па днях, перакрыжаванне пры навядзенні (сінхранізавана з картай).
import { h, s } from "./dom.js";
import { num, t } from "./i18n.js";

const HEIGHT = 170;
const M = { top: 10, right: 8, bottom: 22, left: 44 };

function niceStep(range, count) {
  const raw = range / Math.max(count, 1);
  const pow = 10 ** Math.floor(Math.log10(raw));
  const f = raw / pow;
  return (f < 1.5 ? 1 : f < 3 ? 2 : f < 7 ? 5 : 10) * pow;
}

function ticks(min, max, count) {
  const step = niceStep(max - min || 1, count);
  const out = [];
  for (let v = Math.ceil(min / step) * step; v <= max + 1e-9; v += step) out.push(+v.toFixed(6));
  return { step, values: out };
}

/**
 * days: [{ segs: [[[lng, lat, alt, km], ...], ...], color }] — km назапашваецца ў межах дня
 * onHover(point|null): point = { lng, lat, alt, km, day }
 */
export function renderProfile(container, days, onHover) {
  const tip = h("div", { class: "tooltip", hidden: true });
  const svg = s("svg", { tabindex: "0", role: "img" });
  container.replaceChildren(svg, tip);

  // Плоскі спіс кропак з назапашанай адлегласцю праз усе дні.
  const pts = [];
  const series = [];
  let offset = 0;
  days.forEach((d, di) => {
    for (const seg of d.segs) {
      const start = pts.length;
      for (const [lng, lat, alt, km] of seg) {
        if (alt == null) continue;
        pts.push({ lng, lat, alt, km: offset + km, day: di });
      }
      if (pts.length > start) series.push({ from: start, to: pts.length, color: d.color });
    }
    const lastSeg = d.segs[d.segs.length - 1];
    offset += lastSeg?.length ? lastSeg[lastSeg.length - 1][3] : 0;
  });
  if (pts.length < 2) {
    container.replaceChildren();
    return { destroy() {} };
  }

  let minAlt = Infinity, maxAlt = -Infinity;
  for (const p of pts) { minAlt = Math.min(minAlt, p.alt); maxAlt = Math.max(maxAlt, p.alt); }
  const yt = ticks(minAlt, maxAlt, 3);
  const y0 = Math.floor(minAlt / yt.step) * yt.step;
  const y1 = Math.max(Math.ceil(maxAlt / yt.step) * yt.step, y0 + yt.step);
  const xMax = pts[pts.length - 1].km;

  svg.setAttribute("aria-label",
    `${t("profile")}: ${num(xMax, 1)} ${t("km")}, ${num(minAlt)}–${num(maxAlt)} ${t("m")}`);

  let width = 0, sx, sy, cross, dot, active = -1;

  function draw() {
    width = container.clientWidth;
    if (!width) return;
    svg.setAttribute("viewBox", `0 0 ${width} ${HEIGHT}`);
    const iw = width - M.left - M.right, ih = HEIGHT - M.top - M.bottom;
    sx = (km) => M.left + (km / xMax) * iw;
    sy = (alt) => M.top + ih - ((alt - y0) / (y1 - y0)) * ih;

    const grid = s("g", { class: "grid" });
    const labels = s("g");
    for (let v = y0; v <= y1 + 1e-9; v += yt.step) {
      const y = Math.round(sy(v)) + 0.5;
      if (v > y0) grid.append(s("line", { x1: M.left, x2: width - M.right, y1: y, y2: y }));
      labels.append(s("text", { class: "tick", x: M.left - 6, y: y + 4, "text-anchor": "end" }, num(v)));
    }
    const xt = ticks(0, xMax, Math.max(2, Math.floor(iw / 70)));
    for (const v of xt.values) {
      labels.append(s("text", {
        class: "tick", x: sx(v), y: HEIGHT - 6,
        "text-anchor": v === 0 ? "start" : "middle",
      }, v === 0 ? `0 ${t("km")}` : num(v, 1)));
    }

    const shapes = s("g");
    const base = sy(y0);
    for (const ser of series) {
      let line = "";
      for (let i = ser.from; i < ser.to; i++) {
        line += `${i === ser.from ? "M" : "L"}${sx(pts[i].km).toFixed(1)},${sy(pts[i].alt).toFixed(1)}`;
      }
      const area = `${line}L${sx(pts[ser.to - 1].km).toFixed(1)},${base}L${sx(pts[ser.from].km).toFixed(1)},${base}Z`;
      shapes.append(
        s("path", { class: "area", d: area, fill: ser.color }),
        s("path", { class: "line", d: line, stroke: ser.color }),
      );
    }

    cross = s("line", { class: "cross", y1: M.top, y2: base, visibility: "hidden" });
    dot = s("circle", { class: "dot", r: 4.5, visibility: "hidden" });
    const hit = s("rect", { x: M.left, y: 0, width: iw, height: HEIGHT, fill: "transparent" });
    svg.replaceChildren(grid,
      s("line", { class: "baseline", x1: M.left, x2: width - M.right, y1: Math.round(base) + 0.5, y2: Math.round(base) + 0.5 }),
      shapes, labels, cross, dot, hit);
    if (active >= 0) show(active);
  }

  function nearest(km) {
    let lo = 0, hi = pts.length - 1;
    while (hi - lo > 1) {
      const mid = (lo + hi) >> 1;
      if (pts[mid].km < km) lo = mid; else hi = mid;
    }
    return km - pts[lo].km < pts[hi].km - km ? lo : hi;
  }

  function show(i) {
    active = i;
    const p = pts[i];
    const x = sx(p.km), y = sy(p.alt);
    cross.setAttribute("x1", x); cross.setAttribute("x2", x); cross.setAttribute("visibility", "visible");
    dot.setAttribute("cx", x); dot.setAttribute("cy", y);
    dot.setAttribute("fill", series.find((ser) => i >= ser.from && i < ser.to)?.color || "currentColor");
    dot.setAttribute("visibility", "visible");
    const [altText, kmText] = t("altAt", num(p.alt), num(p.km, 1));
    tip.replaceChildren(h("strong", null, altText), h("span", null, kmText));
    tip.hidden = false;
    const half = tip.offsetWidth / 2;
    tip.style.left = `${Math.min(Math.max(x, half), width - half)}px`;
    tip.style.top = `${Math.max(0, y - 52)}px`;
    onHover?.(p);
  }

  function hide() {
    active = -1;
    cross?.setAttribute("visibility", "hidden");
    dot?.setAttribute("visibility", "hidden");
    tip.hidden = true;
    onHover?.(null);
  }

  function onMove(e) {
    const r = svg.getBoundingClientRect();
    const x = e.clientX - r.left;
    if (x < M.left - 4 || x > width - M.right + 4) return hide();
    show(nearest(((x - M.left) / (width - M.left - M.right)) * xMax));
  }

  function onKey(e) {
    const step = Math.max(1, Math.round(pts.length / 100));
    if (e.key === "ArrowRight") show(Math.min(pts.length - 1, (active < 0 ? 0 : active + step)));
    else if (e.key === "ArrowLeft") show(Math.max(0, (active < 0 ? 0 : active - step)));
    else if (e.key === "Home") show(0);
    else if (e.key === "End") show(pts.length - 1);
    else if (e.key === "Escape") hide();
    else return;
    e.preventDefault();
  }

  svg.addEventListener("pointermove", onMove);
  svg.addEventListener("pointerdown", onMove);
  svg.addEventListener("pointerleave", hide);
  svg.addEventListener("keydown", onKey);
  svg.addEventListener("blur", hide);
  const ro = new ResizeObserver(() => { if (container.clientWidth !== width) draw(); });
  ro.observe(container);
  draw();

  return {
    destroy() { ro.disconnect(); onHover?.(null); },
  };
}
