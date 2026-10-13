function currentPlatform() {
  // <html data-platform> is set before the first paint and kept current on
  // every switch, so it stays correct even before the page scripts assign the
  // .active class (the catalog page only does that after its fetch resolves).
  return document.documentElement.dataset.platform
    || document.querySelector(".platform-tab.active")?.dataset.platform
    || "ios";
}

document.querySelectorAll("[data-copy-catalog-url]").forEach((button) => {
  let resetTimer;

  button.addEventListener("click", async () => {
    const label = button.querySelector("[data-copy-label]");
    const status = button.parentElement.querySelector("[data-copy-status]");
    const platform = currentPlatform();
    const originalLabel = label.textContent;
    button.disabled = true;
    status.textContent = "";
    status.classList.remove("error");

    try {
      const response = await fetch("/api/catalog-url", { headers: { Accept: "application/json" } });
      const result = await response.json();
      const url = result.urls?.[platform] || result.url;
      if (!response.ok || !url) throw new Error(result.error || "获取目录地址失败");

      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(url);
      } else {
        const input = document.createElement("textarea");
        input.value = url;
        input.setAttribute("readonly", "");
        input.style.position = "fixed";
        input.style.opacity = "0";
        document.body.append(input);
        input.select();
        const copied = document.execCommand("copy");
        input.remove();
        if (!copied) throw new Error("浏览器不允许复制，请手动复制：" + url);
      }

      const name = url.split("/").pop();
      label.textContent = "已复制";
      status.textContent = `${name} 地址已复制到剪贴板`;
    } catch (error) {
      label.textContent = "复制失败";
      status.textContent = error.message || "复制失败，请重试";
      status.classList.add("error");
    } finally {
      button.disabled = false;
      clearTimeout(resetTimer);
      resetTimer = setTimeout(() => {
        label.textContent = originalLabel;
        status.textContent = "";
      }, 2600);
    }
  });
});
