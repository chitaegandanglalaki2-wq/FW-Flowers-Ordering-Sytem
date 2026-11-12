(() => {
  document.querySelectorAll("[data-analytics-range]").forEach((button) => {
    button.addEventListener("click", () => {
      const form = button.closest("form");
      const startInput = form && form.querySelector('[name="start_date"]');
      const endInput = form && form.querySelector('[name="end_date"]');
      if (!form || !startInput || !endInput) return;

      const end = new Date();
      const start = new Date(end);
      const range = button.dataset.analyticsRange;
      if (range === "month") {
        start.setDate(1);
      } else {
        const days = Number(range);
        if (!Number.isInteger(days) || days < 1) return;
        start.setDate(start.getDate() - days + 1);
      }
      const localDate = (date) => {
        const year = date.getFullYear();
        const month = String(date.getMonth() + 1).padStart(2, "0");
        const day = String(date.getDate()).padStart(2, "0");
        return `${year}-${month}-${day}`;
      };
      startInput.value = localDate(start);
      endInput.value = localDate(end);
      form.requestSubmit();
    });
  });

  document.querySelectorAll("[data-chart-fallback]").forEach((fallback) => {
    if (typeof Chart === "undefined") fallback.hidden = false;
  });

  if (typeof Chart !== "undefined") {
    Chart.defaults.font.family = "Poppins, Arial, sans-serif";
    Chart.defaults.color = "#797483";
    Chart.defaults.plugins.tooltip.backgroundColor = "#282334";
    Chart.defaults.plugins.tooltip.padding = 12;
    Chart.defaults.plugins.tooltip.cornerRadius = 10;
  }
})();
