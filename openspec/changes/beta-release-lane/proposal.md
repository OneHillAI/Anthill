# A beta release lane: try a build before it reaches everyone

Full spec: `docs/specs/beta-release-lane.md`.

## Why

The repo is public and every installed app follows `releases/latest`. Tagging `vX.Y.Z` makes a release live for
everyone at once. There is no place to install a build, use it and judge it before it ships, and no way to say
"this build is the one we tested".

## What changes

- A **Cut Beta** workflow (started by hand, from `main`) builds a signed "Anthill Beta" release candidate
  `X.Y.Z-rc.N` and publishes it as a pre-release plus a rolling `beta-channel` feed. The live feed and the stable
  download are untouched.
- The beta app has its own name, bundle id, update feed and data folder, so it installs next to the live app and
  cannot touch its data. The backend names its data folder after the app (`ANTHILL_APP_NAME`); the live app's
  folder is unchanged.
- Today's two stable release workflows fire only on `vX.Y.Z`, so a beta tag cannot start a live release.
- A **Promote** workflow (second PR) ships one tested beta to live, behind an approval only the founder can give,
  and refuses if code has merged since the beta (the beta must be tested as is).

## Out of scope

A feed limited to team members, automatic betas on merge, per-PR previews, and consolidating the build steps.
