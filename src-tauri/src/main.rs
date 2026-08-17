// Prevents additional console window on Windows in release.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::net::TcpStream;
use std::process::Child;
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

use anyhow::Result;
use tauri::Manager;

// The Python sidecar is only spawned in release builds; in dev it is owned by
// `beforeDevCommand`. Gate these so debug builds don't warn about dead code.
#[cfg(not(debug_assertions))]
use std::path::PathBuf;
#[cfg(not(debug_assertions))]
use std::process::Command;
#[cfg(not(debug_assertions))]
use tauri::AppHandle;

const PYTHON_SERVER_PORT: u16 = 9800;
const PORT_READY_TIMEOUT_SECS: u64 = 20;
const POLL_INTERVAL_MS: u64 = 250;

// Resolve which Python binary to use for the sidecar.
//
// On macOS a GUI-launched .app has a very narrow PATH, so we can't rely on
// bare `python3` resolving to a usable interpreter. We probe well-known
// absolute paths, preferring a user-installed modern Python (Homebrew) and
// falling back to the always-present system /usr/bin/python3.
#[cfg(not(debug_assertions))]
#[cfg(target_os = "windows")]
fn find_python() -> PathBuf {
    PathBuf::from("pythonw")
}

#[cfg(not(debug_assertions))]
#[cfg(not(target_os = "windows"))]
fn find_python() -> PathBuf {
    for p in [
        "/opt/homebrew/bin/python3",
        "/usr/local/bin/python3",
        "/usr/bin/python3",
    ] {
        if std::path::Path::new(p).exists() {
            return PathBuf::from(p);
        }
    }
    PathBuf::from("python3")
}

// Build a sane PATH for the sidecar so python3 / rsync / ssh are all
// discoverable even when the app is launched from the GUI (minimal PATH).
#[cfg(not(debug_assertions))]
fn python_path_env() -> String {
    const BASE: &str = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin";
    match std::env::var("PATH") {
        Ok(existing) if !existing.is_empty() => format!("{BASE}:{existing}"),
        _ => BASE.to_string(),
    }
}

fn main() {
    let server_child: Arc<Mutex<Option<Child>>> = Arc::new(Mutex::new(None));
    let server_child_clone = server_child.clone();

    tauri::Builder::default()
        .setup(move |app| {
            let app_handle = app.handle().clone();
            let child_arc = server_child.clone();

            // In production the Python server is launched as a sidecar by Rust.
            // In dev mode it is started by `beforeDevCommand`, so we must NOT spawn
            // a second instance (would clash on port 9800).
            #[cfg(not(debug_assertions))]
            {
            // Resolve server.py location (different layout in dev vs bundled)
            let server_py = resolve_server_py(&app_handle)
                .expect("Failed to locate syncmaster/server.py");
            eprintln!("[syncmaster] using server.py: {}", server_py.display());

            // Start python server process
            let working_dir = server_py.parent().unwrap().to_path_buf();
            let python_bin = find_python();
            let child = Command::new(&python_bin)
                .arg(&server_py)
                .current_dir(&working_dir)
                .env("SYNCMASTER_PORT", PYTHON_SERVER_PORT.to_string())
                .env("PATH", python_path_env())
                .env_remove("__PYVENV_LAUNCHER__")
                .spawn()
                .unwrap_or_else(|e| {
                    panic!(
                        "Could not start `{}`.\n\
                         Please make sure Python 3 is installed and available on PATH.\n\
                        Error: {e}",
                        python_bin.display()
                    )
                });

            let pid = child.id();
            eprintln!("[syncmaster] python server started, pid={pid}");
            *child_arc.lock().unwrap() = Some(child);
            }

            let child_for_kill = child_arc.clone();
            let app_handle_for_wait = app_handle.clone();

            // Spawn a background thread that waits for the HTTP port, then navigates.
            // Works in both dev (beforeDevCommand owns the server) and prod (we own it).
            thread::spawn(move || {
                match wait_for_port(PYTHON_SERVER_PORT, PORT_READY_TIMEOUT_SECS) {
                    Ok(_) => {
                        eprintln!(
                            "[syncmaster] port {PYTHON_SERVER_PORT} ready, loading UI"
                        );
                        let url = format!("http://127.0.0.1:{PYTHON_SERVER_PORT}");
                        if let Some(win) = app_handle_for_wait.get_webview_window("main") {
                            let _ = win.navigate(url.parse().unwrap());
                        }
                    }
                    Err(e) => {
                        eprintln!("[syncmaster] FATAL: {e}");
                        // Kill python if it's stuck, then exit whole app
                        if let Ok(mut guard) = child_for_kill.lock() {
                            if let Some(mut c) = guard.take() {
                                let _ = c.kill();
                            }
                        }
                        let _ = app_handle_for_wait.exit(1);
                    }
                }
            });

            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(move |_app_handle, event| {
            // When the app is fully exiting, reap the python server.
            if matches!(event, tauri::RunEvent::Exit) {
                if let Ok(mut guard) = server_child_clone.lock() {
                    if let Some(mut child) = guard.take() {
                        let _ = child.kill();
                        let _ = child.wait();
                    }
                }
            }
        });
}

/// Resolve the absolute path to `syncmaster/server.py`.
///
/// Layouts (tried in order):
/// - Prod (bundled): `<resource_dir>/webapp/server.py`  (copied by beforeBuildCommand)
/// - Prod flat:      `<resource_dir>/server.py`
/// - Dev:            `<repo>/src-tauri/../syncmaster/server.py`
#[cfg(not(debug_assertions))]
fn resolve_server_py(app: &AppHandle) -> Result<PathBuf> {
    // 1) Try bundled resources first (production layout)
    if let Ok(res_dir) = app.path().resource_dir() {
        let candidates = [
            res_dir.join("webapp").join("server.py"),
            res_dir.join("syncmaster").join("server.py"),
            res_dir.join("server.py"),
        ];
        for c in candidates {
            if c.exists() {
                return Ok(c);
            }
        }
    }

    // 2) Fallback to dev layout: `src-tauri/../syncmaster/server.py`
    // `CARGO_MANIFEST_DIR` == <repo>/src-tauri when built from cargo
    let manifest = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let dev = manifest
        .parent()
        .ok_or_else(|| anyhow::anyhow!("no parent of manifest dir"))?
        .join("syncmaster")
        .join("server.py");
    if dev.exists() {
        return Ok(dev);
    }

    anyhow::bail!(
        "syncmaster/server.py not found. Looked in resource dir and at {}",
        dev.display()
    )
}

fn wait_for_port(port: u16, timeout_secs: u64) -> Result<()> {
    let start = Instant::now();
    let deadline = start + Duration::from_secs(timeout_secs);
    let addr = format!("127.0.0.1:{port}");

    while Instant::now() < deadline {
        match TcpStream::connect_timeout(
            &addr.parse().unwrap(),
            Duration::from_millis(200),
        ) {
            Ok(_) => return Ok(()),
            Err(_) => thread::sleep(Duration::from_millis(POLL_INTERVAL_MS)),
        }
    }

    anyhow::bail!(
        "Timed out waiting for syncmaster HTTP server on {addr} after {timeout_secs}s"
    )
}
