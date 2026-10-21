# [App] NIBE Local HMI — local controller menus in Home Assistant, with protected editing

Hi everyone,

I'd like to share **NIBE Local HMI**, an experimental Home Assistant app that brings a compatible
NIBE controller's local menu tree into the HA interface.

The focus is interactive menu browsing and editing: controller labels, help text, units and
supported settings, without maintaining a separate register map for each menu screen.

## What it does

- Discovers controllers locally and pairs as **Home Assistant**; no manual token copying.
- Opens through HA Ingress with your HA login.
- Shows the controller's menu tree with one-level Back navigation.
- Edits supported numeric, selection, Boolean and text fields directly in their rows.
- Masks recognized password fields, with an eye button for explicit reveal.
- Keeps changes locked by default and after every restart. Unlocking requires an active risk acknowledgement.
- Checks controller metadata, ranges and step sizes before writing.
- Saves pairing credentials using AES-256-GCM encryption.

## Installation

Add this repository under **Settings → Apps → Install app → ⋮ → Repositories**:

https://github.com/Oniel10/nibe-local-hmi

Install **NIBE Local HMI**, start it, open the interface and pair the intended controller.

## Current scope and limitations

This is an early experimental release. One amd64 HA OS installation and real controller menu
reads have been checked; a broad model/firmware compatibility list is still to be established.
The app requires the local token-pairing/menu API used by myUplink PRO. aarch64 is a configured
build target but has not been tested on physical hardware. Automated write tests use mocks.

Host networking is used for discovery, and port 8099 must be free. Controller TLS certificates
are currently not verified, so please use a trusted LAN. The encryption key is stored locally
for unattended restart; a full backup containing both key and ciphertext can decrypt the token.

The app does not create MQTT entities. Schedules, events and special controller screens are not
yet implemented. Interface controls are currently German; menu labels come from the controller.

This project was developed with AI assistance. It is independent of NIBE and is shared for free non-commercial use of unmodified versions only; see LICENSE.

Feedback and compatibility reports are welcome. Please include model/firmware and HA versions,
but remove serial numbers, usernames, passwords and tokens from reports and screenshots.

Documentation, security notes and issue templates:
https://github.com/Oniel10/nibe-local-hmi
