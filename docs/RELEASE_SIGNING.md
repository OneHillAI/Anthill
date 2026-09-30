# Release signing + notarization (Apple Developer ID)

This makes the downloaded `Anthill.dmg` and `Anthill-Appliance.pkg` **open with no Gatekeeper
warning** - "just open it", instead of the unsigned-build `xattr`/"Open Anyway" dance.

The release workflow (`.github/workflows/release.yml`) already signs, notarizes, and staples
**automatically** once the secrets below exist. There is no code to change - you add the
secrets once, then every tagged release is smooth. **You add the secrets in GitHub's UI; they
are never shared with anyone and never printed in logs.**

It is fully optional and gated: with no secrets, releases still build (unsigned).

## What you need

1. An **Apple Developer Program** membership (99 USD/year). Enrollment can take a day or two
   (Apple verifies your identity/organization), so start there if you have not enrolled.
2. Two **Developer ID** certificates (Apple issues both under one membership):
   - **Developer ID Application** - signs `Anthill.app` / `.dmg`.
   - **Developer ID Installer** - signs `Anthill-Appliance.pkg`.
3. An **App Store Connect API key** - notarytool uses it to notarize (no Apple ID password in CI).

## Step 1 - create the two certificates

Easiest in **Xcode**: Settings -> Accounts -> select your team -> **Manage Certificates** ->
**+** -> create **Developer ID Application** and again **+** -> **Developer ID Installer**.
(Or via developer.apple.com -> Certificates, which has you upload a CSR from Keychain Access.)

They land in your login keychain. Confirm the exact identity strings:

```bash
security find-identity -v -p codesigning   # -> "Developer ID Application: NAME (TEAMID)"
security find-identity -v                   # also lists "Developer ID Installer: NAME (TEAMID)"
```

Your **TEAMID** is the 10-character code in parentheses (also under developer.apple.com ->
Membership).

## Step 2 - export both certs as one .p12

In **Keychain Access** (login keychain, "My Certificates"): select **both** Developer ID
certificates together (Cmd-click), right-click -> **Export 2 items...** -> save
`anthill-certs.p12` and set an **export password** (you will store it as a secret). Make sure
each certificate's disclosure triangle shows a private key - export the certs, which carries
the keys.

Base64-encode it for the secret:

```bash
base64 -i anthill-certs.p12 | pbcopy   # now on your clipboard -> paste into MACOS_CERT_P12
```

## Step 3 - create the App Store Connect API key (for notarization)

**App Store Connect** -> **Users and Access** -> **Integrations** -> **App Store Connect API**
-> **Team Keys** -> generate a key with the **Developer** role. Note:

- **Key ID** (e.g. `ABCD123XYZ`) -> secret `AC_API_KEY_ID`
- **Issuer ID** (a UUID at the top of the page) -> secret `AC_API_ISSUER_ID`
- Download the **`AuthKey_XXXX.p8`** (you can only download it once), then base64 it:

```bash
base64 -i AuthKey_ABCD123XYZ.p8 | pbcopy   # -> paste into AC_API_KEY_B64
```

## Step 4 - add the GitHub repository secrets

Repo -> **Settings** -> **Secrets and variables** -> **Actions** -> **New repository secret**,
for each:

| Secret | Value |
|---|---|
| `MACOS_CERT_P12` | base64 of `anthill-certs.p12` (step 2) |
| `MACOS_CERT_PASSWORD` | the .p12 export password (step 2) |
| `MACOS_SIGN_IDENTITY` | `Developer ID Application: NAME (TEAMID)` |
| `MACOS_INSTALLER_IDENTITY` | `Developer ID Installer: NAME (TEAMID)` |
| `AC_API_KEY_ID` | the API Key ID (step 3) |
| `AC_API_ISSUER_ID` | the API Issuer ID (step 3) |
| `AC_API_KEY_B64` | base64 of the `.p8` (step 3) |

## Step 5 - cut a release

Bump `pyproject.toml`, then assemble the `## [x.y.z]` CHANGELOG section from the PR fragments with
`python scripts/build_changelog.py x.y.z <date>` (the release gate enforces the section exists and that
no fragment is left behind; see [CONTRIBUTING.md](../CONTRIBUTING.md#releasing-maintainers)), then tag:

```bash
git tag v0.5.5 && git push origin v0.5.5
```

CI builds, **signs, notarizes, staples**, and attaches both `Anthill.dmg` and
`Anthill-Appliance.pkg` to the GitHub Release - with "just open it" release notes. Notarization
adds a few minutes per artifact.

## Local signed build (optional)

```bash
export MACOS_SIGN_IDENTITY="Developer ID Application: NAME (TEAMID)"
export INSTALLER_ID="Developer ID Installer: NAME (TEAMID)"
export AC_API_KEY_ID=ABCD123XYZ AC_API_ISSUER_ID=<uuid> AC_API_KEY_PATH=AuthKey_ABCD123XYZ.p8
bash scripts/build-dmg.sh             # signed + notarized dmg
bash scripts/build-appliance-pkg.sh   # signed + notarized pkg
```

## Verify a built artifact

```bash
spctl -a -vvv dist/Anthill.app                       # -> "accepted, source=Notarized Developer ID"
spctl -a -vv -t install dist/Anthill-Appliance.pkg   # -> "accepted"
xcrun stapler validate dist/Anthill.dmg              # -> "The validate action worked!"
```

## Notes

- The app is signed under **hardened runtime** with `scripts/entitlements.plist` - the minimal
  set an embedded CPython needs (unsigned-executable-memory, JIT, library-validation off). Do
  not remove it or the notarized app is killed on launch.
- The `.pkg` is a scripts-only installer; signing it with the **Installer** cert + notarizing is
  what removes its Gatekeeper warning.
- Renewing/rotating certs: re-export the .p12 and update `MACOS_CERT_P12` / the identity secrets.
