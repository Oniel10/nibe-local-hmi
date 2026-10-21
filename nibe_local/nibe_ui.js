'use strict';
document.addEventListener('DOMContentLoaded', () => {
  const context = document.getElementById('ui-context');
  if (!context) return;
  const status = document.getElementById('ui-status');
  const hide = (input, button) => {
    input.type = 'password';
    button.setAttribute('aria-pressed', 'false');
    button.setAttribute('aria-label', 'Passwort anzeigen');
    button.title = 'Passwort anzeigen';
    if (input.dataset.loaded === 'yes' && input.dataset.dirty !== 'yes') {
      input.value = '';
      delete input.dataset.loaded;
    }
  };
  document.querySelectorAll('[data-eye]').forEach(button => {
    const input = button.parentElement.querySelector('[data-secret-input]');
    input.addEventListener('input', () => { input.dataset.dirty = 'yes'; });
    button.addEventListener('click', async () => {
      if (input.type === 'text') { hide(input, button); return; }
      button.disabled = true;
      try {
        if (!input.value) {
          const body = new URLSearchParams({csrf: context.dataset.csrf,
            menu: button.dataset.menu, parameter: button.dataset.parameter});
          const response = await fetch(context.dataset.base + '/secret/reveal', {
            method: 'POST', credentials: 'same-origin', body,
            headers: {'Content-Type': 'application/x-www-form-urlencoded'}, cache: 'no-store'
          });
          if (!response.ok) throw new Error('Passwort konnte nicht abgerufen werden.');
          const data = await response.json();
          if (typeof data.value !== 'string') throw new Error('Passwort nicht verfügbar.');
          input.value = data.value;
          input.dataset.loaded = 'yes';
          delete input.dataset.dirty;
        }
        input.type = 'text';
        button.setAttribute('aria-pressed', 'true');
        button.setAttribute('aria-label', 'Passwort verbergen');
        button.title = 'Passwort verbergen';
        status.textContent = '';
      } catch (_) {
        status.textContent = 'Passwort konnte nicht abgerufen werden. Bitte die Verbindung prüfen.';
      } finally { button.disabled = false; }
    });
  });
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) document.querySelectorAll('[data-eye]').forEach(button => {
      hide(button.parentElement.querySelector('[data-secret-input]'), button);
    });
  });
});
