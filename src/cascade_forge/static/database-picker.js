(() => {
  "use strict";
  let name = new URLSearchParams(location.search).get("database") || localStorage.getItem("cf.database") || "默认数据库", resolveReady;
  const ready = new Promise(resolve => {resolveReady = resolve;});
  const api = window.CFDatabase = {
    ready, get name() {return name;},
    query(url) {const result = new URL(url, location.origin); if (!result.searchParams.has("database")) result.searchParams.set("database", name); return result.pathname + result.search;},
    body(body) {return {...body, database: name};}
  };
  document.addEventListener("DOMContentLoaded", async () => {
    const root = document.querySelector("[data-database-picker]");
    if (!root) {resolveReady(); return;}
    const select = root.querySelector("select"), feedback = root.querySelector("[data-database-feedback]");
    const form = root.querySelector("[data-database-new-form]"); let items = [];
    function changed() {
      name = select.value; localStorage.setItem("cf.database", name);
      root.querySelector("[data-database-path]").textContent = items.find(item => item.name === name)?.path || "";
      document.querySelectorAll('a[href^="/api/workbench/dataset/export"]').forEach(link => link.href = api.query("/api/workbench/dataset/export"));
      document.dispatchEvent(new CustomEvent("cf:database-changed", {detail: {name}}));
    }
    async function refresh(selected = name) {
      const response = await window.CFOutput.request("/api/workbench/databases");
      const data = await response.json(); items = data.items;
      select.replaceChildren(...items.map(item => new Option(item.name, item.name)));
      select.value = items.some(item => item.name === selected) ? selected : data.default;
      changed();
    }
    select.addEventListener("change", changed);
    root.querySelector("[data-database-create]").addEventListener("click", () => {form.hidden = false; form.elements.name.focus();});
    root.querySelector("[data-database-cancel]").addEventListener("click", () => form.hidden = true);
    form.addEventListener("submit", async event => {
      event.preventDefault(); const button = form.querySelector("[type=submit]"); button.disabled = true;
      try {const result = await (await window.CFOutput.request("/api/workbench/databases", {name: form.elements.name.value})).json(); await refresh(result.name); form.hidden = true; form.reset(); feedback.textContent = "数据库已创建";}
      catch (error) {feedback.textContent = error.message;} finally {button.disabled = false;}
    });
    root.querySelector("[data-database-scan]").addEventListener("click", async event => {
      const database = name; event.currentTarget.disabled = true; feedback.textContent = "正在扫描…";
      try {
        const result = await (await window.CFOutput.request(`/api/workbench/databases/${encodeURIComponent(database)}/scan`, {})).json();
        if (database !== name) return;
        feedback.textContent = `读取 ${result.imported.length} 个 DAT，已有原生文件 ${result.skipped.length} 个。` + result.errors.map(item => `\n${item.file}: ${item.error}`).join("");
        document.dispatchEvent(new CustomEvent("cf:database-scanned"));
      } catch (error) {feedback.textContent = error.message;} finally {root.querySelector("[data-database-scan]").disabled = false;}
    });
    try {await refresh();} catch (error) {feedback.textContent = error.message;} finally {resolveReady();}
  });
})();
