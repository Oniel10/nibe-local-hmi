# Changelog

## 0.6.0 — Extended local HMI and explicit firmware management

- Operating overview, local notifications, device status, clock/time zones and guarded event actions.
- Explicit menu-assistant lifecycle, nested rows and second-based time editors.
- WLAN scan, open/PSK/IEEE-802.1X configuration, cellular and aid-mode controls when device-supported.
- Public NIBE Austria S-regler download without cloud login; exact SWU staging/export.
- Separately acknowledged local firmware multipart transfer, progress, bounds, SHA-256, stale/replay guards and no automatic retries.
- Local parameter metadata and guarded single-point reads; no secret raw catalog export.
- Exact local API coverage matrix; graphical custom pages remain explicitly incomplete.
- Dedicated HTTPS-mock HMI/firmware regression suite. No firmware sent to real heating equipment for validation.

## 0.5.0 — First public experimental package

- Git-based Home Assistant app repository with installation badge and English/German documentation.
- Experimental status, explicit compatibility and security limitations, non-commercial, no-modification licensing and original app artwork.
- Reproducible controller mocks, browser eye-button checks and CI build workflows.
- No customer deployment records, pairing credentials or vendor APK content in the distribution.

## 0.4.1 — Private prototype

- NIBE Local HMI name, inline editing, one-level Back navigation, text fields and masked password reveal.
- Server-enforced write lock, active acknowledgement and restart defaults.
- Local pairing and encrypted credential storage inherited from the prototype.
