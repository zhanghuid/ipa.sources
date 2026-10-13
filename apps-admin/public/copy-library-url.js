/**
 * Copy buttons for the subscription-source page.
 *
 * The heading button copies the manifest of the category currently selected;
 * the buttons on the aggregate tab copy the two global published feeds. URLs
 * come from the Worker (`/api/library/config`) so they honour PUBLIC_BASE_URL
 * instead of being hardcoded in markup.
 */

function currentCategory() {
  return document.documentElement.dataset.category
    || document.querySelector(".category-tab.active")?.dataset.category
    || "live";
}

async function copyText(text) {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const input = document.createElement("textarea");
  input.value = text;
  input.setAttribute("readonly", "");
  input.style.position = "fixed";
  input.style.opacity = "0";
  document.body.append(input);
  input.select();
  const copied = document.execCommand("copy");
  input.remove();
  if (!copied) throw new Error("浏览器不允许复制，请手动复制：" + text);
}

function announce(button, status, text, error = false) {
  const label = button.querySelector("[data-copy-label]");
  const original = button.dataset.originalLabel || label.textContent;
  button.dataset.originalLabel = original;
  label.textContent = error ? "复制失败" : "已复制";
  status.textContent = text;
  status.classList.toggle("error", error);
  button.disabled = false;
  clearTimeout(button._resetTimer);
  button._resetTimer = setTimeout(() => {
    label.textContent = original;
    status.textContent = "";
  }, 2600);
}

async function shareURLs() {
  const response = await fetch("/api/library/config", { headers: { Accept: "application/json" } });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "获取清单地址失败");
  return result.urls || {};
}

document.querySelectorAll("[data-copy-library-url]").forEach((button) => {
  button.addEventListener("click", async () => {
    const status = button.parentElement.querySelector("[data-copy-status]");
    button.disabled = true;
    status.textContent = "";
    status.classList.remove("error");
    try {
      const urls = await shareURLs();
      const category = currentCategory();
      const url = urls.categories?.[category];
      if (!url) throw new Error("没有该分类的清单地址");
      await copyText(url);
      announce(button, status, `${category}.json 地址已复制到剪贴板`);
    } catch (error) {
      announce(button, status, error.message || "复制失败，请重试", true);
    }
  });
});

document.querySelectorAll("[data-copy-global-url]").forEach((button) => {
  button.addEventListener("click", async () => {
    const status = document.querySelector("#global-entries [data-copy-status]");
    button.disabled = true;
    status.textContent = "";
    status.classList.remove("error");
    try {
      const urls = await shareURLs();
      const key = button.dataset.copyGlobalUrl;
      const url = urls.files?.[key];
      if (!url) throw new Error("没有该聚合入口地址");
      await copyText(url);
      announce(button, status, `${key}.json 地址已复制到剪贴板`);
    } catch (error) {
      announce(button, status, error.message || "复制失败，请重试", true);
    }
  });
});
