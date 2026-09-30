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
use tauri::{AppHandle, Manager, WebviewWindow};
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
        .build(tauri::generate_context!())
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
async fn spawn_backend(
    app: &AppHandle,
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
fn show_on(app: &AppHandle, port: u16) -> Result<(), Box<dyn std::error::Error>> {
    let url = format!("http://127.0.0.1:{port}/");
    let window: WebviewWindow = app.get_webview_window("main").ok_or("no `main` window")?;
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
async fn open_profile(app: AppHandle, id: String) -> Result<(), String> {
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
