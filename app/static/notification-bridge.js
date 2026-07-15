(function () {
  if (window.__nutricityNotificationBridge) return;
  window.__nutricityNotificationBridge = true;

  const token = () => localStorage.getItem("admin_access_token") || "";
  const api = (path, options) => fetch(path, {
    cache: "no-store",
    headers: { "Content-Type": "application/json", ...(token() ? { "X-Admin-Token": token() } : {}) },
    ...options,
  }).then((response) => response.ok ? response.json() : Promise.reject(new Error("Notification request failed")));

  function mount() {
    if (document.querySelector(".notification-menu")) return true;
    const button = [...document.querySelectorAll("button[title='Notifications']")].find((item) => item.offsetParent !== null);
    if (!button || button.closest(".notification-bridge-menu")) return false;
    const menu = document.createElement("div");
    menu.className = "notification-bridge-menu";
    menu.style.cssText = "position:relative";
    button.replaceWith(menu);
    menu.appendChild(button);
    const dropdown = document.createElement("div");
    dropdown.style.cssText = "display:none;position:absolute;z-index:2100;top:calc(100% + 10px);right:0;width:min(430px,calc(100vw - 2rem));max-height:520px;overflow:auto;border:1px solid #dbe2ea;border-radius:8px;background:#fff;box-shadow:0 12px 32px rgba(24,36,51,.18);font:14px/1.35 system-ui,sans-serif;color:#182433";
    menu.appendChild(dropdown);
    button.addEventListener("click", () => {
      dropdown.style.display = dropdown.style.display === "none" ? "block" : "none";
      if (dropdown.style.display === "block") refresh(dropdown);
    });
    return true;
  }

  async function refresh(dropdown) {
    try {
      const result = await api("/api/notifications?limit=40");
      dropdown.innerHTML = "";
      const header = document.createElement("div");
      header.textContent = "Notifications - click an item to clear it";
      header.style.cssText = "position:sticky;top:0;padding:12px;border-bottom:1px solid #dbe2ea;background:#fff;font-weight:650";
      dropdown.appendChild(header);
      const rows = result.notifications || [];
      if (!rows.length) {
        const empty = document.createElement("div");
        empty.textContent = "No active notifications.";
        empty.style.cssText = "padding:20px;text-align:center;color:#667382";
        dropdown.appendChild(empty);
        return;
      }
      rows.forEach((row) => {
        const item = document.createElement("div");
        item.style.cssText = `padding:12px;border-bottom:1px solid #e6eaf0;cursor:pointer;${row.pinned ? "background:#eaf3ff" : ""}`;
        const top = document.createElement("div");
        top.style.cssText = "display:flex;align-items:center;justify-content:space-between;gap:8px";
        const title = document.createElement("strong");
        title.textContent = row.title || "Notification";
        const pin = document.createElement("button");
        pin.type = "button";
        pin.textContent = row.pinned ? "Unpin" : "Pin";
        pin.title = row.pinned ? "Unpin notification" : "Pin notification";
        pin.style.cssText = "border:0;background:transparent;color:#206bc4;cursor:pointer";
        pin.addEventListener("click", async (event) => {
          event.stopPropagation();
          await api(`/api/notifications/${row.id}/pin`, { method: "POST", body: JSON.stringify({ pinned: !row.pinned }) });
          refresh(dropdown);
        });
        top.append(title, pin);
        const message = document.createElement("div");
        message.textContent = row.message || "";
        message.style.marginTop = "6px";
        item.append(top, message);
        if (row.odoo_order_name) {
          const meta = document.createElement("small");
          meta.textContent = `Order: ${row.odoo_order_name}`;
          meta.style.cssText = "display:block;margin-top:5px;color:#667382";
          item.appendChild(meta);
        }
        item.addEventListener("click", async () => {
          await api(`/api/notifications/${row.id}/dismiss`, { method: "POST" });
          refresh(dropdown);
        });
        dropdown.appendChild(item);
      });
    } catch (_) {
      // The bridge is only a compatibility layer for an older served bundle.
    }
  }

  const timer = window.setInterval(() => {
    if (mount()) window.clearInterval(timer);
  }, 500);
  mount();
}());
