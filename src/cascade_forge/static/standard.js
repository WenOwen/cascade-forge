(() => {
  "use strict";
  document.addEventListener("DOMContentLoaded", () => {
    const root = document.querySelector("[data-standard-root]"); if (!root) return;
    const form = root.querySelector("form"), defaults = JSON.parse(root.dataset.defaultArc);
    const error = root.querySelector("[data-standard-error]"), state = root.querySelector("[data-standard-state]");
    let family = "naca65", current = null, sequence = 0;
    function body() {
      const f = form.elements, value = name => Number(f[name].value);
      if (family === "naca65") return {family, spec: {family: f.naca_family.value, thickness_ratio: value("thickness_ratio"),
        design_lift_coefficient: value("design_lift_coefficient"), loading_parameter_a: value("loading_parameter_a")}};
      const spec = {...defaults, mean_line: {...defaults.mean_line, family: "double", closure: "max_camber"}};
      for (const name of ["inlet_metal_angle_deg", "outlet_metal_angle_deg"]) spec.mean_line[name] = value(name);
      for (const name of ["max_thickness_ratio", "max_thickness_station", "leading_edge_radius_ratio", "trailing_edge_thickness_ratio", "trailing_edge_half_wedge_deg"]) spec[name] = value(name);
      return {family, spec};
    }
    const output = window.CFOutput.attach({request: () => current || body()});
    function invalidate() { sequence++; current = null; output.setReady(false); state.textContent = "参数已修改，请生成"; }
    function syncNaca() {
      const f = form.elements, selected = f.naca_family.value;
      f.loading_parameter_a.readOnly = selected !== "65";
      if (selected === "65-compressor") f.loading_parameter_a.value = 1;
      if (selected === "65A") f.loading_parameter_a.value = 0.8;
    }
    form.addEventListener("input", invalidate);
    form.elements.naca_family.addEventListener("change", () => {syncNaca(); invalidate();});
    root.querySelectorAll("[data-standard-family]").forEach(button => button.addEventListener("click", () => {
      family = button.dataset.standardFamily; invalidate(); error.textContent = "";
      root.querySelectorAll("[data-standard-family]").forEach(node => node.classList.toggle("active", node === button));
      root.querySelectorAll("[data-standard-fields]").forEach(fieldset => {fieldset.hidden = fieldset.disabled = fieldset.dataset.standardFields !== family;});
      output.setReady(false);
      document.dispatchEvent(new CustomEvent("cf:standard-family"));
    }));
    form.addEventListener("submit", async event => {
      event.preventDefault(); invalidate(); const token = sequence, requested = body();
      error.textContent = ""; state.textContent = "生成中…";
      try {
        if (family === "double_arc") {
          const limits = await (await window.CFOutput.request("/api/v2/arc/limits", requested.spec)).json();
          if (token !== sequence) return;
          root.querySelector("[data-standard-limits]").textContent = `尾缘半楔角上界：${limits.maximum_feasible_half_wedge_deg?.toFixed(3) ?? "无可行值"}°`;
          if (!limits.geometry_feasible) throw new Error(limits.geometry_error || "当前参数不满足几何可行域");
        }
        const record = await (await window.CFOutput.request("/api/workbench/preview", requested)).json();
        if (token !== sequence) return;
        await showRecord(record);
      } catch (exc) { if (token === sequence) {error.textContent = exc.message; state.textContent = "生成失败";} }
    });
    async function showRecord(record) {
        current = {family: record.family, spec: record.spec}; output.setReady(true); state.textContent = "已生成";
        root.querySelector("[data-standard-name]").textContent = record.name;
        root.querySelector("[data-standard-id]").textContent = record.geometry_id;
        const traces = ["suction", "pressure", "camber"].map((side, index) => ({
          x: record[side].map(p => p[0]), y: record[side].map(p => p[1]), mode: "lines", name: ["吸力面", "压力面", "中弧线"][index],
          line: {color: ["#0071e3", "#ff7a00", "#6e6e73"][index], width: index === 2 ? 1 : 2, dash: index === 2 ? "dash" : "solid"}
        }));
        await window.CFCharts.render("standard-chart", traces, {xaxis: {title: "x/c"}, yaxis: {title: "y/c", scaleanchor: "x", scaleratio: 1}});
    }
    window.CFStandard = {request: () => current || body(), async loadRecord(record) {
      if (!["naca65", "double_arc"].includes(record.family)) throw new Error("不能在标准叶型页编辑此类样本");
      family = record.family; invalidate(); error.textContent = "";
      root.querySelectorAll("[data-standard-family]").forEach(node => node.classList.toggle("active", node.dataset.standardFamily === family));
      root.querySelectorAll("[data-standard-fields]").forEach(fieldset => fieldset.hidden = fieldset.disabled = fieldset.dataset.standardFields !== family);
      const f = form.elements, spec = record.spec;
      if (family === "naca65") {f.naca_family.value = spec.family; for (const name of ["thickness_ratio", "design_lift_coefficient", "loading_parameter_a"]) f[name].value = spec[name]; syncNaca();}
      else {for (const name of ["inlet_metal_angle_deg", "outlet_metal_angle_deg"]) f[name].value = spec.mean_line[name]; for (const name of ["max_thickness_ratio", "max_thickness_station", "leading_edge_radius_ratio", "trailing_edge_thickness_ratio", "trailing_edge_half_wedge_deg"]) f[name].value = spec[name];}
      await showRecord(record); return true;
    }};
    syncNaca(); output.setReady(false);
  });
})();
