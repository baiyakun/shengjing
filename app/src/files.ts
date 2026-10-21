// 选择本机文件。引擎和界面在同一台机器上，直接传文件路径，不用上传。
import { open } from "@tauri-apps/plugin-dialog";
import { inTauri } from "./engine";

export const AUDIO_FILTER = {
  name: "音频",
  extensions: ["wav", "mp3", "m4a", "flac", "aac", "ogg", "aiff", "caf"],
};
export const MEDIA_FILTER = {
  name: "音频或视频",
  extensions: [...AUDIO_FILTER.extensions, "mp4", "mov", "mkv", "webm"],
};

export async function pickFile(filter = AUDIO_FILTER): Promise<string | null> {
  if (inTauri) {
    const chosen = await open({ multiple: false, directory: false, filters: [filter] });
    return typeof chosen === "string" ? chosen : null;
  }
  // 浏览器里调试界面时没有系统对话框，退回手动输入路径
  return window.prompt("输入文件的完整路径");
}
