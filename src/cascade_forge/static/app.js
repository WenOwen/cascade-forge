(() => {
  "use strict";

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const colors = { blue: "#0071e3", orange: "#ff7a00", ink: "#1d1d1f", muted: "#6e6e73", green: "#248a3d", red: "#d70015" };
  const plotConfig = { responsive: true, displaylogo: false, scrollZoom: true, modeBarButtonsToRemove: ["lasso2d", "select2d"] };
  const baseLayout = { paper_bgcolor: "#edf4fb", plot_bgcolor: "#edf4fb", font: { family: "-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif", size: 11, color: colors.muted }, margin: { l: 48, r: 18, t: 18, b: 42 }, hovermode: "closest", legend: { orientation: "h", y: 1.02, x: 0 } };

  const Charts = (() => {
    const observed = new WeakSet();
    const observer = new ResizeObserver(entries => entries.forEach(entry => {
      if (entry.target?._fullLayout && entry.target.getClientRects().length) window.Plotly?.Plots.resize(entry.target)?.catch(() => {});
    }));
    function render(target, data, layout = {}, config = {}) {
      const node = typeof target === "string" ? document.getElementById(target) : target;
      if (!node || !window.Plotly) return Promise.resolve();
      node.classList.remove("empty-chart");
      const merged = { ...baseLayout, ...layout, margin: { ...baseLayout.margin, ...(layout.margin || {}) } };
      if (!observed.has(node)) { observer.observe(node); observed.add(node); }
      return Plotly.react(node, data, merged, { ...plotConfig, ...config }).then(() => { if (node.getClientRects().length) return Plotly.Plots.resize(node); });
    }
    function resizeAll() { $$(".plotly-chart").forEach(node => { if (node._fullLayout && node.getClientRects().length) Plotly.Plots.resize(node)?.catch(() => {}); }); }
    window.addEventListener("resize", resizeAll, { passive: true });
    document.addEventListener("htmx:afterSettle", resizeAll);
    function dispose(node) { observer.unobserve(node); observed.delete(node); if (node._fullLayout) window.Plotly?.purge(node); }
    return { render, resizeAll, dispose };
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
    if (!response.ok) {
      const detail = payload?.detail || payload || `HTTP ${response.status}`;
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail, null, 2));
    }
    return payload;
  }
  function debounce(fn, wait) { let timer; return (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), wait); }; }
  function downloadJSON(name, payload) { const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" }); const link = document.createElement("a"); link.href = URL.createObjectURL(blob); link.download = name; link.click(); URL.revokeObjectURL(link.href); }
  function escapeHTML(value) { return String(value ?? "").replace(/[&<>'"]/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[char]); }
  function xy(points) { return { x: points.map(p => p[0]), y: points.map(p => p[1]) }; }

  function initShell() {
    const toggle = $("[data-sidebar-toggle]"); const scrim = $("[data-sidebar-scrim]");
    toggle?.addEventListener("click", () => document.body.classList.toggle("sidebar-open"));
    scrim?.addEventListener("click", () => document.body.classList.remove("sidebar-open"));
    const tip = document.createElement("div"); tip.id = "parameter-tooltip"; tip.className = "parameter-tooltip"; tip.role = "tooltip"; tip.hidden = true; document.body.append(tip);
    const hideTip = () => { tip.hidden = true; };
    $$('[data-tooltip]').forEach(button => {
      button.setAttribute("aria-describedby", tip.id);
      const showTip = () => {
        tip.textContent = button.dataset.tooltip; tip.hidden = false;
        const rect = button.getBoundingClientRect();
        tip.style.left = `${Math.max(8, Math.min(innerWidth - tip.offsetWidth - 8, rect.right - tip.offsetWidth))}px`;
        tip.style.top = `${rect.top > tip.offsetHeight + 12 ? rect.top - tip.offsetHeight - 8 : rect.bottom + 8}px`;
      };
      button.addEventListener("mouseenter", showTip); button.addEventListener("focus", showTip);
      button.addEventListener("mouseleave", hideTip); button.addEventListener("blur", hideTip);
      button.addEventListener("keydown", event => { if (event.key === "Escape") hideTip(); });
    });
    window.addEventListener("scroll", hideTip, true); window.addEventListener("resize", hideTip);
  }


  function initStudio() {
    const root = $("[data-studio-root]"); if (!root) return;
    const form = $("#studio-form", root); const defaults = JSON.parse(root.dataset.defaultSpec);
    const referenceSelect = form.elements.metal_angle_reference;
    const numeric = ["inlet_metal_angle_deg", "outlet_metal_angle_deg", "stagger_deg", "camber_le_tangent_ratio", "camber_te_tangent_ratio", "max_thickness_ratio", "max_thickness_x_ratio", "leading_edge_radius_ratio", "trailing_edge_radius_ratio", "fore_shape_factor", "aft_shape_factor"];
    let currentPayload = null; let currentSpec = structuredClone(defaults);
    let analysisTab = "thickness"; let requestToken = 0; let activeReference = defaults.intent.metal_angle_reference || "axial";
    let lastStagger = Number(defaults.intent.stagger_deg);
    let previewTimer, ready = false, unsaved = false, lastPreviewUnsaved = false, designName = "未命名叶型", designTags = [];
    const feedback = $("[data-studio-feedback]", root), feedbackText = $("[data-studio-error]", root);
    const saveState = $("[data-design-save-state]", root);
    let profileOutput = null;
    function actions(enabled) { ready = enabled; $$("[data-current-action]").forEach(button => button.disabled = !enabled); profileOutput?.setReady(enabled); }
    function edited() {
      unsaved = true; requestToken++; clearTimeout(previewTimer); actions(false);
      saveState.textContent = "有未保存修改";
      $("[data-preview-state]").textContent = "等待更新";
      $("[data-preview-state]").className = "pill running";
    }
    function showError(message) { feedbackText.textContent = message; feedback.hidden = false; }
    form.addEventListener("submit", event => event.preventDefault());

    const control = name => $(`[data-parameter='${name}']`, form);
    const getValue = name => Number($("[data-number]", control(name)).value);
    function setValue(name, value) {
      const group = control(name); if (!group) return;
      $("[data-number]", group).value = String(Number(value));
      $("[data-range]", group).value = String(Number(value));
    }
    function setAngleBounds(reference) {
      const bounds = reference === "axial" ? [-84, 84] : [6, 174];
      ["inlet_metal_angle_deg", "outlet_metal_angle_deg"].forEach(name => {
        const group = control(name);
        [$("[data-number]", group), $("[data-range]", group)].forEach(input => {
          input.min = bounds[0]; input.max = bounds[1];
        });
        $("[data-min-label]", group).textContent = bounds[0];
        $("[data-max-label]", group).textContent = bounds[1];
      });
    }
    function updateDerived() {
      const inlet = getValue("inlet_metal_angle_deg"); const outlet = getValue("outlet_metal_angle_deg");
      const axialInlet = activeReference === "axial" ? inlet : 90 - inlet;
      const axialOutlet = activeReference === "axial" ? outlet : 90 - outlet;
      $("[data-angle-difference]").textContent = `${(axialInlet - axialOutlet).toFixed(2)}°`;
    }
    function syncFormFromSpec(spec) {
      const intent = spec.intent; activeReference = intent.metal_angle_reference || "axial";
      referenceSelect.value = activeReference; setAngleBounds(activeReference);
      numeric.forEach(name => setValue(name, intent[name]));
      form.elements.linked_thickness.checked = spec.linked_thickness;
      lastStagger = Number(intent.stagger_deg); updateDerived();
    }
    syncFormFromSpec(defaults);

    function buildSpec() {
      const base = currentSpec; const intent = { ...base.intent, metal_angle_reference: activeReference };
      numeric.forEach(name => intent[name] = getValue(name));
      const axial = (value, ref) => ref === "tangential" ? 90 - value : value;
      const sameShape = numeric.filter(name => name !== "stagger_deg").every(name => {
        const angle = name === "inlet_metal_angle_deg" || name === "outlet_metal_angle_deg";
        const before = angle ? axial(base.intent[name], base.intent.metal_angle_reference) - base.intent.stagger_deg : base.intent[name];
        const after = angle ? axial(intent[name], activeReference) - intent.stagger_deg : intent[name];
        return Math.abs(before - after) < 1e-10;
      }) && base.linked_thickness === form.elements.linked_thickness.checked;
      if (sameShape && base.camber_curve) return { ...base, intent };
      return { ...base, authoring_mode: "basic", intent, linked_thickness: form.elements.linked_thickness.checked, camber_curve: null, suction_thickness_curve: null, pressure_thickness_curve: null, edge: null, resolution_residuals: {} };
    }
    async function preview(full = false, suppliedSpec = null) {
      clearTimeout(previewTimer);
      const token = ++requestToken; const state = $("[data-preview-state]");
      actions(false);
      if (!suppliedSpec) {
        const invalid = numeric.find(name => {
          const input = $("[data-number]", control(name));
          return input.value === "" || !Number.isFinite(Number(input.value)) || Number(input.value) < Number(input.min) || Number(input.value) > Number(input.max);
        });
        if (invalid) { showError("请填写有效数值，并保持在滑动条两端标注的范围内。"); state.textContent = "请检查参数"; return false; }
      }
      try {
        state.textContent = full ? "完整验证…" : "计算中…"; state.className = "pill running";
        const payload = await fetchJSON("/api/v2/preview", { method: "POST", body: JSON.stringify(suppliedSpec || buildSpec()) });
        if (token !== requestToken) return false;
        currentPayload = payload; currentSpec = payload.geometry.spec;
        state.textContent = "已同步"; state.className = "pill success";
        feedback.hidden = true; actions(true); lastPreviewUnsaved = unsaved;
        saveState.textContent = unsaved ? "有未保存修改" : "预览已就绪";
        renderStudio(payload); return true;
      } catch (error) {
        if (token !== requestToken) return false;
        state.textContent = "预览未更新"; state.className = "pill rejected";
        showError(`预览未成功，保存和导出已暂停。${error.message}`); return false;
      }
    }
    const throttledPreview = () => { clearTimeout(previewTimer); previewTimer = setTimeout(() => preview(false), 220); };
    $$('[data-parameter]', form).forEach(group => {
      const name = group.dataset.parameter; const number = $("[data-number]", group); const range = $("[data-range]", group);
      [number, range].forEach(input => input.addEventListener("input", () => {
        edited();
        if (input.value === "" || !Number.isFinite(Number(input.value))) { showError("请输入数值，完成输入后会自动预览。"); return; }
        let value = Number(input.value);
        if (value < Number(input.min) || value > Number(input.max)) { showError(`请输入 ${input.min}–${input.max} 范围内的数值。`); return; }
        if (name === "stagger_deg") {
          const toAxial = value => activeReference === "axial" ? value : 90 - value;
          const localInlet = toAxial(getValue("inlet_metal_angle_deg")) - lastStagger;
          const localOutlet = toAxial(getValue("outlet_metal_angle_deg")) - lastStagger;
          value = Math.max(-84, -84 - localInlet, -84 - localOutlet, Math.min(84, 84 - localInlet, 84 - localOutlet, value));
        }
        if (input === range || name === "stagger_deg") input.value = String(value);
        (input === number ? range : number).value = String(value);
        if (name === "stagger_deg") {
          const delta = value - lastStagger; const direction = activeReference === "axial" ? 1 : -1;
          setValue("inlet_metal_angle_deg", getValue("inlet_metal_angle_deg") + direction * delta);
          setValue("outlet_metal_angle_deg", getValue("outlet_metal_angle_deg") + direction * delta);
          lastStagger = value;
        }
        updateDerived(); throttledPreview();
      }));
    });
    form.elements.linked_thickness.addEventListener("change", () => { edited(); preview(true); });
    referenceSelect.addEventListener("change", () => {
      const next = referenceSelect.value;
      if (next !== activeReference) {
        const inlet = 90 - getValue("inlet_metal_angle_deg");
        const outlet = 90 - getValue("outlet_metal_angle_deg");
        activeReference = next; setAngleBounds(activeReference);
        setValue("inlet_metal_angle_deg", inlet);
        setValue("outlet_metal_angle_deg", outlet); updateDerived();
      }
      // This is a display-only conversion: no geometry request or identity change.
    });
    $$('[data-analysis]').forEach(button => button.addEventListener("click", () => { analysisTab = button.dataset.analysis; $$('[data-analysis]').forEach(item => item.classList.toggle("active", item === button)); if (currentPayload) renderAnalysis(currentPayload); }));
    $("[data-reset-view]").addEventListener("click", () => { const chart = $("#profile-chart"); if (chart?._fullLayout) Plotly.relayout(chart, { "xaxis.autorange": true, "yaxis.autorange": true }); });
    function restorePreview() {
      clearTimeout(previewTimer); requestToken++;
      syncFormFromSpec(currentSpec); feedback.hidden = true;
      unsaved = lastPreviewUnsaved; saveState.textContent = unsaved ? "有未保存修改" : "已恢复上次成功预览";
      if (currentPayload) {
        actions(true); $("[data-preview-state]").textContent = "已同步";
        $("[data-preview-state]").className = "pill success";
      } else preview(true);
    }
    $("[data-restore-preview]").addEventListener("click", restorePreview);
    $("[data-export-spec]").addEventListener("click", () => { if (ready) downloadJSON(`${currentPayload.geometry.geometry_id}-spec.json`, currentSpec); });
    $("[data-export-coordinates]")?.addEventListener("click", () => {
      if (!ready) return;
      const rows = ["side,point,x_over_c,y_over_c"];
      for (const side of ["suction", "pressure"]) currentPayload.geometry[side].forEach((point, index) => rows.push([side, index, ...point].join(",")));
      const link = document.createElement("a"); link.href = URL.createObjectURL(new Blob([rows.join("\r\n")], { type: "text/csv;charset=utf-8" }));
      link.download = `${currentPayload.geometry.geometry_id}.csv`; link.click(); setTimeout(() => URL.revokeObjectURL(link.href), 1000);
    });
    function normalizeTags(tags) { return (Array.isArray(tags) ? tags : typeof tags === "string" ? tags.split(/[,，]/) : []).filter(tag => typeof tag === "string").map(tag => tag.trim()).filter(Boolean); }
    function mayReplace() { return !unsaved || window.confirm("当前设计有未保存修改。要放弃这些修改并打开另一个设计吗？"); }
    async function loadDesign(record) {
      if (!mayReplace()) return false;
      clearTimeout(previewTimer);
      if (!await preview(true, record.spec)) throw new Error(feedbackText.textContent || "无法打开设计，请重试。");
      syncFormFromSpec(currentSpec); unsaved = lastPreviewUnsaved = false;
      designName = String(record.name || "未命名叶型").slice(0, 120); designTags = normalizeTags(record.tags);
      $("[data-design-label]").textContent = `${designName}${record.revision ? ` · 修订 ${record.revision}` : ""}`;
      saveState.textContent = "已打开保存的设计";
      return true;
    }
    window.CFStudio = { loadDesign };
    profileOutput = window.CFOutput?.attach({request: () => ({family: "custom", spec: currentSpec})});
    const fileInput = $("[data-spec-file]"); $("[data-import-spec]").addEventListener("click", () => fileInput.click());
    fileInput.addEventListener("change", async () => {
      if (!fileInput.files[0]) return;
      try {
        const data = JSON.parse(await fileInput.files[0].text());
        if (!mayReplace()) return;
        if (!await preview(true, data.spec || data)) return;
        syncFormFromSpec(currentSpec); designName = String(data.name || "导入设计").slice(0, 120); designTags = normalizeTags(data.tags);
        unsaved = lastPreviewUnsaved = true; $("[data-design-label]").textContent = designName;
        saveState.textContent = "已导入，尚未保存"; notice("设计已导入并更新预览");
      } catch (error) { showError(`无法导入设计：${error.message}`); }
      finally { fileInput.value = ""; }
    });
    const dialog = $("#workspace-dialog"), saveForm = $("form", dialog), saveError = $("[data-save-error]", dialog);
    let saving = false;
    $("[data-save-workspace]").addEventListener("click", () => {
      if (!ready) return;
      saveForm.elements.name.value = designName; saveForm.elements.tags.value = designTags.join(", ");
      saveError.textContent = ""; dialog.showModal();
    });
    $("[data-cancel-save]", dialog).addEventListener("click", () => { if (!saving) dialog.close(); });
    dialog.addEventListener("cancel", event => { if (saving) event.preventDefault(); });
    saveForm.addEventListener("submit", async event => {
      event.preventDefault(); if (saving || !ready) return;
      const name = saveForm.elements.name.value.trim();
      if (!name) { saveError.textContent = "请填写设计名称。"; saveForm.elements.name.focus(); return; }
      const tags = saveForm.elements.tags.value.split(/[,，]/).map(value => value.trim()).filter(Boolean);
      const submittedSpec = structuredClone(currentSpec), token = requestToken;
      saving = true; saveError.textContent = "";
      $$("button, input", saveForm).forEach(node => node.disabled = true);
      try {
        const saved = await fetchJSON("/api/v2/workspace/designs", { method: "POST", body: JSON.stringify({ name, tags, spec: submittedSpec }) });
        if (token === requestToken) { unsaved = lastPreviewUnsaved = false; designName = name; designTags = tags; $("[data-design-label]").textContent = name; saveState.textContent = "已备份，可查看历史快照"; }
        dialog.close(); notice(`已备份「${name}」`);
        document.dispatchEvent(new CustomEvent("cf:design-saved", { detail: saved }));
      } catch (error) { saveError.textContent = `保存失败，请重试：${error.message}`; }
      finally { saving = false; $$("button, input", saveForm).forEach(node => node.disabled = false); }
    });
    document.addEventListener("cf:profile-saved", event => {
      if ($("[data-preview-state]").textContent === "已同步" && event.detail?.submitted?.family === "custom" && JSON.stringify(event.detail.submitted.spec) === JSON.stringify(currentSpec)) {
        unsaved = lastPreviewUnsaved = false;
        saveState.textContent = `已保存至「${event.detail.dataset || window.CFDatabase.name}」`;
      }
    });
    window.addEventListener("beforeunload", event => { if (unsaved) { event.preventDefault(); event.returnValue = ""; } });
    $("[data-show-controls]").addEventListener("change", () => { if (currentPayload) renderStudio(currentPayload); });
    $(".studio-diagnostics").addEventListener("toggle", () => { if ($(".studio-diagnostics").open && currentPayload) renderStudio(currentPayload); });

    function metricText(value) {
      if (typeof value !== "number") return String(value);
      if (Math.abs(value) >= 10000 || (value !== 0 && Math.abs(value) < 0.001)) return value.toExponential(3);
      return value.toLocaleString("zh-CN", {maximumFractionDigits: 6});
    }
    function openAnalysis(tab) {
      if (tab) {analysisTab = tab; $$('[data-analysis]').forEach(button => button.classList.toggle("active", button.dataset.analysis === tab));}
      $(".studio-diagnostics").open = true;
      if (currentPayload) renderAnalysis(currentPayload);
      $(".studio-diagnostics").scrollIntoView({behavior: "smooth", block: "start"});
    }
    function renderIssues(report) {
      const section = $("[data-geometry-issues]", root), list = $("[data-geometry-issue-list]", root);
      const issues = report.issues || []; section.hidden = !issues.length; list.replaceChildren();
      issues.forEach(issue => {
        const item = document.createElement("li"); item.dataset.severity = issue.severity;
        const title = document.createElement("strong"); title.textContent = `${issue.severity === "error" ? "未通过" : "警告"}：${issue.message}`;
        const values = document.createElement("p");
        values.textContent = [issue.value != null ? `指标值：${metricText(issue.value)}` : "", issue.limit != null ? `规则要求：${metricText(issue.limit)}` : ""].filter(Boolean).join("；");
        const button = document.createElement("button"); button.type = "button"; button.className = "button";
        const tab = issue.code.includes("metal_angle") ? "angle" : ["tmax_mismatch", "xt_mismatch", "non_positive_thickness"].includes(issue.code) ? "thickness" : null;
        button.textContent = tab === "angle" ? "查看金属线角度" : tab === "thickness" ? "查看厚度" : "打开形状检查";
        button.addEventListener("click", () => {$("[data-analysis-interior]", root).checked = true; openAnalysis(tab);});
        item.append(title, values, button); list.append(item);
      });
      $("[data-validation-status]").title = issues.map(issue => issue.message).join("；") || "未触发当前几何检查规则";
    }
    $("[data-analysis-interior]", root).addEventListener("change", () => {if (currentPayload) renderAnalysis(currentPayload);});

    function renderStudio(payload) {
      const geometry = payload.geometry; const placed = payload.placement; const controls = placed.control_points;
      const traces = [
        { ...xy(placed.suction), type: "scatter", mode: "lines", name: "吸力面", line: { color: colors.blue, width: 2.4 } },
        { ...xy(placed.pressure), type: "scatter", mode: "lines", name: "压力面", line: { color: colors.orange, width: 2.4 } },
        { ...xy(placed.camber), type: "scatter", mode: "lines", name: "中弧线", line: { color: colors.muted, width: 1.3, dash: "dash" } },
        { ...xy(controls), type: "scatter", mode: "lines+markers", name: "控制多边形", visible: $("[data-show-controls]").checked, line: { color: "rgba(0,113,227,.35)", dash: "dot" }, marker: { size: 7, color: "white", line: { color: colors.blue, width: 1.5 } } },
        { ...xy(placed.chord), type: "scatter", mode: "lines", name: "弦线", line: { color: colors.ink, width: 1.3 } },
        { ...xy(placed.axial_reference), type: "scatter", mode: "lines", name: "轴向基准", line: { color: "#b8b8bd", width: 1, dash: "dashdot" } },
        { ...xy(placed.stagger_arc), type: "scatter", mode: "lines", name: "安装角", line: { color: colors.green, width: 2 } }
      ];
      Charts.render("profile-chart", traces, { xaxis: { title: "轴向 x/c", constrain: "domain", zeroline: false }, yaxis: { title: "切向 y/c", scaleanchor: "x", scaleratio: 1, zeroline: false }, uirevision: `${geometry.geometry_id}:${placed.stagger_deg}`, annotations: [{ x: placed.stagger_arc.at(-1)[0], y: placed.stagger_arc.at(-1)[1], text: `γ = ${placed.stagger_deg.toFixed(2)}°`, showarrow: false, xshift: 18, yshift: 10 }] });
      $("[data-geometry-id]").textContent = geometry.geometry_id;
      $("[data-geometry-label]").textContent = payload.label || "自定义叶型";
      const validation = $("[data-validation-status]"); validation.textContent = ({ valid: "几何有效", warning: "有几何警告", rejected: "几何未通过" })[payload.validation.status] || payload.validation.status; validation.className = `pill ${payload.validation.status}`;
      renderIssues(payload.validation);
      $("[data-le-radius]").textContent = `r/c = ${geometry.edge_metrics.leading_edge_radius_ratio.toExponential(4)}`;
      $("[data-te-radius]").textContent = `r/c = ${geometry.edge_metrics.trailing_edge_radius_ratio.toExponential(4)}`;
      renderEdge("le-chart", payload, "leading"); renderEdge("te-chart", payload, "trailing"); renderAnalysis(payload);
    }
    function renderEdge(id, payload, edge) {
      const geometry = payload.geometry; const placed = payload.placement; const isLE = edge === "leading";
      const circle = placed[`${edge}_osculating_circle`]; const edgeData = placed.edges[edge]; const center = edgeData.center; const nose = edgeData.nose; const tangent = edgeData.tangent; const radius = geometry.edge_metrics[`${edge}_edge_radius_ratio`];
      const count = Math.max(20, Math.floor(placed.suction.length * .16)); const pick = points => isLE ? points.slice(0, count) : points.slice(-count);
      const surfacePoints = [...pick(placed.suction), ...pick(placed.pressure)]; const tangentLength = radius * 2.2;
      const tangentTrace = { x: [nose[0] - tangent[0] * tangentLength, nose[0] + tangent[0] * tangentLength], y: [nose[1] - tangent[1] * tangentLength, nose[1] + tangent[1] * tangentLength], mode: "lines", name: "鼻点切线", line: { color: colors.muted, dash: "dot" } };
      Charts.render(id, [{ ...xy(surfacePoints), mode: "markers", name: "轮廓", marker: { size: 3, color: "#c7c7cc" } }, { ...xy(circle), mode: "lines", name: "密切圆", line: { color: colors.blue, width: 2, dash: "dash" } }, { x: [center[0]], y: [center[1]], mode: "markers", name: "圆心", marker: { symbol: "cross", size: 10, color: colors.red } }, { x: [nose[0]], y: [nose[1]], mode: "markers", name: "鼻点", marker: { size: 8, color: colors.orange } }, tangentTrace], { margin: { l: 46, r: 12, t: 26, b: 38 }, xaxis: { title: "轴向 x/c", zeroline: false }, yaxis: { title: "切向 y/c", scaleanchor: "x", scaleratio: 1, zeroline: false }, showlegend: true, legend: { orientation: "h", y: 1.08 }, uirevision: placed.stagger_deg });
    }
    function renderAnalysis(payload) {
      const a = payload.analysis, interior = $("[data-analysis-interior]", root).checked;
      const margin = Math.max(6, Math.floor(0.05 * a.suction_s.length));
      const curvatureTab = ["curvature", "gradient"].includes(analysisTab);
      $("[data-analysis-controls]", root).hidden = !curvatureTab;
      let traces, xTitle = "局部弦向位置 x/c", yTitle, help, summary = "", shapes = [];
      if (analysisTab === "thickness") {
        traces = [{x: a.camber_x, y: a.thickness, name: "厚度 t/c", line: {color: colors.blue, width: 2}}]; yTitle = "厚度 t/c";
        help = "横轴从前缘到尾缘；纵轴是厚度与弦长之比。看最大厚度及其位置是否符合设定，内部厚度是否始终为正。t/c = 0.10 表示厚度是弦长的 10%。";
        summary = `最大厚度 t/c：${metricText(payload.features.max_thickness_ratio)}；位置 x/c：${metricText(payload.features.max_thickness_x_ratio)}`;
      } else if (curvatureTab) {
        const gradient = analysisTab === "gradient", suffix = gradient ? "curvature_gradient" : "curvature";
        const sides = ["suction", "pressure"], names = ["吸力面", "压力面"];
        traces = sides.map((side, index) => ({x: interior ? a[`${side}_s`].slice(margin, -margin) : a[`${side}_s`], y: interior ? a[`${side}_${suffix}`].slice(margin, -margin) : a[`${side}_${suffix}`], name: names[index], line: {color: index ? colors.orange : colors.blue}}));
        xTitle = "沿各自叶面从前缘累计的弧长 s/c"; yTitle = gradient ? "归一化曲率梯度 c² dκ/ds" : "归一化曲率 κc";
        summary = interior ? `按采样点数排除两端各 5%，每端至少 6 点；当前每端排除 ${margin} 点，与内部指标统计区间一致。` : "当前显示完整曲线；内部峰值统计仍排除两端采样点。曲率和梯度不设固定判定上限。";
        help = gradient ? "看弯曲程度变化是否突然：绝对值大的峰表示曲率变化快，不等于曲线不连续。优先检查内部尖峰和连续振荡；头尾圆角、离散采样也可能产生高值，不能仅凭一个点判断形状。红色标记仅表示内部最大绝对梯度，不代表超限。不设固定曲率或梯度上限，也不据此判断气动性能。" : "绝对值越大，局部弯曲越急，局部半径越小：R/c 约等于 1/|κc|。接近 0 表示局部较直；正负只表示弯曲方向，不代表好坏。两条曲线的 s/c 分别沿各自叶面累计，同一个 s/c 不一定对应同一个弦向位置；不要仅按左右两条曲线的高低判断优劣。";
        if (gradient) {
          let peak = null;
          sides.forEach((side, sideIndex) => {const values = a[`${side}_${suffix}`]; for (let i = margin; i < values.length - margin; i++) if (!peak || Math.abs(values[i]) > Math.abs(peak.y)) peak = {x: a[`${side}_s`][i], y: values[i], side: names[sideIndex]};});
          if (peak) {traces.push({x: [peak.x], y: [peak.y], mode: "markers", name: "内部最大绝对梯度", marker: {color: colors.red, size: 9}, hovertemplate: `${peak.side}<br>s/c=%{x:.6f}<br>c² dκ/ds=%{y:.4e}<extra></extra>`}); summary += ` 内部峰值：${peak.side}，s/c = ${metricText(peak.x)}，绝对值 ${metricText(Math.abs(peak.y))}。`;}
        }
      } else {
        traces = [{x: a.camber_x, y: a.angle_deg, name: "中弧线切线轴向角", line: {color: colors.ink}}]; yTitle = "相对轴向的金属线角度 / °";
        help = "这是中弧线切线相对轴向的方向角，不是进口/出口气流角或攻角。沿 x/c 从前缘读到尾缘，两端应接近设定的进口/出口金属角；看中间变化是否平顺，是否存在突跳或不期望的反向波动。即使左侧切换成切向角表示，此图仍按轴向角显示；图线端点是离散近似，校验使用曲线解析切线。";
        summary = `解析端点轴向角：进口 ${metricText(payload.features.inlet_metal_angle_axial_deg)}°，出口 ${metricText(payload.features.outlet_metal_angle_axial_deg)}°。`;
      }
      $("[data-analysis-summary]", root).textContent = summary; $("[data-analysis-help]", root).textContent = help;
      Charts.render("analysis-chart", traces, {xaxis: {title: {text: xTitle}, automargin: true, zeroline: false}, yaxis: {title: {text: yTitle}, automargin: true, zeroline: true}, shapes, margin: {l: 70, r: 15, t: 32, b: 58}, uirevision: `${payload.geometry.geometry_id}:${analysisTab}:${interior}`});
    }
    preview(true);
  }









  document.addEventListener("DOMContentLoaded", () => { initShell(); initStudio(); });
})();
