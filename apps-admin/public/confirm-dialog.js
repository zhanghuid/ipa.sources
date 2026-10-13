/**
 * Shared confirmation dialog.
 *
 * Replaces window.confirm so destructive actions can explain what is about to
 * happen, match the page's visual language, and work with touch and keyboard.
 * Exposes window.appConfirm(options) -> Promise<boolean>.
 */
(function () {
  let ui = null;
  let pending = null;

  function build() {
    const dialog = document.createElement("dialog");
    dialog.className = "confirm-dialog";
    dialog.setAttribute("role", "alertdialog");
    dialog.setAttribute("aria-labelledby", "confirm-dialog-title");
    dialog.setAttribute("aria-describedby", "confirm-dialog-message");

    const card = document.createElement("div");
    card.className = "confirm-card";

    const mark = document.createElement("span");
    mark.className = "confirm-mark";
    mark.setAttribute("aria-hidden", "true");
    mark.innerHTML = '<svg viewBox="0 0 20 20"><path d="M3.8 5.4h12.4M8 5.4V3.9a1 1 0 0 1 1-1h2a1 1 0 0 1 1 1v1.5M5.5 5.4l.7 10a1.3 1.3 0 0 0 1.3 1.2h5a1.3 1.3 0 0 0 1.3-1.2l.7-10M8.3 8.6v5M11.7 8.6v5" /></svg>';

    const copy = document.createElement("div");
    copy.className = "confirm-copy";
    const title = document.createElement("h2");
    title.id = "confirm-dialog-title";
    title.className = "confirm-title";
    const message = document.createElement("p");
    message.id = "confirm-dialog-message";
    message.className = "confirm-message";
    const detail = document.createElement("p");
    detail.className = "confirm-detail";
    detail.hidden = true;
    copy.append(title, message, detail);

    const actions = document.createElement("div");
    actions.className = "confirm-actions";
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.className = "button button-ghost";
    const accept = document.createElement("button");
    accept.type = "button";
    accept.className = "button button-danger";
    actions.append(cancel, accept);

    card.append(mark, copy, actions);
    dialog.append(card);
    document.body.append(dialog);

    const settle = (result) => {
      const resolve = pending;
      pending = null;
      if (dialog.open) dialog.close();
      if (resolve) resolve(result);
    };

    cancel.addEventListener("click", () => settle(false));
    accept.addEventListener("click", () => settle(true));
    // Clicking the backdrop (outside the card) or pressing Escape cancels.
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) settle(false);
    });
    dialog.addEventListener("cancel", (event) => {
      event.preventDefault();
      settle(false);
    });

    return { dialog, card, title, message, detail, cancel, accept };
  }

  function render(options) {
    const settings = options || {};
    const dialog = ui.dialog;
    ui.card.classList.toggle("danger", settings.tone !== "default");
    ui.title.textContent = settings.title || "确认操作";
    ui.message.textContent = settings.message || "";
    ui.detail.textContent = settings.detail || "";
    ui.detail.hidden = !settings.detail;
    ui.cancel.textContent = settings.cancelLabel || "取消";
    ui.accept.textContent = settings.confirmLabel || "删除";
    ui.accept.setAttribute("aria-label", settings.confirmLabel || "删除");
    return dialog;
  }

  window.appConfirm = function appConfirm(options) {
    if (!ui) ui = build();
    const settings = options || {};
    const dialog = render(settings);
    // Focus returns to the control that opened the dialog, which keeps keyboard
    // users oriented after cancelling a destructive action.
    const restoreFocus = settings.trigger || document.activeElement;
    // Opening a second prompt must not leave the first promise unresolved.
    if (pending) {
      const previous = pending;
      pending = null;
      previous(false);
    }
    if (dialog.open) dialog.close();
    dialog.showModal();
    // The non-destructive choice gets focus so Enter never deletes by accident.
    ui.cancel.focus();
    return new Promise((resolve) => {
      pending = (result) => {
        if (restoreFocus && typeof restoreFocus.focus === "function" && restoreFocus.isConnected !== false) {
          restoreFocus.focus();
        }
        resolve(result);
      };
    });
  };
})();
