//! Anthill desktop shell (Tauri v2). Compiles in release CI, not locally (see ../README.md).
//!
//! The web UI is NOT re-bundled here. The shell spawns the existing PyInstaller backend
//! (`anthill/desktop.py`) as a Tauri **sidecar** in headless mode (`ANTHILL_NO_BROWSER=1`),
//! reads the `PORT=<n>` it prints on stdout, waits for it to start listening, then points the
//! native window at `http://127.0.0.1:<port>`. The shell owns the sidecar's lifetime explicitly:
//! tauri-plugin-shell does NOT reap sidecar children on exit, so we track the `CommandChild` in
//! managed state and kill it on window-close / app-exit and before starting a replacement; the
//! sidecar also self-exits if this shell dies (see `anthill/desktop.py`). Without that, the backend
//! outlives the app as an orphaned `anthill-server` still holding its port across quit/relaunch.
//! This mirrors the standalone dmg launcher, with the WKWebView/WebView2/WebKitGTK window replacing
//! `webbrowser.open`.
//!
//! On launch it also runs the **auto-updater**: it checks the release endpoint in
//! `tauri.conf.json` (`plugins.updater`), and if a newer, minisign-verified release is published it
//! downloads, installs, and restarts into it - so cutting a release auto-pushes to every install.
//! The check is best-effort: an unreachable server never blocks startup. The updater only reaches
//! installs once the repo's releases are public (at launch); see engineering-plans/TAURI_AUTOUPDATE.md.

use std::sync::Mutex;
use std::time::Duration;
use tauri::{AppHandle, Manager, Runtime, WebviewWindow};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;
use tauri_plugin_updater::UpdaterExt;

/// How long to wait for the backend to start accepting connections before giving up.
const HEALTH_TIMEOUT_SECS: u64 = 120;

/// The backend sidecar currently shown in the window, plus which profile it serves. Held in managed
/// state so a profile switch or an app exit can stop it and (for a switch) start a replacement -
/// each profile is its own backend process, the isolation boundary (its own DB, keys and data; see
/// anthill/profiles.py). Tracking the profile lets us treat "open the profile that's already
/// showing" as a no-op re-show rather than a second process fighting the first over one SQLite DB.
/// `profile` is `None` for the boot backend, where the Python side resolves the active/last-used one.
#[derive(Default)]
struct BackendState {
    child: Option<CommandChild>,
    profile: Option<String>,
}

struct Backend(Mutex<BackendState>);

/// The app's compile-time context: `tauri.conf.json`, the embedded assets and the capabilities/ACL.
/// Expanded in exactly ONE place: `generate_context!()` embeds Info.plist under a fixed symbol name,
/// so expanding it twice in one crate is a duplicate-symbol error. `run()` and the ACL tests both
/// build from this, which is also what lets the tests exercise the same capabilities that ship.
fn app_context<R: Runtime>() -> tauri::Context<R> {
    tauri::generate_context!()
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let mut builder = tauri::Builder::default();
    // Single-instance MUST be the first plugin registered (Tauri requirement). A second launch of an
    // already-running Anthill would spawn a second backend that fights the first over the shared
    // SQLite database (~/Library/Application Support/Anthill/anthill.db) and hang with no window - so
    // instead we focus the window that is already up and let the new process exit. Desktop-only: the
    // plugin (and the multi-process failure mode it guards) does not exist on mobile.
    #[cfg(desktop)]
    {
        builder = builder.plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.show();
                let _ = window.unminimize();
                let _ = window.set_focus();
            }
        }));
    }
    builder
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .manage(Backend(Mutex::new(BackendState::default())))
        .invoke_handler(tauri::generate_handler![open_profile])
        .setup(|app| {
            let handle = app.handle().clone();
            // Off the UI thread so the splash window paints immediately. Check for an update first
            // (a release auto-installs and the app restarts into it); then boot the local backend so
            // the app still works when up to date or offline.
            tauri::async_runtime::spawn(async move {
                if let Err(err) = check_and_apply_update(&handle).await {
                    // Never fatal: an unreachable update server must not block the app from starting.
                    eprintln!("anthill-desktop: update check skipped: {err}");
                }
                if let Err(err) = boot_backend(handle.clone()).await {
                    // Fail loudly: the backend is dead, so there is no web UI to show. Rather than
                    // leave a hidden window and an app that "opens nothing", surface the error so the
                    // user can act (reopen, or report it) instead of staring at a dead Dock icon.
                    eprintln!("anthill-desktop: backend boot failed: {err}");
                    show_startup_error(&handle, &err.to_string());
                }
            });
            Ok(())
        })
        .build(app_context())
        .expect("error while building the Anthill desktop shell")
        // Own the sidecar's lifetime: tauri-plugin-shell does NOT reap sidecar children when the app
        // exits, so without this the backend survives every quit/relaunch as an orphaned
        // anthill-server still listening on its port. Kill the tracked child on the terminal Exit
        // event (Cmd+Q, menu Quit, or last-window-close) and on an explicit window close.
        .run(|app_handle, event| match event {
            tauri::RunEvent::WindowEvent {
                event: tauri::WindowEvent::CloseRequested { .. },
                ..
            } => stop_backend(app_handle),
            tauri::RunEvent::Exit => stop_backend(app_handle),
            _ => {}
        });
}

/// Reveal a fatal startup failure instead of leaving a hidden, dead window. The backend never came
/// up, so there is no local web UI to navigate to; we rewrite the splash document in place (fully
/// self-contained - no assets, no network) and show the window so the user sees an actionable
/// message rather than an app that silently opens nothing.
fn show_startup_error(app: &AppHandle, detail: &str) {
    let Some(window) = app.get_webview_window("main") else {
        eprintln!("anthill-desktop: no `main` window to show the startup error in");
        return;
    };
    // Escape into a JS template literal: backslash, backtick and `${` are the only breakouts.
    let safe = detail
        .replace('\\', "\\\\")
        .replace('`', "\\`")
        .replace("${", "\\${");
    let js = format!(
        "document.body.innerHTML = `\
         <div style=\"max-width:34rem;margin:0 auto;padding:3rem 2rem;text-align:left\">\
         <div style=\"font-size:1.25rem;font-weight:600;margin-bottom:.75rem\">Anthill couldn't start</div>\
         <p style=\"opacity:.8;line-height:1.55;margin:0 0 1rem\">The local backend didn't start, so \
         there's nothing to show. Quitting and reopening Anthill usually fixes it. If it keeps \
         happening, please report the details below.</p>\
         <pre style=\"background:#000;color:#ff9a8a;padding:1rem;border-radius:8px;overflow:auto;\
         white-space:pre-wrap;font-size:12px;margin:0\">{safe}</pre></div>`;"
    );
    let _ = window.show();
    let _ = window.eval(&js);
}

/// Check the configured update endpoint (tauri.conf.json `plugins.updater`); if a newer,
/// signature-verified release exists, download and install it, then restart into it - so cutting a
/// release auto-pushes to every installed app with no user action. When already up to date this
/// returns `Ok(())` and the caller boots the backend; when the update server is unreachable it
/// returns an error the caller logs and ignores (the app then runs offline on the last version).
async fn check_and_apply_update(app: &AppHandle) -> tauri_plugin_updater::Result<()> {
    if let Some(update) = app.updater()?.check().await? {
        // Progress callbacks are intentionally no-ops here; the splash window is already up.
        update
            .download_and_install(|_chunk, _total| {}, || {})
            .await?;
        app.restart(); // never returns: relaunches into the just-installed version
    }
    Ok(())
}

/// Spawn the backend sidecar for `profile` (None lets the backend resolve the active/last-used
/// profile itself), read the `PORT=<n>` it prints, and wait until it accepts connections. Returns the
/// port and the child handle (kept so a later switch can stop it). A background task then drains the
/// sidecar's output for its lifetime so a full stdout pipe can never block it.
async fn spawn_backend<R: Runtime>(
    app: &AppHandle<R>,
    profile: Option<&str>,
) -> Result<(u16, CommandChild), Box<dyn std::error::Error>> {
    let mut cmd = app
        .shell()
        .sidecar("anthill-server")?
        // Headless mode: desktop.py serves without opening a browser, binds a free port,
        // and prints `PORT=<n>` on stdout for us to read.
        .env("ANTHILL_NO_BROWSER", "1")
        // Our PID so the sidecar can watch *this shell* and self-exit if we vanish. A one-file
        // PyInstaller build runs uvicorn in a child of the bootloader, and on a hard shell crash
        // (SIGKILL / Force Quit, where RunEvent::Exit never fires to kill the bootloader) that
        // bootloader is merely re-parented and keeps the worker alive - so watching getppid() alone
        // can't catch it. Watching this PID does. See `_exit_when_orphaned` in anthill/desktop.py.
        .env("ANTHILL_SHELL_PID", std::process::id().to_string());
    if let Some(id) = profile {
        // Root this backend at the chosen profile's isolated data home.
        cmd = cmd.env("ANTHILL_PROFILE", id);
    }
    let (mut rx, child) = cmd.spawn()?;

    let mut port: Option<u16> = None;
    while let Some(event) = rx.recv().await {
        match event {
            CommandEvent::Stdout(bytes) => {
                if let Some(p) = parse_port_line(&String::from_utf8_lossy(&bytes)) {
                    port = Some(p);
                    break;
                }
            }
            CommandEvent::Stderr(bytes) => {
                eprintln!("anthill-server: {}", String::from_utf8_lossy(&bytes).trim_end());
            }
            CommandEvent::Terminated(payload) => {
                return Err(format!("sidecar exited before serving: {payload:?}").into());
            }
            _ => {}
        }
    }
    let port = port.ok_or("sidecar never printed a PORT= line")?;

    // Keep consuming output so the sidecar's stdout pipe never fills and blocks it.
    tauri::async_runtime::spawn(async move {
        while let Some(event) = rx.recv().await {
            if let CommandEvent::Stderr(bytes) = event {
                eprintln!("anthill-server: {}", String::from_utf8_lossy(&bytes).trim_end());
            }
        }
    });

    wait_until_up(port).await?;
    Ok((port, child))
}

/// Point the main window at the backend on `port` and reveal it.
fn show_on<R: Runtime>(app: &AppHandle<R>, port: u16) -> Result<(), Box<dyn std::error::Error>> {
    let url = format!("http://127.0.0.1:{port}/");
    let window: WebviewWindow<R> = app.get_webview_window("main").ok_or("no `main` window")?;
    window.navigate(tauri::Url::parse(&url)?)?;
    window.show()?;
    Ok(())
}

/// First boot: start the active profile's backend and show the window on it.
async fn boot_backend(app: AppHandle) -> Result<(), Box<dyn std::error::Error>> {
    let (port, child) = spawn_backend(&app, None).await?;
    {
        let backend = app.state::<Backend>();
        let mut state = backend.0.lock().unwrap();
        // Guard against ever leaking a prior backend by overwriting its handle (a no-op on the
        // normal single first boot). The boot backend serves whichever profile the Python resolves.
        if let Some(old) = state.child.replace(child) {
            let _ = old.kill();
        }
        state.profile = None;
    }
    show_on(&app, port)?;
    Ok(())
}

/// Stop the running backend sidecar, if any. Idempotent: `kill()` consumes the handle, so taking it
/// out of the shared state kills it at most once. This is how the sidecar's lifetime is tied to the
/// shell's - called on window close and app exit so the backend never lingers as an orphaned process.
fn stop_backend(app: &AppHandle) {
    let backend = app.state::<Backend>();
    let mut state = backend.0.lock().unwrap();
    state.profile = None;
    if let Some(child) = state.child.take() {
        let _ = child.kill();
    }
}

/// Switch the window to another profile. Invoked from the web UI
/// (`window.__TAURI__.core.invoke('open_profile', { id })`). If that profile's backend is already
/// the one running, just re-reveal the window - one sidecar per profile, since a second process
/// would fight the first over the same SQLite DB. Otherwise start the chosen profile's backend (its
/// own isolated data + a fresh port) first, repoint the window, then stop the previous backend - so
/// the window never lands on a dead page. If the new backend fails to start, the current profile is
/// left running and the error is returned to the caller.
#[tauri::command]
async fn open_profile<R: Runtime>(app: AppHandle<R>, id: String) -> Result<(), String> {
    {
        let backend = app.state::<Backend>();
        let state = backend.0.lock().unwrap();
        if state.child.is_some() && state.profile.as_deref() == Some(id.as_str()) {
            drop(state);
            let window = app.get_webview_window("main").ok_or("no `main` window")?;
            window.show().map_err(|e| e.to_string())?;
            let _ = window.set_focus();
            return Ok(());
        }
    }
    let (port, child) = spawn_backend(&app, Some(&id)).await.map_err(|e| e.to_string())?;
    let previous = {
        let backend = app.state::<Backend>();
        let mut state = backend.0.lock().unwrap();
        state.profile = Some(id.clone());
        state.child.replace(child)
    };
    show_on(&app, port).map_err(|e| e.to_string())?;
    if let Some(old) = previous {
        let _ = old.kill(); // best-effort: the switch already succeeded
    }
    Ok(())
}

/// Pull the port out of a `PORT=8000` line (anthill/desktop.py prints exactly this).
fn parse_port_line(line: &str) -> Option<u16> {
    line.trim().strip_prefix("PORT=")?.trim().parse::<u16>().ok()
}

/// Poll the backend with a minimal HTTP GET until it actually answers (dependency-free: raw
/// `std::net`, no HTTP client crate - matching this file's existing no-new-dependency approach).
///
/// A bare TCP `connect()` only proves the OS accepted a SYN into the socket's backlog - uvicorn
/// binds and listens *before* running the ASGI app's lifespan startup, so `connect()` can succeed
/// while the app underneath still isn't answering requests yet. `show_on` navigated the window the
/// moment that connect succeeded, so a slow first boot (migrations, index warmup) could point the
/// window at the backend before it could serve the first real request - which failed, and a leftover
/// PWA service-worker fallback (`sw.js`) showed its generic "You're offline / reconnect to your
/// network" page, on a backend that was never actually unreachable, just not ready yet one instant
/// earlier (live report: fresh v0.12.2 install). Waiting for a real `HTTP/` response line instead of
/// just a socket handshake closes that gap at its source.
async fn wait_until_up(port: u16) -> Result<(), Box<dyn std::error::Error>> {
    let healthy = tauri::async_runtime::spawn_blocking(move || {
        use std::io::{Read, Write};
        use std::net::TcpStream;
        for _ in 0..HEALTH_TIMEOUT_SECS {
            if let Ok(mut stream) = TcpStream::connect(("127.0.0.1", port)) {
                stream.set_read_timeout(Some(Duration::from_secs(2))).ok();
                stream.set_write_timeout(Some(Duration::from_secs(2))).ok();
                let request = format!("GET / HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n");
                if stream.write_all(request.as_bytes()).is_ok() {
                    let mut buf = [0u8; 32];
                    if let Ok(n) = stream.read(&mut buf) {
                        if buf[..n].starts_with(b"HTTP/") {
                            return true;
                        }
                    }
                }
            }
            std::thread::sleep(Duration::from_secs(1));
        }
        false
    })
    .await?;
    if healthy {
        Ok(())
    } else {
        Err("backend did not start responding to HTTP requests within the health timeout".into())
    }
}

/// The window shows the backend over `http://127.0.0.1:<port>`, which Tauri treats as a REMOTE origin:
/// for remote origins it rejects any command that no capability explicitly grants ("Command
/// open_profile not allowed by ACL" - founder report, 2026-10-02: creating/opening a second profile
/// failed in the packaged app). An app command can only be granted once `build.rs` declares it in an
/// app manifest (that is what generates the `allow-open-profile` permission), so these tests drive the
/// REAL embedded capabilities through Tauri's mock runtime, from the same kind of origin the shipped
/// window uses - something no browser or Python test can reproduce.
///
/// Unix-only because the stand-in sidecar is a shell script. Needs `python3` for the end-to-end test.
#[cfg(all(test, unix))]
mod acl_tests {
    use super::{open_profile, Backend, BackendState};
    use std::os::unix::fs::PermissionsExt;
    use std::path::PathBuf;
    use std::sync::{Mutex, MutexGuard};
    use tauri::Manager;

    /// Serializes these tests: each one swaps the sidecar file Tauri resolves, a single shared path.
    static SIDECAR_SLOT: Mutex<()> = Mutex::new(());

    /// A sidecar that dies at once, so a test that merely wants to know "was the command dispatched?"
    /// can never start a real backend (on a developer machine that file could be a real build).
    const DIES_AT_ONCE: &str = "#!/bin/sh\nexit 1\n";

    /// A sidecar that behaves like the real one where it matters to the shell: print `PORT=<n>`, then
    /// answer HTTP on that port until killed.
    const SERVES_HTTP: &str = "#!/bin/sh\n\
        PORT=$(python3 -c 'import socket; s = socket.socket(); s.bind((\"127.0.0.1\", 0)); print(s.getsockname()[1])')\n\
        echo \"PORT=$PORT\"\n\
        exec python3 -m http.server \"$PORT\" --bind 127.0.0.1 >/dev/null 2>&1\n";

    /// Puts `script` where Tauri looks for the `anthill-server` sidecar and puts back whatever was
    /// there (tauri-build copies the real or placeholder sidecar there) when dropped, even on panic.
    struct SidecarStandIn {
        path: PathBuf,
        original: Option<(Vec<u8>, std::fs::Permissions)>,
        _slot: MutexGuard<'static, ()>,
    }

    impl SidecarStandIn {
        fn install(script: &str) -> Self {
            let slot = SIDECAR_SLOT
                .lock()
                .unwrap_or_else(|poisoned| poisoned.into_inner());
            // Resolve the path exactly as tauri-plugin-shell does for `sidecar("anthill-server")`: next to
            // the executable, except that a test binary lives in `deps/`, so it goes one level up.
            let exe = tauri::utils::platform::current_exe().unwrap();
            let exe_dir = exe.parent().unwrap();
            let base_dir = if exe_dir.ends_with("deps") {
                exe_dir.parent().unwrap()
            } else {
                exe_dir
            };
            let path = base_dir.join("anthill-server");
            let original = std::fs::read(&path)
                .ok()
                .zip(std::fs::metadata(&path).ok().map(|m| m.permissions()));
            let _ = std::fs::remove_file(&path);
            std::fs::write(&path, script).unwrap();
            std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o755)).unwrap();
            SidecarStandIn {
                path,
                original,
                _slot: slot,
            }
        }
    }

    impl Drop for SidecarStandIn {
        fn drop(&mut self) {
            let _ = std::fs::remove_file(&self.path);
            if let Some((bytes, permissions)) = self.original.take() {
                let _ = std::fs::write(&self.path, bytes);
                let _ = std::fs::set_permissions(&self.path, permissions);
            }
        }
    }

    /// Invoke `open_profile` as if the page at `origin_url` had called it, with `sidecar_script` as the
    /// backend, then stop any backend the command started (the shell plugin never reaps sidecars).
    /// Returns the command's result (`Err` carries an ACL rejection or whatever `open_profile` itself
    /// returned) and which profile the shell recorded as showing.
    fn invoke_open_profile_from(
        origin_url: &str,
        sidecar_script: &str,
    ) -> (Result<(), String>, Option<String>) {
        let _sidecar = SidecarStandIn::install(sidecar_script);
        let app = tauri::test::mock_builder()
            .plugin(tauri_plugin_shell::init())
            .manage(Backend(Mutex::new(BackendState::default())))
            .invoke_handler(tauri::generate_handler![open_profile])
            .build(super::app_context())
            .expect("build the app with the real capabilities");
        // The capability targets the window label `main` (as in tauri.conf.json); the mock app does not
        // create config windows on its own.
        let webview = tauri::WebviewWindowBuilder::new(&app, "main", Default::default())
            .build()
            .expect("create the `main` window");
        let response = tauri::test::get_ipc_response(
            &webview,
            tauri::webview::InvokeRequest {
                cmd: "open_profile".into(),
                callback: tauri::ipc::CallbackFn(0),
                error: tauri::ipc::CallbackFn(1),
                url: origin_url.parse().unwrap(),
                body: tauri::ipc::InvokeBody::Json(serde_json::json!({ "id": "work-test" })),
                headers: Default::default(),
                invoke_key: tauri::test::INVOKE_KEY.to_string(),
            },
        );
        let (child, profile) = {
            let state = app.state::<Backend>();
            let mut state = state.0.lock().unwrap();
            (state.child.take(), state.profile.clone())
        };
        if let Some(child) = child {
            let _ = child.kill();
        }
        let result = match response {
            Ok(_) => Ok(()),
            Err(value) => Err(value
                .as_str()
                .map(str::to_owned)
                .unwrap_or_else(|| value.to_string())),
        };
        (result, profile)
    }

    fn rejected_by_acl(error: &str) -> bool {
        error.contains("not allowed")
    }

    #[test]
    fn open_profile_is_allowed_from_the_local_backend_origin() {
        // The sidebar switcher lives on every page, including the root the window is first pointed at.
        for origin in [
            "http://127.0.0.1:52744/",
            "http://127.0.0.1:52744/profiles",
            "http://localhost:8123/profiles",
        ] {
            // The stand-in sidecar dies at once, so the command is dispatched and then fails to start
            // a backend: all that matters here is that the ACL did not stop it first.
            let (result, _) = invoke_open_profile_from(origin, DIES_AT_ONCE);
            let error = result.expect_err("the stand-in sidecar exits immediately");
            assert!(
                !rejected_by_acl(&error),
                "open_profile was rejected by the ACL from {origin}: {error}"
            );
        }
    }

    #[test]
    fn open_profile_is_still_refused_to_any_other_origin() {
        for origin in [
            "https://example.com/profiles",
            "http://192.168.1.20:8000/profiles",
        ] {
            let (result, _) = invoke_open_profile_from(origin, DIES_AT_ONCE);
            let error = result.expect_err("an unlisted origin must not reach open_profile");
            assert!(
                rejected_by_acl(&error),
                "open_profile must not be reachable from {origin}, got: {error:?}"
            );
        }
    }

    /// Once the ACL lets it through, the real `open_profile` has to actually work: start the chosen
    /// profile's backend, read its `PORT=` line, wait for it to answer HTTP, record which profile is
    /// showing, and re-point the window. This code path had never run in the packaged app (every call
    /// died at the ACL), so a stand-in sidecar proves the rest of the chain too, instead of just
    /// moving the failure one layer deeper.
    #[test]
    fn open_profile_starts_the_chosen_profiles_backend_once_allowed() {
        let (result, profile) =
            invoke_open_profile_from("http://127.0.0.1:52744/profiles", SERVES_HTTP);
        assert_eq!(
            result,
            Ok(()),
            "open_profile should have started the backend"
        );
        assert_eq!(profile.as_deref(), Some("work-test"));
    }
}

#[cfg(test)]
mod tests {
    use super::parse_port_line;

    #[test]
    fn parses_a_port_line() {
        assert_eq!(parse_port_line("PORT=8000"), Some(8000));
        assert_eq!(parse_port_line("  PORT=52744\n"), Some(52744));
    }

    #[test]
    fn ignores_non_port_lines() {
        assert_eq!(parse_port_line("INFO: started"), None);
        assert_eq!(parse_port_line("PORT="), None);
        assert_eq!(parse_port_line("PORT=notaport"), None);
        assert_eq!(parse_port_line("PORT=99999999"), None); // overflows u16 -> rejected
    }
}
