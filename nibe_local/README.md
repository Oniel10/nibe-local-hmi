# NIBE Local HMI

Experimental local menu interface for compatible NIBE controllers. Discover and pair the
controller, browse its menu tree and edit supported values inline after unlocking writes.
HA Ingress handles login. Pairing tokens are stored with AES-256-GCM encryption.

Writes are locked after every restart. Unlocking requires an active acknowledgement.
Password values stay masked unless the eye button is pressed.

**Known security limits:** controller TLS certificates are not verified; the encryption key
is stored locally for automatic restart. Use a trusted LAN and protect full app backups.
Host networking is used for mDNS; host port 8099 must be free.

See the Documentation tab for setup and supported features. This app does not publish MQTT
entities. UI controls are currently German; controller menu labels use its own language.

[Project and issues](https://github.com/Oniel10/nibe-local-hmi) · free non-commercial use of unmodified versions only; see LICENSE.
