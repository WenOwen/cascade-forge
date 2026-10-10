(() => {
  "use strict";
  document.addEventListener("DOMContentLoaded", () => {
    const root = document.querySelector("[data-profile-rail]"); if (!root) return;
    const list = root.querySelector("[data-rail-list]"), status = root.querySelector("[data-rail-status]");
    const source = root.querySelector("[data-rail-source]"); let active = "actions", offset = 0, token = 0, next = false;
    const pageSize = 12;
    function switchTab(name, focus = false) {
      active = name;
      root.querySelectorAll("[data-rail-tab]").forEach(button => {const selected = button.dataset.railTab === name; button.setAttribute("aria-selected", String(selected)); button.tabIndex = selected ? 0 : -1; if (selected && focus) button.focus();});
      root.querySelectorAll("[data-rail-panel]").forEach(panel => panel.hidden = panel.dataset.railPanel !== name);
      if (name === "saved") load();
    }
    root.querySelectorAll("[data-rail-tab]").forEach(button => {
      button.addEventListener("click", () => switchTab(button.dataset.railTab));
      button.addEventListener("keydown", event => {if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {event.preventDefault(); switchTab(event.key === "Home" ? "actions" : event.key === "End" ? "saved" : active === "actions" ? "saved" : "actions", true);}});
    });
    async function load() {
      await window.CFDatabase.ready;
      const current = ++token, workspace = source?.value === "workspace";
      status.textContent = "正在读取…"; root.querySelector("[data-rail-prev]").disabled = root.querySelector("[data-rail-next]").disabled = true;
      try {
        const family = window.CFStandard?.request().family || "";
        const url = workspace ? `/api/v2/workspace/designs?offset=${offset}&limit=${pageSize + 1}` : `/api/workbench/profiles?offset=${offset}&limit=${pageSize}&family=${family}`;
        const data = await (await window.CFOutput.request(url)).json(); if (current !== token) return;
        next = workspace ? data.items.length > pageSize : offset + pageSize < data.total; list.replaceChildren();
        for (const item of data.items.slice(0, pageSize)) {
          const card = document.createElement("article"); card.className = "rail-saved-card";
          const name = document.createElement("strong"); name.textContent = item.name;
          const id = document.createElement("small"); id.textContent = workspace ? `修订 ${item.revision}` : ({custom: "自定义叶型", naca65: "NACA65", double_arc: "双圆弧", imported: "DAT 导入"}[item.family] || "已保存截面");
          const button = document.createElement("button"); button.type = "button"; button.className = "button"; button.textContent = "打开叶型";
          button.addEventListener("click", async () => {
            const version = token; button.disabled = true;
            try {
              const record = await (await window.CFOutput.request(workspace ? `/api/v2/workspace/designs/${item.design_id}` : `/api/workbench/profiles/${item.geometry_id}`)).json();
              if (version !== token) return;
              if (record.family === "imported" || (window.CFStudio && record.family && record.family !== "custom")) {location.href = `/database?search=${encodeURIComponent(item.geometry_id)}&database=${encodeURIComponent(window.CFDatabase.name)}`; return;}
              const opened = window.CFStudio ? await window.CFStudio.loadDesign(workspace ? record : {name: record.name, spec: record.spec}) : await window.CFStandard.loadRecord(record);
              if (opened !== false) switchTab("actions");
            } catch (error) {status.textContent = error.message;} finally {button.disabled = false;}
          });
          card.append(name, id, button); list.append(card);
        }
        status.textContent = data.items.length ? "点击样本可继续编辑或导出。" : "暂无保存记录。";
        root.querySelector("[data-rail-page]").textContent = `第 ${offset / pageSize + 1} 页`;
        root.querySelector("[data-rail-prev]").disabled = offset === 0; root.querySelector("[data-rail-next]").disabled = !next;
      } catch (error) {if (current === token) status.textContent = error.message;}
    }
    root.querySelector("[data-rail-refresh]").addEventListener("click", () => {offset = 0; load();});
    root.querySelector("[data-rail-prev]").addEventListener("click", () => {offset = Math.max(0, offset - pageSize); load();});
    root.querySelector("[data-rail-next]").addEventListener("click", () => {offset += pageSize; load();});
    source?.addEventListener("change", () => {offset = 0; load();});
    root.querySelector("[data-rail-open-history]")?.addEventListener("click", () => {
      source.value = "workspace"; offset = 0;
      root.querySelector("[data-rail-history-source]").open = true;
      switchTab("saved", true);
    });
    ["cf:design-saved", "cf:profile-saved", "cf:database-changed", "cf:database-scanned", "cf:standard-family"].forEach(name => document.addEventListener(name, () => {token++; offset = 0; if (source && ["cf:profile-saved", "cf:database-changed", "cf:database-scanned"].includes(name)) source.value = "dataset"; if (active === "saved") load();}));
    if (source && new URLSearchParams(location.search).get("open") === "workspace") {source.value = "workspace"; root.querySelector("[data-rail-history-source]").open = true; switchTab("saved");}
  });
})();
