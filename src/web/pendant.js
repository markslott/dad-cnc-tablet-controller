(() => {
  const PIN_KEY = "mach3ShopPin";
  const STEP_SIZES = [0.001, 0.01, 0.1, 1];

  const els = {
    overlay: document.getElementById("pin-overlay"),
    pinForm: document.getElementById("pin-form"),
    pinInput: document.getElementById("pin-input"),
    pinError: document.getElementById("pin-error"),
    conn: document.getElementById("conn-pill"),
    ready: document.getElementById("ready-pill"),
    machMsg: document.getElementById("mach-msg"),
    droX: document.getElementById("dro-x"),
    droY: document.getElementById("dro-y"),
    droZ: document.getElementById("dro-z"),
    stop: document.getElementById("btn-stop"),
    reset: document.getElementById("btn-reset"),
    modeCont: document.getElementById("mode-cont"),
    modeStep: document.getElementById("mode-step"),
    jogRate: document.getElementById("jog-rate"),
    jogRateVal: document.getElementById("jog-rate-val"),
    fro: document.getElementById("feed-override"),
    froVal: document.getElementById("fro-val"),
  };

  let pin = sessionStorage.getItem(PIN_KEY) || "";
  let pinRequired = false;
  let ws = null;
  let reconnectTimer = null;
  let heartbeatTimer = null;
  let connected = false;
  let canJog = false;
  let jogMode = "cont";
  let stepSize = 0.01;
  let activeJogs = new Map(); // key -> {axis, direction}
  let lastStatus = null;

  function fmt(n) {
    const v = Number(n);
    if (!Number.isFinite(v)) return "—.----";
    const sign = v < 0 ? "-" : "";
    return sign + Math.abs(v).toFixed(4);
  }

  function headers() {
    const h = { "Content-Type": "application/json" };
    if (pin) h["X-Shop-Pin"] = pin;
    return h;
  }

  async function api(path, body, method = "POST") {
    const opts = { method, headers: headers() };
    if (body !== undefined) opts.body = JSON.stringify(body);
    const res = await fetch(path, opts);
    if (res.status === 401) {
      pin = "";
      sessionStorage.removeItem(PIN_KEY);
      showPin(true);
      throw new Error("PIN required");
    }
    if (!res.ok) {
      let detail = res.statusText;
      try {
        const data = await res.json();
        detail = data.detail || detail;
      } catch {
        /* ignore */
      }
      throw new Error(detail);
    }
    if (res.headers.get("content-type")?.includes("application/json")) {
      return res.json();
    }
    return null;
  }

  function showPin(on) {
    els.overlay.classList.toggle("hidden", !on);
    els.overlay.setAttribute("aria-hidden", on ? "false" : "true");
    if (on) els.pinInput.focus();
  }

  function setConn(state, label) {
    els.conn.textContent = label;
    els.conn.classList.remove("pill-ok", "pill-down", "pill-warn");
    els.conn.classList.add(state);
  }

  function applyStatus(s) {
    lastStatus = s;
    els.droX.textContent = fmt(s.dro?.x);
    els.droY.textContent = fmt(s.dro?.y);
    els.droZ.textContent = fmt(s.dro?.z);
    canJog = Boolean(s.can_jog);
    document.body.classList.toggle("not-ready", !canJog);

    if (!s.connected) {
      const blocked = (s.error || "").toLowerCase().includes("listen failed")
        || (s.error || "").toLowerCase().includes("self-test could not connect");
      els.ready.textContent = blocked ? "Modbus :502 blocked" : "Waiting for Mach3";
      els.ready.className = "pill pill-down";
      els.machMsg.hidden = false;
      els.machMsg.textContent = s.error
        || "Mach3 is not polling this PC on port 502. Start the pendant, Master address 127.0.0.1, Test, then TCP Modbus Run.";
    } else if (s.estop) {
      els.ready.textContent = "E-STOP";
      els.ready.className = "pill pill-down";
      els.ready.title = "";
    } else if (s.stopped || !s.reset_ok) {
      els.ready.textContent = "Reset needed";
      els.ready.className = "pill pill-warn";
    } else if (s.in_cycle) {
      els.ready.textContent = "In cycle";
      els.ready.className = "pill pill-warn";
    } else {
      els.ready.textContent = (s.backend || "ok").toUpperCase() + " ready";
      els.ready.className = "pill pill-ok";
    }
    if (s.connected) {
      els.machMsg.hidden = true;
      els.machMsg.textContent = "";
    }

    if (s.jog_mode && s.jog_mode !== jogMode && activeJogs.size === 0) {
      setModeUi(s.jog_mode);
    }
    if (typeof s.step_size === "number") {
      const nearest = STEP_SIZES.reduce((a, b) =>
        Math.abs(b - s.step_size) < Math.abs(a - s.step_size) ? b : a
      );
      if (nearest !== stepSize) setStepUi(nearest);
    }
    if (typeof s.jog_rate === "number" && document.activeElement !== els.jogRate) {
      els.jogRate.value = String(Math.round(s.jog_rate));
      els.jogRateVal.textContent = `${Math.round(s.jog_rate)}%`;
    }
    if (typeof s.feed_override === "number" && document.activeElement !== els.fro) {
      els.fro.value = String(Math.round(s.feed_override));
      els.froVal.textContent = `${Math.round(s.feed_override)}%`;
    }
  }

  function setModeUi(mode) {
    jogMode = mode;
    els.modeCont.classList.toggle("active", mode === "cont");
    els.modeStep.classList.toggle("active", mode === "step");
  }

  function setStepUi(size) {
    stepSize = size;
    document.querySelectorAll(".step-size").forEach((btn) => {
      btn.classList.toggle("active", Number(btn.dataset.size) === size);
    });
  }

  function wsUrl() {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const qs = pin ? `?pin=${encodeURIComponent(pin)}` : "";
    return `${proto}//${location.host}/ws/state${qs}`;
  }

  function stopHeartbeat() {
    if (heartbeatTimer) {
      clearInterval(heartbeatTimer);
      heartbeatTimer = null;
    }
  }

  function startHeartbeat() {
    if (heartbeatTimer) return;
    heartbeatTimer = setInterval(() => {
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "heartbeat" }));
      }
    }, 50);
  }

  function connectWs() {
    if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
      return;
    }
    setConn("pill-warn", "Connecting");
    ws = new WebSocket(wsUrl());
    ws.onopen = () => {
      connected = true;
      setConn("pill-ok", location.host);
      startHeartbeat();
    };
    ws.onmessage = (ev) => {
      try {
        applyStatus(JSON.parse(ev.data));
      } catch {
        /* ignore */
      }
    };
    ws.onclose = () => {
      connected = false;
      stopHeartbeat();
      killLocalJogs();
      setConn("pill-down", "Reconnecting");
      scheduleReconnect();
    };
    ws.onerror = () => {
      try {
        ws.close();
      } catch {
        /* ignore */
      }
    };
  }

  function scheduleReconnect() {
    if (reconnectTimer) return;
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null;
      connectWs();
    }, 600);
  }

  function jogKey(axis, dir) {
    return `${axis}:${dir}`;
  }

  async function startJog(axis, dir, button) {
    if (!canJog) return;
    const key = jogKey(axis, dir);
    if (activeJogs.has(key)) return;
    button.classList.add("held");
    if (jogMode === "step") {
      try {
        await api("/api/jog/step", { axis, direction: dir, step_size: stepSize });
      } catch (err) {
        console.warn(err);
      } finally {
        button.classList.remove("held");
      }
      return;
    }
    activeJogs.set(key, { axis, direction: dir, button });
    startHeartbeat();
    try {
      await api("/api/jog/on", { axis, direction: dir });
    } catch (err) {
      activeJogs.delete(key);
      button.classList.remove("held");
      console.warn(err);
    }
  }

  async function endJog(axis, dir, button) {
    const key = jogKey(axis, dir);
    const tracked = activeJogs.get(key);
    activeJogs.delete(key);
    if (button) button.classList.remove("held");
    if (tracked) {
      try {
        await api("/api/jog/off", { axis, direction: dir });
      } catch (err) {
        console.warn(err);
      }
    }
    if (activeJogs.size === 0) {
      /* keep ws heartbeat; server only trips while jogging */
    }
  }

  async function killLocalJogs() {
    const pending = [...activeJogs.values()];
    activeJogs.clear();
    document.querySelectorAll(".jog.held").forEach((b) => b.classList.remove("held"));
    if (pending.length) {
      try {
        await api("/api/jog/off-all", {});
      } catch {
        /* ignore while disconnected */
      }
    }
  }

  function bindJogButtons() {
    document.querySelectorAll(".jog").forEach((btn) => {
      const axis = Number(btn.dataset.axis);
      const dir = Number(btn.dataset.dir);

      btn.addEventListener("pointerdown", (ev) => {
        ev.preventDefault();
        btn.setPointerCapture(ev.pointerId);
        startJog(axis, dir, btn);
      });
      const stop = (ev) => {
        ev.preventDefault();
        endJog(axis, dir, btn);
      };
      btn.addEventListener("pointerup", stop);
      btn.addEventListener("pointercancel", stop);
      btn.addEventListener("lostpointercapture", () => endJog(axis, dir, btn));
    });
  }

  function bindUi() {
    els.stop.addEventListener("click", async () => {
      await killLocalJogs();
      try {
        await api("/api/stop", {});
      } catch (err) {
        console.warn(err);
      }
    });
    els.reset.addEventListener("click", async () => {
      await killLocalJogs();
      try {
        await api("/api/reset", {});
      } catch (err) {
        console.warn(err);
      }
    });
    els.modeCont.addEventListener("click", async () => {
      await killLocalJogs();
      setModeUi("cont");
      try {
        await api("/api/jog/mode", { mode: "cont" });
      } catch (err) {
        console.warn(err);
      }
    });
    els.modeStep.addEventListener("click", async () => {
      await killLocalJogs();
      setModeUi("step");
      try {
        await api("/api/jog/mode", { mode: "step" });
      } catch (err) {
        console.warn(err);
      }
    });
    document.querySelectorAll(".step-size").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const size = Number(btn.dataset.size);
        setStepUi(size);
        try {
          await api("/api/jog/step-size", { size });
        } catch (err) {
          console.warn(err);
        }
      });
    });
    els.jogRate.addEventListener("input", () => {
      els.jogRateVal.textContent = `${els.jogRate.value}%`;
    });
    els.jogRate.addEventListener("change", async () => {
      try {
        await api("/api/jog/rate", { percent: Number(els.jogRate.value) });
      } catch (err) {
        console.warn(err);
      }
    });
    els.fro.addEventListener("input", () => {
      els.froVal.textContent = `${els.fro.value}%`;
    });
    els.fro.addEventListener("change", async () => {
      try {
        await api("/api/feed-override", { percent: Number(els.fro.value) });
      } catch (err) {
        console.warn(err);
      }
    });

    window.addEventListener("blur", () => {
      killLocalJogs();
    });
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) killLocalJogs();
    });
    window.addEventListener("pagehide", () => {
      killLocalJogs();
    });
    window.addEventListener("keydown", (ev) => {
      if (ev.key === "Escape") {
        api("/api/stop", {}).catch(() => {});
        killLocalJogs();
      }
    });
  }

  els.pinForm.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const value = els.pinInput.value.trim();
    try {
      const res = await fetch("/api/auth", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pin: value }),
      });
      if (!res.ok) {
        els.pinError.classList.remove("hidden");
        return;
      }
      pin = value;
      sessionStorage.setItem(PIN_KEY, pin);
      els.pinError.classList.add("hidden");
      showPin(false);
      connectWs();
    } catch {
      els.pinError.classList.remove("hidden");
    }
  });

  async function boot() {
    bindJogButtons();
    bindUi();
    try {
      const cfg = await fetch("/api/config").then((r) => r.json());
      pinRequired = Boolean(cfg.pin_required);
    } catch {
      pinRequired = false;
    }
    if (pinRequired && !pin) {
      showPin(true);
      return;
    }
    if (pinRequired && pin) {
      try {
        await api("/api/auth", { pin });
      } catch {
        showPin(true);
        return;
      }
    }
    connectWs();
  }

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  }

  boot();
})();
