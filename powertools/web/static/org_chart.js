// Interactive org chart, no libraries: the same file runs in the Org tab and inside an exported .html (offline).
//   OrgChart(container, data) with data = {root: node, above: [node...], generated}
//   node = {id, name, title, department, office, mail, reports: [node...]}
// Left-to-right tidy tree in SVG: pan (drag), zoom (wheel, buttons), click a node for its details, the +/- dot to fold
// a team. Search goes down the whole branch (name, title, department, office, e-mail): the paths to the matches
// unfold, Enter jumps to the next one. Colours by department, with a legend that highlights one.
(function () {
  "use strict";
  const W = 230, H = 50, GX = 70, GY = 12, FOLD_AT = 2;   // node size, gaps; levels shown at first
  const CSS = `
.oc{position:relative;display:grid;grid-template-columns:1fr 260px;height:100%;min-height:420px;border:1px solid var(--line,#e2e2e8);border-radius:10px;overflow:hidden;background:var(--panel,#fff);color:var(--text,#1b1b1f);font:13px/1.4 "Segoe UI",system-ui,sans-serif}
.oc-stage{position:relative;overflow:hidden;cursor:grab;background:var(--bg,#f4f4f6)}
.oc-stage.drag{cursor:grabbing}
.oc svg.oc-svg{width:100%;height:100%;display:block;fill:initial;stroke:initial}
.oc-bar{position:absolute;top:10px;left:10px;right:10px;display:flex;gap:6px;align-items:center;flex-wrap:wrap;pointer-events:none}
.oc-bar>*{pointer-events:auto}
.oc-bar input{flex:1;min-width:180px;max-width:360px;padding:7px 10px;border:1px solid var(--line,#ddd);border-radius:8px;background:var(--panel,#fff);color:inherit;font:inherit}
.oc-bar button{padding:6px 10px;border:1px solid var(--line,#ddd);border-radius:8px;background:var(--panel,#fff);color:inherit;font:inherit;cursor:pointer}
.oc-bar button:hover{background:var(--hover,#f0f0f4)}
.oc-count{color:var(--muted,#74747e);background:var(--panel,#fff);padding:4px 8px;border-radius:8px}
.oc-side{border-left:1px solid var(--line,#e2e2e8);overflow:auto;padding:12px}
.oc-side h4{margin:14px 0 6px;font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted,#74747e)}
.oc-side h4:first-child{margin-top:0}
.oc-det b{font-size:15px}.oc-det div{margin:2px 0}.oc-det .m{color:var(--muted,#74747e)}
.oc-path{margin:6px 0 0;padding:0;list-style:none}.oc-path li{padding:2px 0;cursor:pointer}.oc-path li:hover{text-decoration:underline}
.oc-leg{list-style:none;margin:0;padding:0}.oc-leg li{display:flex;gap:8px;align-items:center;padding:3px 4px;border-radius:6px;cursor:pointer}
.oc-leg li:hover,.oc-leg li.on{background:var(--hover,#f0f0f4)}.oc-leg i{width:10px;height:10px;border-radius:3px;flex:none}
.oc-leg span{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.oc-leg em{font-style:normal;color:var(--muted,#74747e)}
.oc-node rect.card{fill:var(--panel,#fff);stroke:var(--line,#dcdce4);stroke-width:1}
.oc-node .band{stroke:none}
.oc-node text{fill:var(--text,#1b1b1f);stroke:none;font-size:12.5px}
.oc-node text.t{fill:var(--muted,#74747e);font-size:11px}
.oc-node .dot{stroke:var(--line,#c9c9d2);fill:var(--panel,#fff);cursor:pointer}
.oc-node .dot-t{fill:var(--muted,#74747e);font-size:11px;text-anchor:middle;pointer-events:none}
.oc-node{cursor:pointer}
.oc-node.hit rect.card{stroke:#e9a21b;stroke-width:2.5}
.oc-node.cur rect.card{stroke:#ef3b4a;stroke-width:3}
.oc-node.sel rect.card{stroke:var(--accent,#5b5bd6);stroke-width:2.5}
.oc-node.dim{opacity:.28}
.oc-link{fill:none;stroke:var(--line,#cfcfd8);stroke-width:1.4}
.oc-link.dim{opacity:.25}
@media (max-width:760px){.oc{grid-template-columns:1fr}.oc-side{display:none}}`;

  // distinct colours, given to the biggest departments first; the rest grey
  const PALETTE = ["#4e79a7", "#f28e2b", "#59a14f", "#e15759", "#b07aa1", "#76b7b2", "#edc948", "#9c755f", "#ff9da7",
    "#2f8f9d", "#8c6bb1", "#a0cbe8"];
  let colors = {};
  const color = (dept) => colors[dept || ""] || "#a3a3ad";
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
  const cut = (s, n) => (s && s.length > n ? s.slice(0, n - 1) + "…" : s || "");
  const SVGNS = "http://www.w3.org/2000/svg";
  const el = (tag, attrs, parent) => { const e = document.createElementNS(SVGNS, tag); for (const k in attrs) e.setAttribute(k, attrs[k]); if (parent) parent.appendChild(e); return e; };

  function OrgChart(host, data) {
    if (!document.getElementById("oc-css")) { const st = document.createElement("style"); st.id = "oc-css"; st.textContent = CSS; document.head.appendChild(st); }
    host.innerHTML = `<div class="oc"><div class="oc-stage">
        <svg class="oc-svg"><g class="oc-world"><g class="oc-links"></g><g class="oc-nodes"></g></g></svg>
        <div class="oc-bar"><input type="search" placeholder="Search down: name, title, department, office, e-mail">
          <span class="oc-count" hidden></span><button data-a="prev" title="Previous match">↑</button><button data-a="next" title="Next match (Enter)">↓</button>
          <button data-a="fit" title="Fit to screen">Fit</button><button data-a="in">+</button><button data-a="out">−</button>
          <button data-a="open" title="Unfold every team">Unfold all</button><button data-a="fold" title="Back to the first levels">Fold</button></div></div>
      <div class="oc-side"><h4>Person</h4><div class="oc-det m">Click a person.</div><h4>Departments</h4><ul class="oc-leg"></ul></div></div>`;
    const $ = (s) => host.querySelector(s);
    const svg = $(".oc-svg"), world = $(".oc-world"), gLinks = $(".oc-links"), gNodes = $(".oc-nodes"), stage = $(".oc-stage");

    // index: parent pointers, depth, team sizes, search text
    const all = [];
    (function index(n, parent, depth) {
      n.parent = parent; n.depth = depth; n.reports = n.reports || []; n.folded = depth >= FOLD_AT && n.reports.length > 0;
      n.text = [n.name, n.title, n.department, n.office, n.mail].join(" ").toLowerCase();
      all.push(n); n.reports.forEach((r) => index(r, n, depth + 1));
      n.size = n.reports.reduce((a, r) => a + 1 + r.size, 0);
    })(data.root, null, 0);
    const above = data.above || [];

    let view = { x: 40, y: 40, k: 1 }, hits = [], cur = -1, sel = null, dept = null;
    const apply = () => world.setAttribute("transform", `translate(${view.x},${view.y}) scale(${view.k})`);

    function layout() {   // y from the visible leaves, top to bottom; a parent sits in the middle of its children
      let row = 0;
      const vis = [];
      (function place(n) {
        vis.push(n); n.x = n.depth * (W + GX);
        const kids = n.folded ? [] : n.reports;
        if (!kids.length) { n.y = row * (H + GY); row++; return; }
        kids.forEach(place);
        n.y = (kids[0].y + kids[kids.length - 1].y) / 2;
      })(data.root);
      return vis;
    }

    function render() {
      const vis = layout();
      gLinks.innerHTML = ""; gNodes.innerHTML = "";
      const hitSet = new Set(hits), q = !!hits.length || dept !== null;
      for (const n of vis) {
        if (n.parent && !n.parent.folded) {
          const x1 = n.parent.x + W, y1 = n.parent.y + H / 2, x2 = n.x, y2 = n.y + H / 2, mx = (x1 + x2) / 2;
          el("path", { d: `M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`, class: "oc-link" + (q && !lit(n) ? " dim" : "") }, gLinks);
        }
        const g = el("g", { class: "oc-node" + (hitSet.has(n) ? " hit" : "") + (hits[cur] === n ? " cur" : "") + (sel === n ? " sel" : "")
          + (q && !lit(n) ? " dim" : ""), transform: `translate(${n.x},${n.y})` }, gNodes);
        el("rect", { class: "card", width: W, height: H, rx: 9 }, g);
        el("rect", { class: "band", width: 5, height: H - 12, x: 6, y: 6, rx: 2, fill: color(n.department) }, g);
        const t1 = el("text", { x: 18, y: 20 }, g); t1.textContent = cut(n.name, 30);
        const t2 = el("text", { x: 18, y: 37, class: "t" }, g); t2.textContent = cut([n.title, n.department].filter(Boolean).join(" · "), 38);
        const tip = el("title", {}, g); tip.textContent = [n.name, n.title, n.department, n.office, n.mail].filter(Boolean).join("\n");
        g.addEventListener("click", (e) => { e.stopPropagation(); select(n); });
        if (n.reports.length) {
          const d = el("g", { transform: `translate(${W},${H / 2})` }, g);
          el("circle", { class: "dot", r: 11 }, d);
          const tt = el("text", { class: "dot-t", y: 4 }, d); tt.textContent = n.folded ? n.size : "−";
          d.addEventListener("click", (e) => { e.stopPropagation(); n.folded = !n.folded; render(); });
        }
      }
    }
    // a node stays bright when it is a match, on the path to a match, or in the highlighted department
    function lit(n) {
      if (dept !== null && n.department !== dept) return false;
      if (!hits.length) return true;
      return n.onPath === stamp;
    }
    let stamp = 0;
    function markPaths() { stamp++; for (const h of hits) for (let p = h; p; p = p.parent) p.onPath = stamp; }

    function center(n) {
      const r = stage.getBoundingClientRect();
      view.x = r.width / 2 - (n.x + W / 2) * view.k; view.y = r.height / 2 - (n.y + H / 2) * view.k; apply();
    }
    function fit() {
      const vis = layout(), r = stage.getBoundingClientRect();
      const w = Math.max(...vis.map((n) => n.x)) + W + 40, h = Math.max(...vis.map((n) => n.y)) + H + 80;
      view.k = Math.max(0.15, Math.min(1.2, Math.min(r.width / w, (r.height - 50) / h)));
      view.x = 20; view.y = 56 + Math.max(0, (r.height - 56 - h * view.k) / 2); apply();
    }
    function fitTo(nodes) {   // the group when it fits at a readable size, else its highest person at a readable size
      const r = stage.getBoundingClientRect();
      const x0 = Math.min(...nodes.map((n) => n.x)), x1 = Math.max(...nodes.map((n) => n.x)) + W;
      const y0 = Math.min(...nodes.map((n) => n.y)), y1 = Math.max(...nodes.map((n) => n.y)) + H;
      const k = Math.min(1.2, (r.width - 40) / (x1 - x0 + 40), (r.height - 90) / (y1 - y0 + 40));
      if (k >= 0.55) { view.k = k; view.x = 20 - x0 * k + (r.width - 40 - (x1 - x0) * k) / 2; view.y = 70 - y0 * k; apply(); return; }
      view.k = 0.85; center(nodes.reduce((a, b) => (b.depth < a.depth || (b.depth === a.depth && b.y < a.y) ? b : a)));
    }
    function unfoldTo(n) { for (let p = n.parent; p; p = p.parent) p.folded = false; }

    function select(n) {
      sel = n;
      const path = []; for (let p = n.parent; p; p = p.parent) path.unshift(p);
      const chain = [...above.slice().reverse(), ...path];
      $(".oc-det").classList.remove("m");
      $(".oc-det").innerHTML = `<b>${esc(n.name)}</b>
        ${n.title ? `<div>${esc(n.title)}</div>` : ""}${n.department ? `<div class="m">${esc(n.department)}</div>` : ""}
        ${n.office ? `<div class="m">${esc(n.office)}</div>` : ""}${n.mail ? `<div><a href="mailto:${esc(n.mail)}">${esc(n.mail)}</a></div>` : ""}
        <div class="m">${n.reports.length} direct report(s), ${n.size} in total</div>
        ${chain.length ? `<h4>Reports to</h4><ul class="oc-path">${chain.map((p, i) => `<li data-i="${i}">${"&nbsp;".repeat(i)}↳ ${esc(p.name)}${p.title ? ` <span class="m">${esc(cut(p.title, 26))}</span>` : ""}</li>`).join("")}</ul>` : ""}`;
      $(".oc-det").querySelectorAll(".oc-path li").forEach((li) => {
        const p = chain[Number(li.dataset.i)];
        if (p.reports) li.onclick = () => { unfoldTo(p); render(); select(p); center(p); };   // the chain above is not in the tree
      });
      render();
    }

    function search(q) {
      q = q.trim().toLowerCase();
      hits = q.length < 2 ? [] : all.filter((n) => q.split(/\s+/).every((w) => n.text.includes(w)));
      cur = hits.length ? 0 : -1;
      const c = $(".oc-count");
      c.hidden = q.length < 2; c.textContent = hits.length ? `1 of ${hits.length}` : "no match";
      hits.forEach(unfoldTo); markPaths(); render();
      if (hits.length) center(hits[0]);
    }
    function step(d) {
      if (!hits.length) return;
      cur = (cur + d + hits.length) % hits.length;
      $(".oc-count").textContent = `${cur + 1} of ${hits.length}`;
      render(); center(hits[cur]); select(hits[cur]);
    }

    // legend: the biggest departments
    const counts = {};
    for (const n of all) counts[n.department || ""] = (counts[n.department || ""] || 0) + 1;
    colors = {};
    Object.entries(counts).filter(([d]) => d).sort((a, b) => b[1] - a[1]).slice(0, PALETTE.length)
      .forEach(([d], i) => (colors[d] = PALETTE[i]));
    $(".oc-leg").innerHTML = Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, 14).map(([d, c]) =>
      `<li data-d="${esc(d)}"><i style="background:${color(d)}"></i><span>${esc(d || "(no department)")}</span><em>${c}</em></li>`).join("");
    $(".oc-leg").querySelectorAll("li").forEach((li) => (li.onclick = () => {
      const d = li.dataset.d;
      dept = dept === d ? null : d;
      $(".oc-leg").querySelectorAll("li").forEach((x) => x.classList.toggle("on", x.dataset.d === dept));
      // the department's heads (their manager is in another department): the path to them unfolds, their teams stay as they are
      const group = dept === null ? [] : all.filter((n) => n.department === dept && (!n.parent || n.parent.department !== dept));
      group.forEach(unfoldTo);
      render();
      if (group.length) { layout(); fitTo(group); } else fit();
    }));

    // controls, pan and zoom
    let t = null;
    const input = $(".oc-bar input");
    input.addEventListener("input", () => { clearTimeout(t); t = setTimeout(() => search(input.value), 180); });
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); step(e.shiftKey ? -1 : 1); } });
    const zoom = (f, cx, cy) => {
      const r = stage.getBoundingClientRect(); cx = cx ?? r.width / 2; cy = cy ?? r.height / 2;
      const k = Math.max(0.1, Math.min(2.5, view.k * f));
      view.x = cx - (cx - view.x) * (k / view.k); view.y = cy - (cy - view.y) * (k / view.k); view.k = k; apply();
    };
    host.querySelectorAll(".oc-bar button").forEach((b) => (b.onclick = () => {
      const a = b.dataset.a;
      if (a === "prev") step(-1); else if (a === "next") step(1); else if (a === "fit") fit();
      else if (a === "in") zoom(1.25); else if (a === "out") zoom(0.8);
      else if (a === "open") { all.forEach((n) => (n.folded = false)); render(); fit(); }
      else if (a === "fold") { all.forEach((n) => (n.folded = n.depth >= FOLD_AT && n.reports.length > 0)); render(); fit(); }
    }));
    stage.addEventListener("wheel", (e) => { e.preventDefault(); const r = stage.getBoundingClientRect(); zoom(e.deltaY < 0 ? 1.12 : 0.89, e.clientX - r.left, e.clientY - r.top); }, { passive: false });
    let drag = null;
    stage.addEventListener("pointerdown", (e) => { if (e.target.closest(".oc-bar, .oc-node")) return; drag = { x: e.clientX - view.x, y: e.clientY - view.y }; stage.classList.add("drag"); stage.setPointerCapture(e.pointerId); });
    stage.addEventListener("pointermove", (e) => { if (!drag) return; view.x = e.clientX - drag.x; view.y = e.clientY - drag.y; apply(); });
    stage.addEventListener("pointerup", () => { drag = null; stage.classList.remove("drag"); });

    render(); requestAnimationFrame(fit);
    return { search, select: (id) => { const n = all.find((x) => x.id === id); if (n) { unfoldTo(n); select(n); center(n); } } };
  }
  window.OrgChart = OrgChart;
})();
