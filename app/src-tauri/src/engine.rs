//! 主引擎守护：启动 Python 引擎进程，读取就绪端口，崩溃后自动重启，退出 App 时优雅关闭。

use serde::Serialize;
use std::fs::{self, OpenOptions};
use std::io::{BufRead, BufReader, Read};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::{Arc, Condvar, Mutex};
use std::thread;
use std::time::{Duration, Instant};
use tauri::{AppHandle, Manager};

/// 连续崩溃这么多次后停止自动重启，等用户手动点“重试”。
const MAX_AUTO_RESTARTS: u32 = 5;

#[derive(Clone, Debug)]
pub struct LaunchConfig {
    pub python: PathBuf,
    pub data_dir: PathBuf,
    pub env: Vec<(String, String)>,
}

/// 开发时的仓库根目录（app/src-tauri 的上两级）。
fn dev_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("..").join("..")
}

impl LaunchConfig {
    pub fn resolve(app: &AppHandle) -> Result<Self, Box<dyn std::error::Error>> {
        let data_dir = app.path().app_data_dir()?;
        fs::create_dir_all(data_dir.join("logs"))?;
        let mut env: Vec<(String, String)> = Vec::new();

        let python = if let Ok(p) = std::env::var("SHENGJING_ENGINE_PYTHON") {
            PathBuf::from(p)
        } else if cfg!(debug_assertions) {
            // scripts/dev_setup.sh 建立的基础环境
            dev_root().join(".dev/base-venv/bin/python")
        } else {
            // 发布版：随 App 打包的 Python 运行环境
            app.path().resource_dir()?.join("runtime/base/bin/python3")
        };

        if cfg!(debug_assertions) {
            let root = dev_root();
            let analysis = root.join(".dev/analysis-venv/bin/python");
            if analysis.exists() {
                env.push((
                    "SHENGJING_PY_ANALYSIS".into(),
                    analysis.display().to_string(),
                ));
            }
            let music = root.join(".dev/ACE-Step-1.5/.venv/bin/python");
            if music.exists() {
                env.push(("SHENGJING_PY_MUSIC".into(), music.display().to_string()));
            }
        } else if let Ok(resources) = app.path().resource_dir() {
            let ffmpeg = resources.join("runtime/bin/ffmpeg");
            if ffmpeg.exists() {
                env.push(("SHENGJING_FFMPEG".into(), ffmpeg.display().to_string()));
            }
        }
        // 扩展包（分析、音乐）装在数据目录下，由引擎自己查找
        env.push(("SHENGJING_PY_BASE".into(), python.display().to_string()));
        Ok(Self {
            python,
            data_dir,
            env,
        })
    }

    fn log_path(&self) -> PathBuf {
        self.data_dir.join("logs").join("engine.log")
    }
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct EngineInfo {
    pub status: &'static str,
    pub base_url: Option<String>,
    pub token: String,
    pub error: Option<String>,
    pub restarts: u32,
}

struct State {
    status: &'static str,
    port: Option<u16>,
    token: String,
    error: Option<String>,
    restarts: u32,
    pid: Option<u32>,
    shutting_down: bool,
    restart_requested: bool,
}

type Shared = Arc<(Mutex<State>, Condvar)>;

pub struct EngineSupervisor {
    config: LaunchConfig,
    shared: Shared,
}

impl EngineSupervisor {
    pub fn new(config: LaunchConfig) -> Self {
        let state = State {
            status: "starting",
            port: None,
            token: String::new(),
            error: None,
            restarts: 0,
            pid: None,
            shutting_down: false,
            restart_requested: false,
        };
        Self {
            config,
            shared: Arc::new((Mutex::new(state), Condvar::new())),
        }
    }

    pub fn start(&self) {
        let config = self.config.clone();
        let shared = self.shared.clone();
        thread::spawn(move || supervise(config, shared));
    }

    pub fn info(&self) -> EngineInfo {
        let state = self.shared.0.lock().unwrap();
        EngineInfo {
            status: state.status,
            base_url: state.port.map(|p| format!("http://127.0.0.1:{p}")),
            token: state.token.clone(),
            error: state.error.clone(),
            restarts: state.restarts,
        }
    }

    pub fn restart(&self) {
        let (lock, cvar) = &*self.shared;
        let mut state = lock.lock().unwrap();
        state.restarts = 0;
        state.error = None;
        state.restart_requested = true;
        if let Some(pid) = state.pid {
            signal(pid, "TERM");
        }
        cvar.notify_all();
    }

    /// 退出 App 时调用：先发 SIGTERM 让引擎关闭模型进程，最多等 10 秒，再强制结束。
    pub fn shutdown(&self) {
        let (lock, cvar) = &*self.shared;
        let pid = {
            let mut state = lock.lock().unwrap();
            state.shutting_down = true;
            cvar.notify_all();
            state.pid
        };
        let Some(pid) = pid else { return };
        signal(pid, "TERM");
        let deadline = Instant::now() + Duration::from_secs(10);
        let mut state = lock.lock().unwrap();
        while state.pid.is_some() && Instant::now() < deadline {
            let (next, _) = cvar
                .wait_timeout(state, Duration::from_millis(200))
                .unwrap();
            state = next;
        }
        if state.pid.is_some() {
            signal(pid, "KILL");
        }
    }
}

fn signal(pid: u32, sig: &str) {
    let _ = Command::new("kill")
        .arg(format!("-{sig}"))
        .arg(pid.to_string())
        .status();
}

fn random_token() -> String {
    let mut bytes = [0u8; 32];
    if let Ok(mut f) = fs::File::open("/dev/urandom") {
        let _ = f.read_exact(&mut bytes);
    }
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

fn parse_ready(line: &str) -> Option<u16> {
    let value: serde_json::Value = serde_json::from_str(line.trim()).ok()?;
    if value.get("event")?.as_str()? != "ready" {
        return None;
    }
    u16::try_from(value.get("port")?.as_u64()?).ok()
}

/// 等待一段时间，期间如果要求重启或退出就提前返回。
fn wait_or_wake(shared: &Shared, timeout: Option<Duration>) {
    let (lock, cvar) = &**shared;
    let mut state = lock.lock().unwrap();
    let deadline = timeout.map(|t| Instant::now() + t);
    while !state.shutting_down && !state.restart_requested {
        match deadline {
            Some(d) => {
                let now = Instant::now();
                if now >= d {
                    break;
                }
                state = cvar.wait_timeout(state, d - now).unwrap().0;
            }
            None => state = cvar.wait(state).unwrap(),
        }
    }
    state.restart_requested = false;
}

fn supervise(config: LaunchConfig, shared: Shared) {
    let (lock, cvar) = &*shared;
    loop {
        if lock.lock().unwrap().shutting_down {
            break;
        }
        let token = random_token();
        let log_path = config.log_path();
        let log = OpenOptions::new().create(true).append(true).open(&log_path);

        let mut cmd = Command::new(&config.python);
        cmd.arg("-m")
            .arg("shengjing_engine")
            .arg("--port")
            .arg("0")
            .arg("--data-dir")
            .arg(&config.data_dir)
            .envs(config.env.iter().map(|(k, v)| (k.as_str(), v.as_str())))
            .env("SHENGJING_ENGINE_TOKEN", &token)
            .stdin(Stdio::null())
            .stdout(Stdio::piped());
        match log {
            Ok(file) => cmd.stderr(file),
            Err(_) => cmd.stderr(Stdio::null()),
        };

        let mut child = match cmd.spawn() {
            Ok(child) => child,
            Err(err) => {
                {
                    let mut state = lock.lock().unwrap();
                    state.status = "failed";
                    state.error = Some(format!(
                        "无法启动引擎（{}）：{err}",
                        config.python.display()
                    ));
                }
                // 解释器都找不到，自动重试没有意义，等用户点“重试”
                wait_or_wake(&shared, None);
                continue;
            }
        };

        {
            let mut state = lock.lock().unwrap();
            state.status = if state.restarts > 0 {
                "restarting"
            } else {
                "starting"
            };
            state.token = token;
            state.pid = Some(child.id());
            state.port = None;
        }

        if let Some(stdout) = child.stdout.take() {
            for line in BufReader::new(stdout).lines() {
                let Ok(line) = line else { break };
                if let Some(port) = parse_ready(&line) {
                    let mut state = lock.lock().unwrap();
                    state.port = Some(port);
                    state.status = "ready";
                    state.error = None;
                }
            }
        }
        let exit = child.wait();

        let backoff = {
            let mut state = lock.lock().unwrap();
            state.pid = None;
            state.port = None;
            cvar.notify_all();
            if state.shutting_down {
                state.status = "stopped";
                break;
            }
            if state.restart_requested {
                state.restart_requested = false;
                state.status = "starting";
                continue;
            }
            state.restarts += 1;
            let detail = match exit {
                Ok(status) => status.to_string(),
                Err(err) => err.to_string(),
            };
            state.error = Some(format!(
                "引擎意外退出（{detail}），详情见 {}",
                log_path.display()
            ));
            if state.restarts > MAX_AUTO_RESTARTS {
                state.status = "failed";
                None
            } else {
                state.status = "restarting";
                Some(Duration::from_secs(1u64 << state.restarts.min(5)))
            }
        };
        wait_or_wake(&shared, backoff);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_ready_line() {
        assert_eq!(
            parse_ready(r#"{"event": "ready", "port": 54321, "version": "0.1.0"}"#),
            Some(54321)
        );
        assert_eq!(parse_ready("INFO starting"), None);
        assert_eq!(parse_ready(r#"{"event": "other", "port": 1}"#), None);
    }

    #[test]
    fn token_is_random_hex() {
        let a = random_token();
        assert_eq!(a.len(), 64);
        assert_ne!(a, random_token());
    }

    /// 用一个 shell 脚本冒充 Python 引擎：报告就绪后按需要退出。
    fn fake_engine(dir: &Path, body: &str) -> LaunchConfig {
        use std::os::unix::fs::PermissionsExt;
        fs::create_dir_all(dir.join("logs")).unwrap();
        let script = dir.join("fake-python");
        fs::write(
            &script,
            format!("#!/bin/sh\necho '{{\"event\": \"ready\", \"port\": 4321}}'\n{body}\n"),
        )
        .unwrap();
        fs::set_permissions(&script, fs::Permissions::from_mode(0o755)).unwrap();
        LaunchConfig {
            python: script,
            data_dir: dir.to_path_buf(),
            env: vec![],
        }
    }

    fn wait_for(sup: &EngineSupervisor, pred: impl Fn(&EngineInfo) -> bool) -> EngineInfo {
        let deadline = Instant::now() + Duration::from_secs(8);
        loop {
            let info = sup.info();
            if pred(&info) || Instant::now() > deadline {
                return info;
            }
            thread::sleep(Duration::from_millis(50));
        }
    }

    #[test]
    fn becomes_ready_and_shuts_down() {
        let dir = std::env::temp_dir().join(format!("sj-ready-{}", std::process::id()));
        let sup = EngineSupervisor::new(fake_engine(&dir, "exec sleep 30"));
        sup.start();
        let info = wait_for(&sup, |i| i.status == "ready");
        assert_eq!(info.status, "ready");
        assert_eq!(info.base_url.as_deref(), Some("http://127.0.0.1:4321"));
        assert_eq!(info.token.len(), 64);
        sup.shutdown();
        assert_eq!(wait_for(&sup, |i| i.status == "stopped").status, "stopped");
        let _ = fs::remove_dir_all(dir);
    }

    #[test]
    fn restarts_after_crash() {
        let dir = std::env::temp_dir().join(format!("sj-crash-{}", std::process::id()));
        let sup = EngineSupervisor::new(fake_engine(&dir, "exit 3"));
        sup.start();
        let info = wait_for(&sup, |i| i.restarts >= 1);
        assert!(info.restarts >= 1);
        assert!(info.error.unwrap_or_default().contains("意外退出"));
        sup.shutdown();
        let _ = fs::remove_dir_all(dir);
    }

    #[test]
    fn missing_python_reports_failure() {
        let dir = std::env::temp_dir().join(format!("sj-missing-{}", std::process::id()));
        fs::create_dir_all(dir.join("logs")).unwrap();
        let config = LaunchConfig {
            python: dir.join("nope"),
            data_dir: dir.clone(),
            env: vec![],
        };
        let sup = EngineSupervisor::new(config);
        sup.start();
        let info = wait_for(&sup, |i| i.status == "failed");
        assert_eq!(info.status, "failed");
        assert!(info.error.unwrap_or_default().contains("无法启动引擎"));
        sup.shutdown();
        let _ = fs::remove_dir_all(dir);
    }
}
