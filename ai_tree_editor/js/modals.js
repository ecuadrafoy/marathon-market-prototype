/* modals.js — vanilla helpers for the New Tree / New Leaf dialogs.
 *
 * Modals use the native <dialog> element (good keyboard handling, ESC-to-close
 * out of the box). This module just adds:
 *   - openModal / closeModal that flip the open state
 *   - renderFieldErrors that paints validation errors next to inputs
 *   - escapeHtml for safe error text injection
 *
 * No dependencies. Loaded as a global object BTModals (mirrors BTApi).
 */

const BTModals = (() => {
  function openModal(id) {
    const dlg = document.getElementById(id);
    if (!dlg) throw new Error(`No <dialog id="${id}">`);
    clearFieldErrors(dlg);
    if (typeof dlg.showModal === "function") {
      dlg.showModal();
    } else {
      // Fallback for ancient browsers — dialog polyfill not bundled.
      dlg.setAttribute("open", "");
    }
    return dlg;
  }

  function closeModal(id) {
    const dlg = document.getElementById(id);
    if (!dlg) return;
    if (typeof dlg.close === "function") {
      dlg.close();
    } else {
      dlg.removeAttribute("open");
    }
  }

  function clearFieldErrors(root) {
    for (const el of root.querySelectorAll(".field-error")) {
      el.textContent = "";
      el.hidden = true;
    }
  }

  /**
   * Render field-tagged errors. `errors` is an array of {field, message}
   * matching the server's payload. Errors whose `field` doesn't match any
   * `[data-field]` element get attached to a generic catch-all element with
   * `[data-field-fallback]` if present, else swallowed silently.
   */
  function renderFieldErrors(root, errors) {
    clearFieldErrors(root);
    const fallback = root.querySelector("[data-field-fallback]");
    const fallbackMessages = [];
    for (const err of errors || []) {
      const target = root.querySelector(
        `[data-field="${cssEscape(err.field)}"] .field-error`
      );
      if (target) {
        target.textContent = err.message;
        target.hidden = false;
      } else {
        fallbackMessages.push(`${err.field || "(form)"}: ${err.message}`);
      }
    }
    if (fallback) {
      fallback.textContent = fallbackMessages.join(" · ");
      fallback.hidden = fallbackMessages.length === 0;
    }
  }

  function cssEscape(s) {
    if (window.CSS && CSS.escape) return CSS.escape(s);
    return String(s).replace(/[^a-zA-Z0-9_-]/g, (c) =>
      "\\" + c.charCodeAt(0).toString(16) + " "
    );
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    })[c]);
  }

  return { openModal, closeModal, clearFieldErrors, renderFieldErrors, escapeHtml };
})();
