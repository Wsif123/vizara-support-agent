(() => {
  "use strict";

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

  let toastTimer;
  function toast(message) {
    const box = $("#toast");
    const text = $("#toastText");
    if (!box || !text) return;
    text.textContent = message;
    box.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => box.classList.remove("show"), 2800);
  }
  window.vizaraToast = toast;

  // Modals ---------------------------------------------------------------
  function openModal(id) {
    const modal = document.getElementById(id);
    if (!modal) return false;
    modal.classList.add("open");
    const first = modal.querySelector("input:not([type=hidden]),select,textarea");
    if (first) setTimeout(() => first.focus(), 30);
    return true;
  }
  function closeModal(target) {
    const modal = target && target.closest(".modal-backdrop");
    if (modal) modal.classList.remove("open");
  }
  $$("[data-modal-open]").forEach((button) => {
    button.addEventListener("click", () => openModal(button.dataset.modalOpen));
  });
  $$("[data-modal-close]").forEach((button) => {
    button.addEventListener("click", () => closeModal(button));
  });
  $$(".modal-backdrop").forEach((backdrop) => {
    backdrop.addEventListener("click", (event) => {
      if (event.target === backdrop) closeModal(backdrop);
    });
  });

  // Payments: populate the shared modal from a table row.
  $$("[data-pay-invoice]").forEach((button) => {
    button.addEventListener("click", () => {
      const id = button.dataset.payInvoice;
      const form = $("#payForm");
      const number = $("#payInvoiceNumber");
      const amount = $("#payInvoiceAmount");
      const hidden = $("#payInvoiceId");
      if (!form) return;
      form.action = `/billing/invoices/${encodeURIComponent(id)}/paid`;
      if (hidden) hidden.value = id;
      if (number) number.textContent = button.dataset.number || "";
      if (amount) amount.textContent = button.dataset.amount || "";
      openModal("payModal");
    });
  });

  $$("[data-link-invoice]").forEach((button) => {
    button.addEventListener("click", () => {
      const select = $("#linkInvoiceId");
      const label = $("#linkLabel");
      if (select) select.value = button.dataset.linkInvoice || "";
      if (label && button.dataset.number) label.value = `Checkout for ${button.dataset.number}`;
      openModal("linkModal");
    });
  });

  // Confirmation prompts --------------------------------------------------
  $$("form[data-confirm]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (!window.confirm(form.dataset.confirm)) event.preventDefault();
    });
  });

  // Copy buttons ----------------------------------------------------------
  $$("[data-copy]").forEach((button) => {
    button.addEventListener("click", async () => {
      const value = button.dataset.copy;
      if (!value) return;
      try {
        await navigator.clipboard.writeText(value);
        toast("Copied to clipboard");
      } catch (error) {
        toast("Could not copy — select the text manually");
      }
    });
  });

  // Table filters ---------------------------------------------------------
  $$("[data-filter-table]").forEach((input) => {
    input.addEventListener("input", () => {
      const table = document.getElementById(input.dataset.filterTable);
      if (!table) return;
      const needle = input.value.trim().toLowerCase();
      $$("tbody tr", table).forEach((row) => {
        row.style.display = !needle || row.textContent.toLowerCase().includes(needle) ? "" : "none";
      });
    });
  });

  // Auto-submit filter selects -------------------------------------------
  $$("[data-autosubmit]").forEach((select) => {
    select.addEventListener("change", () => {
      const form = select.closest("form");
      if (form) form.submit();
    });
  });

  // Open a <details> section when reached through a hash link.
  if (location.hash) {
    const target = document.querySelector(location.hash);
    if (target && target.tagName === "DETAILS") {
      target.open = true;
      setTimeout(() => target.scrollIntoView({ block: "start", behavior: "smooth" }), 50);
    }
  }

  // Command palette -------------------------------------------------------
  const palette = $("#palette");
  const paletteInput = $("#paletteInput");
  const paletteItems = $$(".palette-item");
  let selected = 0;

  function paletteVisible() {
    return paletteItems.filter((item) => item.style.display !== "none");
  }
  function filterPalette(query) {
    const needle = query.trim().toLowerCase();
    paletteItems.forEach((item) => {
      item.style.display = !needle || item.textContent.toLowerCase().includes(needle) ? "flex" : "none";
    });
    const visible = paletteVisible();
    selected = Math.max(0, Math.min(selected, Math.max(0, visible.length - 1)));
    visible.forEach((item, index) => item.classList.toggle("sel", index === selected));
    const empty = $("#paletteEmpty");
    if (empty) empty.remove();
    if (!visible.length) {
      const div = document.createElement("div");
      div.id = "paletteEmpty";
      div.className = "palette-empty";
      div.textContent = "No matches";
      $("#paletteList").appendChild(div);
    }
  }
  function openPalette() {
    if (!palette) return;
    palette.classList.add("open");
    paletteInput.value = "";
    selected = 0;
    filterPalette("");
    setTimeout(() => paletteInput.focus(), 20);
  }
  function closePalette() {
    if (palette) palette.classList.remove("open");
  }
  function activatePalette(item) {
    if (!item) return;
    if (item.dataset.href) {
      location.href = item.dataset.href;
      return;
    }
    if (item.dataset.action === "new-key") {
      closePalette();
      if (!openModal("keyModal")) location.href = "/keys";
    }
  }
  if (paletteInput) {
    paletteInput.addEventListener("input", () => {
      selected = 0;
      filterPalette(paletteInput.value);
    });
    paletteInput.addEventListener("keydown", (event) => {
      const visible = paletteVisible();
      if (event.key === "ArrowDown") {
        event.preventDefault();
        selected = Math.min(selected + 1, visible.length - 1);
        filterPalette(paletteInput.value);
      } else if (event.key === "ArrowUp") {
        event.preventDefault();
        selected = Math.max(selected - 1, 0);
        filterPalette(paletteInput.value);
      } else if (event.key === "Enter") {
        event.preventDefault();
        activatePalette(visible[selected]);
      } else if (event.key === "Escape") {
        closePalette();
      }
    });
    paletteItems.forEach((item) => item.addEventListener("click", () => activatePalette(item)));
    palette.addEventListener("click", (event) => {
      if (event.target === palette) closePalette();
    });
    const opener = $("#openPalette");
    if (opener) opener.addEventListener("click", openPalette);
  }

  // Keyboard shortcuts ----------------------------------------------------
  let gPressed = false;
  let gTimer;
  document.addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      openPalette();
      return;
    }
    if (event.key === "Escape") {
      closePalette();
      $$(".modal-backdrop.open").forEach((modal) => modal.classList.remove("open"));
      return;
    }
    if (event.target.matches("input,textarea,select")) return;
    if (event.key.toLowerCase() === "g") {
      gPressed = true;
      clearTimeout(gTimer);
      gTimer = setTimeout(() => { gPressed = false; }, 900);
      return;
    }
    if (gPressed) {
      const map = { o: "/", c: "/clients", v: "/conversations", b: "/billing", k: "/keys", s: "/settings" };
      if (map[event.key.toLowerCase()]) {
        location.href = map[event.key.toLowerCase()];
        gPressed = false;
      }
    }
  });
})();
