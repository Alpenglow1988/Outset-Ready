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

  const calculator = document.querySelector("[data-alcohol-calculator]");
  if (calculator) {
    const form = calculator.closest("form");
    const kind = form.elements.namedItem("kind");
    const value = form.elements.namedItem("value");
    const ml = form.elements.namedItem("drink_ml");
    const abv = form.elements.namedItem("drink_abv");
    const count = form.elements.namedItem("drink_count");
    const result = calculator.querySelector("[data-alcohol-result]");
    let lastCalculated = null;

    const updateVisibility = () => {
      const visible = kind.value === "alcohol_units";
      calculator.hidden = !visible;
      calculator.querySelectorAll("input").forEach((input) => {
        input.disabled = !visible;
      });
    };

    const calculate = () => {
      const size = Number(ml.value);
      const strength = Number(abv.value);
      const drinks = Number(count.value);
      if (!ml.value || !abv.value || !count.value ||
          !Number.isFinite(size) || size <= 0 ||
          !Number.isFinite(strength) || strength < 0 || strength > 100 ||
          !Number.isInteger(drinks) || drinks < 1) {
        if (value.value === lastCalculated) value.value = "";
        lastCalculated = null;
        result.textContent = "Enter a valid size, strength and number of drinks.";
        return;
      }
      const units = (size * strength * drinks / 1000).toFixed(2);
      value.value = units;
      lastCalculated = units;
      result.textContent = units + " UK unit" + (units === "1.00" ? "" : "s") +
        " for " + drinks + " drink" + (drinks === 1 ? "" : "s") + ".";
    };

    kind.addEventListener("change", updateVisibility);
    [ml, abv, count].forEach((input) => input.addEventListener("input", calculate));
    updateVisibility();
  }

  const todayLink = document.querySelector("[data-calendar-today]");
  if (todayLink) {
    const setLocalToday = () => {
      const now = new Date();
      const day = [
        now.getFullYear(),
        String(now.getMonth() + 1).padStart(2, "0"),
        String(now.getDate()).padStart(2, "0"),
      ].join("-");
      const destination = new URL(todayLink.href);
      destination.searchParams.set("on", day);
      todayLink.href = destination.toString();
    };
    setLocalToday();
    todayLink.addEventListener("click", setLocalToday);
  }
})();
