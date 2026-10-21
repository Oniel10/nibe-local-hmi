# Release checklist

1. Check that Oniel10/nibe-local-hmi is the intended public repository and choose main as its default branch.
2. Review the code, free non-commercial use of unmodified versions only; see LICENSE and independent-project wording. Confirm redistribution rights for any inherited code.
3. Run the local tests and review GitHub Actions results. No green CI result is claimed before Actions has run.
4. Confirm both configured container architectures build; mark hardware runtime coverage separately.
5. Test fresh install, discovery, pairing, restart, Ingress navigation and default-on write lock in HA.
6. Exercise write validation only on mocks until a volunteer deliberately opts into a controlled hardware test.
7. Check README, DOCS, security limitations, migration notes, and CHANGELOG. Do not claim a vendor/model compatibility list without evidence.
8. Scan the repository and release ZIP for customer data, tokens, keys, APKs and deployment notes.
9. Keep VERSION, nibe_local/config.yaml, startup version and Docker labels in sync.
10. Enable Issues, useful topic tags and private vulnerability reporting in GitHub settings.
11. Create a release tag matching VERSION after review. Do not move published version tags.
12. Verify the repository URL and My Home Assistant badge, then publish the reviewed community draft.

A public repository provides the normal HA install/update path. No container registry account is
required: `config.yaml` omits `image`, so Supervisor builds from source. This repository's workflow
builds containers for validation and never pushes images. After both test versions and both container architectures pass on a main-branch push, it creates an experimental GitHub release matching VERSION, including a source ZIP. Existing releases are left unchanged. Installation and updating Home Assistant remain user actions.

The independent repository app will not overwrite an existing local install. Follow MIGRATION.md.
