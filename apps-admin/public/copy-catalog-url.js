document.querySelectorAll("[data-copy-catalog-url]").forEach((button) => {
  let resetTimer;

  button.addEventListener("click", async () => {
    const label = button.querySelector("[data-copy-label]");
    const status = button.parentElement.querySelector("[data-copy-status]");
    const originalLabel = "复制 apps.json";
    button.disabled = true;
    status.textContent = "";
    status.classList.remove("error");

    try {
      const response = await fetch("/api/catalog-url", { headers: { Accept: "application/json" } });
      const result = await response.json();
      if (!response.ok || !result.url) throw new Error(result.error || "获取目录地址失败");

      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(result.url);
      } else {
        const input = document.createElement("textarea");
        input.value = result.url;
        input.setAttribute("readonly", "");
        input.style.position = "fixed";
        input.style.opacity = "0";
        document.body.append(input);
        input.select();
        const copied = document.execCommand("copy");
        input.remove();
        if (!copied) throw new Error("浏览器不允许复制，请手动复制：" + result.url);
      }

      label.textContent = "已复制";
      status.textContent = "apps.json 地址已复制到剪贴板";
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
