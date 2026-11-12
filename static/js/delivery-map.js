(() => {
  const initializeMaps = () => {
    document.querySelectorAll("[data-delivery-map]").forEach((panel) => {
    const canvas = panel.querySelector("[data-map-canvas]");
    const message = panel.querySelector("[data-map-message]");
    const statusLabel = panel.querySelector("[data-map-order-status]");
    const etaLabel = panel.querySelector("[data-map-eta]");
    const distanceLabel = panel.querySelector("[data-map-distance]");
    const eyebrow = panel.querySelector("[data-map-eyebrow]");
    const title = panel.querySelector("[data-map-title]");
    const destinationLabel = panel.querySelector("[data-map-destination-label]");
    const destinationText = panel.querySelector("[data-map-destination]");
    if (!canvas || !message || !window.L) {
      if (message) message.textContent = "The map could not load. Check your internet connection and refresh the page.";
      return;
    }

    const map = L.map(canvas, { scrollWheelZoom: false });
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
    }).addTo(map);

    const riderIcon = L.divIcon({
      className: "delivery-rider-marker",
      html: '<div class="rider-scooter-pin"><span class="rider-emoji">🛵</span><span class="rider-pulse"></span></div>',
      iconSize: [44, 44],
      iconAnchor: [22, 22]
    });
    let routeCoordinates = [];
    let segmentLengths = [];
    let totalLength = 0;
    let riderMarker = null;
    let displayedProgress = null;
    let animationFrame = null;
    let continuousRiderFrame = null;
    let isMovingSimulation = false;

    const segmentLength = (a, b) => {
      const radians = (degrees) => degrees * Math.PI / 180;
      const dLat = radians(b[0] - a[0]);
      const dLon = radians(b[1] - a[1]);
      const lat1 = radians(a[0]);
      const lat2 = radians(b[0]);
      const value = Math.sin(dLat / 2) ** 2
        + Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLon / 2) ** 2;
      return 6371000 * 2 * Math.atan2(Math.sqrt(value), Math.sqrt(1 - value));
    };

    const positionAt = (progress) => {
      if (routeCoordinates.length < 2 || totalLength === 0) return routeCoordinates[0];
      let remaining = totalLength * Math.max(0, Math.min(1, progress));
      for (let index = 0; index < segmentLengths.length; index += 1) {
        const length = segmentLengths[index];
        if (remaining <= length || index === segmentLengths.length - 1) {
          const ratio = length ? Math.min(1, remaining / length) : 0;
          const start = routeCoordinates[index];
          const end = routeCoordinates[index + 1];
          return [
            start[0] + (end[0] - start[0]) * ratio,
            start[1] + (end[1] - start[1]) * ratio
          ];
        }
        remaining -= length;
      }
      return routeCoordinates[routeCoordinates.length - 1];
    };

    const setRiderPosition = (progress) => {
      if (riderMarker) riderMarker.setLatLng(positionAt(progress));
    };

    const stopMovingSimulation = () => {
      isMovingSimulation = false;
      if (continuousRiderFrame) {
        window.cancelAnimationFrame(continuousRiderFrame);
        continuousRiderFrame = null;
      }
    };

    const startMovingSimulation = (initialProgress) => {
      if (continuousRiderFrame) return;
      isMovingSimulation = true;
      let lastTimestamp = performance.now();
      let simProgress = typeof initialProgress === "number" && initialProgress > 0 && initialProgress < 1
        ? initialProgress
        : (displayedProgress !== null ? displayedProgress : 0.05);
      const CYCLE_DURATION_MS = 22000; // ~22 seconds from shop to customer

      const step = (now) => {
        if (!isMovingSimulation) {
          continuousRiderFrame = null;
          return;
        }
        const delta = now - lastTimestamp;
        lastTimestamp = now;
        simProgress += delta / CYCLE_DURATION_MS;
        if (simProgress > 1) {
          simProgress = 0;
        }
        displayedProgress = simProgress;
        setRiderPosition(simProgress);
        continuousRiderFrame = window.requestAnimationFrame(step);
      };
      continuousRiderFrame = window.requestAnimationFrame(step);
    };

    const animateTo = (progress) => {
      stopMovingSimulation();
      const target = Math.max(0, Math.min(1, progress));
      displayedProgress = target;
      setRiderPosition(target);
    };

    const refreshMap = async () => {
      try {
        const response = await fetch(panel.dataset.routeUrl, {
          credentials: "same-origin",
          headers: { Accept: "application/json" }
        });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.error || `Map request failed: ${response.status}`);
        const start = [payload.start.latitude, payload.start.longitude];
        const destination = [payload.destination.latitude, payload.destination.longitude];
        const createShopPopup = (titleText) => {
          const popup = document.createElement("div");
          const titleElement = document.createElement("strong");
          titleElement.textContent = titleText;
          const nameElement = document.createElement("div");
          nameElement.textContent = payload.shop_name || "FuzzyWire Bouquet";
          const addressElement = document.createElement("div");
          addressElement.textContent = payload.shop_address || "";
          popup.append(titleElement, nameElement, addressElement);
          return popup;
        };

        if (payload.fulfillment_method === "Pickup") {
          if (!map._deliveryLayersReady) {
            L.marker(start)
              .addTo(map)
              .bindTooltip("Shop for Pick-up", { permanent: true, direction: "top" })
              .bindPopup(createShopPopup("Shop for Pick-up"))
              .openPopup();
            map.setView(start, 16);
            map._deliveryLayersReady = true;
            window.setTimeout(() => map.invalidateSize(), 100);
          }
          if (statusLabel) statusLabel.textContent = payload.order_status;
          if (eyebrow) eyebrow.textContent = "SHOP PICKUP LOCATION";
          if (title) title.textContent = "Pick up your order here";
          if (destinationLabel) destinationLabel.textContent = "Shop for Pick-up:";
          if (destinationText) {
            destinationText.textContent = `${payload.shop_name || "FuzzyWire Bouquet"} · ${payload.shop_address || ""}`;
          }
          if (etaLabel) etaLabel.textContent = "No delivery route or rider is used for pickup.";
          if (distanceLabel) distanceLabel.textContent = "";
          message.textContent = payload.shop_location_is_approximate
            ? "The street could not be pinned exactly. This is an approximate pin for the municipality."
            : "The pin marks the shop pickup location.";
          return false;
        }

        if (Array.isArray(payload.route) && payload.route.length >= 2) {
          routeCoordinates = payload.route.map(([longitude, latitude]) => [latitude, longitude]);
        } else {
          routeCoordinates = [start, destination];
        }

        segmentLengths = routeCoordinates.slice(1).map((point, index) => (
          segmentLength(routeCoordinates[index], point)
        ));
        totalLength = segmentLengths.reduce((sum, length) => sum + length, 0);

        const createDestinationPopup = () => {
          const popup = document.createElement("div");
          const titleElement = document.createElement("strong");
          titleElement.textContent = "Customer Delivery Address";
          const addrElement = document.createElement("div");
          addrElement.textContent = payload.destination_address || "";
          popup.append(titleElement, addrElement);
          return popup;
        };

        if (!map._deliveryLayersReady) {
          // White casing line for contrast
          L.polyline(routeCoordinates, {
            color: "#ffffff",
            weight: 8,
            opacity: 0.95
          }).addTo(map);

          // Pink route line
          const route = L.polyline(routeCoordinates, {
            color: "#e94375",
            weight: 5,
            opacity: 0.92,
            dashArray: payload.route_is_approximate ? "8 8" : null
          }).addTo(map);

          L.marker(start)
            .addTo(map)
            .bindTooltip("🌸 Flower Shop", { direction: "top" })
            .bindPopup(createShopPopup("Flower shop"));

          L.marker(destination)
            .addTo(map)
            .bindTooltip("📍 Customer Address", { direction: "top" })
            .bindPopup(createDestinationPopup());

          riderMarker = L.marker(start, { icon: riderIcon, zIndexOffset: 1000 })
            .addTo(map)
            .bindPopup("Simulated rider moving en route to customer");

          map.fitBounds(route.getBounds().pad(0.15));
          map._deliveryLayersReady = true;
          window.setTimeout(() => map.invalidateSize(), 150);
        }

        if (statusLabel) statusLabel.textContent = payload.order_status;
        if (eyebrow) {
          eyebrow.textContent = payload.route_is_approximate
            ? "APPROXIMATE ROUTE · LIVE SIMULATED RIDER"
            : "ESTIMATED ROUTE · LIVE SIMULATED RIDER";
        }
        if (distanceLabel) {
          const distanceType = payload.route_is_approximate ? "Approx. route" : "Road route";
          distanceLabel.textContent = `${distanceType}: ${Number(payload.route_distance_km).toFixed(2)} km`;
        }
        if (payload.order_status === "Delivered" || payload.order_status === "Picked Up") {
          etaLabel.textContent = payload.order_status === "Picked Up"
            ? "Pickup completed. No rider movement is shown."
            : "Delivered! Rider has reached the destination.";
          stopMovingSimulation();
          displayedProgress = 1.0;
          setRiderPosition(1.0);
        } else if (payload.order_status === "Out for Delivery") {
          etaLabel.textContent = payload.eta_minutes > 0
            ? `Out for Delivery: Estimated arrival in about ${payload.eta_minutes} min.`
            : "Rider is actively delivering along the route line to your address.";
          startMovingSimulation(payload.progress);
        } else {
          etaLabel.textContent = "The rider will start moving once the order is Out for Delivery.";
          stopMovingSimulation();
          displayedProgress = 0.0;
          setRiderPosition(0.0);
        }

        message.textContent = payload.order_status === "Delivered"
          ? "Delivery completed. The simulated rider is at the destination."
          : payload.order_status === "Picked Up"
            ? "Pickup completed. This map does not show rider movement."
            : payload.order_status === "Out for Delivery"
              ? "Your order is out for delivery! The rider is actively moving along the route from the shop to your address."
              : payload.road_route_unavailable
                ? "Road routing is approximate. The line shows the delivery route from the flower shop to your address."
                : payload.destination_is_approximate
                  ? "The street could not be pinned exactly. The map shows the route to your Laguna municipality."
                  : "Estimated route from the flower shop to your delivery address.";
        return true;
      } catch (error) {
        message.textContent = error.message || "The delivery map is temporarily unavailable.";
        console.error("Could not update the simulated delivery map.", error);
        return false;
      }
    };

    refreshMap().then((isDelivery) => {
      if (isDelivery) window.setInterval(refreshMap, 10000);
    });
    });
  };

  if (window.L) {
    initializeMaps();
    return;
  }

  const fallbackScript = document.createElement("script");
  fallbackScript.src = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js";
  fallbackScript.onload = initializeMaps;
  fallbackScript.onerror = () => {
    document.querySelectorAll("[data-delivery-map] [data-map-message]").forEach((message) => {
      message.textContent = "The map could not load. Check your internet connection and refresh the page.";
    });
  };
  document.head.append(fallbackScript);
})();
