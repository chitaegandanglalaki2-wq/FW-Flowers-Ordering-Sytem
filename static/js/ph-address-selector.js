(() => {
  const fetchJson = async (url) => {
    const response = await fetch(url, { headers: { Accept: "application/json" } });
    if (!response.ok) throw new Error(`Address data request failed: ${response.status}`);
    return response.json();
  };

  const setOptions = (select, items, label, selectedValue = "") => {
    select.replaceChildren(new Option(label, ""));
    items.forEach((item) => select.add(new Option(item.name.trim(), item.code)));
    select.value = items.some((item) => item.code === selectedValue) ? selectedValue : "";
  };

  document.querySelectorAll("[data-ph-address-selector]").forEach((selector) => {
    const regionSelect = selector.querySelector("[data-ph-region]");
    const provinceSelect = selector.querySelector("[data-ph-province]");
    const provinceGroup = selector.querySelector("[data-ph-province-group]");
    const citySelect = selector.querySelector("[data-ph-city]");
    const barangaySelect = selector.querySelector("[data-ph-barangay]");
    const barangayManual = selector.querySelector("[data-ph-barangay-manual]");
    const postalSelect = selector.querySelector("[data-ph-postal]");
    const postalOther = selector.querySelector("[data-ph-postal-other]");
    const status = selector.querySelector("[data-ph-address-status]");
    const orderAddressFields = selector.closest("[data-order-address-fields]");
    const addressChoiceToggles = selector.closest("form").querySelectorAll("[data-address-choice-toggle]");
    const addressRequired = selector.dataset.required === "true";
    const allowedProvinceCode = selector.dataset.allowedProvinceCode || "";
    const allowedCityCodes = (selector.dataset.allowedCityCodes || "")
      .split(",")
      .map((code) => code.trim())
      .filter(Boolean);
    const isAddressActive = () => {
      const deliveryFields = selector.closest("[data-delivery-fields]");
      return !(deliveryFields && deliveryFields.hidden)
        && !(orderAddressFields && orderAddressFields.hidden);
    };
    let regions = [];
    let provinces = [];
    let cities = [];

    const synchronizeAddressControls = () => {
      if (!addressRequired) return;
      if (orderAddressFields && addressChoiceToggles.length) {
        const selectedAddressChoice = selector.closest("form").querySelector(
          "[data-address-choice-toggle]:checked"
        );
        orderAddressFields.hidden = !selectedAddressChoice || selectedAddressChoice.value !== "new";
      }
      const active = isAddressActive();
      const hasProvinceOptions = provinces.some(
        (province) => province.regionCode === regionSelect.value
      );
      const selectedCity = cities.find((city) => city.code === citySelect.value);
      const hasRegionCityWithoutProvince = cities.some(
        (city) => city.regionCode === regionSelect.value && !city.provinceCode
      );
      selector.querySelector('[name="delivery_street_address"]').disabled = !active;
      regionSelect.disabled = !active;
      regionSelect.required = active;
      provinceSelect.disabled = !active || provinceGroup.hidden;
      provinceSelect.required = active && !provinceGroup.hidden
        && (!selectedCity || Boolean(selectedCity.provinceCode));
      citySelect.disabled = !active || !regionSelect.value
        || (hasProvinceOptions && !provinceSelect.value && !hasRegionCityWithoutProvince);
      citySelect.required = active;
      barangaySelect.disabled = !active || barangaySelect.hidden || !citySelect.value;
      barangaySelect.required = active && !barangaySelect.hidden;
      barangayManual.disabled = !active || barangayManual.hidden;
      barangayManual.required = active && !barangayManual.hidden;
      postalSelect.disabled = !active || !citySelect.value;
      postalOther.disabled = !active || postalOther.hidden;
      postalOther.required = false;
    };

    const resetSelect = (select, placeholder) => {
      select.replaceChildren(new Option(placeholder, ""));
      select.value = "";
      select.disabled = true;
    };

    const updatePostalField = () => {
      const otherSelected = postalSelect.value === "other";
      postalOther.hidden = !otherSelected;
      postalOther.required = false;
      if (!otherSelected) postalOther.value = "";
    };

    const fillPostalCodes = (city, selectedCode = "") => {
      const knownCode = (city.zipCode || "").trim();
      postalSelect.replaceChildren(new Option("Choose ZIP / postal code", ""));
      if (knownCode) postalSelect.add(new Option(`${knownCode} — ${city.name.trim()}`, knownCode));
      postalSelect.add(new Option("Other / enter manually", "other"));
      postalSelect.value = selectedCode && selectedCode === knownCode
        ? knownCode
        : selectedCode ? "other" : knownCode || "";
      postalOther.value = selectedCode && selectedCode !== knownCode ? selectedCode : "";
      updatePostalField();
      synchronizeAddressControls();
    };

    const fillBarangays = async (cityCode, selectedCode = "", selectedName = "") => {
      barangaySelect.hidden = false;
      barangayManual.hidden = true;
      barangayManual.required = false;
      barangayManual.value = "";
      resetSelect(barangaySelect, "Loading barangays...");
      if (!cityCode) {
        resetSelect(barangaySelect, "Choose barangay");
        return;
      }
      const endpoint = selector.dataset.barangaysUrlPattern.replace(
        "CITY_CODE",
        encodeURIComponent(cityCode)
      );
      try {
        const barangays = await fetchJson(endpoint);
        if (barangays.length === 0) {
          barangaySelect.hidden = true;
          barangayManual.hidden = false;
          barangayManual.disabled = !isAddressActive();
          barangayManual.value = selectedName;
          status.textContent = "Barangay choices are unavailable for this city/municipality. Enter the barangay name.";
          synchronizeAddressControls();
          return;
        }
        setOptions(barangaySelect, barangays, "Choose barangay", selectedCode);
        barangaySelect.disabled = !isAddressActive();
        status.textContent = "";
        synchronizeAddressControls();
      } catch (error) {
        barangaySelect.hidden = false;
        barangayManual.hidden = true;
        resetSelect(barangaySelect, "Could not load barangays");
        status.textContent = "Barangays could not be loaded. Please refresh and try again.";
        console.error("Could not load Philippine barangay data.", error);
        synchronizeAddressControls();
      }
    };

    const fillCities = (regionCode, provinceCode, selectedCode = "") => {
      resetSelect(citySelect, "Choose city / municipality");
      if (!regionCode) return [];
      let matches = cities.filter((city) => (
        provinceCode
          ? city.provinceCode === provinceCode
          : city.regionCode === regionCode && !city.provinceCode
      ));
      if (allowedCityCodes.length > 0) {
        matches = matches.filter((city) => allowedCityCodes.includes(city.code));
      }
      setOptions(citySelect, matches, "Choose city / municipality", selectedCode);
      citySelect.disabled = matches.length === 0 || !isAddressActive();
      return matches;
    };

    const fillProvinces = (regionCode, selectedProvince = "", selectedCity = "", selectedBarangay = "", selectedPostal = "", selectedBarangayName = "") => {
      const matches = provinces.filter((province) => (
        province.regionCode === regionCode
        && (!allowedProvinceCode || province.code === allowedProvinceCode)
      ));
      provinceGroup.hidden = matches.length === 0;
      setOptions(provinceSelect, matches, "Choose province", selectedProvince);
      provinceSelect.disabled = matches.length === 0 || !isAddressActive();
      const activeProvince = matches.length > 0 ? provinceSelect.value : "";
      const matchingCities = fillCities(regionCode, activeProvince, selectedCity);
      const selectedCityRecord = matchingCities.find((city) => city.code === citySelect.value);
      resetSelect(barangaySelect, "Choose barangay");
      if (selectedCityRecord) {
        fillPostalCodes(selectedCityRecord, selectedPostal);
        fillBarangays(selectedCityRecord.code, selectedBarangay, selectedBarangayName);
      } else {
        postalSelect.replaceChildren(new Option("Choose ZIP / postal code", ""));
        postalOther.value = "";
        updatePostalField();
      }
      synchronizeAddressControls();
    };

    regionSelect.addEventListener("change", () => {
      provinceSelect.value = "";
      citySelect.value = "";
      barangaySelect.value = "";
      barangayManual.value = "";
      postalOther.value = "";
      updatePostalField();
      fillProvinces(regionSelect.value);
    });
    provinceSelect.addEventListener("change", () => {
      const matchingCities = fillCities(regionSelect.value, provinceSelect.value);
      resetSelect(barangaySelect, "Choose barangay");
      barangaySelect.hidden = false;
      barangayManual.hidden = true;
      barangayManual.required = false;
      barangayManual.value = "";
      postalSelect.replaceChildren(new Option("Choose ZIP / postal code", ""));
      postalOther.value = "";
      updatePostalField();
      status.textContent = matchingCities.length ? "" : "No city/municipality data is available for this province.";
    });
    citySelect.addEventListener("change", () => {
      const city = cities.find((item) => item.code === citySelect.value);
      resetSelect(barangaySelect, "Choose barangay");
      barangaySelect.hidden = false;
      barangayManual.hidden = true;
      barangayManual.required = false;
      barangayManual.value = "";
      if (!city) {
        postalSelect.replaceChildren(new Option("Choose ZIP / postal code", ""));
        postalOther.value = "";
        updatePostalField();
        return;
      }
      fillPostalCodes(city);
      fillBarangays(city.code);
    });
    postalSelect.addEventListener("change", updatePostalField);

    Promise.all([
      fetchJson(selector.dataset.regionsUrl),
      fetchJson(selector.dataset.provincesUrl),
      fetchJson(selector.dataset.citiesUrl)
    ]).then(([regionData, provinceData, cityData]) => {
      regions = regionData;
      provinces = provinceData;
      cities = cityData;
      const visibleRegions = allowedProvinceCode
        ? regions.filter((region) => provinces.some(
          (province) => province.code === allowedProvinceCode
            && province.regionCode === region.code
        ))
        : regions;
      const selectedRegion = regionSelect.dataset.selected;
      setOptions(regionSelect, visibleRegions, "Choose region", selectedRegion);
      if (regionSelect.value) {
        fillProvinces(
          regionSelect.value,
          provinceSelect.dataset.selected,
          citySelect.dataset.selected,
          barangaySelect.dataset.selected,
          postalSelect.dataset.selected,
          barangayManual.dataset.selected
        );
      }
      synchronizeAddressControls();
    }).catch((error) => {
      status.textContent = "Address options could not be loaded. Please refresh and try again.";
      console.error("Could not load Philippine address data.", error);
    });

    addressChoiceToggles.forEach((option) => option.addEventListener("change", synchronizeAddressControls));
    selector.closest("form").querySelectorAll('input[name="fulfillment_method"]').forEach((option) => {
      option.addEventListener("change", synchronizeAddressControls);
    });
    synchronizeAddressControls();
  });
})();
