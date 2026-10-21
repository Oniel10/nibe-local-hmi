"use strict";
(() => {
  const context = document.getElementById("ui-context");
  if (!context) return;
  const base = context.dataset.base;
  const csrf = context.dataset.csrf;
  const wifiList = document.getElementById("wifi-networks");
  const wifiStatus = document.getElementById("wifi-status");
  const wifiRescan = document.getElementById("wifi-rescan");
  let wifiTimer, wifiController, wifiGeneration = 0, leaving = false;
  async function scanWifi(generation, attempt = 0) {
    if (leaving || generation !== wifiGeneration) return;
    const controller = new AbortController();
    wifiController = controller;
    const timeout = setTimeout(() => controller.abort(), 20000);
    try {
      const response = await fetch(base + "/hmi/api/network", {cache: "no-store", credentials: "same-origin", signal: controller.signal});
      if (!response.ok) throw new Error();
      const data = await response.json();
      if (leaving || generation !== wifiGeneration) return;
      const networks = data.networks ?? data.wifiNetworks;
      if (networks !== undefined && !Array.isArray(networks)) throw new Error();
      const rows = (networks || []).filter(net => net && typeof net === "object" && typeof net.ssid === "string").slice(0, 200);
      // An empty interim response must not erase results already received.
      if (rows.length) {
        wifiList.replaceChildren();
        for (const net of rows) {
          const row = document.createElement("div"); row.className = "row";
          const name = document.createElement("span"); name.textContent = net.ssid;
          const detail = document.createElement("span"); detail.textContent = String(net.auth || "") + " · " + String(net.rssi ?? net.strength ?? "–");
          row.appendChild(name); row.appendChild(detail); wifiList.appendChild(row);
        }
      }
      wifiStatus.textContent = rows.length ? rows.length + " WLAN-Netze gefunden." : "Noch keine neuen Ergebnisse von der WP.";
      if (attempt < 11) {
        wifiStatus.textContent += " Suche läuft …";
        wifiTimer = setTimeout(() => scanWifi(generation, attempt + 1), 5000);
      } else {
        wifiStatus.textContent += " Suche beendet. Bei Bedarf erneut suchen oder SSID manuell eingeben.";
        wifiRescan.disabled = false;
      }
    } catch {
      if (leaving || generation !== wifiGeneration) return;
      wifiStatus.textContent = "WLAN-Suche nicht erreichbar. Verbindung prüfen und erneut suchen. Vorhandene Ergebnisse bleiben sichtbar.";
      wifiRescan.disabled = false;
    } finally { clearTimeout(timeout); }
  }
  function startWifi() {
    clearTimeout(wifiTimer);
    if (wifiController) wifiController.abort();
    wifiRescan.disabled = true;
    wifiStatus.textContent = "WLAN-Suche läuft …";
    scanWifi(++wifiGeneration);
  }
  if (wifiList && wifiStatus && wifiRescan) {
    wifiRescan.addEventListener("click", startWifi);
    startWifi();
  }
  window.addEventListener("pagehide", () => {
    leaving = true; ++wifiGeneration; clearTimeout(wifiTimer);
    if (wifiController) wifiController.abort();
  });
  document.querySelectorAll("[data-local-eye]").forEach(button => {
    button.addEventListener("click", () => {
      const input = document.getElementById(button.dataset.localEye);
      if (!input) return;
      const show = input.type === "password";
      input.type = show ? "text" : "password";
      button.setAttribute("aria-pressed", String(show));
      button.setAttribute("aria-label", show ? "Passwort verbergen" : "Passwort anzeigen");
    });
  });
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) return;
    document.querySelectorAll("[data-local-eye]").forEach(button => {
      const input = document.getElementById(button.dataset.localEye);
      if (input) input.type = "password";
      button.setAttribute("aria-pressed", "false");
      button.setAttribute("aria-label", "Passwort anzeigen");
    });
  });
  const status = document.getElementById("firmware-status");
  const fileForm = document.getElementById("firmware-file");
  let timer;
  let busy = false;
  async function poll() {
    if (!status) return;
    try {
      const response = await fetch(base + "/hmi/firmware/status", {cache: "no-store", credentials: "same-origin"});
      if (!response.ok) throw new Error();
      const job = await response.json();
      status.textContent = job.message + (job.bytes ? " · " + (job.bytes / 1048576).toFixed(1) + " MiB" : "");
      if (job.state === "running") {
        busy = true;
        timer = setTimeout(poll, 1500);
      } else if (busy) {
        const link = document.createElement("a");
        link.href = base + "/hmi/firmware";
        link.textContent = " Ansicht aktualisieren";
        status.appendChild(link);
      }
    } catch {
      status.textContent = "Status momentan nicht erreichbar. WP-Display prüfen; Übertragung nicht ungeprüft wiederholen.";
    }
  }
  if (status) poll();
  if (fileForm) fileForm.addEventListener("submit", event => {
    event.preventDefault();
    const file = fileForm.elements.firmware.files[0];
    if (!file || file.size > 512 * 1024 * 1024 || !/^[a-z0-9][a-z0-9._-]{0,119}\.swu$/i.test(file.name)) {
      status.textContent = "Bitte eine gültige .swu-Datei bis 512 MiB wählen.";
      return;
    }
    const button = fileForm.querySelector("button[type=submit]");
    button.disabled = true;
    const request = new XMLHttpRequest();
    request.open("POST", base + "/hmi/firmware/stage");
    request.setRequestHeader("Content-Type", "application/octet-stream");
    request.setRequestHeader("X-CSRF-Token", csrf);
    request.setRequestHeader("X-Filename", encodeURIComponent(file.name));
    request.setRequestHeader("X-File-Length", String(file.size));
    request.upload.addEventListener("progress", event => {
      if (event.lengthComputable) status.textContent = "Datei zur App übertragen: " + Math.round(event.loaded / event.total * 100) + " %";
    });
    request.addEventListener("load", () => {
      button.disabled = false;
      if (request.status === 200) location.assign(base + "/hmi/firmware");
      else status.textContent = "Datei nicht angenommen. Format, Größe und Verbindung prüfen. Es wurde kein WP-Update gestartet.";
    });
    request.addEventListener("error", () => {
      button.disabled = false;
      status.textContent = "Dateiübertragung zur App fehlgeschlagen. Es wurde kein WP-Update gestartet.";
    });
    request.send(file);
  });
  window.addEventListener("pagehide", () => clearTimeout(timer));
})();
