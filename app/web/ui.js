"use strict";
window.WorkflowUI = (() => {
  let pending = false,
    previous = "";
  function resize() {
    if (pending) return;
    pending = true;
    requestAnimationFrame(() => {
      pending = false;
      const root =
        document.getElementById("widget") ||
        document.getElementById("settings-shell");
      if (!root) return;
      // Measure intrinsic content, not body.scrollHeight (which includes the old frame height).
      const settings = document.body.classList.contains("settings-page");
      const height = Math.min(
        settings ? 760 : 600,
        Math.ceil(root.getBoundingClientRect().height) + (settings ? 32 : 12),
      );
      const width = document.documentElement.clientWidth;
      const key = width + ":" + height;
      if (width > 0 && height > 0 && previous !== key) {
        previous = key;
        BX24.resizeWindow(width, height);
      }
    });
  }
  function observe() {
    const root =
      document.getElementById("widget") ||
      document.getElementById("settings-shell");
    if (root && typeof ResizeObserver !== "undefined")
      new ResizeObserver(resize).observe(root);
    window.addEventListener("resize", resize);
    resize();
  }
  function textColor(color) {
    const rgb = color
      .slice(1)
      .match(/../g)
      .map((value) => {
        const channel = parseInt(value, 16) / 255;
        return channel <= 0.04045
          ? channel / 12.92
          : ((channel + 0.055) / 1.055) ** 2.4;
      });
    return rgb[0] * 0.2126 + rgb[1] * 0.7152 + rgb[2] * 0.0722 > 0.179
      ? "#172b3a"
      : "#ffffff";
  }
  function paint(button, settings) {
    button.textContent = settings.label;
    button.title = settings.label;
    button.style.backgroundColor = settings.color;
    button.style.color = textColor(settings.color);
    button.style.width = settings.width + "px";
    button.style.height = settings.height + "px";
  }
  return { resize, observe, paint };
})();
