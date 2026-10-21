# Compatibility

The app requires the local token-pairing/menu API used by myUplink PRO. Do not infer model or
firmware support from a different REST/MQTT project. A broad compatibility matrix is not available.

| Area | Evidence so far |
| --- | --- |
| HA OS, amd64 | Built, installed, Ingress and real controller menus checked on one installation. |
| mDNS and saved pairing | Used on that installation. |
| Numeric/text writes | Local HTTPS or in-process mocks; no real heating/credential changes performed for release validation. |
| aarch64 | Configured build target; no physical runtime test yet. |
| Specific model/firmware combinations | Not yet documented sufficiently for a supported-device claim. |

Report controller model, firmware version, HA OS/Core version, CPU architecture and which functions
work. Do not include serial numbers, credentials or backups. See the compatibility issue template.

0.6.1: the repository app upgrade on amd64 HA OS and read-only device/clock/menu access were checked. No controller setting or firmware was changed. The investigated NIBE controller returns 404 for systemFlow; the app shows clearly marked schematic functional groups from operating values instead.
