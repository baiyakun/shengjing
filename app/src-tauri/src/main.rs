// 发布版不弹出终端窗口（只对 Windows 有意义，macOS 上无影响）
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    shengjing_lib::run()
}
