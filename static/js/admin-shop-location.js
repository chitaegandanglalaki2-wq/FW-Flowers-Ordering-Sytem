(() => {
  const mapElement = document.getElementById("shopLocationMap");
  if (!mapElement) return;

  const form = mapElement.closest("form");
  const municipality = document.getElementById("shopMunicipality");
  const barangay = document.getElementById("shopBarangay");
  const barangayMessage = document.getElementById("shopBarangayMessage");
  const address = document.getElementById("shopAddress");
  const mapMessage = document.getElementById("shopLocationMapMessage");
  const saveButton = document.getElementById("saveShopLocation");
  const latitudeField = document.getElementById("shopMapLatitude");
  const longitudeField = document.getElementById("shopMapLongitude");
  const approximateField = document.getElementById("shopMapApproximate");
  let marker = null;
  let map = null;
  let previewTimer = null;
  let previewRequest = 0;

  const selectBarangays = async (keepSelection) => {
    const cityCode = municipality.value;
    if (!cityCode) {
      barangay.replaceChildren(new Option("Choose municipality first", ""));
      barangay.disabled = true;
      barangay.required = true;
      return;
    }

    const selectedName = keepSelection ? barangay.value : "";
    barangay.disabled = true;
    barangay.replaceChildren(new Option("Loading barangays…", ""));
    barangayMessage.textContent = "";
    try {
      const url = barangay.dataset.barangaysUrl.replace(
        "CITY_CODE",
        encodeURIComponent(cityCode)
      );
      const response = await fetch(url, { headers: { Accept: "application/json" } });
      if (!response.ok) throw new Error(`Barangay request failed: ${response.status}`);
      const options = await response.json();
      barangay.replaceChildren(new Option("Choose barangay", ""));
      options.forEach((item) => {
        barangay.add(new Option(item.name.trim(), item.name.trim()));
      });
      if (options.length === 0) {
        barangayMessage.textContent = "No barangay list is available for this municipality.";
      }
      if (options.some((item) => item.name.trim().toLowerCase() === selectedName.toLowerCase())) {
        barangay.value = options.find(
          (item) => item.name.trim().toLowerCase() === selectedName.toLowerCase()
        ).name.trim();
      }
      barangay.disabled = false;
    } catch (error) {
      barangay.replaceChildren(new Option("Could not load barangays", ""));
      barangay.disabled = true;
      barangayMessage.textContent = "Barangays could not be loaded. Refresh and try again.";
      console.error("Could not load shop barangays.", error);
    }
  };

  const setMarker = (latitude, longitude, approximate) => {
    const point = [latitude, longitude];
    if (!marker) {
      marker = L.marker(point, { draggable: true }).addTo(map);
      marker.on("dragend", () => {
        const moved = marker.getLatLng();
        latitudeField.value = moved.lat.toFixed(7);
        longitudeField.value = moved.lng.toFixed(7);
        approximateField.value = "false";
        mapMessage.textContent = "Pin moved. The exact marker will be used for pickup and delivery maps.";
      });
    } else {
      marker.setLatLng(point);
    }
    latitudeField.value = Number(latitude).toFixed(7);
    longitudeField.value = Number(longitude).toFixed(7);
    approximateField.value = approximate ? "true" : "false";
    marker
      .bindTooltip("Shop for Pick-up", { permanent: true, direction: "top" })
      .bindPopup("Shop for Pick-up");
    map.setView(point, approximate ? 14 : 17);
    mapMessage.textContent = approximate
      ? "Approximate municipality pin. Drag it to the exact shop location."
      : "Address found. Drag the marker to the exact shop location if needed.";
    window.setTimeout(() => map.invalidateSize(), 100);
  };

  const previewAddress = async () => {
    const cityCode = municipality.value;
    if (!cityCode) {
      mapMessage.textContent = "Choose the shop municipality to show its map.";
      latitudeField.value = "";
      longitudeField.value = "";
      approximateField.value = "";
      saveButton.disabled = true;
      return;
    }
    const requestId = ++previewRequest;
    saveButton.disabled = true;
    mapMessage.textContent = "Finding the shop address on the map…";
    const params = new URLSearchParams({
      province_code: document.getElementById("shopProvince").value,
      municipality_code: cityCode,
      barangay: barangay.value,
      complete_address: address.value
    });
    try {
      const response = await fetch(`${mapElement.dataset.previewUrl}?${params}`, {
        headers: { Accept: "application/json" }
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "Could not find this address.");
      if (requestId !== previewRequest) return;
      setMarker(result.latitude, result.longitude, result.approximate);
      saveButton.disabled = false;
    } catch (error) {
      if (requestId !== previewRequest) return;
      latitudeField.value = "";
      longitudeField.value = "";
      approximateField.value = "";
      mapMessage.textContent = error.message || "Could not find this address on the map.";
      saveButton.disabled = false;
      console.error("Could not preview shop address.", error);
    }
  };

  const initializeMap = () => {
    if (!window.L) {
      mapMessage.textContent = "The map could not load. Check your internet connection.";
      saveButton.disabled = true;
      return;
    }
    const lagunaBounds = L.latLngBounds([13.85, 120.75], [14.65, 121.85]);
    map = L.map(mapElement, {
      maxBounds: lagunaBounds,
      maxBoundsViscosity: 0.9,
      scrollWheelZoom: false
    });
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
    }).addTo(map);
    map.fitBounds(lagunaBounds);

    municipality.addEventListener("change", async () => {
      previewRequest += 1;
      latitudeField.value = "";
      longitudeField.value = "";
      approximateField.value = "";
      saveButton.disabled = true;
      await selectBarangays(false);
      previewAddress();
    });
    barangay.addEventListener("change", () => {
      latitudeField.value = "";
      longitudeField.value = "";
      approximateField.value = "";
      previewAddress();
    });
    address.addEventListener("input", () => {
      previewRequest += 1;
      latitudeField.value = "";
      longitudeField.value = "";
      approximateField.value = "";
      saveButton.disabled = true;
      window.clearTimeout(previewTimer);
      previewTimer = window.setTimeout(previewAddress, 1200);
    });
    form.addEventListener("submit", (event) => {
      if (!latitudeField.value || !longitudeField.value || saveButton.disabled) {
        event.preventDefault();
        mapMessage.textContent = "Wait for the map pin to load before saving.";
      }
    });

    saveButton.disabled = true;
    selectBarangays(true).then(previewAddress);
    window.setTimeout(() => map.invalidateSize(), 100);
    return map;
  };

  const fallbackScript = document.createElement("script");
  fallbackScript.src = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js";
  fallbackScript.onload = initializeMap;
  fallbackScript.onerror = () => {
    mapMessage.textContent = "The map could not load. Check your internet connection and refresh.";
    saveButton.disabled = true;
  };
  if (window.L) initializeMap();
  else document.head.append(fallbackScript);
})();
