#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::path::PathBuf;
use std::process::Command;
use std::sync::Mutex;
use std::time::{Duration, Instant};

use tauri::{Manager, RunEvent};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

const SERVER_PORT: u16 = 8788;
const STARTUP_TIMEOUT_SECONDS: u64 = 120;

#[derive(Default)]
struct ServerState {
    child: Mutex<Option<CommandChild>>,
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

fn check_health() -> bool {
    let url = format!("{}/api/health", server_url());
    let client = match reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(2))
        .build()
    {
        Ok(client) => client,
        Err(_) => return false,
    };

    match client.get(url).send() {
        Ok(response) if response.status().is_success() => {
            match response.json::<serde_json::Value>() {
                Ok(body) => body.get("ok").and_then(|value| value.as_bool()) == Some(true),
                Err(_) => false,
            }
        }
        _ => false,
    }
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

    if check_health() {
        println!("Reusing existing VoiceMeeting server on {}", server_url());
        set_server_status(&state, "ready", "");
        return Ok(server_url());
    }

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

    let mut command = app.shell().command(
        server_exe
            .to_str()
            .ok_or_else(|| "Invalid server executable path".to_string())?,
    );
    if let Some(server_dir) = server_exe.parent() {
        command = command.current_dir(server_dir);
    }

    command = command.args(vec![
        "--host".to_string(),
        "127.0.0.1".to_string(),
        "--port".to_string(),
        port_str,
        "--data-dir".to_string(),
        data_dir_str,
        "--models-dir".to_string(),
        models_dir_str,
        "--parent-pid".to_string(),
        parent_pid_str,
        "--allow-model-download".to_string(),
    ]);

    let (mut rx, child) = command.spawn().map_err(|error| {
        let message = format!("Failed to spawn VoiceMeeting server: {error}");
        set_server_status(&state, "error", &message);
        message
    })?;
    let pid = child.pid();
    *state.pid.lock().map_err(|error| error.to_string())? = Some(pid);
    *state.child.lock().map_err(|error| error.to_string())? = Some(child);

    let app_for_logs = app.clone();
    tauri::async_runtime::spawn(async move {
        while let Some(event) = rx.recv().await {
            match event {
                CommandEvent::Stdout(line) => {
                    let text = String::from_utf8_lossy(&line).to_string();
                    println!("voice-meeting-server: {}", text);
                    let state = app_for_logs.state::<ServerState>();
                    push_server_log(&state, text);
                }
                CommandEvent::Stderr(line) => {
                    let text = String::from_utf8_lossy(&line).to_string();
                    eprintln!("voice-meeting-server: {}", text);
                    let state = app_for_logs.state::<ServerState>();
                    push_server_log(&state, text);
                }
                _ => {}
            }
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
    let _child = state.child.lock().ok().and_then(|mut child| child.take());
    set_server_status(state, "stopped", "");

    if let Some(pid) = pid {
        println!("Stopping VoiceMeeting server pid={}", pid);
        #[cfg(unix)]
        {
            let _ = Command::new("kill")
                .args(["-TERM", &pid.to_string()])
                .output();
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
