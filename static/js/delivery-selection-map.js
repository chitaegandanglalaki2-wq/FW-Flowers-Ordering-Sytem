(() => {
  const TOWNS = [
    { code: "0403407000", name: "Cavinti", lat: 14.2464, lng: 121.5074, fee: "0.00", isLocal: true },
    { code: "0403419000", name: "Pagsanjan", lat: 14.2736, lng: 121.4528, fee: "100.00", isLocal: false },
    { code: "0403413000", name: "Lumban", lat: 14.2981, lng: 121.4589, fee: "100.00", isLocal: false },
    { code: "0403409000", name: "Kalayaan", lat: 14.3547, lng: 121.5583, fee: "100.00", isLocal: false },
    { code: "0403426000", name: "Santa Cruz", lat: 14.2789, lng: 121.4156, fee: "100.00", isLocal: false },
    { code: "0403422000", name: "Pila", lat: 14.2344, lng: 121.3642, fee: "100.00", isLocal: false },
  ];

  const initDeliverySelectionMap = () => {
    const card = document.querySelector("[data-delivery-map-selector]");
    if (!card) return;
    const canvas = card.querySelector("#deliverySelectionMap");
    if (!canvas || !window.L) return;

    // Check if already initialized
    if (canvas._leaflet_id) return;

    const townNameLabel = card.querySelector("[data-selected-town-name]");
    const townStatusLabel = card.querySelector("[data-selected-town-status]");
    const townFeeLabel = card.querySelector("[data-selected-town-fee]");
    const pills = card.querySelectorAll(".delivery-town-pill");
    const citySelect = document.querySelector("[data-ph-city]");

    const shopLat = parseFloat(canvas.dataset.shopLat) || 14.2464;
    const shopLng = parseFloat(canvas.dataset.shopLng) || 121.5074;
    const shopName = canvas.dataset.shopName || "FuzzyWire Bouquet Shop";

    // Initialize Leaflet map centered on the Laguna service corridor
    const map = L.map(canvas, {
      center: [14.28, 121.46],
      zoom: 12,
      scrollWheelZoom: false,
    });

    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    }).addTo(map);

    // Custom Icons
    const createPinIcon = (emoji, label, isShop = false) => L.divIcon({
      className: `town-map-pin ${isShop ? "shop-map-pin" : ""}`,
      html: `<div class="town-pin-wrapper"><span class="pin-icon">${emoji}</span><span class="pin-label">${label}</span></div>`,
      iconSize: [60, 36],
      iconAnchor: [30, 34],
      popupAnchor: [0, -32],
    });

    const shopIcon = createPinIcon("🌸", "Shop", true);
    const townIcon = (name) => createPinIcon("📍", name, false);

    // Add Shop Marker
    const shopMarker = L.marker([shopLat, shopLng], { icon: shopIcon, zIndexOffset: 1000 }).addTo(map);
    shopMarker.bindPopup(`<strong>🌸 ${shopName}</strong><br>Cavinti, Laguna<br><span class="text-success small fw-semibold">Shop Headquarters · Free Delivery</span>`);

    // Add Town Markers
    const townMarkers = {};
    TOWNS.forEach((town) => {
      if (town.code === "0403407000") {
        // Cavinti is also the shop location
        townMarkers[town.code] = shopMarker;
        return;
      }
      const marker = L.marker([town.lat, town.lng], { icon: townIcon(town.name) }).addTo(map);
      marker.bindPopup(`<strong>📍 ${town.name}, Laguna</strong><br><span class="text-pink small fw-semibold">₱${town.fee} Delivery Fee</span><br><button type="button" class="btn btn-sm btn-pink mt-1 py-0 px-2 select-this-town-btn" data-code="${town.code}">Select ${town.name}</button>`);
      marker.on("click", () => {
        selectTown(town.code);
      });
      townMarkers[town.code] = marker;
    });

    // Coverage Corridor Line connecting the 6 towns
    const corridorCoords = [
      [14.2344, 121.3642], // Pila
      [14.2789, 121.4156], // Santa Cruz
      [14.2736, 121.4528], // Pagsanjan
      [14.2981, 121.4589], // Lumban
      [14.3547, 121.5583], // Kalayaan
    ];
    L.polyline(corridorCoords, {
      color: "#f1ceda",
      weight: 6,
      opacity: 0.65,
    }).addTo(map);

    // Shop Branch from Pagsanjan to Cavinti
    L.polyline([
      [14.2736, 121.4528], // Pagsanjan
      [14.2464, 121.5074], // Cavinti
    ], {
      color: "#f1ceda",
      weight: 6,
      opacity: 0.65,
    }).addTo(map);

    // Dynamic Route Line
    let activeRouteLine = null;

    const selectTown = (townCode) => {
      const town = TOWNS.find((t) => t.code === townCode) || TOWNS[0];

      // Update pills
      pills.forEach((p) => {
        const isMatch = p.dataset.townCode === town.code;
        p.classList.toggle("active", isMatch);
        p.setAttribute("aria-checked", String(isMatch));
      });

      // Update Info Labels
      if (townNameLabel) townNameLabel.textContent = `${town.name}, Laguna`;
      if (townStatusLabel) {
        townStatusLabel.textContent = town.isLocal ? "(Shop Town · Local Delivery)" : "(Laguna Delivery Zone)";
      }
      if (townFeeLabel) {
        townFeeLabel.textContent = town.isLocal
          ? "₱0.00 (Free in Cavinti)"
          : `₱${town.fee} Delivery Fee`;
      }

      // Update Dropdown if present and different
      if (citySelect && citySelect.value !== town.code) {
        // If options loaded, set it
        const option = citySelect.querySelector(`option[value="${town.code}"]`);
        if (option) {
          citySelect.value = town.code;
          citySelect.dispatchEvent(new Event("change", { bubbles: true }));
        } else {
          // If options still loading, store as data-selected
          citySelect.dataset.selected = town.code;
        }
      }

      // Draw highlighted route line from shop to selected town
      if (activeRouteLine) {
        map.removeLayer(activeRouteLine);
        activeRouteLine = null;
      }

      if (!town.isLocal) {
        // Intermediate waypoint through Pagsanjan junction for natural road look
        const waypoints = [
          [shopLat, shopLng],
          [14.2736, 121.4528], // Pagsanjan
          [town.lat, town.lng],
        ];
        activeRouteLine = L.polyline(waypoints, {
          color: "#e94375",
          weight: 4,
          opacity: 0.9,
          dashArray: "6, 8",
        }).addTo(map);
      }

      // Smooth pan map
      map.panTo([town.lat, town.lng], { animate: true, duration: 0.6 });

      // Open popup for marker
      const marker = townMarkers[town.code];
      if (marker && marker.getPopup()) {
        marker.openPopup();
      }
    };

    // Pill click listeners
    pills.forEach((pill) => {
      pill.addEventListener("click", () => {
        selectTown(pill.dataset.townCode);
      });
    });

    // Listen to popup inner button clicks
    map.on("popupopen", (e) => {
      const container = e.popup.getElement();
      if (!container) return;
      const btn = container.querySelector(".select-this-town-btn");
      if (btn) {
        btn.addEventListener("click", () => {
          selectTown(btn.dataset.code);
          map.closePopup();
        });
      }
    });

    // Synchronize when dropdown changes
    if (citySelect) {
      citySelect.addEventListener("change", () => {
        const matchingTown = TOWNS.find((t) => t.code === citySelect.value);
        if (matchingTown) {
          selectTown(matchingTown.code);
        }
      });
    }

    // Refresh map dimensions when switching delivery/pickup tabs
    const fulfillmentRadios = document.querySelectorAll('input[name="fulfillment_method"]');
    fulfillmentRadios.forEach((radio) => {
      radio.addEventListener("change", () => {
        if (radio.value === "Delivery") {
          setTimeout(() => {
            map.invalidateSize();
          }, 150);
        }
      });
    });

    // Initial default selection: check citySelect value or default to Cavinti
    const initialCode = (citySelect && citySelect.value) || (citySelect && citySelect.dataset.selected) || "0403407000";
    selectTown(initialCode);

    // Initial size adjustment
    setTimeout(() => {
      map.invalidateSize();
    }, 250);
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initDeliverySelectionMap);
  } else {
    initDeliverySelectionMap();
  }
})();

