/* 圆弧族实验室前端：所有几何计算都在 Python 后端完成，这里只负责表单、可行域查询与绘图。 */
(() => {
  "use strict";

  const $ = (selector, scope = document) => scope.querySelector(selector);
  const $$ = (selector, scope = document) => [...scope.querySelectorAll(selector)];
  const root = $("[data-arc-root]");
  if (!root) return;

  const charts = window.CFCharts;
  const form = $("[data-arc-form]", root);
  const errorBox = $("[data-arc-error]", root);
  const statePill = $("[data-arc-state]", root);
  const designation = $("[data-arc-designation]", root);
  const profileIdBox = $("[data-arc-profile-id]", root);
  const meanLineIdBox = $("[data-arc-mean-line-id]", root);
  const downloadButton = $("[data-arc-download]", root);
  const generateButton = $("[data-arc-generate]", root);
  const familySelect = $("[data-arc-family]", root);
  const closureSelect = $("[data-arc-closure]", root);
  const closureField = $("[data-arc-closure-field]", root);
  const junctionField = $("[data-arc-junction-field]", root);
  const junctionHint = $("[data-arc-junction-hint]", root);
  const anglesField = $("[data-arc-angles-field]", root);
  const anglesLabel = $("[data-arc-angles-label]", root);
  const anglesHint = $("[data-arc-angles-hint]", root);
  const stationsField = $("[data-arc-stations-field]", root);
  const stationsHint = $("[data-arc-stations-hint]", root);
  const normalsToggle = $("[data-arc-normals]", root);
  const arcsToggle = $("[data-arc-arcs]", root);

  const MODEL_LIMITS = {
    trailing_edge_half_wedge_deg: { min: 0.51, max: 44.9 },
    junction_station: { min: 0.0001, max: 0.9999 },
  };

  let current = null;
  let limits = null;
  let limitsTimer = 0;
  let limitsSequence = 0;
  const uiRevision = `arc-${Date.now()}`;

  // 按 name 取控件：多数是 <input type=number>，但 points_per_side 是 <select>。
  const numberInput = name => form.querySelector(`[name="${name}"]`);
  const numberValue = name => {
    const input = numberInput(name);
    return input ? Number(input.value) : 0;
  };
  const setHint = (node, text) => {
    if (node) node.textContent = text || "";
  };

  /** 把后端返回的错误统一成可读字符串。
   *
   * Pydantic 的请求校验失败把 `detail` 返回成**数组**，直接赋给 textContent 会渲染成
   * "[object Object]"（旧实现的真实缺陷）。这里逐条取出字段路径与消息再拼接。
   */
  function describeError(payload, fallback = "生成失败") {
    const detail = payload && payload.detail !== undefined ? payload.detail : payload;
    if (typeof detail === "string" && detail.trim()) return detail;
    if (Array.isArray(detail)) {
      const parts = detail.map(item => {
        if (typeof item === "string") return item;
        if (!item || typeof item !== "object") return String(item);
        const location = Array.isArray(item.loc)
          ? item.loc.filter(part => part !== "body").join(".")
          : "";
        const message = item.msg || item.message || JSON.stringify(item);
        return location ? `${location}：${message}` : String(message);
      });
      if (parts.length) return parts.join("；");
    }
    if (detail && typeof detail === "object") return JSON.stringify(detail);
    return fallback;
  }

  async function fetchJSON(url, body) {
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    let payload = null;
    try {
      payload = await response.json();
    } catch (parseError) {
      payload = { detail: `响应不是合法 JSON（HTTP ${response.status}）` };
    }
    if (!response.ok) throw new Error(describeError(payload, `HTTP ${response.status}`));
    return payload;
  }

  function clampControl(input) {
    if (!input || input.value === "") return;
    const min = Number(input.min);
    const max = Number(input.max);
    let value = Number(input.value);
    if (!Number.isFinite(value)) return;
    if (Number.isFinite(min)) value = Math.max(min, value);
    if (Number.isFinite(max)) value = Math.min(max, value);
    input.value = String(value);
  }

  function pairControls() {
    $$("[data-arc-parameter]", root).forEach(group => {
      const number = $("[data-arc-number]", group);
      const range = $("[data-arc-range]", group);
      [number, range].forEach(input => {
        input.addEventListener("input", () => {
          if (input.value === "") return;
          clampControl(input);
          (input === number ? range : number).value = input.value;
          scheduleLimits();
        });
        input.addEventListener("change", () => {
          clampControl(input);
          (input === number ? range : number).value = input.value;
          scheduleLimits();
        });
      });
    });
  }

  function setRange(name, minimum, maximum) {
    const input = numberInput(name);
    const group = input ? input.closest("[data-arc-parameter]") : null;
    const range = group ? $("[data-arc-range]", group) : null;
    const minLabel = $(`[data-arc-min="${name}"]`, root);
    const maxLabel = $(`[data-arc-max="${name}"]`, root);
    const low = Number(minimum.toFixed(4));
    const high = Number(maximum.toFixed(4));
    for (const node of [input, range]) {
      if (!node) continue;
      node.min = String(low);
      node.max = String(high);
    }
    if (minLabel) minLabel.textContent = String(low);
    if (maxLabel) maxLabel.textContent = String(high);
    for (const node of [input, range]) {
      if (node) clampControl(node);
    }
    if (input && range) range.value = input.value;
  }

  function syncVisibility() {
    const family = familySelect.value;
    const closure = closureSelect.value;
    closureField.hidden = family !== "double";
    junctionField.hidden = !(family === "double" && closure === "junction_station");
    anglesField.hidden = !(family === "multiple" || (family === "double" && closure === "node_angles"));
    stationsField.hidden = family !== "multiple";
    if (family === "multiple") {
      anglesLabel.textContent = "内节点切线角序列（逗号分隔）";
      setHint(anglesHint, "首尾由进出口金属角给出，这里只填中间节点，必须严格递减。");
    } else if (family === "double" && closure === "node_angles") {
      anglesLabel.textContent = "切点切线角（单个数值）";
      setHint(anglesHint, "必须落在 (−χ₂, χ₁) 内；它是双圆弧唯一的切点自由度。");
    } else {
      setHint(anglesHint, "");
    }
    const inlet = numberInput("inlet_metal_angle_deg");
    const outlet = numberInput("outlet_metal_angle_deg");
    if (inlet && outlet) {
      if (family === "single") {
        // 单圆弧的两端切线角必然相等：这是闭合条件的唯一解，不是约定。
        outlet.value = inlet.value;
        outlet.disabled = true;
        const outletRange = outlet.closest("[data-arc-parameter]")
          ? $("[data-arc-range]", outlet.closest("[data-arc-parameter]"))
          : null;
        if (outletRange) outletRange.disabled = true;
        setHint($("[data-arc-hint='outlet_metal_angle_deg']", root), "单圆弧被锁定为等于进口角 χ₁。");
      } else {
        outlet.disabled = false;
        const outletRange = outlet.closest("[data-arc-parameter]")
          ? $("[data-arc-range]", outlet.closest("[data-arc-parameter]"))
          : null;
        if (outletRange) outletRange.disabled = false;
        setHint($("[data-arc-hint='outlet_metal_angle_deg']", root), "");
      }
    }
  }

  function parseList(value) {
    return String(value || "")
      .split(/[,，]/)
      .map(item => Number(item.trim()))
      .filter(item => Number.isFinite(item));
  }

  function buildSpec() {
    const family = familySelect.value;
    const closure = family === "double" ? closureSelect.value : "max_camber";
    const meanLine = {
      family,
      inlet_metal_angle_deg: numberValue("inlet_metal_angle_deg"),
      outlet_metal_angle_deg: numberValue("outlet_metal_angle_deg"),
      closure,
    };
    if (family === "single") meanLine.closure = "max_camber";
    if (family === "double" && closure === "junction_station") {
      meanLine.junction_station = numberValue("junction_station");
    }
    if (family === "multiple" || (family === "double" && closure === "node_angles")) {
      meanLine.interior_node_angles_deg = parseList(numberInput("interior_node_angles").value);
    }
    if (family === "multiple") {
      // 留空表示自动求解：站位由两条线性闭合条件解出，不是自由参数。
      const stations = parseList(numberInput("interior_node_stations").value);
      if (stations.length) meanLine.interior_node_stations = stations;
    }
    return {
      mean_line: meanLine,
      max_thickness_ratio: numberValue("max_thickness_ratio"),
      max_thickness_station: numberValue("max_thickness_station"),
      leading_edge_radius_ratio: numberValue("leading_edge_radius_ratio"),
      trailing_edge_thickness_ratio: numberValue("trailing_edge_thickness_ratio"),
      trailing_edge_half_wedge_deg: numberValue("trailing_edge_half_wedge_deg"),
      points_per_side: numberValue("points_per_side"),
    };
  }

  const format = (value, digits = 6) =>
    value == null || Number.isNaN(value) ? "—" : Number(value).toFixed(digits);
  const scientific = value =>
    value == null || Number.isNaN(value)
      ? "—"
      : Math.abs(value) < 1e-12
        ? "0"
        : Number(value).toExponential(4);

  function renderBounds(payload, spec) {
    const set = (key, text, infeasible = false) => {
      const node = $(`[data-arc-bound="${key}"]`, root);
      if (!node) return;
      node.textContent = text;
      node.classList.toggle("infeasible", infeasible);
    };
    const ceiling = payload.maximum_feasible_half_wedge_deg;
    const lawCeiling = payload.thickness_law_half_wedge_ceiling_deg;
    if (payload.wedge_bound_exists && Number.isFinite(ceiling)) {
      set("wedge_ceiling", `${format(ceiling, 4)} °`);
      const feasible = spec.trailing_edge_half_wedge_deg <= ceiling;
      set(
        "wedge_current",
        `${format(spec.trailing_edge_half_wedge_deg, 4)} °　${feasible ? "可行" : "超出上界"}`,
        !feasible,
      );
    } else {
      // 数学上界落在参数域下界（0.5°）以下：该厚度合同连最小允许楔角都实现不了。
      set(
        "wedge_ceiling",
        `不存在（厚度律上界仅 ${format(lawCeiling, 4)} °，低于允许下限）`,
        true,
      );
      set("wedge_current", "不可行", true);
    }
    set(
      "junction_station",
      payload.junction_station_range
        ? `(${format(payload.junction_station_range[0], 6)}, ${format(payload.junction_station_range[1], 6)})`
        : "—（仅双圆弧有切点自由度）",
    );
    set(
      "junction_angle",
      payload.junction_angle_bounds_deg
        ? `(${format(payload.junction_angle_bounds_deg[0], 3)}°, ${format(payload.junction_angle_bounds_deg[1], 3)}°)`
        : "—",
    );
    if (payload.interior_node_station_bounds) {
      const rows = payload.interior_node_station_bounds.map((bound, index) =>
        bound
          ? `x${index + 1} ∈ (${format(bound[0], 6)}, ${format(bound[1], 6)})`
          : `x${index + 1} 无可行值`,
      );
      set("interior_stations", rows.join("；") || "—");
    } else {
      set("interior_stations", "—（仅多圆弧有内节点站位）");
    }
    set("arc_length", format(payload.mean_line_arc_length, 9));
    set("terminal_curvature", format(payload.terminal_curvature, 9));
    set(
      "leading_edge",
      `${format(payload.leading_edge_radius_requested_ratio, 6)} / ${format(payload.leading_edge_radius_limit_ratio, 6)}`,
    );
    // 楔角可行不等于这条 spec 能生成：厚度超过中弧线曲率半径时任何楔角都会失败。
    set(
      "geometry",
      payload.geometry_feasible ? "可行" : payload.geometry_error || "不可行",
      !payload.geometry_feasible,
    );
    return Boolean(payload.wedge_feasible);
  }

  function applyLimits(payload, spec) {
    limits = payload;
    const ceiling = payload.maximum_feasible_half_wedge_deg;
    if (!payload.wedge_bound_exists) {
      setHint(
        $("[data-arc-hint='trailing_edge_half_wedge_deg']", root),
        "该厚度合同连 0.51° 都实现不了：请增大 t/c、减小 t_TE/c 或前移 s_t。",
      );
    } else if (Number.isFinite(ceiling) && ceiling > MODEL_LIMITS.trailing_edge_half_wedge_deg.min) {
      setRange(
        "trailing_edge_half_wedge_deg",
        MODEL_LIMITS.trailing_edge_half_wedge_deg.min,
        Math.min(44.9, ceiling),
      );
      setHint(
        $("[data-arc-hint='trailing_edge_half_wedge_deg']", root),
        `当前合同的可行上界 ${ceiling.toFixed(4)}°（滑动条已按它收敛）。`,
      );
    } else {
      setHint(
        $("[data-arc-hint='trailing_edge_half_wedge_deg']", root),
        "当前厚度合同连最小楔角都无法实现：请增大 t/c、减小 t_TE/c 或前移 s_t。",
      );
    }
    if (payload.junction_station_range) {
      const [low, high] = payload.junction_station_range;
      const inset = Math.min(1e-4, 0.1 * (high - low));
      setRange("junction_station", low + inset, high - inset);
      setHint(
        junctionHint,
        `可达站位 (${low.toFixed(6)}, ${high.toFixed(6)})；超出会直接报错。`,
      );
    } else {
      setRange("junction_station", MODEL_LIMITS.junction_station.min + 1e-4, MODEL_LIMITS.junction_station.max - 1e-4);
      setHint(junctionHint, "可达区间由进出口金属角决定。");
    }
    if (payload.interior_node_stations && payload.interior_node_stations.length) {
      const rows = payload.interior_node_stations.map(value => value.toFixed(6)).join(", ");
      setHint(
        stationsHint,
        `自动解出的站位：${rows}。留空即采用该组；填入自定义值必须严格递增且落在可达区间内。`,
      );
    }
    return renderBounds(payload, spec);
  }

  async function refreshLimits() {
    const sequence = ++limitsSequence;
    let spec;
    try {
      spec = buildSpec();
    } catch (buildError) {
      return;
    }
    try {
      const payload = await fetchJSON("/api/v2/arc/limits", spec);
      if (sequence !== limitsSequence) return;
      const feasible = applyLimits(payload, spec);
      if (feasible) setHint(errorBox, "");
    } catch (limitsError) {
      if (sequence !== limitsSequence) return;
      limits = null;
      setHint(errorBox, `参数尚不可用：${limitsError.message}`);
    }
  }

  function scheduleLimits() {
    clearTimeout(limitsTimer);
    limitsTimer = setTimeout(refreshLimits, 260);
  }

  function renderFeatures(geometry) {
    const body = $("[data-arc-features]", root);
    body.innerHTML = "";
    const features = geometry.features;
    const rows = [
      ["段数 N", features.arc_count.toFixed(0)],
      ["进口金属角 χ₁ (°)", format(features.inlet_metal_angle_deg, 4)],
      ["出口金属角 χ₂ (°)", format(features.outlet_metal_angle_deg, 4)],
      ["弯度角 θ (°)", format(features.camber_angle_deg, 4)],
      ["前缘弯度占比 a", format(features.camber_fraction_le, 4)],
      ["最大厚度 t/c", format(features.max_thickness_ratio, 6)],
      ["最大厚度位置 s_t", format(features.max_thickness_station, 4)],
      ["最大厚度弦向位置", format(features.max_thickness_x_ratio, 4)],
      ["最大弯度 y/c", format(features.max_camber_ratio, 6)],
      ["最大弯度弦向位置", format(features.max_camber_x_ratio, 4)],
      ["标称前缘半径 a₁²/2", format(features.leading_edge_radius_ratio, 6)],
      ["尾缘厚度 t_TE/c", format(features.trailing_edge_thickness_ratio, 6)],
      ["实现尾缘半楔角 (°)", format(features.trailing_edge_half_wedge_deg, 4)],
      ["中弧线弧长比 L", format(features.mean_line_arc_length_ratio, 6)],
      ["弦线闭合误差", scientific(features.chord_closure_error_ratio)],
      ["各段半径 r/c", geometry.arc_radii.map(value => value.toFixed(4)).join(", ")],
      ["各段转角 (°)", geometry.arc_turns_deg.map(value => value.toFixed(2)).join(", ")],
    ];
    for (const [label, value] of rows) {
      const tr = document.createElement("tr");
      const th = document.createElement("td");
      th.textContent = label;
      const td = document.createElement("td");
      td.textContent = value;
      tr.append(th, td);
      body.append(tr);
    }
  }

  const METRIC_KEYS = [
    "chord_closure_error",
    "chord_length_error",
    "node_rebuild_error",
    "node_tangency_error",
    "leading_edge_radius_limit_ratio",
    "leading_edge_radius_analytic_ratio",
    "leading_edge_radius_probe_ratio",
    "mean_line_arclength_factor",
    "pressure_offset_clearance_min",
    "suction_pressure_gap_min",
    "suction_max_abs_curvature",
    "pressure_max_abs_curvature",
    "trailing_edge_half_wedge_requested_deg",
    "self_intersection",
  ];

  const FIXED_POINT_METRICS = new Set([
    "leading_edge_radius_limit_ratio",
    "leading_edge_radius_analytic_ratio",
    "leading_edge_radius_probe_ratio",
    "mean_line_arclength_factor",
    "pressure_offset_clearance_min",
    "suction_pressure_gap_min",
    "suction_max_abs_curvature",
    "pressure_max_abs_curvature",
    "trailing_edge_half_wedge_requested_deg",
  ]);

  function renderMetrics(geometry) {
    for (const key of METRIC_KEYS) {
      const node = $(`[data-arc-metric="${key}"]`, root);
      if (!node) continue;
      const value = geometry.diagnostics[key];
      if (value == null) {
        node.textContent = "—";
        continue;
      }
      // 半径类与角度类量不是「越接近 0 越好」，按定点显示；其余按科学计数法。
      node.textContent = FIXED_POINT_METRICS.has(key) ? format(value, 8) : scientific(value);
    }
  }

  function renderNodes(geometry) {
    const body = $("[data-arc-nodes]", root);
    body.innerHTML = "";
    const count = geometry.node_angles_deg.length;
    for (let index = 0; index < count; index += 1) {
      const tr = document.createElement("tr");
      const cells = [
        index === 0 ? "J₀（前缘）" : index === count - 1 ? `J${index}（尾缘）` : `J${index}`,
        format(geometry.node_stations[index], 6),
        format(geometry.node_angles_deg[index], 4),
        index < geometry.arc_radii.length ? format(geometry.arc_radii[index], 6) : "—",
        index < geometry.arc_turns_deg.length ? format(geometry.arc_turns_deg[index], 4) : "—",
      ];
      for (const value of cells) {
        const td = document.createElement("td");
        td.textContent = value;
        tr.append(td);
      }
      body.append(tr);
    }
  }

  function arcTraces(geometry) {
    const xs = [];
    const ys = [];
    const rx = [];
    const ry = [];
    const degrees = geometry.node_angles_deg;
    geometry.arc_centers.forEach((center, index) => {
      const radius = geometry.arc_radii[index];
      const start = (degrees[index] * Math.PI) / 180;
      const end = (degrees[index + 1] * Math.PI) / 180;
      const steps = 60;
      for (let step = 0; step <= steps; step += 1) {
        const theta = start + (end - start) * (step / steps);
        xs.push(center[0] - radius * Math.sin(theta));
        ys.push(center[1] + radius * Math.cos(theta));
      }
      xs.push(null);
      ys.push(null);
      const middle = 0.5 * (start + end);
      rx.push(center[0], center[0] - radius * Math.sin(middle), null);
      ry.push(center[1], center[1] + radius * Math.cos(middle), null);
    });
    return { xs, ys, rx, ry };
  }

  function renderPlot(geometry) {
    const traces = [
      {
        x: geometry.suction.map(point => point[0]),
        y: geometry.suction.map(point => point[1]),
        name: "吸力面",
        mode: "lines",
        line: { color: "#0071e3", width: 2 },
      },
      {
        x: geometry.pressure.map(point => point[0]),
        y: geometry.pressure.map(point => point[1]),
        name: "压力面",
        mode: "lines",
        line: { color: "#ff7a00", width: 2 },
      },
      {
        x: geometry.camber.map(point => point[0]),
        y: geometry.camber.map(point => point[1]),
        name: "中弧线",
        mode: "lines",
        line: { color: "#1d1d1f", width: 1.4, dash: "dash" },
      },
    ];

    if (normalsToggle.checked) {
      const xs = [];
      const ys = [];
      const step = Math.max(1, Math.floor(geometry.suction.length / 40));
      for (let index = 0; index < geometry.suction.length; index += step) {
        xs.push(geometry.suction[index][0], geometry.pressure[index][0], null);
        ys.push(geometry.suction[index][1], geometry.pressure[index][1], null);
      }
      traces.push({
        x: xs,
        y: ys,
        name: "法向厚度",
        mode: "lines",
        line: { color: "#c7c7cc", width: 1 },
        hoverinfo: "skip",
      });
    }

    if (arcsToggle.checked) {
      const arcs = arcTraces(geometry);
      traces.push({
        x: arcs.xs,
        y: arcs.ys,
        name: "各段圆弧",
        mode: "lines",
        line: { color: "#248a3d", width: 1.2, dash: "dot" },
        hoverinfo: "skip",
      });
      traces.push({
        x: arcs.rx,
        y: arcs.ry,
        name: "半径",
        mode: "lines",
        line: { color: "#c7c7cc", width: 1, dash: "dot" },
        hoverinfo: "skip",
      });
      traces.push({
        x: geometry.node_points.map(point => point[0]),
        y: geometry.node_points.map(point => point[1]),
        name: "圆弧节点",
        mode: "markers",
        marker: { color: "#d70015", size: 8, symbol: "diamond" },
      });
      traces.push({
        x: geometry.arc_centers.map(point => point[0]),
        y: geometry.arc_centers.map(point => point[1]),
        name: "圆心",
        mode: "markers",
        marker: { color: "#6e6e73", size: 7, symbol: "cross" },
      });
    }

    const layout = {
      margin: { l: 48, r: 16, t: 12, b: 42 },
      xaxis: { title: { text: "x / c" }, zeroline: false, scaleanchor: "y", scaleratio: 1 },
      yaxis: { title: { text: "y / c" }, zeroline: false },
      legend: { orientation: "h", y: -0.18 },
      uirevision: uiRevision,
    };
    if (charts) charts.render("arc-profile-chart", traces, layout);
    else window.Plotly?.react("arc-profile-chart", traces, layout, { responsive: true, displaylogo: false });
  }

  function clearResults() {
    current = null;
    designation.textContent = "尚未生成";
    profileIdBox.textContent = "—";
    meanLineIdBox.textContent = "—";
    downloadButton.disabled = true;
  }

  async function generate() {
    errorBox.textContent = "";
    statePill.textContent = "计算中…";
    generateButton.disabled = true;
    let payload;
    try {
      payload = await fetchJSON("/api/v2/arc/generate", buildSpec());
    } catch (requestError) {
      // 失败时必须清掉旧几何，否则界面上会同时出现「上一次成功的结果」与错误状态。
      statePill.textContent = "参数不可行";
      errorBox.textContent = requestError.message;
      clearResults();
      return;
    } finally {
      generateButton.disabled = false;
    }
    current = payload.geometry;
    statePill.textContent = "已生成";
    designation.textContent = current.designation;
    profileIdBox.textContent = current.profile_id;
    meanLineIdBox.textContent = current.mean_line_id;
    renderFeatures(current);
    renderMetrics(current);
    renderNodes(current);
    renderPlot(current);
    downloadButton.disabled = false;
    await refreshLimits();
  }

  function downloadCsv() {
    if (!current) return;
    const rows = [["side", "index", "x_over_c", "y_over_c"]];
    for (const [side, points] of [
      ["suction", current.suction],
      ["pressure", current.pressure],
    ]) {
      points.forEach((point, index) => rows.push([side, index, point[0], point[1]]));
    }
    const blob = new Blob([rows.map(row => row.join(",")).join("\r\n")], {
      type: "text/csv;charset=utf-8",
    });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${current.profile_id}.csv`;
    link.click();
    URL.revokeObjectURL(url);
  }

  form.addEventListener("submit", event => {
    event.preventDefault();
    generate();
  });
  familySelect.addEventListener("change", () => {
    syncVisibility();
    scheduleLimits();
  });
  closureSelect.addEventListener("change", () => {
    syncVisibility();
    scheduleLimits();
  });
  for (const name of [
    "inlet_metal_angle_deg",
    "interior_node_angles",
    "interior_node_stations",
  ]) {
    const input = numberInput(name);
    if (input) input.addEventListener("input", scheduleLimits);
  }
  const samplesSelect = $("[data-arc-samples]", root);
  if (samplesSelect) samplesSelect.addEventListener("change", generate);
  const inletInput = numberInput("inlet_metal_angle_deg");
  if (inletInput) {
    inletInput.addEventListener("input", () => {
      if (familySelect.value === "single") {
        const outlet = numberInput("outlet_metal_angle_deg");
        outlet.value = inletInput.value;
        const range = outlet.closest("[data-arc-parameter]")
          ? $("[data-arc-range]", outlet.closest("[data-arc-parameter]"))
          : null;
        if (range) range.value = inletInput.value;
      }
    });
  }
  for (const toggle of [normalsToggle, arcsToggle]) {
    toggle.addEventListener("change", () => {
      if (current) renderPlot(current);
    });
  }
  downloadButton.addEventListener("click", downloadCsv);

  pairControls();
  syncVisibility();
  refreshLimits();
  generate();
})();
