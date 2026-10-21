mod engine;

use engine::{EngineInfo, EngineSupervisor, LaunchConfig};
use tauri::Manager;

/// 界面查询引擎的地址、令牌和状态（引擎重启后端口和令牌会变）。
#[tauri::command]
fn engine_info(state: tauri::State<'_, EngineSupervisor>) -> EngineInfo {
    state.info()
}

/// 手动重启引擎（引擎卡住或启动失败时）。
#[tauri::command]
fn restart_engine(state: tauri::State<'_, EngineSupervisor>) {
    state.restart();
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .setup(|app| {
            let config = LaunchConfig::resolve(app.handle())?;
            let supervisor = EngineSupervisor::new(config);
            supervisor.start();
            app.manage(supervisor);
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![engine_info, restart_engine])
        .build(tauri::generate_context!())
        .expect("创建 Tauri 应用失败");

    app.run(|handle, event| {
        if let tauri::RunEvent::Exit = event {
            // 退出 App 时让引擎优雅退出，引擎会先关闭它启动的所有模型进程
            if let Some(supervisor) = handle.try_state::<EngineSupervisor>() {
                supervisor.shutdown();
            }
        }
    });
}
