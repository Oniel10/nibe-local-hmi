"use strict";
(() => {
  const context = document.getElementById("ui-context");
  if (!context) return;
  const base = context.dataset.base;
  const csrf = context.dataset.csrf;
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
