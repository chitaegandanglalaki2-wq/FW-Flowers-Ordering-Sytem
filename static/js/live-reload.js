(() => {
  const script = document.currentScript;
  if (!script) return;

  const url = script.dataset.reloadUrl;
  let version = script.dataset.version;
  if (!url || !version) return;

  const checkForChanges = async () => {
    try {
      const response = await fetch(url, {
        cache: "no-store",
        credentials: "same-origin",
        headers: { Accept: "application/json" },
      });
      if (!response.ok) throw new Error("Live reload is unavailable.");

      const data = await response.json();
      if (data.version !== version) {
        window.location.reload();
        return;
      }
    } catch {
      // The development server briefly disconnects while Python changes reload.
    }

    window.setTimeout(checkForChanges, 1000);
  };

  window.setTimeout(checkForChanges, 1000);
})();
