(() => {
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  document.querySelectorAll("[data-password-toggle]").forEach((toggle) => {
    const passwordInput = document.getElementById(toggle.dataset.passwordToggle);
    if (!passwordInput) return;
    toggle.addEventListener("click", () => {
      const shouldShow = passwordInput.type === "password";
      passwordInput.type = shouldShow ? "text" : "password";
      toggle.setAttribute("aria-pressed", String(shouldShow));
      toggle.setAttribute(
        "aria-label",
        `${shouldShow ? "Hide" : "Show"} ${passwordInput.id === "registerConfirmPassword" ? "confirm password" : "password"}`
      );
      const openEye = toggle.querySelector("[data-eye-open]");
      const closedEye = toggle.querySelector("[data-eye-closed]");
      if (openEye) openEye.hidden = shouldShow;
      if (closedEye) closedEye.hidden = !shouldShow;
    });
  });

  const activateDialogFocus = (dialog) => {
    const closeLink = dialog.querySelector(".customer-profile-modal-close");
    dialog.focus();
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && closeLink) {
        window.location.assign(closeLink.href);
        return;
      }
      if (event.key !== "Tab") return;
      const focusable = Array.from(dialog.querySelectorAll(
        'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
      )).filter((element) => element.getClientRects().length > 0);
      if (!focusable.length) {
        event.preventDefault();
        dialog.focus();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    });
  };

  const passwordRules = [
    { id: "length", test: (val) => val.length >= 8 },
    { id: "uppercase", test: (val) => /[A-Z]/.test(val) },
    { id: "lowercase", test: (val) => /[a-z]/.test(val) },
    { id: "number", test: (val) => /[0-9]/.test(val) },
    { id: "special", test: (val) => /[^A-Za-z0-9]/.test(val) },
  ];

  const evaluatePassword = (passwordInput, isSubmitting = false) => {
    if (!passwordInput) return true;
    const form = passwordInput.closest("form") || document;
    const card = form.querySelector(".password-requirements-card");
    const badge = card ? card.querySelector("[data-password-badge]") : null;
    const errorMsg = card ? card.querySelector("[data-password-error]") : null;
    const value = passwordInput.value || "";
    const isTouched = passwordInput._hasInteracted || value.length > 0 || isSubmitting;

    const results = passwordRules.map((rule) => ({
      id: rule.id,
      met: rule.test(value),
    }));
    const allMet = results.every((r) => r.met);

    if (card) {
      results.forEach(({ id, met }) => {
        const item = card.querySelector(`[data-rule="${id}"]`);
        if (!item) return;
        const icon = item.querySelector(".requirement-icon");
        if (met) {
          item.classList.add("is-met");
          item.classList.remove("is-unmet");
          if (icon) icon.textContent = "✓";
        } else if (isTouched) {
          item.classList.add("is-unmet");
          item.classList.remove("is-met");
          if (icon) icon.textContent = "✕";
        } else {
          item.classList.remove("is-met", "is-unmet");
          if (icon) icon.textContent = "•";
        }
      });

      card.classList.toggle("all-met", allMet);

      if (badge) {
        if (allMet) {
          badge.textContent = "Requirements met ✓";
          badge.classList.remove("badge-danger");
          badge.classList.add("badge-success");
        } else if (isTouched) {
          badge.textContent = "Requirements not met";
          badge.classList.add("badge-danger");
          badge.classList.remove("badge-success");
        } else {
          badge.textContent = "Incomplete";
          badge.classList.remove("badge-danger", "badge-success");
        }
      }

      if (errorMsg) {
        errorMsg.hidden = allMet || !isSubmitting;
      }
    }

    if (isTouched) {
      if (allMet) {
        passwordInput.classList.remove("is-invalid");
        passwordInput.classList.add("is-valid");
        passwordInput.setCustomValidity("");
      } else {
        passwordInput.classList.add("is-invalid");
        passwordInput.classList.remove("is-valid");
        passwordInput.setCustomValidity("Please meet all password requirements.");
      }
    } else {
      passwordInput.classList.remove("is-invalid", "is-valid");
      passwordInput.setCustomValidity("");
    }

    return allMet;
  };

  const evaluateConfirmPassword = (confirmationInput, isSubmitting = false) => {
    if (!confirmationInput) return true;
    const form = confirmationInput.closest("form") || document;
    const passwordId = confirmationInput.dataset.confirmPassword || "registerPassword";
    const passwordInput = document.getElementById(passwordId);
    const feedback = form.querySelector("[data-confirm-feedback]");
    const confirmVal = confirmationInput.value || "";
    const pwdVal = passwordInput ? passwordInput.value || "" : "";
    const isTouched = isSubmitting || confirmVal.length > 0;

    if (!isTouched) {
      confirmationInput.classList.remove("is-invalid", "is-valid");
      confirmationInput.setCustomValidity("");
      if (feedback) feedback.hidden = true;
      return true;
    }

    const matches = confirmVal.length > 0 && confirmVal === pwdVal;
    if (matches) {
      confirmationInput.classList.remove("is-invalid");
      confirmationInput.classList.add("is-valid");
      confirmationInput.setCustomValidity("");
      if (feedback) feedback.hidden = true;
      return true;
    } else {
      confirmationInput.classList.add("is-invalid");
      confirmationInput.classList.remove("is-valid");
      confirmationInput.setCustomValidity("Passwords do not match.");
      if (feedback) {
        feedback.hidden = false;
        feedback.textContent = "Passwords do not match.";
      }
      return false;
    }
  };

  document.querySelectorAll("[data-password-input], #registerPassword, #newPassword").forEach((passwordInput) => {
    passwordInput._hasInteracted = false;
    const form = passwordInput.closest("form") || document;

    passwordInput.addEventListener("input", () => {
      passwordInput._hasInteracted = true;
      evaluatePassword(passwordInput, false);
      const confirmInput = form.querySelector(`[data-confirm-password="${passwordInput.id}"], [data-confirm-password]`);
      if (confirmInput && confirmInput.value) {
        evaluateConfirmPassword(confirmInput, false);
      }
    });

    passwordInput.addEventListener("blur", () => {
      if (passwordInput.value) {
        passwordInput._hasInteracted = true;
        evaluatePassword(passwordInput, false);
      }
    });
  });

  document.querySelectorAll("[data-confirm-password]").forEach((confirmationInput) => {
    const form = confirmationInput.closest("form") || document;
    const passwordId = confirmationInput.dataset.confirmPassword;
    const passwordInput = passwordId ? document.getElementById(passwordId) : null;

    confirmationInput.addEventListener("input", () => {
      evaluateConfirmPassword(confirmationInput, false);
    });

    confirmationInput.addEventListener("blur", () => {
      if (confirmationInput.value) {
        evaluateConfirmPassword(confirmationInput, false);
      }
    });

    if (passwordInput) {
      passwordInput.addEventListener("input", () => {
        if (confirmationInput.value) {
          evaluateConfirmPassword(confirmationInput, false);
        }
      });
    }
  });

  document.querySelectorAll("[data-registration-form]").forEach((form) => {
    const registrationConfirmation = form.querySelector("#registerConfirmPassword");
    if (registrationConfirmation && !registrationConfirmation.dataset.confirmPassword) {
      registrationConfirmation.dataset.confirmPassword = "registerPassword";
    }

    form.addEventListener("submit", (event) => {
      const passwordInput = form.querySelector("[data-password-input], #registerPassword, #newPassword");
      if (passwordInput) {
        passwordInput._hasInteracted = true;
        const passwordValid = evaluatePassword(passwordInput, true);
        if (!passwordValid) {
          event.preventDefault();
          event.stopPropagation();
          passwordInput.reportValidity();
          passwordInput.focus();
          return;
        }
      }

      const confirmationInput = form.querySelector("[data-confirm-password], #registerConfirmPassword, #confirmPassword");
      if (confirmationInput) {
        const confirmValid = evaluateConfirmPassword(confirmationInput, true);
        if (!confirmValid) {
          event.preventDefault();
          event.stopPropagation();
          confirmationInput.reportValidity();
          confirmationInput.focus();
          return;
        }
      }

      const termsCheckbox = form.querySelector("[data-registration-terms]");
      if (termsCheckbox && !termsCheckbox.checked) {
        event.preventDefault();
        window.alert("Please agree to the terms and conditions before registering.");
        termsCheckbox.focus();
      }
    });
  });
  document.addEventListener("submit", (event) => {
    const form = event.target;
    const cancellationStatus = form.matches("[data-confirm-on-cancel]") &&
      form.querySelector("[data-cancellation-status]")?.value === "Cancelled";
    const message = event.submitter?.dataset.confirm ||
      form.dataset.confirmMessage ||
      (cancellationStatus ? form.dataset.cancelConfirm || "Cancel this order? The customer will be notified." : "");
    if (message && !window.confirm(message)) event.preventDefault();
  });

  document.querySelectorAll("[data-print-receipt]").forEach((button) => {
    button.addEventListener("click", () => {
      document.body.classList.add("printing-receipt");
      window.print();
    });
  });
  window.addEventListener("afterprint", () => {
    document.body.classList.remove("printing-receipt");
    document.body.classList.remove("printing-waybill");
  });
  document.querySelectorAll("[data-print-waybill]").forEach((button) => {
    button.addEventListener("click", () => {
      document.body.classList.add("printing-waybill");
      window.print();
    });
  });

  const profileDialog = document.querySelector("[data-profile-dialog] .customer-profile-modal");
  if (profileDialog) activateDialogFocus(profileDialog);

  const floatingDialog = document.querySelector("[data-floating-dialog]");
  if (floatingDialog) activateDialogFocus(floatingDialog);

  document.querySelectorAll("[data-back-home]").forEach((button) => {
    button.addEventListener("click", () => {
      if (window.history.length > 1) window.history.back();
      else window.location.assign(button.dataset.backHome);
    });
  });

  const heroCarousel = document.querySelector("[data-hero-carousel]");
  if (heroCarousel) {
    const slides = Array.from(heroCarousel.querySelectorAll("[data-hero-slide]"));
    const dots = Array.from(heroCarousel.querySelectorAll("[data-hero-dot]"));
    let activeSlide = 0;
    let touchStartX = null;

    const showSlide = (nextIndex) => {
      activeSlide = (nextIndex + slides.length) % slides.length;
      slides.forEach((slide, index) => {
        slide.hidden = index !== activeSlide;
      });
      dots.forEach((dot, index) => {
        dot.setAttribute("aria-current", String(index === activeSlide));
      });
    };

    const previousButton = heroCarousel.querySelector("[data-hero-previous]");
    const nextButton = heroCarousel.querySelector("[data-hero-next]");
    if (previousButton && nextButton && slides.length > 1) {
      previousButton.addEventListener("click", () => showSlide(activeSlide - 1));
      nextButton.addEventListener("click", () => showSlide(activeSlide + 1));
      dots.forEach((dot) => {
        dot.addEventListener("click", () => showSlide(Number(dot.dataset.heroDot)));
      });
      heroCarousel.addEventListener("keydown", (event) => {
        if (event.key === "ArrowLeft") {
          event.preventDefault();
          showSlide(activeSlide - 1);
        } else if (event.key === "ArrowRight") {
          event.preventDefault();
          showSlide(activeSlide + 1);
        }
      });
      heroCarousel.addEventListener("touchstart", (event) => {
        touchStartX = event.changedTouches[0].clientX;
      }, { passive: true });
      heroCarousel.addEventListener("touchend", (event) => {
        if (touchStartX === null) return;
        const swipeDistance = event.changedTouches[0].clientX - touchStartX;
        touchStartX = null;
        if (Math.abs(swipeDistance) >= 50) {
          showSlide(activeSlide + (swipeDistance < 0 ? 1 : -1));
        }
      }, { passive: true });
    }
  }

  const menuToggle = document.querySelector(".admin-menu-toggle");
  const adminMenu = document.getElementById("adminMenu");

  document.querySelectorAll("[data-cancellation-status]").forEach((statusSelect) => {
    const form = statusSelect.closest("form");
    const reasonField = form && form.querySelector("[data-cancellation-reason-field]");
    const reasonInput = reasonField && reasonField.querySelector("textarea");
    if (!reasonField || !reasonInput) return;

    const updateCancellationReasonField = () => {
      const isCancelled = statusSelect.value === "Cancelled";
      reasonField.hidden = !isCancelled;
      reasonInput.required = isCancelled;
    };

    statusSelect.addEventListener("change", updateCancellationReasonField);
    updateCancellationReasonField();
  });

  if (menuToggle && adminMenu) {
    const isNarrowAdminLayout = () => window.matchMedia("(max-width: 991.98px)").matches;
    const closeAdminMenu = (restoreFocus = false) => {
      adminMenu.classList.remove("is-open");
      menuToggle.setAttribute("aria-expanded", "false");
      if (restoreFocus) menuToggle.focus();
    };

    menuToggle.addEventListener("click", () => {
      if (!isNarrowAdminLayout()) return;
      const isOpen = menuToggle.getAttribute("aria-expanded") === "true";
      adminMenu.classList.toggle("is-open", !isOpen);
      menuToggle.setAttribute("aria-expanded", String(!isOpen));
    });

    document.addEventListener("click", (event) => {
      if (
        isNarrowAdminLayout() &&
        menuToggle.getAttribute("aria-expanded") === "true" &&
        !adminMenu.contains(event.target) &&
        !menuToggle.contains(event.target)
      ) {
        closeAdminMenu();
      }
    });

    document.addEventListener("keydown", (event) => {
      if (isNarrowAdminLayout() && event.key === "Escape" && menuToggle.getAttribute("aria-expanded") === "true") {
        closeAdminMenu(true);
      }
    });

    window.addEventListener("resize", () => {
      if (!isNarrowAdminLayout()) closeAdminMenu();
    });
  }

  document.querySelectorAll("[data-admin-search]").forEach((input) => {
    const table = document.querySelector(input.dataset.adminSearch);
    if (!table) return;
    const rows = Array.from(table.querySelectorAll("tbody tr")).filter((row) => !row.hasAttribute("data-empty-row"));
    const emptyMessage = document.querySelector(input.dataset.adminSearchEmpty);
    input.addEventListener("input", () => {
      const query = input.value.trim().toLocaleLowerCase();
      let visibleRows = 0;
      rows.forEach((row) => {
        const matches = row.textContent.toLocaleLowerCase().includes(query);
        row.hidden = !matches;
        if (matches) visibleRows += 1;
      });
      if (emptyMessage) emptyMessage.hidden = visibleRows > 0 || rows.length === 0;
    });
  });

  const customerMenuToggle = document.querySelector(".customer-menu-toggle");
  const customerMenu = document.getElementById("customerMenu");
  const copyTextToClipboard = async (text) => {
    if (window.isSecureContext && navigator.clipboard?.writeText) {
      try {
        await navigator.clipboard.writeText(text);
        return;
      } catch (error) {
        console.warn("Clipboard API failed; trying the compatibility fallback.", error);
      }
    }

    const temporaryInput = document.createElement("textarea");
    temporaryInput.value = text;
    temporaryInput.setAttribute("readonly", "");
    temporaryInput.style.position = "fixed";
    temporaryInput.style.opacity = "0";
    temporaryInput.style.pointerEvents = "none";
    document.body.append(temporaryInput);
    try {
      temporaryInput.focus();
      temporaryInput.select();
      temporaryInput.setSelectionRange(0, temporaryInput.value.length);
      if (!document.execCommand("copy")) {
        throw new Error("The browser did not copy the selected text.");
      }
    } finally {
      temporaryInput.remove();
    }
  };

  document.querySelectorAll("[data-copy-to]").forEach((button) => {
    button.addEventListener("click", async () => {
      const source = document.getElementById(button.dataset.copyTo);
      const status = button.parentElement.querySelector("[data-copy-status]");
      if (!source) {
        throw new Error(`Clipboard source not found: ${button.dataset.copyTo}`);
      }
      try {
        await copyTextToClipboard(source.textContent.trim());
        button.textContent = "Copied!";
        if (status) status.textContent = "GCash number copied!";
      } catch (error) {
        console.error("Could not copy the GCash number.", error);
        if (status) status.textContent = "Copy failed. Please select the number and copy it manually.";
      }
    });
  });

  if (customerMenuToggle && customerMenu) {
    const closeCustomerMenu = () => {
      customerMenu.classList.add("d-none");
      customerMenuToggle.setAttribute("aria-expanded", "false");
    };

    customerMenuToggle.addEventListener("click", () => {
      const isOpen = customerMenuToggle.getAttribute("aria-expanded") === "true";
      customerMenu.classList.toggle("d-none", isOpen);
      customerMenuToggle.setAttribute("aria-expanded", String(!isOpen));
    });
    customerMenu.addEventListener("click", (event) => {
      if (event.target.closest("a")) closeCustomerMenu();
    });
    document.addEventListener("click", (event) => {
      if (!customerMenu.contains(event.target) && !customerMenuToggle.contains(event.target)) {
        closeCustomerMenu();
      }
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") closeCustomerMenu();
    });
  }

  const notificationLink = document.querySelector(".notification-nav-link");
  if (notificationLink) {
    const updateUnreadBadge = async () => {
      const response = await fetch(notificationLink.dataset.unreadUrl, {
        credentials: "same-origin",
        headers: { Accept: "application/json" }
      });
      if (!response.ok) throw new Error(`Notification count request failed: ${response.status}`);
      const data = await response.json();
      const count = Number(data.count);
      if (!Number.isInteger(count) || count < 0) {
        throw new Error("Notification count response was invalid.");
      }

      document.querySelectorAll("[data-notification-count]").forEach((menuBadge) => {
        menuBadge.textContent = String(count);
        menuBadge.hidden = count === 0;
      });

      let badge = notificationLink.querySelector(".notification-count");
      if (count > 0) {
        if (!badge) {
          badge = document.createElement("span");
          badge.className = "notification-count";
          notificationLink.append(badge);
        }
        badge.textContent = String(count);
        notificationLink.setAttribute("aria-label", `Notifications, ${count} unread`);
      } else {
        if (badge) badge.remove();
        notificationLink.setAttribute("aria-label", "Notifications");
      }
    };

    updateUnreadBadge().catch((error) => console.error("Could not refresh notifications.", error));
    window.setInterval(() => {
      updateUnreadBadge().catch((error) => console.error("Could not refresh notifications.", error));
    }, 20000);
  }

  const adminNotificationMenu = document.querySelector("[data-admin-unread-url]");
  if (adminNotificationMenu) {
    const updateAdminBadges = async () => {
      const response = await fetch(adminNotificationMenu.dataset.adminUnreadUrl, {
        credentials: "same-origin",
        headers: { Accept: "application/json" }
      });
      if (!response.ok) throw new Error(`Admin notification count request failed: ${response.status}`);
      const counts = await response.json();
      const badges = [
        ["[data-admin-notification-count]", counts.notifications],
        ["[data-admin-order-count]", counts.orders],
        ["[data-admin-feedback-count]", counts.feedback],
        ["[data-admin-chat-count]", counts.chats],
        ["[data-admin-payment-count]", counts.payments],
        ["[data-admin-verification-count]", counts.verifications],
        ["[data-admin-product-count]", counts.low_stock_products],
        ["[data-admin-material-count]", counts.low_stock_materials],
        ["[data-admin-total-count]", counts.total]
      ];
      badges.forEach(([selector, count]) => {
        if (!Number.isInteger(count) || count < 0) {
          return;
        }
        document.querySelectorAll(selector).forEach((badge) => {
          badge.textContent = String(count);
          badge.hidden = count === 0;
        });
      });
    };

    updateAdminBadges().catch((error) => console.error("Could not refresh admin notifications.", error));
    window.setInterval(() => {
      updateAdminBadges().catch((error) => console.error("Could not refresh admin notifications.", error));
    }, 20000);
  }

  const initializeChatPanel = (chatPanel) => {
    if (!chatPanel || chatPanel.dataset.chatInitialized === "true") return;
    chatPanel.dataset.chatInitialized = "true";
    const messageList = document.getElementById("chatMessages");
    const status = document.getElementById("chatStatus");
    const form = document.getElementById("chatForm");
    const input = document.getElementById("chatMessageInput");
    const sendButton = document.getElementById("chatSendButton");
    const currentRole = chatPanel.dataset.currentRole;
    const conversationId = chatPanel.dataset.conversationId;
    const messagesUrl = new URL(chatPanel.dataset.messagesUrl, window.location.origin);
    if (conversationId && conversationId !== "undefined") {
      messagesUrl.searchParams.set("conversation_id", conversationId);
    }
    let lastMessageId = 0;
    let isLoading = false;

    const setChatStatus = (message, isError = false) => {
      status.textContent = message;
      status.classList.toggle("chat-status-error", isError);
      status.hidden = !message;
    };

    const liveClock = chatPanel.querySelector("#chatLiveClock");
    if (liveClock) {
      const updateLiveClock = () => {
        const now = new Date();
        const dateStr = now.toLocaleDateString("en-US", {
          month: "short",
          day: "numeric",
          year: "numeric"
        });
        const timeStr = now.toLocaleTimeString("en-US", {
          hour: "numeric",
          minute: "2-digit",
          second: "2-digit",
          hour12: true
        });
        liveClock.textContent = `${dateStr} · ${timeStr}`;
      };
      updateLiveClock();
      window.setInterval(updateLiveClock, 1000);
    }

    const renderMessage = (message) => {
      const row = document.createElement("article");
      row.className = `chat-message-row ${message.sender_role === currentRole ? "chat-message-own" : "chat-message-other"}`;

      const bubble = document.createElement("div");
      bubble.className = "chat-message-bubble";

      const sender = document.createElement("strong");
      sender.className = "chat-message-sender";
      sender.textContent = message.sender_role === currentRole
        ? "You"
        : (message.sender_role === "admin" ? "FuzzyWire Support" : "Customer");

      const body = document.createElement("p");
      body.className = "chat-message-body";
      body.textContent = message.body;

      const time = document.createElement("time");
      time.className = "chat-message-time";
      time.dateTime = message.created_at;
      const parsedDate = new Date(message.created_at);
      if (Number.isNaN(parsedDate.getTime())) {
        time.textContent = message.created_at;
      } else {
        const dateStr = parsedDate.toLocaleDateString("en-US", {
          month: "short",
          day: "numeric",
          year: "numeric"
        });
        const timeStr = parsedDate.toLocaleTimeString("en-US", {
          hour: "numeric",
          minute: "2-digit",
          hour12: true
        });
        time.textContent = `${dateStr} · ${timeStr}`;
      }

      bubble.append(sender, body, time);
      row.append(bubble);
      messageList.append(row);
    };

    const loadMessages = async () => {
      if (isLoading) return;
      isLoading = true;
      try {
        const response = await fetch(messagesUrl, {
          credentials: "same-origin",
          headers: { Accept: "application/json" }
        });
        if (!response.ok) throw new Error(`Chat refresh failed: ${response.status}`);
        let data;
        try {
          data = await response.json();
        } catch (_) {
          throw new Error("Chat response was not valid JSON.");
        }
        if (!Array.isArray(data.messages)) throw new Error("Chat response did not contain a message list.");

        const shouldScroll = lastMessageId === 0 ||
          messageList.scrollHeight - messageList.scrollTop - messageList.clientHeight < 100;
        if (lastMessageId === 0) messageList.replaceChildren();
        data.messages.forEach((message) => {
          if (Number(message.id) > lastMessageId) {
            renderMessage(message);
            lastMessageId = Number(message.id);
          }
        });
        if (data.messages.length === 0 && lastMessageId === 0) {
          setChatStatus("Send a message to start chatting with the shop.");
        } else {
          setChatStatus("");
        }
        if (shouldScroll) messageList.scrollTop = messageList.scrollHeight;
      } catch (error) {
        console.error("Could not load chat messages.", error);
        setChatStatus("Could not connect to chat. We’ll try again shortly.", true);
      } finally {
        isLoading = false;
      }
    };

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const body = input.value.trim();
      if (!body) {
        setChatStatus("Write a message before sending.", true);
        input.focus();
        return;
      }

      sendButton.disabled = true;
      setChatStatus("");
      try {
        const response = await fetch(messagesUrl, {
          method: "POST",
          credentials: "same-origin",
          headers: {
            Accept: "application/json",
            "Content-Type": "application/json",
            "X-CSRF-Token": chatPanel.dataset.csrfToken
          },
          body: JSON.stringify({ conversation_id: conversationId, body })
        });
        let data = {};
        try {
          data = await response.json();
        } catch (_) {}
        if (!response.ok) throw new Error(data.error || `Message send failed: ${response.status}`);
        input.value = "";
        await loadMessages();
        input.focus();
      } catch (error) {
        console.error("Could not send chat message.", error);
        setChatStatus(error.message || "Could not send your message. Please try again.", true);
      } finally {
        sendButton.disabled = false;
      }
    });

    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        form.requestSubmit();
      }
    });

    loadMessages();
    const widgetWindow = chatPanel.closest("#customerChatWindow");
    window.setInterval(() => {
      if (!widgetWindow || !widgetWindow.hidden) loadMessages();
    }, 4000);
  };

  const chatPanel = document.getElementById("chatPanel");
  if (chatPanel && chatPanel.dataset.conversationId) {
    initializeChatPanel(chatPanel);
  }

  const chatWidget = document.getElementById("customerChatWidget");
  if (chatWidget) {
    const toggle = document.getElementById("chatWidgetToggle");
    const windowPanel = document.getElementById("customerChatWindow");
    const closeButton = document.getElementById("chatWidgetClose");
    const widgetPanel = chatWidget.querySelector("#chatPanel");
    let conversationRequest;

    const openChatWidget = async () => {
      windowPanel.hidden = false;
      toggle.setAttribute("aria-expanded", "true");
      toggle.setAttribute("aria-label", "Close chat with FuzzyWire");

      if (!widgetPanel.dataset.conversationId) {
        if (!conversationRequest) {
          conversationRequest = fetch(widgetPanel.dataset.conversationUrl, {
            credentials: "same-origin",
            headers: { Accept: "application/json" }
          }).then(async (response) => {
            if (!response.ok) throw new Error(`Chat setup failed: ${response.status}`);
            const data = await response.json();
            if (!Number.isInteger(Number(data.conversation_id))) {
              throw new Error("Chat setup response was invalid.");
            }
            return String(data.conversation_id);
          }).finally(() => {
            conversationRequest = undefined;
          });
        }

        try {
          widgetPanel.dataset.conversationId = await conversationRequest;
        } catch (error) {
          console.error("Could not start the chat widget.", error);
          const status = widgetPanel.querySelector("#chatStatus");
          status.textContent = "Could not connect to chat. Please try again.";
          status.classList.add("chat-status-error");
          return;
        }
      }

      initializeChatPanel(widgetPanel);
      widgetPanel.querySelector("#chatMessageInput").focus();
    };

    const closeChatWidget = () => {
      windowPanel.hidden = true;
      toggle.setAttribute("aria-expanded", "false");
      toggle.setAttribute("aria-label", "Open chat with FuzzyWire");
      toggle.focus();
    };

    toggle.addEventListener("click", () => {
      if (windowPanel.hidden) openChatWidget();
      else closeChatWidget();
    });
    closeButton.addEventListener("click", closeChatWidget);
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !windowPanel.hidden) closeChatWidget();
    });
  }

  const productGrid = document.getElementById("productGrid");
  if (productGrid && productGrid.querySelector(".product-grid-item")) {
    const productItems = Array.from(productGrid.querySelectorAll(".product-grid-item"));
    const originalOrder = new Map(productItems.map((item, index) => [item, index]));
    const searchInput = document.getElementById("productSearch");
    const flowerFilter = document.getElementById("flowerFilter");
    const colorFilter = document.getElementById("colorFilter");
    const occasionFilter = document.getElementById("occasionFilter");
    const availabilityFilter = document.getElementById("availabilityFilter");
    const minPriceInput = document.getElementById("minPrice");
    const maxPriceInput = document.getElementById("maxPrice");
    const sortSelect = document.getElementById("productSort");
    const resultCount = document.getElementById("shopResultCount");
    const emptyState = document.getElementById("noProductsFound");

    const flowerTypes = [...new Set(
      productItems.map((item) => item.dataset.flower.trim()).filter(Boolean)
    )].sort((first, second) => first.localeCompare(second));

    flowerTypes.forEach((flower) => {
      const option = document.createElement("option");
      option.value = flower.toLocaleLowerCase();
      option.textContent = flower;
      flowerFilter.append(option);
    });

    [
      [colorFilter, "color"],
      [occasionFilter, "occasion"]
    ].forEach(([select, attribute]) => {
      const values = [...new Set(
        productItems.map((item) => item.dataset[attribute].trim()).filter(Boolean)
      )].sort((first, second) => first.localeCompare(second));
      values.forEach((value) => {
        const option = document.createElement("option");
        option.value = value.toLocaleLowerCase();
        option.textContent = value;
        select.append(option);
      });
    });

    const updateProducts = () => {
      const query = searchInput.value.trim().toLocaleLowerCase();
      const selectedFlower = flowerFilter.value;
      const selectedColor = colorFilter.value;
      const selectedOccasion = occasionFilter.value;
      const availability = availabilityFilter.value;
      const minimumPrice = minPriceInput.value === "" ? null : Number(minPriceInput.value);
      const maximumPrice = maxPriceInput.value === "" ? null : Number(maxPriceInput.value);
      let visibleCount = 0;

      productItems.forEach((item) => {
        const searchableText = [
          item.dataset.name,
          item.dataset.description,
          item.dataset.flower,
          item.dataset.color,
          item.dataset.occasion
        ].join(" ").toLocaleLowerCase();
        const matchesSearch = searchableText.includes(query);
        const matchesFlower = !selectedFlower ||
          item.dataset.flower.trim().toLocaleLowerCase() === selectedFlower;
        const matchesColor = !selectedColor ||
          item.dataset.color.trim().toLocaleLowerCase() === selectedColor;
        const matchesOccasion = !selectedOccasion ||
          item.dataset.occasion.trim().toLocaleLowerCase() === selectedOccasion;
        const stock = Number(item.dataset.stock);
        const matchesAvailability = !availability ||
          (availability === "available" ? stock > 0 : stock === 0);
        const price = Number(item.dataset.price);
        const matchesPrice = (minimumPrice === null || price >= minimumPrice)
          && (maximumPrice === null || price <= maximumPrice);
        item.hidden = !(matchesSearch && matchesFlower && matchesColor
          && matchesOccasion && matchesAvailability && matchesPrice);
        if (!item.hidden) visibleCount += 1;
      });

      const sortBy = sortSelect.value;
      const sorters = {
        "price-low": (first, second) => Number(first.dataset.price) - Number(second.dataset.price),
        "price-high": (first, second) => Number(second.dataset.price) - Number(first.dataset.price),
        rating: (first, second) => Number(second.dataset.rating) - Number(first.dataset.rating),
        sold: (first, second) => Number(second.dataset.sold) - Number(first.dataset.sold)
      };
      const orderedItems = [...productItems];
      if (sorters[sortBy]) orderedItems.sort(sorters[sortBy]);
      else orderedItems.sort((first, second) => originalOrder.get(first) - originalOrder.get(second));
      orderedItems.forEach((item) => productGrid.append(item));

      resultCount.textContent = `${visibleCount} ${visibleCount === 1 ? "bouquet" : "bouquets"}`;
      emptyState.classList.toggle("d-none", visibleCount !== 0);
      productGrid.classList.toggle("d-none", visibleCount === 0);
    };

    searchInput.addEventListener("input", updateProducts);
    [flowerFilter, colorFilter, occasionFilter, availabilityFilter, sortSelect]
      .forEach((control) => control.addEventListener("change", updateProducts));
    [minPriceInput, maxPriceInput].forEach((control) => control.addEventListener("input", updateProducts));
  }

  if (!reducedMotion && "IntersectionObserver" in window) {
    const revealTargets = document.querySelectorAll(
      ".hero, .service-strip, .product-card, .panel, .stat-card, .admin-sidebar"
    );
    const revealObserver = new IntersectionObserver((entries, observer) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          entry.target.classList.add("is-visible");
          observer.unobserve(entry.target);
        }
      });
    }, { threshold: 0.12 });

    revealTargets.forEach((element) => {
      element.classList.add("js-reveal");
      revealObserver.observe(element);
    });
  }

  const backToTop = document.createElement("button");
  backToTop.type = "button";
  backToTop.className = "back-to-top";
  backToTop.setAttribute("aria-label", "Back to top");
  backToTop.innerHTML = '<span aria-hidden="true">↑</span>';
  document.body.append(backToTop);

  const updateBackToTop = () => {
    backToTop.classList.toggle("is-visible", window.scrollY > 420);
  };
  window.addEventListener("scroll", updateBackToTop, { passive: true });
  backToTop.addEventListener("click", () => {
    window.scrollTo({ top: 0, behavior: reducedMotion ? "auto" : "smooth" });
  });
  updateBackToTop();

  // Cookie Privacy Notice Popup
  (() => {
    const banner = document.getElementById("cookiePrivacyBanner");
    const dismissBtn = document.getElementById("cookiePrivacyDismiss");
    if (!banner || !dismissBtn) return;

    const STORAGE_KEY = "fw_cookie_notice_dismissed";
    try {
      if (localStorage.getItem(STORAGE_KEY) === "true") {
        banner.hidden = true;
        return;
      }
    } catch {
      // In case localStorage is disabled or restricted
    }

    // Reveal banner gently after brief delay for smooth appearance
    setTimeout(() => {
      banner.hidden = false;
      requestAnimationFrame(() => {
        banner.classList.add("is-visible");
      });
    }, 450);

    dismissBtn.addEventListener("click", () => {
      banner.classList.remove("is-visible");
      banner.classList.add("is-dismissing");
      try {
        localStorage.setItem(STORAGE_KEY, "true");
      } catch {}
      setTimeout(() => {
        banner.hidden = true;
      }, 300);
    });
  })();

  // Universal integer stepper (+1 / -1) button handling
  document.addEventListener("click", (event) => {
    const stepperBtn = event.target.closest("[data-stepper-btn]");
    if (!stepperBtn) return;
    const direction = stepperBtn.dataset.stepperBtn;
    const targetId = stepperBtn.dataset.target;
    let input = targetId ? document.getElementById(targetId) : null;
    if (!input) {
      const group = stepperBtn.closest(".input-group");
      if (group) input = group.querySelector('input[type="number"]');
    }
    if (!input || input.disabled || input.readOnly) return;

    event.preventDefault();
    const rawVal = parseInt(input.value, 10);
    const num = isNaN(rawVal) ? 0 : rawVal;
    const minAttr = input.getAttribute("min");
    const maxAttr = input.getAttribute("max");
    const min = (minAttr !== null && minAttr !== "") ? parseInt(minAttr, 10) : -Infinity;
    const max = (maxAttr !== null && maxAttr !== "") ? parseInt(maxAttr, 10) : Infinity;

    let nextVal = num;
    if (direction === "up" || direction === "+1" || direction === "1") {
      nextVal = Math.min(max, num + 1);
    } else {
      nextVal = Math.max(min, num - 1);
    }

    input.value = nextVal;
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
  });

  // Source protection & DevTools deterrence
  (() => {
    // 1. Disable right-click context menu (blocks "Inspect", "View Page Source", save image)
    document.addEventListener("contextmenu", (event) => {
      const tag = event.target && event.target.tagName ? event.target.tagName.toLowerCase() : "";
      if (tag !== "input" && tag !== "textarea") {
        event.preventDefault();
      }
    });

    // 2. Block DevTools & source inspection shortcuts
    document.addEventListener("keydown", (event) => {
      if (event.key === "F12" || event.keyCode === 123) {
        event.preventDefault();
        event.stopPropagation();
        return false;
      }
      if (event.ctrlKey && event.shiftKey && (
        event.key === "I" || event.key === "i" ||
        event.key === "J" || event.key === "j" ||
        event.key === "C" || event.key === "c"
      )) {
        event.preventDefault();
        event.stopPropagation();
        return false;
      }
      if (event.ctrlKey && (event.key === "U" || event.key === "u")) {
        event.preventDefault();
        event.stopPropagation();
        return false;
      }
      if (event.ctrlKey && (event.key === "S" || event.key === "s")) {
        event.preventDefault();
        event.stopPropagation();
        return false;
      }
    });

    // 3. Prevent dragging images
    document.addEventListener("dragstart", (event) => {
      if (event.target && event.target.tagName === "IMG") {
        event.preventDefault();
      }
    });

    // 4. Debugger deterrent when DevTools is opened
    try {
      setInterval(() => {
        const start = performance.now();
        (function antiInspect() {
          debugger;
        })();
        const duration = performance.now() - start;
        if (duration > 100) {
          // DevTools inspector active
        }
      }, 1500);
    } catch {}
  })();
})();
