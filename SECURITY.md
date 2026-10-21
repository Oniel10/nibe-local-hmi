# Security policy and model

Experimental software. No independent security audit is claimed.

- Pairing credentials are AES-256-GCM encrypted at rest with a random local key.
- Full app-data/backups include both ciphertext and key; compromise of both defeats this protection.
- Controller HTTPS certificates are currently not verified. Trusted LAN use only.
- Host networking is used for mDNS. Port 8099 exists on the host, with an app-level peer allowlist.
- HA Ingress and admin-only access provide browser login; no separate app password.
- CSRF protection and a restart-default write lock are enforced on the server.
- The lock is shared across app views, and does not protect against other controller clients.
- Password values are returned only by the explicit reveal operation and are masked elsewhere when recognized.

Do not report vulnerabilities with tokens, controller serials, credentials or backups attached.
Use GitHub's private vulnerability reporting on this repository **if the maintainer has enabled it**.
If that option is absent, open a minimal issue asking for a private contact without exploit details.
No email address or guaranteed response time is declared.

Only the newest experimental release is intended to receive fixes. Updating a container alone
cannot reverse settings changed at the controller. Keep backups and verify controller behavior.
