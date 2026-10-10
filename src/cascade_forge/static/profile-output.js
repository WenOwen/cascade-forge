(() => {
  "use strict";
  const labels = {
    custom: {max_thickness_ratio: "最大厚度 t/c", max_thickness_x_ratio: "厚度位置 x/c", inlet_metal_angle_deg: "进口金属角", outlet_metal_angle_deg: "出口金属角", leading_edge_radius_ratio: "前缘半径"},
    naca65: {thickness_ratio: "名义厚度 t/c", design_lift_coefficient: "设计升力系数"},
    double_arc: {max_thickness_ratio: "最大厚度 t/c", max_thickness_station: "厚度位置 sₜ", inlet_metal_angle_deg: "进口金属角", outlet_metal_angle_deg: "出口金属角", trailing_edge_half_wedge_deg: "尾缘半楔角"}
  };
  async function request(url, body, options = {}) {
    if (window.CFDatabase && url.startsWith("/api/workbench/") && !url.startsWith("/api/workbench/databases")) {
      url = window.CFDatabase.query(url);
      if (body?.family) body = window.CFDatabase.body(body);
    }
    const response = await fetch(url, {method: body ? "POST" : "GET", ...(body ? {headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)} : {}), ...options});
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(typeof payload.detail === "string" ? payload.detail : JSON.stringify(payload.detail || `HTTP ${response.status}`));
    }
    return response;
  }
  async function download(url, body) {
    const response = await request(url, body), blob = await response.blob();
    const link = document.createElement("a"), href = URL.createObjectURL(blob);
    link.href = href; link.download = /filename="([^"]+)"/.exec(response.headers.get("Content-Disposition") || "")?.[1] || "download";
    link.click(); setTimeout(() => URL.revokeObjectURL(href), 1000);
  }
  window.CFOutput = {request, download, attach(provider) {
    const root = document.querySelector("[data-profile-output]"); if (!root) return;
    const status = root.querySelector("[data-output-status]"), sweep = root.querySelector("[data-sweep-form]");
    const dialog = document.querySelector("[data-geomturbo-dialog]"), geometryForm = dialog.querySelector("form");
    let ready = false, busy = false, jobRunning = false, lastFamily = "";
    const coordinateNote = root.querySelector("[data-coordinate-note]"), nativeNote = coordinateNote?.textContent;
    function update() {
      const body = provider.request(), imported = body?.family === "imported";
      root.querySelectorAll("button[data-profile-save], button[data-profile-export], button[data-profile-3d], button[data-sweep-start]").forEach(button => button.disabled = !ready || busy || (button.matches("[data-sweep-start]") && jobRunning) || (imported && button.matches("[data-profile-3d]")));
      if (coordinateNote) coordinateNote.textContent = imported ? "DAT 导入保留原文件坐标与单位，不自动归一化；尚未确认坐标系，暂不用于三维导出。" : nativeNote;
      if (body && body.family !== lastFamily) {
        lastFamily = body.family; sweep.elements.parameter.replaceChildren();
        Object.entries(labels[lastFamily] || {}).forEach(([key, label]) => sweep.elements.parameter.add(new Option(label, key)));
        sweep.elements.minimum.value = ""; sweep.elements.maximum.value = "";
      }
    }
    async function execute(action) {
      busy = true; status.textContent = "处理中…"; update();
      try { await action(); } catch (error) { status.textContent = error.message; }
      finally { busy = false; update(); }
    }
    root.querySelector("[data-profile-save]").addEventListener("click", () => execute(async () => {
      const submitted = structuredClone(provider.request());
      const result = await (await request("/api/workbench/profiles", submitted)).json();
      status.textContent = `已保存至「${result.dataset}」`;
      document.dispatchEvent(new CustomEvent("cf:profile-saved", {detail: {...result, submitted}}));
    }));
    root.querySelectorAll("[data-profile-export]").forEach(button => button.addEventListener("click", () => execute(async () => {
      const identifier = provider.identifier?.();
      await download(identifier ? `/api/workbench/profiles/${encodeURIComponent(identifier)}/export/${button.dataset.profileExport}` : `/api/workbench/export/${button.dataset.profileExport}`, identifier ? null : provider.request());
      status.textContent = "坐标文件已准备";
    })));
    root.querySelector("[data-profile-3d]").addEventListener("click", () => execute(async () => {
      let identifier = provider.identifier?.();
      if (!identifier) identifier = (await (await request("/api/workbench/profiles", provider.request())).json()).geometry_id;
      const result = await (await request(`/api/workbench/profiles?family=${provider.request().family}&limit=100`)).json();
      for (const name of ["root_id", "tip_id"]) {
        const select = geometryForm.elements[name]; select.replaceChildren(new Option("当前叶型", identifier));
        result.items.filter(item => item.geometry_id !== identifier).forEach((item, index) => select.add(new Option(`${item.label || item.name}（样本 ${index + 1}）`, item.geometry_id)));
      }
      dialog.querySelector("[data-geomturbo-error]").textContent = ""; dialog.showModal();
      status.textContent = "请填写三维尺寸后导出";
    }));
    dialog.querySelector("[data-close-geomturbo]").addEventListener("click", () => dialog.close());
    geometryForm.addEventListener("submit", async event => {
      event.preventDefault(); const values = geometryForm.elements, error = dialog.querySelector("[data-geomturbo-error]");
      const submit = geometryForm.querySelector("[type=submit]"); submit.disabled = true;
      try {
        await download("/api/workbench/geomturbo", {
          name: "blade", blade_count: Number(values.blade_count.value), rotation_direction: values.rotation_direction.value,
          sections: ["root", "tip"].map((side, i) => ({span_fraction: i, geometry_id: values[`${side}_id`].value,
            radius_m: Number(values[`${side}_radius`].value), chord_m: Number(values[`${side}_chord`].value), stagger_deg: Number(values[`${side}_stagger`].value)}))
        }); dialog.close(); status.textContent = "GeomTurbo 文件已准备";
      } catch (exc) { error.textContent = exc.message; } finally { submit.disabled = false; }
    });
    sweep.addEventListener("submit", event => {
      event.preventDefault(); if (jobRunning) return;
      execute(async () => {
        const report = root.querySelector("[data-sweep-status]"); jobRunning = true;
        try {
          const result = await (await request("/api/workbench/batches", {...provider.request(), parameter: sweep.elements.parameter.value,
            minimum: Number(sweep.elements.minimum.value), maximum: Number(sweep.elements.maximum.value), count: Number(sweep.elements.count.value)})).json();
          status.textContent = "批量生成已启动";
          for (;;) {
            const job = await (await request(`/api/v2/jobs/${result.job_id}`)).json();
            report.textContent = "批量生成中…";
            if (job.status === "failed") throw new Error(job.error || "批量生成失败");
            if (job.status === "completed") {
              const data = job.result;
              report.textContent = `成功 ${data.generated}/${data.requested}，唯一叶型 ${data.unique}。` + data.failures.map(item => `\n${item.value}: ${item.error}`).join("");
              status.textContent = "批量生成结束，可浏览数据库或下载数据集"; break;
            }
            await new Promise(resolve => setTimeout(resolve, 1000));
          }
        } finally { jobRunning = false; }
      });
    });
    return {setReady(value) { ready = value; update(); }};
  }};
})();
