(() => {
  const resetForm = (form) => {
    form.dataset.submitting = "";
    form.removeAttribute("aria-busy");
    form.querySelectorAll("button[type='submit']").forEach((button) => {
      button.disabled = false;
      button.classList.remove("is-pending");
      if (button.dataset.originalLabel) {
        button.textContent = button.dataset.originalLabel;
      }
    });
  };

  document.querySelectorAll("form[data-pending]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (form.dataset.submitting === "true") {
        event.preventDefault();
        return;
      }

      form.dataset.submitting = "true";
      form.setAttribute("aria-busy", "true");
      form.querySelectorAll("button[type='submit']").forEach((button) => {
        button.dataset.originalLabel = button.textContent;
        button.disabled = true;
        button.classList.add("is-pending");
        if (button.dataset.pendingLabel) {
          button.textContent = button.dataset.pendingLabel;
        }
      });
    });
  });

  window.addEventListener("pageshow", () => {
    document.querySelectorAll("form[data-pending]").forEach(resetForm);
  });
})();
