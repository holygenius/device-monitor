# Sharing Device Monitor 1.0

The ad-hoc-signed DMG can be shared on GitHub Releases or your website. Apple
notarization is optional for this distribution approach. Explain the first-open
security exception, and clearly label the download Apple Silicon / not notarized.
See INSTALL.txt and https://support.apple.com/102445 .

## Release checklist

1. Use only the clean source directory prepared under `publish/device-monitor`.
   Build environments, old versions, local logs, and draft X posts are excluded.
2. Attach `Device-Monitor-1.0-Apple-Silicon.dmg`, the source ZIP, and SHA256SUMS.txt
   to a GitHub Release. Keep the MIT license and third-party notices in the repo.
3. Include installation steps, Open Anyway instructions, and compatibility limits.
   This build targets macOS 11+ and has only been tested on macOS 26.6.1 here.
4. Publish the actual validation scope: this build was checked locally. Do not claim
   validation on another Mac, Intel, or older macOS releases.
5. Audit repository visibility before publishing. Publishing this project does not
   require making any other repository public.

## Optional: eliminate the unidentified-developer installation step

Obtain a Developer ID Application identity from your Apple Developer account.
A local Apple Development identity is not a public-distribution identity.

```sh
DEVICE_MONITOR_SIGN_IDENTITY='Developer ID Application: YOUR NAME (TEAMID)' \
  .venv/bin/python build_release.py
```

PyInstaller signs nested code; the builder then seals and verifies the app.
Store notary credentials locally with `xcrun notarytool store-credentials`.
Submit the signed DMG using your keychain profile:

```sh
xcrun notarytool submit Device-Monitor-1.0-Apple-Silicon.dmg \
  --keychain-profile YOUR_PROFILE --wait
```

After Apple returns Accepted, staple and validate:

```sh
xcrun stapler staple Device-Monitor-1.0-Apple-Silicon.dmg
xcrun stapler validate Device-Monitor-1.0-Apple-Silicon.dmg
```

Review the notary log and test the downloaded result. Signature verification
alone does not prove notarization. Optionally adopt your own bundle identifier.

https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution
