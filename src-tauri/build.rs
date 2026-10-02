fn main() {
    // The window shows the backend over http://127.0.0.1:<port>, a REMOTE origin to Tauri, and Tauri
    // refuses any app command from a remote origin that a capability does not explicitly grant. A
    // command can only be granted once it is declared in the app manifest: that is what generates its
    // `allow-<command>` permission (see capabilities/default.json). Without this, `open_profile`
    // (the sidebar profile switcher and the Profiles page's Open button) failed in the packaged app
    // with "Command open_profile not allowed by ACL". Add every new `#[tauri::command]` here too.
    tauri_build::try_build(
        tauri_build::Attributes::new()
            .app_manifest(tauri_build::AppManifest::new().commands(&["open_profile"])),
    )
    .expect("failed to run tauri-build");
}
