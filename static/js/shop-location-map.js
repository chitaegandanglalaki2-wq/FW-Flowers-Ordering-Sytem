(() => {
  const mapElement = document.getElementById("publicShopMap");
  if (!mapElement) return;
  const message = document.getElementById("publicShopMapMessage");

  const showMap = () => {
    if (!window.L) {
      message.textContent = "The map could not load. Check your internet connection and refresh.";
      return;
    }
    const map = L.map(mapElement, { scrollWheelZoom: false });
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
    }).addTo(map);

    fetch(mapElement.dataset.locationUrl, {
      headers: { Accept: "application/json" }
    })
      .then(async (response) => {
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.error || `Map request failed: ${response.status}`);
        return payload;
      })
      .then((payload) => {
        const point = [payload.latitude, payload.longitude];
        const popup = document.createElement("div");
        const title = document.createElement("strong");
        title.textContent = "Shop for Pick-up";
        const name = document.createElement("div");
        name.textContent = payload.shop_name;
        const address = document.createElement("div");
        address.textContent = payload.address;
        popup.append(title, name, address);
        L.marker(point)
          .addTo(map)
          .bindTooltip("Shop for Pick-up", { permanent: true, direction: "top" })
          .bindPopup(popup)
          .openPopup();
        map.setView(point, payload.approximate ? 13 : 17);
        message.textContent = payload.approximate
          ? "This is an approximate pin for the municipality. The shop street address could not be pinned exactly."
          : "The map pin shows the shop pickup location.";
        window.setTimeout(() => map.invalidateSize(), 100);
      })
      .catch((error) => {
        message.textContent = error.message || "The shop location is temporarily unavailable.";
        console.error("Could not load the shop pickup map.", error);
      });
  };

  if (window.L) {
    showMap();
    return;
  }
  const fallbackScript = document.createElement("script");
  fallbackScript.src = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js";
  fallbackScript.onload = showMap;
  fallbackScript.onerror = () => {
    message.textContent = "The map could not load. Check your internet connection and refresh.";
  };
  document.head.append(fallbackScript);
})();
