#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};

#[cfg(unix)]
use std::os::unix::process::CommandExt;
use tauri::{Manager, RunEvent};

const SERVER_PORT: u16 = 8788;
const STARTUP_TIMEOUT_SECONDS: u64 = 120;
const REQUIRED_API_REVISION: u64 = 2;

#[derive(Default)]
struct ServerState {
    pid: Mutex<Option<u32>>,
    status: Mutex<String>,
    error: Mutex<String>,
    server_path: Mutex<String>,
    data_dir: Mutex<String>,
    models_dir: Mutex<String>,
    logs: Mutex<Vec<String>>,
}

fn set_server_status(state: &ServerState, status: &str, error: &str) {
    if let Ok(mut value) = state.status.lock() {
        *value = status.to_string();
    }
    if let Ok(mut value) = state.error.lock() {
        *value = error.to_string();
    }
}

fn push_server_log(state: &ServerState, line: String) {
    if let Ok(mut logs) = state.logs.lock() {
        logs.push(line);
        let overflow = logs.len().saturating_sub(24);
        if overflow > 0 {
            logs.drain(0..overflow);
        }
    }
}

fn recent_server_logs(state: &ServerState) -> Vec<String> {
    state
        .logs
        .lock()
        .map(|logs| logs.clone())
        .unwrap_or_default()
}

#[cfg(target_os = "macos")]
fn request_native_microphone_permission() -> Result<(), String> {
    use block2::RcBlock;
    use objc2::runtime::Bool;
    use objc2_av_foundation::{AVAuthorizationStatus, AVCaptureDevice, AVMediaTypeAudio};
    use std::sync::mpsc;

    let audio_type = unsafe { AVMediaTypeAudio }
        .ok_or_else(|| "macOS audio media type is unavailable.".to_string())?;
    let status = unsafe { AVCaptureDevice::authorizationStatusForMediaType(audio_type) };

    if status == AVAuthorizationStatus::Authorized {
        return Ok(());
    }
    if status == AVAuthorizationStatus::Denied {
        return Err(
            "麦克风权限已被拒绝，请到 系统设置 > 隐私与安全性 > 麦克风 中允许 VoiceMeeting。"
                .to_string(),
        );
    }
    if status == AVAuthorizationStatus::Restricted {
        return Err("麦克风权限被系统策略限制，当前无法录音。".to_string());
    }
    if status != AVAuthorizationStatus::NotDetermined {
        return Err("无法确认麦克风权限状态。".to_string());
    }

    let (tx, rx) = mpsc::channel();
    let handler = RcBlock::new(move |granted: Bool| {
        let _ = tx.send(granted.as_bool());
    });

    unsafe {
        AVCaptureDevice::requestAccessForMediaType_completionHandler(audio_type, &handler);
    }

    match rx.recv_timeout(Duration::from_secs(120)) {
        Ok(true) => Ok(()),
        Ok(false) => Err(
            "麦克风权限已被拒绝，请到 系统设置 > 隐私与安全性 > 麦克风 中允许 VoiceMeeting。"
                .to_string(),
        ),
        Err(_) => Err("等待麦克风权限授权超时。".to_string()),
    }
}

#[cfg(not(target_os = "macos"))]
fn request_native_microphone_permission() -> Result<(), String> {
    Ok(())
}

#[tauri::command]
fn request_microphone_permission() -> Result<(), String> {
    request_native_microphone_permission()
}

#[tauri::command]
fn backend_status(state: tauri::State<'_, ServerState>) -> serde_json::Value {
    let health_ok = check_health();
    let status = if health_ok {
        "ready".to_string()
    } else {
        state
            .status
            .lock()
            .map(|value| value.clone())
            .unwrap_or_else(|_| "unknown".to_string())
    };
    let error = state
        .error
        .lock()
        .map(|value| value.clone())
        .unwrap_or_default();
    let server_path = state
        .server_path
        .lock()
        .map(|value| value.clone())
        .unwrap_or_default();
    let data_dir = state
        .data_dir
        .lock()
        .map(|value| value.clone())
        .unwrap_or_default();
    let models_dir = state
        .models_dir
        .lock()
        .map(|value| value.clone())
        .unwrap_or_default();
    let pid = state.pid.lock().ok().and_then(|value| *value);

    serde_json::json!({
        "status": status,
        "health_ok": health_ok,
        "error": error,
        "pid": pid,
        "url": server_url(),
        "server_path": server_path,
        "data_dir": data_dir,
        "models_dir": models_dir,
        "logs": recent_server_logs(&state),
    })
}

#[tauri::command]
fn save_markdown_file(
    default_filename: String,
    content: String,
) -> Result<serde_json::Value, String> {
    let filename = default_filename.trim();
    let filename = if filename.is_empty() {
        "meeting.md".to_string()
    } else {
        filename.to_string()
    };
    let path = rfd::FileDialog::new()
        .add_filter("Markdown", &["md", "markdown"])
        .set_file_name(&filename)
        .save_file();

    let Some(path) = path else {
        return Ok(serde_json::json!({ "saved": false }));
    };

    std::fs::write(&path, content.as_bytes()).map_err(|error| format!("保存文件失败：{error}"))?;

    Ok(serde_json::json!({
        "saved": true,
        "path": path.to_string_lossy(),
    }))
}

fn server_url() -> String {
    format!("http://127.0.0.1:{}", SERVER_PORT)
}

fn health_body() -> Option<serde_json::Value> {
    let url = format!("{}/api/health", server_url());
    let client = match reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(2))
        .build()
    {
        Ok(client) => client,
        Err(_) => return None,
    };

    match client.get(url).send() {
        Ok(response) if response.status().is_success() => response.json::<serde_json::Value>().ok(),
        _ => None,
    }
}

fn compatible_health(body: &serde_json::Value) -> bool {
    body.get("ok").and_then(|value| value.as_bool()) == Some(true)
        && body.get("app").and_then(|value| value.as_str()) == Some("VoiceMeeting")
        && body
            .get("api_revision")
            .and_then(|value| value.as_u64())
            .unwrap_or(0)
            >= REQUIRED_API_REVISION
}

fn check_health() -> bool {
    health_body().as_ref().is_some_and(compatible_health)
}

fn wait_for_health(timeout: Duration) -> bool {
    let start = Instant::now();
    while start.elapsed() < timeout {
        if check_health() {
            return true;
        }
        std::thread::sleep(Duration::from_millis(300));
    }
    false
}

#[cfg(unix)]
fn terminate_port_processes() {
    let port = SERVER_PORT.to_string();
    let output = Command::new("lsof")
        .args(["-ti", &format!("tcp:{port}")])
        .output();

    let Ok(output) = output else {
        return;
    };

    let text = String::from_utf8_lossy(&output.stdout);
    for pid in text.lines().map(str::trim).filter(|line| !line.is_empty()) {
        println!(
            "Stopping stale process on VoiceMeeting port {} pid={}",
            SERVER_PORT, pid
        );
        let _ = Command::new("kill").args(["-TERM", pid]).output();
    }

    std::thread::sleep(Duration::from_millis(700));

    for pid in text.lines().map(str::trim).filter(|line| !line.is_empty()) {
        if Command::new("kill")
            .args(["-0", pid])
            .status()
            .map(|status| status.success())
            .unwrap_or(false)
        {
            println!(
                "Force-stopping stale process on VoiceMeeting port {} pid={}",
                SERVER_PORT, pid
            );
            let _ = Command::new("kill").args(["-KILL", pid]).output();
        }
    }
}

#[cfg(not(unix))]
fn terminate_port_processes() {}

fn server_executable_path(app: &tauri::AppHandle) -> Result<PathBuf, String> {
    let resource_dir = app
        .path()
        .resource_dir()
        .map_err(|error| format!("Failed to resolve resource directory: {error}"))?;

    let executable_name = if cfg!(windows) {
        "voice-meeting-server.exe"
    } else {
        "voice-meeting-server"
    };

    Ok(resource_dir
        .join("resources")
        .join("voice-meeting-server")
        .join(executable_name))
}

fn start_server(app: &tauri::AppHandle) -> Result<String, String> {
    let state = app.state::<ServerState>();
    set_server_status(&state, "starting", "");

    if health_body().is_some() {
        println!(
            "Existing server on {}; restarting owned VoiceMeeting sidecar",
            server_url()
        );
    }
    terminate_port_processes();

    let app_data_dir = app
        .path()
        .app_data_dir()
        .map_err(|error| format!("Failed to resolve app data directory: {error}"))?;
    let data_dir = app_data_dir.join("data");
    let models_dir = app_data_dir.join("models");
    if let Ok(mut value) = state.data_dir.lock() {
        *value = data_dir.to_string_lossy().to_string();
    }
    if let Ok(mut value) = state.models_dir.lock() {
        *value = models_dir.to_string_lossy().to_string();
    }
    std::fs::create_dir_all(&data_dir)
        .map_err(|error| format!("Failed to create data directory: {error}"))?;
    std::fs::create_dir_all(&models_dir)
        .map_err(|error| format!("Failed to create models directory: {error}"))?;

    println!("Starting VoiceMeeting sidecar");
    println!("Data directory: {:?}", data_dir);
    println!("Models directory: {:?}", models_dir);

    let data_dir_str = data_dir
        .to_str()
        .ok_or_else(|| "Invalid data directory path".to_string())?
        .to_string();
    let models_dir_str = models_dir
        .to_str()
        .ok_or_else(|| "Invalid models directory path".to_string())?
        .to_string();
    let port_str = SERVER_PORT.to_string();
    let parent_pid_str = std::process::id().to_string();

    let server_exe = server_executable_path(app)?;
    if let Ok(mut value) = state.server_path.lock() {
        *value = server_exe.to_string_lossy().to_string();
    }
    if !server_exe.exists() {
        let message = format!(
            "VoiceMeeting server executable was not found at {:?}",
            server_exe
        );
        set_server_status(&state, "error", &message);
        return Err(message);
    }

    let mut command = Command::new(&server_exe);
    if let Some(server_dir) = server_exe.parent() {
        command.current_dir(server_dir);
    }

    command
        .args([
            "--host",
            "127.0.0.1",
            "--port",
            &port_str,
            "--data-dir",
            &data_dir_str,
            "--models-dir",
            &models_dir_str,
            "--parent-pid",
            &parent_pid_str,
            "--allow-model-download",
        ])
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());

    #[cfg(unix)]
    {
        command.process_group(0);
    }

    let mut child = command.spawn().map_err(|error| {
        let message = format!("Failed to spawn VoiceMeeting server: {error}");
        set_server_status(&state, "error", &message);
        message
    })?;
    let pid = child.id();
    *state.pid.lock().map_err(|error| error.to_string())? = Some(pid);

    if let Some(stdout) = child.stdout.take() {
        let app_for_logs = app.clone();
        std::thread::spawn(move || {
            use std::io::{BufRead, BufReader};

            for line in BufReader::new(stdout).lines().map_while(Result::ok) {
                println!("voice-meeting-server: {}", line);
                let state = app_for_logs.state::<ServerState>();
                push_server_log(&state, line);
            }
        });
    }

    if let Some(stderr) = child.stderr.take() {
        let app_for_logs = app.clone();
        std::thread::spawn(move || {
            use std::io::{BufRead, BufReader};

            for line in BufReader::new(stderr).lines().map_while(Result::ok) {
                eprintln!("voice-meeting-server: {}", line);
                let state = app_for_logs.state::<ServerState>();
                push_server_log(&state, line);
            }
        });
    }

    let app_for_exit = app.clone();
    std::thread::spawn(move || {
        let result = child.wait();
        let state = app_for_exit.state::<ServerState>();
        if let Ok(mut value) = state.pid.lock() {
            if matches!(*value, Some(current) if current == pid) {
                *value = None;
            }
        }
        let status = state
            .status
            .lock()
            .map(|value| value.clone())
            .unwrap_or_default();
        if status != "stopped" {
            let message = match result {
                Ok(exit) => format!("VoiceMeeting server exited: {exit}"),
                Err(error) => format!("VoiceMeeting server wait failed: {error}"),
            };
            set_server_status(&state, "error", &message);
            push_server_log(&state, message);
        }
    });

    if wait_for_health(Duration::from_secs(STARTUP_TIMEOUT_SECONDS)) {
        set_server_status(&state, "ready", "");
        Ok(server_url())
    } else {
        let recent_logs = recent_server_logs(&state);
        let message = if recent_logs.is_empty() {
            "VoiceMeeting server startup timed out.".to_string()
        } else {
            format!(
                "VoiceMeeting server startup timed out. Recent logs: {}",
                recent_logs.join(" | ")
            )
        };
        set_server_status(&state, "error", &message);
        Err(message)
    }
}

fn stop_server(state: &ServerState) {
    let pid = state.pid.lock().ok().and_then(|mut pid| pid.take());
    set_server_status(state, "stopped", "");

    if let Some(pid) = pid {
        println!("Stopping VoiceMeeting server pid={}", pid);
        #[cfg(unix)]
        {
            let pid_string = pid.to_string();
            let group_string = format!("-{pid}");
            let _ = Command::new("kill").args(["-TERM", &group_string]).output();
            let _ = Command::new("kill").args(["-TERM", &pid_string]).output();
            std::thread::sleep(Duration::from_millis(700));
            if Command::new("kill")
                .args(["-0", &group_string])
                .status()
                .map(|status| status.success())
                .unwrap_or(false)
            {
                let _ = Command::new("kill").args(["-KILL", &group_string]).output();
                let _ = Command::new("kill").args(["-KILL", &pid_string]).output();
            }
        }
        #[cfg(windows)]
        {
            let _ = Command::new("taskkill")
                .args(["/PID", &pid.to_string(), "/T", "/F"])
                .output();
        }
    }
}

fn main() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .manage(ServerState::default())
        .invoke_handler(tauri::generate_handler![
            request_microphone_permission,
            backend_status,
            save_markdown_file
        ])
        .setup(|app| {
            let app_handle = app.handle().clone();
            std::thread::spawn(move || match start_server(&app_handle) {
                Ok(url) => println!("VoiceMeeting server ready: {url}"),
                Err(error) => eprintln!("Failed to start VoiceMeeting server: {error}"),
            });
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("failed to build VoiceMeeting app");

    app.run(|app_handle, event| {
        if let RunEvent::Exit = event {
            let state = app_handle.state::<ServerState>();
            stop_server(&state);
        }
    });
}
