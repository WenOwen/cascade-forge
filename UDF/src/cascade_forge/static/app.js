(() => {
  "use strict";

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const colors = { blue: "#0071e3", orange: "#ff7a00", ink: "#1d1d1f", muted: "#6e6e73", green: "#248a3d", red: "#d70015" };
  const plotConfig = { responsive: true, displaylogo: false, scrollZoom: true, modeBarButtonsToRemove: ["lasso2d", "select2d"] };
  const baseLayout = { paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)", font: { family: "-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif", size: 11, color: colors.muted }, margin: { l: 48, r: 18, t: 18, b: 42 }, hovermode: "closest", legend: { orientation: "h", y: 1.02, x: 0 } };

  const Charts = (() => {
    const observed = new WeakSet();
    const observer = new ResizeObserver(entries => entries.forEach(entry => {
      if (entry.target && entry.target._fullLayout) window.Plotly?.Plots.resize(entry.target);
    }));
    function render(target, data, layout = {}, config = {}) {
      const node = typeof target === "string" ? document.getElementById(target) : target;
      if (!node || !window.Plotly) return Promise.resolve();
      node.classList.remove("empty-chart");
      const merged = { ...baseLayout, ...layout, margin: { ...baseLayout.margin, ...(layout.margin || {}) } };
      if (!observed.has(node)) { observer.observe(node); observed.add(node); }
      return Plotly.react(node, data, merged, { ...plotConfig, ...config }).then(() => Plotly.Plots.resize(node));
    }
    function resizeAll() { $$(".plotly-chart").forEach(node => node._fullLayout && Plotly.Plots.resize(node)); }
    window.addEventListener("resize", resizeAll, { passive: true });
    document.addEventListener("htmx:afterSettle", resizeAll);
    return { render, resizeAll };
  })();
  window.CFCharts = Charts;

  function notice(message, error = false) {
    const node = $("#global-notice");
    if (!node) return;
    node.textContent = message; node.className = `global-notice visible${error ? " error" : ""}`;
    clearTimeout(notice.timer); notice.timer = setTimeout(() => node.className = "global-notice", 3200);
  }
  async function fetchJSON(url, options = {}) {
    const response = await fetch(url, { headers: { "Content-Type": "application/json", ...(options.headers || {}) }, ...options });
    const type = response.headers.get("content-type") || "";
    const payload = type.includes("json") ? await response.json() : await response.text();
    if (!response.ok) throw new Error(payload?.detail || payload || `HTTP ${response.status}`);
    return payload;
  }
  function debounce(fn, wait) { let timer; return (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), wait); }; }
  function downloadJSON(name, payload) { const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" }); const link = document.createElement("a"); link.href = URL.createObjectURL(blob); link.download = name; link.click(); URL.revokeObjectURL(link.href); }
  function escapeHTML(value) { return String(value ?? "").replace(/[&<>'"]/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[char]); }
  function statusPill(status) { return `<span class="pill ${escapeHTML(status)}">${escapeHTML(status)}</span>`; }
  function xy(points) { return { x: points.map(p => p[0]), y: points.map(p => p[1]) }; }
  async function pollJob(jobId, callback) {
    for (;;) {
      const job = await fetchJSON(`/api/v2/jobs/${encodeURIComponent(jobId)}`); callback?.(job);
      if (["completed", "failed"].includes(job.status)) return job;
      await new Promise(resolve => setTimeout(resolve, 900));
    }
  }

  function initShell() {
    const toggle = $("[data-sidebar-toggle]"); const scrim = $("[data-sidebar-scrim]");
    toggle?.addEventListener("click", () => document.body.classList.toggle("sidebar-open"));
    scrim?.addEventListener("click", () => document.body.classList.remove("sidebar-open"));
  }

  function initStudio() {
    const root = $("[data-studio-root]"); if (!root) return;
    const form = $("#studio-form", root); const defaults = JSON.parse(root.dataset.defaultSpec);
    let currentPayload = null; let currentSpec = structuredClone(defaults); let analysisTab = "thickness"; let requestToken = 0;
    const numeric = ["inlet_metal_angle_deg", "turning_angle_deg", "max_thickness_ratio", "max_thickness_x_ratio", "leading_edge_radius_ratio", "trailing_edge_radius_ratio", "trailing_edge_half_wedge_deg", "fore_shape_factor", "aft_shape_factor"];
    numeric.forEach(name => { const input = form.elements[name]; if (input) input.value = defaults.intent[name]; });
    form.elements.camber_family.value = defaults.camber_family; form.elements.thickness_family.value = defaults.thickness_family;
    const syncOutputs = () => $$('output[data-for]', form).forEach(out => { out.textContent = form.elements[out.dataset.for]?.value || ""; }); syncOutputs();
    function buildSpec() {
      const advanced = $("[data-mode='advanced']").classList.contains("active");
      if (advanced) { try { return JSON.parse(form.elements.explicit_spec.value); } catch { throw new Error("显式 spec JSON 无法解析"); } }
      const intent = { ...defaults.intent };
      numeric.forEach(name => intent[name] = Number(form.elements[name].value));
      return { schema_version: "2.0.0", generator_version: defaults.generator_version, coordinate_convention: defaults.coordinate_convention, authoring_mode: "basic", intent, camber_family: form.elements.camber_family.value, thickness_family: form.elements.thickness_family.value, linked_thickness: form.elements.linked_thickness.checked, camber_curve: null, suction_thickness_curve: null, pressure_thickness_curve: null, edge: null, points_per_side: 201, resolution_residuals: {} };
    }
    async function preview(full = false) {
      const token = ++requestToken; const state = $("[data-preview-state]");
      try {
        state.textContent = full ? "完整验证…" : "计算中…"; state.className = "pill running";
        const payload = await fetchJSON("/api/v2/preview", { method: "POST", body: JSON.stringify(buildSpec()) });
        if (token !== requestToken) return;
        currentPayload = payload; currentSpec = payload.geometry.spec;
        form.elements.explicit_spec.value = JSON.stringify(currentSpec, null, 2);
        state.textContent = "已同步"; state.className = "pill success";
        renderStudio(payload);
      } catch (error) { state.textContent = "参数被拒绝"; state.className = "pill rejected"; notice(error.message, true); }
    }
    const throttledPreview = debounce(() => preview(false), 150);
    form.addEventListener("input", event => { syncOutputs(); if (event.target.name !== "explicit_spec") throttledPreview(); else if ($("[data-mode='advanced']").classList.contains("active")) throttledPreview(); });
    form.addEventListener("change", () => preview(true));
    $$('[data-mode]').forEach(button => button.addEventListener("click", () => { $$('[data-mode]').forEach(item => item.classList.toggle("active", item === button)); $(".advanced-fields").hidden = button.dataset.mode !== "advanced"; if (button.dataset.mode === "advanced" && currentPayload) form.elements.explicit_spec.value = JSON.stringify(currentSpec, null, 2); preview(true); }));
    $$('[data-analysis]').forEach(button => button.addEventListener("click", () => { analysisTab = button.dataset.analysis; $$('[data-analysis]').forEach(item => item.classList.toggle("active", item === button)); if (currentPayload) renderAnalysis(currentPayload); }));
    $("[data-reset-view]").addEventListener("click", () => { const chart = $("#profile-chart"); if (chart?._fullLayout) Plotly.relayout(chart, { "xaxis.autorange": true, "yaxis.autorange": true }); });
    $("[data-export-spec]").addEventListener("click", () => downloadJSON(`${currentPayload?.geometry.geometry_id || "cascade-forge"}.json`, currentSpec));
    const fileInput = $("[data-spec-file]"); $("[data-import-spec]").addEventListener("click", () => fileInput.click());
    fileInput.addEventListener("change", async () => { try { currentSpec = JSON.parse(await fileInput.files[0].text()); form.elements.explicit_spec.value = JSON.stringify(currentSpec, null, 2); $("[data-mode='advanced']").click(); } catch (error) { notice(error.message, true); } });
    const dialog = $("#workspace-dialog"); $("[data-save-workspace]").addEventListener("click", () => dialog.showModal());
    dialog.addEventListener("close", async () => { if (dialog.returnValue !== "save" || !currentPayload) return; const data = new FormData($("form", dialog)); try { const saved = await fetchJSON("/api/v2/workspace/designs", { method: "POST", body: JSON.stringify({ name: data.get("name"), tags: String(data.get("tags") || "").split(",").map(v => v.trim()).filter(Boolean), spec: currentSpec }) }); notice(`已保存 ${saved.design_id}`); } catch (error) { notice(error.message, true); } });

    function renderStudio(payload) {
      const geometry = payload.geometry; const suction = xy(geometry.suction); const pressure = xy(geometry.pressure); const camber = xy(geometry.camber);
      const controls = geometry.spec.camber_curve.control_points;
      const traces = [
        { ...suction, type: "scatter", mode: "lines", name: "吸力面", line: { color: colors.blue, width: 2.4 } },
        { ...pressure, type: "scatter", mode: "lines", name: "压力面", line: { color: colors.orange, width: 2.4 } },
        { ...camber, type: "scatter", mode: "lines", name: "中弧线", line: { color: colors.muted, width: 1.3, dash: "dash" } },
        { x: controls.map(p => p[0]), y: controls.map(p => p[1]), type: "scatter", mode: "lines", name: "控制多边形", line: { color: "rgba(0,113,227,.35)", dash: "dot" } }
      ];
      Charts.render("profile-chart", traces, { xaxis: { title: "x/c", constrain: "domain", zeroline: false }, yaxis: { title: "y/c", scaleanchor: "x", scaleratio: 1, zeroline: false }, uirevision: "profile" }).then(() => drawControlOverlay());
      $("[data-geometry-id]").textContent = geometry.geometry_id; $("[data-intent-id]").textContent = geometry.intent_id;
      const validation = $("[data-validation-status]"); validation.textContent = payload.validation.status; validation.className = `pill ${payload.validation.status}`;
      $("[data-le-radius]").textContent = `r/c = ${geometry.edge_metrics.leading_edge_radius_ratio.toExponential(4)}`;
      $("[data-te-radius]").textContent = `r/c = ${geometry.edge_metrics.trailing_edge_radius_ratio.toExponential(4)}`;
      renderEdge("le-chart", geometry, "leading"); renderEdge("te-chart", geometry, "trailing"); renderAnalysis(payload);
    }
    function renderEdge(id, geometry, edge) {
      const isLE = edge === "leading"; const arc = geometry[`${edge}_edge_arc`]; const center = geometry.metadata[`${edge}_edge_center`];
      const contacts = [geometry.metadata[`${edge}_suction_contact`], geometry.metadata[`${edge}_pressure_contact`]]; const radius = geometry.edge_metrics[`${edge}_edge_radius_ratio`];
      const surfacePoints = [...geometry.suction, ...geometry.pressure].filter(p => isLE ? p[0] < .09 : p[0] > .91);
      const tangentTraces = contacts.map((contact, index) => { const radial = [contact[0] - center[0], contact[1] - center[1]]; const norm = Math.hypot(...radial) || 1; const tangent = [-radial[1] / norm, radial[0] / norm]; const length = radius * 1.8; return { x: [contact[0] - tangent[0] * length, contact[0] + tangent[0] * length], y: [contact[1] - tangent[1] * length, contact[1] + tangent[1] * length], mode: "lines", name: index ? "PS 切线" : "SS 切线", line: { color: colors.muted, dash: "dot" } }; });
      Charts.render(id, [ { ...xy(surfacePoints), mode: "markers", name: "轮廓", marker: { size: 3, color: "#c7c7cc" } }, { ...xy(arc), mode: "lines", name: "真实圆弧", line: { color: colors.blue, width: 3 } }, { x: [center[0]], y: [center[1]], mode: "markers", name: "圆心", marker: { symbol: "cross", size: 10, color: colors.red } }, { ...xy(contacts), mode: "markers", name: "接触点", marker: { size: 8, color: colors.orange } }, ...tangentTraces ], { margin: { l: 46, r: 12, t: 12, b: 38 }, xaxis: { title: "x/c", zeroline: false }, yaxis: { title: "y/c", scaleanchor: "x", scaleratio: 1, zeroline: false }, showlegend: true, legend: { orientation: "h", y: 1.08 } });
    }
    function renderAnalysis(payload) {
      const a = payload.analysis; let traces; let xTitle = "x/c"; let yTitle;
      if (analysisTab === "thickness") { traces = [{ x: a.camber_x, y: a.thickness, name: "t/c", line: { color: colors.blue, width: 2 } }]; yTitle = "t/c"; }
      else if (analysisTab === "curvature") { traces = [{ x: a.suction_s, y: a.suction_curvature, name: "吸力面", line: { color: colors.blue } }, { x: a.pressure_s, y: a.pressure_curvature, name: "压力面", line: { color: colors.orange } }]; xTitle = "s/c"; yTitle = "κc"; }
      else if (analysisTab === "gradient") { traces = [{ x: a.suction_s, y: a.suction_curvature_gradient, name: "吸力面", line: { color: colors.blue } }, { x: a.pressure_s, y: a.pressure_curvature_gradient, name: "压力面", line: { color: colors.orange } }]; xTitle = "s/c"; yTitle = "dκ/ds"; }
      else { traces = [{ x: a.camber_x, y: a.angle_deg, name: "中弧线角度", line: { color: colors.ink } }]; yTitle = "角度 / °"; }
      Charts.render("analysis-chart", traces, { xaxis: { title: xTitle, zeroline: false }, yaxis: { title: yTitle, zeroline: false }, margin: { l: 58, r: 15, t: 20, b: 44 } });
    }
    function drawControlOverlay() {
      const chart = $("#profile-chart"); const svg = $("[data-control-overlay]"); if (!chart?._fullLayout || !currentSpec?.camber_curve) return;
      const rect = chart.getBoundingClientRect(); svg.setAttribute("viewBox", `0 0 ${rect.width} ${rect.height}`); svg.innerHTML = "";
      const xa = chart._fullLayout.xaxis; const ya = chart._fullLayout.yaxis; const points = currentSpec.camber_curve.control_points;
      const pixels = points.map(p => [xa.l2p(p[0]) + xa._offset, ya.l2p(p[1]) + ya._offset]);
      const line = document.createElementNS("http://www.w3.org/2000/svg", "polyline"); line.setAttribute("points", pixels.map(p => p.join(",")).join(" ")); svg.appendChild(line);
      pixels.forEach((pixel, index) => { const circle = document.createElementNS("http://www.w3.org/2000/svg", "circle"); circle.setAttribute("cx", pixel[0]); circle.setAttribute("cy", pixel[1]); circle.setAttribute("r", 6); circle.addEventListener("pointerdown", event => { event.preventDefault(); circle.setPointerCapture(event.pointerId); const move = ev => { const bounds = svg.getBoundingClientRect(); const x = xa.p2l(ev.clientX - bounds.left - xa._offset); const y = ya.p2l(ev.clientY - bounds.top - ya._offset); currentSpec.authoring_mode = "advanced"; currentSpec.camber_curve.control_points[index] = [x, y]; form.elements.explicit_spec.value = JSON.stringify(currentSpec, null, 2); circle.setAttribute("cx", ev.clientX - bounds.left); circle.setAttribute("cy", ev.clientY - bounds.top); throttledPreview(); }; const up = () => { circle.removeEventListener("pointermove", move); preview(true); }; circle.addEventListener("pointermove", move); circle.addEventListener("pointerup", up, { once: true }); }); svg.appendChild(circle); });
    }
    preview(true);
  }

  function initDatabase() {
    const root = $("[data-database-root]"); if (!root) return; const form = $("[data-database-filters]"); let offset = 0; const limit = 50; const selected = new Set();
    async function load() { const query = new URLSearchParams({ limit, offset }); new FormData(form).forEach((value, key) => value && query.set(key, value)); try { const data = await fetchJSON(`/api/v2/geometries?${query}`); $("[data-database-count]").textContent = `${data.total} 条记录`; $("[data-page-label]").textContent = `第 ${Math.floor(offset / limit) + 1} 页`; $("[data-database-body]").innerHTML = data.items.map(row => `<tr><td><input type="checkbox" data-select-id="${escapeHTML(row.geometry_id)}" ${selected.has(row.geometry_id) ? "checked" : ""}></td><td><code>${escapeHTML(row.geometry_id)}</code></td><td>${escapeHTML(row.camber_family)} × ${escapeHTML(row.thickness_family)}</td><td>${statusPill(row.validation_status)}</td><td>${Number(row.max_thickness_ratio).toFixed(5)}</td><td>${Number(row.max_thickness_x_ratio).toFixed(5)}</td><td>${Number(row.inlet_metal_angle_deg).toFixed(2)}°</td><td><button class="button" data-view-id="${escapeHTML(row.geometry_id)}">详情</button></td></tr>`).join("") || `<tr><td colspan="8" class="empty-state">没有匹配记录</td></tr>`; bindRows(); } catch (error) { notice(error.message, true); } }
    function bindRows() { $$('[data-select-id]').forEach(box => box.addEventListener("change", () => { box.checked ? selected.add(box.dataset.selectId) : selected.delete(box.dataset.selectId); $("[data-compare-selected]").disabled = selected.size < 2; $("[data-export-selected]").disabled = selected.size !== 1; })); $$('[data-view-id]').forEach(button => button.addEventListener("click", () => showDetail(button.dataset.viewId))); }
    async function showDetail(id) { try { const data = await fetchJSON(`/api/v2/geometries/${id}`); const dialog = $("#geometry-detail"); $("[data-detail-content]").innerHTML = `<p class="eyebrow">GEOMETRY DETAIL</p><h2><code>${escapeHTML(id)}</code></h2><div id="detail-chart" class="plotly-chart analysis-chart"></div><pre>${escapeHTML(JSON.stringify(data.features, null, 2))}</pre>`; dialog.showModal(); const g = data.geometry; Charts.render("detail-chart", [{ ...xy(g.suction), name: "SS", line: { color: colors.blue } }, { ...xy(g.pressure), name: "PS", line: { color: colors.orange } }], { yaxis: { scaleanchor: "x", scaleratio: 1 }, xaxis: { title: "x/c" } }); } catch (error) { notice(error.message, true); } }
    form.addEventListener("submit", event => { event.preventDefault(); offset = 0; load(); }); $("[data-page-prev]").addEventListener("click", () => { offset = Math.max(0, offset - limit); load(); }); $("[data-page-next]").addEventListener("click", () => { offset += limit; load(); }); $("[data-close-dialog]").addEventListener("click", () => $("#geometry-detail").close());
    $("[data-compare-selected]").addEventListener("click", () => { sessionStorage.setItem("cf-compare", [...selected].join("\n")); location.href = "/compare"; });
    $("[data-export-selected]").addEventListener("click", () => { const id = [...selected][0]; location.href = `/api/v2/geometries/${encodeURIComponent(id)}/export`; }); load();
  }

  function initCompare() {
    const root = $("[data-compare-root]"); if (!root) return; const form = $("[data-compare-form]"); const saved = sessionStorage.getItem("cf-compare"); if (saved) form.elements.ids.value = saved;
    form.addEventListener("submit", async event => { event.preventDefault(); const ids = form.elements.ids.value.split(/\s+/).filter(Boolean); try { const data = await fetchJSON("/api/v2/compare", { method: "POST", body: JSON.stringify({ geometry_ids: ids }) }); const traces = []; data.items.forEach((g, index) => { const palette = [colors.blue, colors.orange, colors.green, "#af52de", colors.red]; traces.push({ ...xy(g.suction), name: `${g.geometry_id} SS`, line: { color: palette[index % palette.length] } }, { ...xy(g.pressure), name: `${g.geometry_id} PS`, line: { color: palette[index % palette.length], dash: "dash" } }); }); Charts.render("compare-chart", traces, { yaxis: { scaleanchor: "x", scaleratio: 1, title: "y/c" }, xaxis: { title: "x/c" } }); $("[data-compare-body]").innerHTML = data.features.map(f => `<tr><td><code>${escapeHTML(f.geometry_id)}</code></td><td>${f.camber_family} × ${f.thickness_family}</td><td>${f.max_thickness_ratio.toFixed(5)}</td><td>${f.max_thickness_x_ratio.toFixed(5)}</td><td>${f.max_camber_ratio.toFixed(5)}</td><td>${f.leading_edge_radius_ratio.toExponential(3)}</td><td>${f.trailing_edge_radius_ratio.toExponential(3)}</td></tr>`).join(""); } catch (error) { notice(error.message, true); } }); if (saved) form.requestSubmit();
  }

  function initCascade() {
    const form = $("[data-cascade-form]"); if (!form) return; const range = form.elements.solidity; range.addEventListener("input", () => $("output[data-for='solidity']").textContent = Number(range.value).toFixed(2)); form.addEventListener("submit", async event => { event.preventDefault(); try { const data = await fetchJSON("/api/v2/cascade", { method: "POST", body: JSON.stringify({ geometry_id: form.elements.geometry_id.value.trim(), solidity: Number(range.value) }) }); const traces = []; data.passages.forEach((passage, index) => { traces.push({ ...xy(passage.suction), name: `SS ${passage.offset}`, line: { color: index === 1 ? colors.blue : "#b7b7bc" } }, { ...xy(passage.pressure), name: `PS ${passage.offset}`, line: { color: index === 1 ? colors.orange : "#d0d0d4" } }); }); Charts.render("cascade-chart", traces, { yaxis: { scaleanchor: "x", scaleratio: 1, title: "节距方向 y/c" }, xaxis: { title: "x/c" }, showlegend: false }); $("[data-cascade-summary]").innerHTML = `<dl class="definition-grid"><dt>状态</dt><dd>${statusPill(data.validation.status)}</dd><dt>节距 c</dt><dd>${data.pitch_ratio.toFixed(5)}</dd><dt>喉道代理</dt><dd>${Number(data.validation.metrics.cascade_throat_proxy_ratio).toFixed(5)}</dd></dl>`; } catch (error) { notice(error.message, true); } });
  }

  function initAnchors() {
    const button = $("[data-start-anchor-fit]"); if (!button) return;
    async function loadFits() { const status = $("[data-anchor-status]")?.value || ""; try { const data = await fetchJSON(`/api/v2/anchor-fits?limit=100${status ? `&status=${status}` : ""}`); $("[data-anchor-fit-body]").innerHTML = data.items.map(row => `<tr><td><code>${escapeHTML(row.source_anchor_id)}</code></td><td>${row.camber_family} × ${row.thickness_family}</td><td>${statusPill(row.fit_status)}</td><td>${Number(row.rms_distance_ratio).toExponential(3)}</td><td>${Number(row.max_distance_ratio).toExponential(3)}</td><td>${Number(row.hausdorff_approx_ratio).toExponential(3)}</td></tr>`).join("") || `<tr><td colspan="6" class="empty-state">尚无派生拟合</td></tr>`; } catch (error) { notice(error.message, true); } }
    $("[data-anchor-status]")?.addEventListener("change", loadFits); loadFits();
    button.addEventListener("click", async () => { button.disabled = true; try { const queued = await fetchJSON("/api/v2/anchor-fits", { method: "POST", body: "{}" }); const state = $("[data-anchor-job-state]"); const job = await pollJob(queued.job_id, value => { state.textContent = value.status; state.className = `pill ${value.status}`; $("[data-anchor-progress]").style.width = value.status === "running" ? "55%" : value.status === "completed" ? "100%" : "8%"; }); if (job.status === "failed") throw new Error(job.error); $("[data-anchor-message]").textContent = `完成：${job.result.anchors} 个锚点，${job.result.anchor_fits} 个派生拟合。`; await loadFits(); notice("NASA 锚点拟合完成"); } catch (error) { notice(error.message, true); } finally { button.disabled = false; } });
  }

  function initBlade() {
    const root = $("[data-blade-root]"); if (!root) return; const editor = $("[data-section-editor]"); const template = $("#section-row-template");
    function add(values = {}) { const fragment = template.content.cloneNode(true); const row = $(".section-row", fragment); Object.entries(values).forEach(([key, value]) => { if (row.querySelector(`[name='${key}']`)) row.querySelector(`[name='${key}']`).value = value; }); row.querySelector("[data-remove-section]").addEventListener("click", () => row.remove()); editor.appendChild(fragment); }
    add({ span_fraction: 0, radius_m: .2, chord_m: .04, stagger_deg: 30 }); add({ span_fraction: 1, radius_m: .3, chord_m: .03, stagger_deg: 25 }); $("[data-add-section]").addEventListener("click", () => add());
    $("[data-blade-form]").addEventListener("submit", async event => { event.preventDefault(); const form = event.currentTarget; const sections = $$(".section-row", editor).map(row => { const value = name => row.querySelector(`[name='${name}']`).value; return { span_fraction: Number(value("span_fraction")), radius_m: Number(value("radius_m")), chord_m: Number(value("chord_m")), geometry_id: value("geometry_id"), stagger_deg: Number(value("stagger_deg")), axial_offset_m: 0, tangential_offset_m: 0, lean_m: Number(value("lean_m")), sweep_m: Number(value("sweep_m")) }; }); const schedule = { name: form.elements.name.value, blade_count: Number(form.elements.blade_count.value), rotation_direction: form.elements.rotation_direction.value, sections, output_section_count: Number(form.elements.output_section_count.value), units: "m" }; try { const preview = await fetchJSON("/api/v2/blades/preview", { method: "POST", body: JSON.stringify(schedule) }); const traces = []; preview.sections.forEach((section, index) => ["suction_xyz", "pressure_xyz"].forEach((side, sideIndex) => { const points = section[side]; traces.push({ type: "scatter3d", mode: "lines", x: points.map(p => p[0]), y: points.map(p => p[1]), z: points.map(p => p[2]), name: `${index + 1} ${sideIndex ? "PS" : "SS"}`, line: { color: sideIndex ? colors.orange : colors.blue, width: 3 }, showlegend: index === 0 }); })); Charts.render("blade-chart", traces, { scene: { aspectmode: "data", xaxis: { title: "X / m" }, yaxis: { title: "Y / m" }, zaxis: { title: "Z / m" } }, margin: { l: 0, r: 0, t: 12, b: 0 } }); const queued = await fetchJSON("/api/v2/blades", { method: "POST", body: JSON.stringify(schedule) }); const summary = $("[data-blade-job]"); const job = await pollJob(queued.job_id, value => summary.textContent = `Job ${value.status}…`); if (job.status === "failed") throw new Error(job.error); summary.innerHTML = `已生成 <code>${escapeHTML(job.result.blade_id)}</code> · <a href="/api/v2/artifacts/${encodeURI(job.result.artifact)}">下载 GeomTurbo</a>`; } catch (error) { notice(error.message, true); } });
  }

  function initDatasets() {
    const form = $("[data-batch-form]"); if (!form) return; form.addEventListener("submit", async event => { event.preventDefault(); try { const queued = await fetchJSON("/api/v2/batches", { method: "POST", body: JSON.stringify({ target_valid: Number(form.elements.target_valid.value), workers: Number(form.elements.workers.value) }) }); const summary = $("[data-batch-status]"); const progress = $("[data-batch-progress]"); const job = await pollJob(queued.job_id, value => { summary.textContent = `Job ${value.status} · ${queued.job_id}`; progress.style.width = value.status === "running" ? "58%" : value.status === "completed" ? "100%" : "8%"; }); if (job.status === "failed") throw new Error(job.error); summary.innerHTML = `完成 ${job.result.generated_valid} 个生成叶型；接受率 ${(job.result.acceptance_rate * 100).toFixed(2)}%。`; notice("数据集批次完成"); } catch (error) { notice(error.message, true); } });
  }

  function initAudit() {
    const button = $("[data-run-audit]"); if (!button) return; button.addEventListener("click", async () => { button.disabled = true; try { const data = await fetchJSON("/api/v2/audit?rebuild_sample=8"); const ring = $("[data-audit-ring]"); ring.textContent = data.valid ? "PASS" : "FAIL"; ring.className = `audit-ring ${data.valid ? "pass" : "fail"}`; $("[data-audit-title]").textContent = data.valid ? "数据集通过审计" : "数据集存在未通过项"; $("[data-audit-checks]").innerHTML = Object.entries(data.checks).map(([key, value]) => `<div class="audit-check"><span>${escapeHTML(key)}</span><strong class="${value ? "pass" : "fail"}">${value ? "通过" : "失败"}</strong></div>`).join(""); $("[data-audit-json]").textContent = JSON.stringify(data, null, 2); } catch (error) { notice(error.message, true); } finally { button.disabled = false; } });
  }

  function initJobs() {
    const root = $("[data-jobs-root]"); if (!root) return; async function load() { try { const data = await fetchJSON("/api/v2/jobs"); $("[data-jobs-empty]").hidden = data.items.length > 0; $("[data-jobs-body]").innerHTML = data.items.map(job => { const artifact = job.result?.artifact ? `<a href="/api/v2/artifacts/${encodeURI(job.result.artifact)}">下载制品</a>` : job.error ? escapeHTML(job.error) : "—"; return `<tr><td><code>${escapeHTML(job.job_id)}</code></td><td>${escapeHTML(job.job_type)}</td><td>${statusPill(job.status)}</td><td>${escapeHTML(job.updated_at)}</td><td>${artifact}</td></tr>`; }).join(""); } catch (error) { notice(error.message, true); } } $("[data-refresh-jobs]")?.addEventListener("click", load); load();
  }

  document.addEventListener("DOMContentLoaded", () => { initShell(); initStudio(); initDatabase(); initCompare(); initCascade(); initAnchors(); initBlade(); initDatasets(); initAudit(); initJobs(); });
})();
