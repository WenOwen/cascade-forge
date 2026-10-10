(() => {
  "use strict";

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const root = $("[data-naca-root]");
  if (!root) return;

  const colors = { blue: "#0071e3", orange: "#ff7a00", green: "#248a3d", red: "#d70015", ink: "#1d1d1f", muted: "#6e6e73" };
  const charts = window.CFCharts;
  const form = $("[data-naca-form]", root);
  let inputMode = "general";
  let currentPayload = null;

  function notice(message, error = false) {
    const node = $("#global-notice");
    if (!node) return;
    node.textContent = message;
    node.className = `global-notice visible${error ? " error" : ""}`;
    clearTimeout(notice.timer);
    notice.timer = setTimeout(() => node.className = "global-notice", 3200);
  }

  async function fetchJSON(url, options = {}) {
    const response = await fetch(url, { headers: { "Content-Type": "application/json" }, ...options });
    const payload = await response.json();
    if (!response.ok) {
      const detail = payload.detail || payload;
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    return payload;
  }

  const xy = points => ({ x: points.map(point => point[0]), y: points.map(point => point[1]) });
  const escapeHTML = value => String(value ?? "").replace(/[&<>'"]/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[char]);
  const scientific = value => value == null ? "—" : Number(value).toExponential(3);
  const statusPill = status => `<span class="pill ${escapeHTML(status)}">${escapeHTML(status)}</span>`;

  function setMode(mode) {
    inputMode = mode;
    $$('[data-naca-mode]', root).forEach(button => button.classList.toggle("active", button.dataset.nacaMode === mode));
    $$('[data-general-field]', root).forEach(node => node.hidden = mode !== "general");
    $$('[data-preset-field]', root).forEach(node => node.hidden = mode !== "preset");
  }

  function pairControls() {
    $$('[data-naca-parameter]', root).forEach(group => {
      const number = $("[data-number]", group);
      const range = $("[data-range]", group);
      [number, range].forEach(input => input.addEventListener("input", () => {
        if (input.value === "") return;
        const value = Math.max(Number(input.min), Math.min(Number(input.max), Number(input.value)));
        input.value = String(value);
        (input === number ? range : number).value = input.value;
      }));
    });
  }

  function updateFamily() {
    const compressor = form.elements.family.value === "65-compressor";
    const fixed = compressor || form.elements.family.value === "65A";
    const group = $("[data-naca-parameter='loading_parameter_a']", root);
    $$('input', group).forEach(input => input.disabled = fixed);
    if (fixed) $$('input', group).forEach(input => input.value = compressor ? "1.0" : "0.8");
  }

  async function buildSpec() {
    if (inputMode === "preset") {
      const parsed = await fetchJSON("/api/v2/naca65/presets/parse", {
        method: "POST",
        body: JSON.stringify({ designation: form.elements.preset.value.trim() })
      });
      return parsed.spec;
    }
    return {
      schema_version: "1.0.0",
      algorithm_version: form.elements.family.value === "65-compressor" ? "naca65-compressor-table-v1" : "nasa-sixseries-1974-python-v1",
      family: form.elements.family.value,
      thickness_ratio: Number(form.elements.thickness_ratio.value),
      design_lift_coefficient: Number(form.elements.design_lift_coefficient.value),
      loading_parameter_a: Number(form.elements.loading_parameter_a.value),
      input_mode: "general",
      preset_designation: null,
      points_per_side: 201
    };
  }

  async function generateOnly() {
    setBusy("生成中…");
    try {
      const spec = await buildSpec();
      const payload = await fetchJSON("/api/v2/naca65/generate", { method: "POST", body: JSON.stringify(spec) });
      currentPayload = { reference: payload.reference, fit_report: null, fitted_geometry: null };
      renderResult(currentPayload);
      setBusy("原生坐标已生成", "success");
    } catch (error) {
      reject(error);
    }
  }

  async function generateAndFit() {
    setBusy("约束拟合中…", "running");
    try {
      const spec = await buildSpec();
      currentPayload = await fetchJSON("/api/v2/naca65/fit", {
        method: "POST",
        body: JSON.stringify({ spec, max_outer_iterations: 6, max_design_evaluations: 80 })
      });
      renderResult(currentPayload);
      setBusy("拟合完成", currentPayload.fit_report.fit_status);
    } catch (error) {
      reject(error);
    }
  }

  function setBusy(text, state = "running") {
    const node = $("[data-naca-fit-state]", root);
    node.textContent = text;
    node.className = `pill ${state}`;
    $("[data-naca-error]", root).textContent = "";
    $("[data-naca-fit]", root).disabled = state === "running";
    $("[data-naca-generate]", root).disabled = state === "running";
  }

  function reject(error) {
    setBusy("输入被拒绝", "rejected");
    $("[data-naca-error]", root).textContent = error.message;
    notice(error.message, true);
  }

  function renderResult(payload) {
    const reference = payload.reference;
    const fitted = payload.fitted_geometry;
    $("[data-naca-export-dat]", root).disabled = false;
    const datSource = $("[data-naca-dat-source]", root);
    datSource.querySelector('[value="fitted"]').disabled = !fitted;
    if (!fitted) datSource.value = "native";
    const traces = [
      { ...xy(reference.normalized_suction), mode: "lines", name: "NACA 原生 SS", line: { color: colors.blue, width: 2.6 } },
      { ...xy(reference.normalized_pressure), mode: "lines", name: "NACA 原生 PS", line: { color: colors.orange, width: 2.6 } },
      { ...xy(reference.camber), mode: "lines", name: "NACA 中弧线", line: { color: colors.muted, dash: "dot" } }
    ];
    if (fitted) {
      traces.push(
        { ...xy(fitted.suction), mode: "lines", name: "B-spline SS", line: { color: colors.blue, width: 1.8, dash: "dash" } },
        { ...xy(fitted.pressure), mode: "lines", name: "B-spline PS", line: { color: colors.orange, width: 1.8, dash: "dash" } },
        { ...xy(fitted.camber), mode: "lines", name: "B-spline 中弧线", line: { color: colors.green, dash: "dashdot" } }
      );
      const crossSections = [];
      for (let i = 10; i < fitted.camber.length - 10; i += 10) {
        if (fitted.spec.trailing_edge === "rounded" && fitted.camber[i][0] > 0.95) continue;
        crossSections.push((fitted.metadata.body_pressure || fitted.pressure)[i], fitted.camber[i], (fitted.metadata.body_suction || fitted.suction)[i], [null, null]);
      }
      traces.push({ ...xy(crossSections), mode: "lines", name: "法向厚度连线", hoverinfo: "skip",
        line: { color: "#b4c5ba", width: 1 }, visible: $("[data-naca-normals]", root).checked });
    }
    charts.render("naca-profile-chart", traces, {
      xaxis: { title: { text: "轴向 x/c（γ=0°）" }, constrain: "domain", zeroline: false },
      yaxis: { title: { text: "切向 y/c" }, scaleanchor: "x", scaleratio: 1, zeroline: false },
      uirevision: `${reference.native_id}:${fitted?.geometry_id || "native"}`
    });
    $("[data-naca-designation]", root).textContent = reference.designation;
    $("[data-naca-native-id]", root).textContent = reference.native_id;
    $("[data-naca-geometry-id]", root).textContent = fitted?.geometry_id || "尚未拟合";
    renderMetrics(payload.fit_report);
    renderErrors(reference, payload.fit_report);
    renderEdges(reference, fitted);
    $$('[data-invariant]', root).forEach(node => {
      const value = payload.fit_report?.diagnostics?.[node.dataset.invariant];
      node.textContent = node.hasAttribute("data-angle-value") && value != null ? `${Number(value).toFixed(4)}°` : scientific(value);
    });
    $("[data-naca-warnings]", root).innerHTML = (payload.fit_report?.warnings || []).map(w => `<li>${escapeHTML(w)}</li>`).join("");
    const sample = reference.parameter;
    const thicknessTraces = [{ x: sample, y: reference.thickness.map(t => t / 2), name: "原生半厚度", mode: "lines", line: { color: colors.ink } }];
    if (fitted) thicknessTraces.push({ x: fitted.parameter, y: fitted.thickness.map(t => t / 2), name: "共享 B-spline 半厚度", mode: "lines", line: { color: colors.green, dash: "dash" } });
    charts.render("naca-thickness-chart", thicknessTraces, { xaxis: { title: { text: "中弧线弧长 s" } }, yaxis: { title: { text: "半厚度 t/c" } } });
  }

  function renderMetrics(report) {
    const values = report ? {
      rms: report.rms_distance_ratio,
      max: report.max_distance_ratio,
      "core-rms": report.core_rms_distance_ratio,
      hausdorff: report.hausdorff_approx_ratio
    } : {};
    $$('[data-metric]', root).forEach(node => node.textContent = scientific(values[node.dataset.metric]));
    const status = $("[data-naca-status]", root);
    status.textContent = report?.fit_status || "未拟合";
    status.className = `pill ${report?.fit_status || ""}`;
    const rows = report ? Object.entries(report.feature_deltas).map(([name, value]) => `<tr><td>${escapeHTML(name)}</td><td>${Number(value).toExponential(4)}</td></tr>`).join("") : '<tr><td colspan="2" class="empty-state">拟合后显示</td></tr>';
    $("[data-naca-feature-deltas]", root).innerHTML = rows;
  }

  function renderErrors(reference, report) {
    const x = report?.error_parameter || [];
    const traces = report ? [
      { x, y: report.suction_signed_normal_error, mode: "lines", name: "吸力面", line: { color: colors.blue } },
      { x, y: report.pressure_signed_normal_error, mode: "lines", name: "压力面", line: { color: colors.orange } }
    ] : [];
    charts.render("naca-error-chart", traces, { xaxis: { title: { text: "叶面归一化弧长" }, zeroline: false }, yaxis: { title: { text: "外向叶面法向误差 / c" }, zeroline: true } });
  }

  function renderEdges(reference, fitted) {
    const count = 30;
    const render = (edge, isLeading) => {
      const select = points => isLeading ? points.slice(0, count) : points.slice(-count);
      const traces = [
        { ...xy(select(reference.normalized_suction)), mode: "lines+markers", name: "NACA SS", marker: { size: 3 }, line: { color: colors.blue } },
        { ...xy(select(reference.normalized_pressure)), mode: "lines+markers", name: "NACA PS", marker: { size: 3 }, line: { color: colors.orange } }
      ];
      if (!isLeading && reference.metadata.trailing_edge_arc) {
        traces.push({ ...xy(reference.metadata.trailing_edge_arc), mode: "lines", name: "圆尾缘", line: { color: colors.red, width: 2 } });
      }
      if (fitted) {
        const view = fitted.metadata?.edge_views?.[edge];
        const fittedSuction = view?.suction || select(fitted.suction);
        const fittedPressure = view?.pressure || select(fitted.pressure);
        traces.push(
          { ...xy(fittedSuction), mode: "lines", name: "拟合 SS（加密）", line: { color: colors.blue, dash: "dash" } },
          { ...xy(fittedPressure), mode: "lines", name: "拟合 PS（加密）", line: { color: colors.orange, dash: "dash" } }
        );
        const circle = fitted[`${edge}_osculating_circle`];
        if (circle?.length) {
          traces.push({ ...xy(circle), mode: "lines", name: "极限密切圆", line: { color: colors.red, dash: "dot" } });
          traces.push({ ...xy([fitted.metadata[`${edge}_edge_center`]]), mode: "markers", name: "圆心", marker: { color: colors.red, symbol: "cross", size: 7 } });
        }
        traces.push({ ...xy([isLeading ? fitted.camber[0] : fitted.camber.at(-1)]), mode: "markers", name: isLeading ? "鼻点" : "尾缘端点", marker: { color: colors.ink, size: 6 } });
      }
      const radius = reference.metadata.trailing_edge_radius_ratio;
      const center = reference.metadata.trailing_edge_center;
      const closeup = !isLeading && radius && center;
      charts.render(`naca-${isLeading ? "le" : "te"}-chart`, traces, { margin: { l: 48, r: 12, t: 25, b: 38 }, xaxis: { title: { text: "x/c" }, zeroline: false, ...(closeup ? { range: [center[0] - 5*radius, center[0] + 2*radius] } : {}) }, yaxis: { title: { text: "y/c" }, scaleanchor: "x", scaleratio: 1, zeroline: false, ...(closeup ? { range: [center[1] - 4*radius, center[1] + 4*radius] } : {}) }, showlegend: false });
    };
    render("leading", true);
    render("trailing", false);
    $("[data-naca-le]", root).textContent = fitted ? `r/c = ${fitted.edge_metrics.leading_edge_radius_ratio.toExponential(4)}` : "—";
    const radius = fitted?.edge_metrics.trailing_edge_radius_ratio || reference.metadata.trailing_edge_radius_ratio;
    $("[data-naca-te]", root).textContent = radius ? `圆尾缘 · r/c=${radius.toExponential(2)}` : (fitted ? `尖尾缘 · 楔角 ${fitted.edge_metrics.trailing_wedge_angle_deg.toFixed(3)}°` : "尖尾缘");
  }

  async function loadCases() {
    try {
      const data = await fetchJSON("/api/v2/naca65/cases?limit=100&sort=designation");
      renderSummary(data);
      renderCaseTable(data.items);
      renderHeatmap(data.items);
      return data;
    } catch (error) {
      notice(error.message, true);
      return null;
    }
  }

  function renderSummary(data) {
    const distribution = data.status_distribution || {};
    $("[data-summary='total']", root).textContent = `${Math.min(data.total, 36)}/9`;
    $("[data-summary='valid']", root).textContent = distribution.valid || 0;
    $("[data-summary='warning']", root).textContent = distribution.warning || 0;
    $("[data-summary='rejected']", root).textContent = (distribution.rejected || 0) + (distribution.failed || 0);
    $("[data-naca-progress]", root).style.width = `${Math.min(100, data.total / 9 * 100)}%`;
    const state = $("[data-naca-job-state]", root);
    if (!state.classList.contains("running")) {
      state.textContent = data.total >= 36 ? "36 型已保存" : data.total ? "部分结果已保存" : "未运行";
    }
  }

  function updateCaseDatLinks() {
    const source = $("[data-naca-case-dat-source]", root).value;
    const layout = $("[data-naca-case-dat-layout]", root).value;
    $$('[data-naca-case-dat]', root).forEach(link => {
      link.href = `/api/v2/naca65/cases/${encodeURIComponent(link.dataset.nacaCaseDat)}/export?format=dat&source=${source}&layout=${layout}`;
      link.textContent = `${source === "fitted" ? "拟合" : "原生"}·${layout === "split" ? "ZIP" : "DAT"}`;
      link.title = layout === "split" ? "两份独立 DAT：Suction TE→LE，Pressure LE→TE" : "单条闭合轮廓";
    });
  }

  function renderCaseTable(items) {
    $("[data-naca-cases]", root).innerHTML = items.map(row => `<tr data-fit-id="${escapeHTML(row.fit_id)}"><td><button class="table-link" type="button">${escapeHTML(row.designation)}</button></td><td>${row.family}</td><td>${Number(row.thickness_ratio).toFixed(3)}</td><td>${Number(row.design_lift_coefficient).toFixed(1)}</td><td>${Number(row.loading_parameter_a).toFixed(1)}</td><td>${statusPill(row.fit_status)}</td><td>${scientific(row.rms_distance_ratio)}</td><td>${scientific(row.max_distance_ratio)}</td><td class="export-links"><a data-naca-case-dat="${escapeHTML(row.fit_id)}" href="/api/v2/naca65/cases/${encodeURIComponent(row.fit_id)}/export?format=dat&layout=split">原生·ZIP</a><a href="/api/v2/naca65/cases/${encodeURIComponent(row.fit_id)}/export?format=csv">CSV</a><a href="/api/v2/naca65/cases/${encodeURIComponent(row.fit_id)}/export?format=json">JSON</a><a href="/api/v2/naca65/cases/${encodeURIComponent(row.fit_id)}/export?format=parquet">Parquet</a></td></tr>`).join("") || '<tr><td colspan="9" class="empty-state">尚无基准结果</td></tr>';
    updateCaseDatLinks();
    $$('[data-fit-id]', root).forEach(row => $("button", row).addEventListener("click", async () => {
      try {
        const detail = await fetchJSON(`/api/v2/naca65/cases/${encodeURIComponent(row.dataset.fitId)}`);
        currentPayload = { reference: detail.reference, fit_report: detail.fit_report, fitted_geometry: detail.fitted_geometry };
        renderResult(currentPayload);
        setBusy("拟合完成", detail.fit_report.fit_status);
        root.scrollIntoView({ behavior: "smooth", block: "start" });
      } catch (error) { notice(error.message, true); }
    }));
  }

  function renderHeatmap(items) {
    const xLabels = [...new Set(items.map(row => `${row.family} · a=${Number(row.loading_parameter_a).toFixed(1)} · t=${Number(row.thickness_ratio).toFixed(2)}`))];
    const yValues = [...new Set(items.map(row => Number(row.design_lift_coefficient)))].sort((a, b) => a - b);
    const z = yValues.map(lift => xLabels.map(label => {
      const row = items.find(item => Number(item.design_lift_coefficient) === lift && `${item.family} · a=${Number(item.loading_parameter_a).toFixed(1)} · t=${Number(item.thickness_ratio).toFixed(2)}` === label);
      return row?.rms_distance_ratio ?? null;
    }));
    charts.render("naca-heatmap-chart", [{ type: "heatmap", x: xLabels, y: yValues, z, colorscale: "Blues", colorbar: { title: { text: "RMS/c" } }, hovertemplate: "%{x}<br>Clᵢ=%{y}<br>RMS=%{z:.3e}<extra></extra>" }], { margin: { l: 48, r: 20, t: 20, b: 120 }, xaxis: { tickangle: -45 }, yaxis: { title: { text: "Clᵢ" } } });
  }

  async function runBenchmark() {
    $$('[data-naca-run-benchmark]').forEach(button => button.disabled = true);
    const state = $("[data-naca-job-state]", root);
    try {
      const queued = await fetchJSON("/api/v2/naca65/benchmarks", { method: "POST", body: JSON.stringify({ compressor: true }) });
      state.textContent = "运行中";
      state.className = "pill running";
      for (;;) {
        const [job] = await Promise.all([fetchJSON(`/api/v2/jobs/${queued.job_id}`), loadCases()]);
        if (["completed", "failed"].includes(job.status)) {
          if (job.status === "failed") throw new Error(job.error);
          state.textContent = "36 型完成";
          state.className = "pill completed";
          notice("NACA 65 基准矩阵完成");
          break;
        }
        await new Promise(resolve => setTimeout(resolve, 700));
      }
    } catch (error) {
      state.textContent = "任务失败";
      state.className = "pill failed";
      notice(error.message, true);
    } finally {
      $$('[data-naca-run-benchmark]').forEach(button => button.disabled = false);
      await loadCases();
    }
  }

  $("[data-naca-export-dat]", root).addEventListener("click", async () => {
    if (!currentPayload) return;
    const button = $("[data-naca-export-dat]", root);
    button.disabled = true;
    try {
      const source = $("[data-naca-dat-source]", root).value;
      const layout = $("[data-naca-dat-layout]", root).value;
      const response = await fetch("/api/v2/naca65/export/dat", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reference: currentPayload.reference, fitted_geometry: currentPayload.fitted_geometry, source, layout })
      });
      if (!response.ok) {
        const error = await response.json();
        throw new Error(typeof error.detail === "string" ? error.detail : JSON.stringify(error.detail));
      }
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = url; link.download = `${currentPayload.reference.native_id}-${source}.${layout === "split" ? "zip" : "dat"}`;
      document.body.appendChild(link); link.click(); link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) { notice(error.message, true); }
    finally { button.disabled = false; }
  });
  $$('[data-naca-case-dat-source], [data-naca-case-dat-layout]', root).forEach(select => select.addEventListener("change", updateCaseDatLinks));
  $$('[data-naca-mode]', root).forEach(button => button.addEventListener("click", () => setMode(button.dataset.nacaMode)));
  form.elements.family.addEventListener("change", updateFamily);
  form.addEventListener("submit", event => { event.preventDefault(); generateAndFit(); });
  $("[data-naca-generate]", root).addEventListener("click", generateOnly);
  $$('[data-naca-run-benchmark]').forEach(button => button.addEventListener("click", runBenchmark));
  $$('[data-naca-refresh]').forEach(button => button.addEventListener("click", loadCases));
  $("[data-naca-reset-view]", root).addEventListener("click", () => {
    const chart = $("#naca-profile-chart");
    if (chart?._fullLayout) Plotly.relayout(chart, { "xaxis.autorange": true, "yaxis.autorange": true });
  });
  pairControls();
  $("[data-naca-normals]", root).addEventListener("change", () => {
    if (currentPayload) renderResult(currentPayload);
  });
  updateFamily();
  setMode("general");
  generateOnly();
  loadCases();
})();
