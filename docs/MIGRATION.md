# Moving from the private local app

A local app named `local_nibe_local` and the Git-repository app are separate installations in HA.
The repository URL supplies a different app ID even though both use the internal slug `nibe_local`.
Private app data and encrypted tokens are not automatically transferred.

1. Save the old installation and an appropriate backup; do not uninstall it first.
2. Install the repository app but leave it stopped.
3. Stop the old local app and disable its Start on boot to free host port 8099.
4. Start the repository app and create its own local pairing as Home Assistant.
5. Confirm menu reads and the active write lock before deciding whether to remove the old app.

Do not run both simultaneously on port 8099. Do not paste old tokens or copy the key into app options.
For rollback, stop the repository app and start the preserved local app again. Controller settings
already changed by a user are not reverted by switching apps.
