(() => {
  "use strict";

  const dialog = document.querySelector("#workspace-browser");
  if (!dialog) return;
  const find = name => dialog.querySelector(`[data-workspace-browser-${name}]`);
  const list = find("list"), status = find("status"), errorBox = find("error");
  const pageSize = 12;
  let offset = 0, hasNext = false, loading = false, acting = false;
  let listRequest = null, actionRequest = null, generation = 0, retryAction = null;
  let returnFocus = null;

  function setError(message, retry) {
    find("error-message").textContent = message;
    errorBox.hidden = false;
    retryAction = retry;
  }

  function clearError() {
    errorBox.hidden = true;
    retryAction = null;
  }

  function updateButtons() {
    const busy = loading || acting;
    find("refresh").disabled = busy;
    find("retry").disabled = busy;
    find("prev").disabled = busy || offset === 0;
    find("next").disabled = busy || !hasNext;
    list.querySelectorAll("button").forEach(button => { button.disabled = busy; });
    list.setAttribute("aria-busy", String(busy));
  }

  async function readJSON(url, signal) {
    const response = await fetch(url, { signal, headers: { Accept: "application/json" } });
    if (!response.ok) {
      throw new Error(response.status === 404 ? "这份设计已无法找到，请刷新列表。" : `请求失败（${response.status}），请重试。`);
    }
    return response.json();
  }

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function savedDate(value) {
    // Workspace timestamps are stored in UTC without a timezone suffix.
    let iso = String(value || "").replace(" ", "T").replace(/(\.\d{3})\d+$/, "$1");
    if (iso && !/(Z|[+-]\d{2}:?\d{2})$/i.test(iso)) iso += "Z";
    const date = new Date(iso);
    return Number.isNaN(date.getTime()) ? String(value || "时间未知") : date.toLocaleString("zh-CN", { hour12: false });
  }

  function renderDesign(record) {
    const name = String(record.name || "未命名叶型");
    const revision = String(record.revision ?? "—");
    const card = element("li", "workspace-design-card");
    const info = element("div", "workspace-design-info");
    const title = element("div", "workspace-design-title");
    title.append(element("h3", "", name), element("span", "pill", `修订 ${revision}`));
    info.append(title, element("span", "workspace-design-date", `保存于 ${savedDate(record.created_at)}`));
    if (Array.isArray(record.tags) && record.tags.length) {
      const tags = element("div", "workspace-design-tags");
      tags.setAttribute("aria-label", "标签");
      record.tags.forEach(tag => tags.append(element("span", "pill", String(tag))));
      info.append(tags);
    }
    const actions = element("div", "workspace-design-actions");
    const open = element("button", "button primary", "打开编辑");
    open.type = "button";
    open.setAttribute("aria-label", `打开 ${name}，修订 ${revision}`);
    open.addEventListener("click", () => openDesign(record));
    const download = element("button", "button", "下载备份");
    download.type = "button";
    download.setAttribute("aria-label", `下载 ${name}，修订 ${revision} 的 JSON 备份`);
    download.addEventListener("click", () => downloadDesign(record));
    actions.append(open, download);
    card.append(info, actions);
    return card;
  }

  async function loadList(nextOffset = offset) {
    listRequest?.abort();
    const controller = new AbortController();
    listRequest = controller;
    const token = generation;
    loading = true;
    clearError();
    find("empty").hidden = true;
    status.textContent = "正在读取设计…";
    updateButtons();
    try {
      const data = await readJSON(`/api/v2/workspace/designs?limit=${pageSize + 1}&offset=${nextOffset}`, controller.signal);
      if (controller.signal.aborted || token !== generation || !dialog.open) return;
      if (!Array.isArray(data.items)) throw new Error("设计列表格式异常，请重试。");
      offset = nextOffset;
      hasNext = data.items.length > pageSize;
      const items = data.items.slice(0, pageSize);
      list.replaceChildren(...items.map(renderDesign));
      find("empty").hidden = items.length > 0 || offset > 0;
      find("pagination").hidden = items.length === 0 && offset === 0;
      find("page").textContent = `第 ${Math.floor(offset / pageSize) + 1} 页`;
      status.textContent = items.length ? `第 ${offset + 1}–${offset + items.length} 条 · 最近保存的排在前面` : offset ? "这一页没有设计，可以返回上一页。" : "保存的设计会显示在这里";
    } catch (error) {
      if (error.name === "AbortError" || token !== generation) return;
      status.textContent = "未能读取设计";
      setError(error instanceof TypeError ? "无法连接服务，请检查连接后重试。" : error.message, () => loadList(nextOffset));
    } finally {
      if (listRequest === controller) {
        listRequest = null;
        loading = false;
        updateButtons();
      }
    }
  }

  async function performAction(record, action, operation) {
    if (loading || acting) return;
    const controller = new AbortController();
    actionRequest = controller;
    const token = generation;
    acting = true;
    clearError();
    status.textContent = `正在${action}“${record.name || "未命名叶型"}”…`;
    updateButtons();
    try {
      await operation(controller.signal, token);
    } catch (error) {
      if (error.name === "AbortError" || token !== generation) return;
      status.textContent = `${action}未完成`;
      setError(error instanceof TypeError ? "无法连接服务，请检查连接后重试。" : error.message, () => performAction(record, action, operation));
    } finally {
      if (actionRequest === controller) {
        actionRequest = null;
        acting = false;
        updateButtons();
      }
    }
  }

  function openDesign(record) {
    return performAction(record, "打开", async (signal, token) => {
      const design = await readJSON(`/api/v2/workspace/designs/${encodeURIComponent(record.design_id)}`, signal);
      if (signal.aborted || token !== generation || !dialog.open) return;
      if (!window.CFStudio?.loadDesign) throw new Error("工作室尚未准备好，请稍后重试。");
      const loaded = await window.CFStudio.loadDesign(design);
      if (signal.aborted || token !== generation || !dialog.open) return;
      if (loaded) dialog.close();
      else status.textContent = "尚未打开设计，可重新选择。";
    });
  }

  function downloadDesign(record) {
    return performAction(record, "下载", async (signal, token) => {
      const design = await readJSON(`/api/v2/workspace/designs/${encodeURIComponent(record.design_id)}/export`, signal);
      if (signal.aborted || token !== generation || !dialog.open) return;
      const url = URL.createObjectURL(new Blob([JSON.stringify(design, null, 2)], { type: "application/json" }));
      const link = element("a");
      link.href = url;
      link.download = `${record.design_id}.json`;
      document.body.append(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      status.textContent = "已下载 JSON 备份；可在工作室通过“导入参数”重新使用。";
    });
  }

  function openBrowser(button) {
    if (!window.CFStudio?.loadDesign || dialog.open) return;
    returnFocus = button || document.querySelector("[data-open-workspace]");
    generation += 1;
    dialog.showModal();
    loadList(0);
  }

  document.querySelectorAll("[data-open-workspace]").forEach(button => {
    button.addEventListener("click", () => openBrowser(button));
  });
  dialog.querySelectorAll("[data-workspace-browser-close]").forEach(button => button.addEventListener("click", () => dialog.close()));
  find("refresh").addEventListener("click", () => loadList(0));
  find("retry").addEventListener("click", () => retryAction?.());
  find("prev").addEventListener("click", () => loadList(Math.max(0, offset - pageSize)));
  find("next").addEventListener("click", () => { if (hasNext) loadList(offset + pageSize); });
  dialog.addEventListener("close", () => {
    generation += 1;
    listRequest?.abort();
    actionRequest?.abort();
    if (returnFocus?.isConnected) returnFocus.focus();
  });
  document.addEventListener("cf:design-saved", () => {
    if (dialog.open && !acting) loadList(0);
  });
  function openFromLocation() {
    if (new URLSearchParams(location.search).get("open") === "workspace") openBrowser();
  }
  if (window.CFStudio?.loadDesign) openFromLocation();
  else document.addEventListener("DOMContentLoaded", openFromLocation, { once: true });
})();
