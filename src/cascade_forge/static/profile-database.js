(() => {
  "use strict";
  document.addEventListener("DOMContentLoaded", () => {
    const root = document.querySelector("[data-profile-database]"); if (!root) return;
    const form = root.querySelector("[data-library-filters]"), list = root.querySelector("[data-library-body]");
    const error = root.querySelector("[data-library-error]"), charts = root.querySelector("[data-library-charts]");
    const detail = root.querySelector("[data-library-detail]"), clear = root.querySelector("[data-library-clear]");
    const familyName = {custom: "自定义", naca65: "NACA65", double_arc: "双圆弧", imported: "DAT 导入"};
    form.elements.search.value = new URLSearchParams(location.search).get("search") || "";
    const comparison = new Map(), cache = new Map();
    let offset = 0, total = 0, selected = null, activeId = null, activeItem = null, sequence = 0, viewSequence = 0;
    const output = window.CFOutput.attach({request: () => selected ? {family: selected.family, spec: selected.spec} : null, identifier: () => selected?.geometry_id});
    root.querySelector("[data-sweep-panel]").hidden = true;
    root.querySelector("[data-profile-save]").hidden = true;

    function syncSelection() {
      list.querySelectorAll("[data-sample-id]").forEach(row => {
        const id = row.dataset.sampleId, active = id === activeId;
        row.classList.toggle("active", active); row.classList.toggle("checked", comparison.has(id));
        row.querySelector("input").checked = comparison.has(id);
        row.querySelector("button").setAttribute("aria-pressed", String(active));
      });
      charts.querySelectorAll("[data-chart-id]").forEach(card => card.classList.toggle("active", card.dataset.chartId === activeId));
      clear.disabled = comparison.size === 0;
      root.querySelector("[data-library-selection]").textContent = comparison.size > 1
        ? `已选 ${comparison.size}/4 个样本，使用一致的坐标范围。点击图卡的「导出此样本」切换导出对象。`
        : activeId ? "单样本预览；勾选左侧样本可比较，最多 4 个。" : "从左侧选择样本";
    }
    function getRecord(identifier) {
      if (!cache.has(identifier)) {
        const pending = window.CFOutput.request(`/api/workbench/profiles/${encodeURIComponent(identifier)}`)
          .then(response => response.json()).catch(exc => {cache.delete(identifier); throw exc;});
        cache.set(identifier, pending);
      }
      return cache.get(identifier);
    }
    function setExport(record) {
      selected = record; detail.hidden = !record; output.setReady(Boolean(record));
      root.querySelector("[data-library-id]").textContent = record ? record.geometry_id : "";
      root.querySelector("[data-library-export-target]").textContent = record ? `当前导出样本：${record.name}` : "";
    }
    async function activate(item, keepComparison = false) {
      error.textContent = ""; activeId = item.geometry_id; activeItem = item;
      if (!keepComparison && !comparison.has(activeId)) comparison.clear();
      await showPreview();
    }
    async function showPreview() {
      const token = ++viewSequence; setExport(null); syncSelection();
      const items = comparison.size ? [...comparison.values()] : activeItem ? [activeItem] : [];
      root.querySelector("[data-library-empty]").hidden = items.length > 0;
      root.querySelector("[data-library-name]").textContent = items.length > 1 ? "叶型比较" : "叶型预览";
      charts.classList.toggle("multiple", items.length > 1);
      charts.querySelectorAll(".plotly-chart").forEach(node => window.CFCharts.dispose(node));
      charts.replaceChildren();
      const slots = items.map(item => {
        const card = document.createElement("article"); card.className = "library-chart-card"; card.dataset.chartId = item.geometry_id;
        const heading = document.createElement("div"); heading.className = "library-chart-heading";
        const title = document.createElement("h3"); title.textContent = `${item.name} · ${familyName[item.family] || item.family}`;
        const button = document.createElement("button"); button.type = "button"; button.className = "button"; button.textContent = "导出此样本";
        button.addEventListener("click", () => activate(item, true)); heading.append(title, button);
        const chart = document.createElement("div"); chart.className = "plotly-chart empty-chart"; chart.dataset.empty = "正在读取叶型…";
        const id = document.createElement("code"); id.textContent = item.geometry_id; id.hidden = true;
        card.append(heading, chart, id); charts.append(card); return {item, card, chart, button};
      });
      syncSelection();
      const results = await Promise.all(slots.map(async slot => {
        try { return {slot, record: await getRecord(slot.item.geometry_id)}; }
        catch (exc) { return {slot, error: exc.message}; }
      }));
      if (token !== viewSequence) return;
      const records = results.filter(result => result.record).map(result => result.record);
      const imported = records.some(record => record.family === "imported");
      let minimumY = 0, maximumY = 0, minimumX = 0, maximumX = 1;
      records.forEach(record => ["suction", "pressure"].forEach(side => record[side].forEach(([x, y]) => {
        minimumY = Math.min(minimumY, y); maximumY = Math.max(maximumY, y); minimumX = Math.min(minimumX, x); maximumX = Math.max(maximumX, x);
      })));
      const padY = Math.max(0.025, (maximumY - minimumY) * 0.12), padX = (maximumX - minimumX) * 0.04;
      for (const result of results) {
        if (result.error) {
          const message = document.createElement("p"); message.className = "library-chart-error"; message.textContent = result.error;
          const retry = document.createElement("button"); retry.className = "button"; retry.textContent = "重试"; retry.type = "button";
          retry.addEventListener("click", showPreview); result.slot.chart.replaceWith(message, retry); result.slot.button.disabled = true;
          continue;
        }
        const record = result.record;
        await window.CFCharts.render(result.slot.chart, ["suction", "pressure", "camber"].map((side, index) => ({
          x: record[side].map(p => p[0]), y: record[side].map(p => p[1]), mode: "lines", name: (record.family === "imported" ? ["轮廓 A", "轮廓 B", ""] : ["吸力面", "压力面", "中弧线"])[index],
          line: {color: ["#0071e3", "#ff7a00", "#6e6e73"][index], width: index === 2 ? 1 : 2, dash: index === 2 ? "dash" : "solid"}
        })), {xaxis: {title: {text: imported ? "x（原坐标）" : "x/c"}, automargin: true, range: [minimumX - padX, maximumX + padX]},
          yaxis: {title: {text: imported ? "y（原坐标）" : "y/c"}, automargin: true, range: [minimumY - padY, maximumY + padY], scaleanchor: "x", scaleratio: 1},
          legend: {orientation: "h", y: 1.12, x: 0, yanchor: "bottom"},
          margin: {l: 48, r: 12, t: 34, b: 42}});
        if (token !== viewSequence) return;
      }
      const active = records.find(record => record.geometry_id === activeId);
      setExport(active || null); syncSelection();
      if (imported) root.querySelector("[data-library-selection]").textContent += " DAT 保留原始坐标，比较前请确认单位一致。";
    }
    function toggle(item, input) {
      error.textContent = "";
      if (input.checked) {
        if (comparison.size === 4 && !comparison.has(item.geometry_id)) {
          input.checked = false; error.textContent = "最多比较 4 个样本，请先取消一个已选样本。"; return;
        }
        comparison.set(item.geometry_id, item); activeId = item.geometry_id; activeItem = item;
      } else {
        comparison.delete(item.geometry_id);
        if (comparison.size && !comparison.has(activeId)) {
          activeItem = [...comparison.values()][0]; activeId = activeItem.geometry_id;
        }
      }
      showPreview();
    }
    async function load() {
      const token = ++sequence; error.textContent = "";
      list.setAttribute("aria-busy", "true");
      root.querySelector("[data-library-prev]").disabled = root.querySelector("[data-library-next]").disabled = true;
      try {
        const params = new URLSearchParams(new FormData(form)); params.set("offset", offset); params.set("limit", 25);
        const result = await (await window.CFOutput.request(`/api/workbench/profiles?${params}`)).json();
        if (token !== sequence) return; total = result.total; list.replaceChildren(); list.scrollTop = 0;
        for (const item of result.items) {
          const row = document.createElement("div"); row.className = "library-sample"; row.dataset.sampleId = item.geometry_id;
          const checkbox = document.createElement("input"); checkbox.type = "checkbox"; checkbox.setAttribute("aria-label", `比较 ${item.name}`);
          checkbox.addEventListener("change", () => toggle(item, checkbox));
          const button = document.createElement("button"); button.type = "button"; button.className = "library-sample-button";
          const name = document.createElement("strong"); name.textContent = item.name;
          const family = document.createElement("small"); family.textContent = familyName[item.family] || item.family;
          const id = document.createElement("small"); id.textContent = item.geometry_id; id.hidden = true;
          button.append(name, family, id); button.addEventListener("click", () => activate(item)); row.append(checkbox, button); list.append(row);
        }
        if (!result.items.length) {const empty = document.createElement("p"); empty.className = "field-help"; empty.textContent = "没有匹配叶型，可先生成并保存样本。"; list.append(empty);}
        root.querySelector("[data-library-count]").textContent = `共 ${total} 个叶型`;
        root.querySelector("[data-library-page]").textContent = `第 ${offset / 25 + 1} 页`;
        root.querySelector("[data-library-prev]").disabled = offset === 0;
        root.querySelector("[data-library-next]").disabled = offset + 25 >= total;
        syncSelection();
        if (!activeId && result.items.length) activate(result.items[0]);
      } catch (exc) {
        if (token === sequence) {error.textContent = exc.message; root.querySelector("[data-library-count]").textContent = "读取失败，请重新查询";}
      } finally {if (token === sequence) list.setAttribute("aria-busy", "false");}
    }
    clear.addEventListener("click", () => {comparison.clear(); error.textContent = ""; showPreview();});
    form.addEventListener("submit", event => {event.preventDefault(); offset = 0; load();});
    form.addEventListener("reset", () => {offset = 0; setTimeout(load, 0);});
    root.querySelector("[data-library-prev]").addEventListener("click", () => {offset = Math.max(0, offset - 25); load();});
    root.querySelector("[data-library-next]").addEventListener("click", () => {offset += 25; load();});
    function resetBrowser() {
      viewSequence++; sequence++; offset = 0; activeId = null; activeItem = null;
      comparison.clear(); cache.clear(); setExport(null); showPreview(); load();
    }
    document.addEventListener("cf:database-changed", resetBrowser);
    document.addEventListener("cf:database-scanned", resetBrowser);
    window.CFDatabase.ready.then(load);
  });
})();
