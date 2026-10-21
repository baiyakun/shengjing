// 应用导航：每个页面的名称、说明、图标和颜色（侧边栏、首页、页面标题共用）
import { createContext, useContext } from "react";
import {
  BookOpen, Boxes, Clapperboard, House, Info, Languages, ListChecks, Music2, NotebookPen,
  Podcast, Settings, SlidersHorizontal, UserRound, Waves, type LucideIcon,
} from "lucide-react";

export type PageId =
  | "home" | "audiobook" | "podcast" | "music" | "sfx" | "voices" | "edit" | "meeting"
  | "interpret" | "dubbing" | "tasks" | "models" | "settings" | "about";

export interface AppMeta {
  id: PageId;
  label: string;
  desc: string;
  icon: LucideIcon;
  /** 图标块的颜色（参考 macOS 系统色） */
  tint: string;
  planned?: string;
  /** 只能在本机运行（没有在线服务），Intel 芯片的 Mac 上不能用 */
  localOnly?: boolean;
}

export const APPS: Record<PageId, AppMeta> = {
  home: { id: "home", label: "开始", desc: "所有应用和最近的任务", icon: House, tint: "#8E8E93" },
  audiobook: {
    id: "audiobook", label: "有声书", icon: BookOpen, tint: "#FF9500",
    desc: "小说章节一键变成多角色有声书，带音效和配乐",
  },
  podcast: {
    id: "podcast", label: "AI 播客", icon: Podcast, tint: "#AF52DE",
    desc: "把文章改写成双人对谈节目并配音",
  },
  music: {
    id: "music", label: "音乐", icon: Music2, tint: "#FF2D55",
    desc: "写歌词、生成带人声的歌曲或纯配乐",
  },
  sfx: {
    id: "sfx", label: "音效库", icon: Waves, tint: "#30B0C7",
    desc: "用一句话生成环境声和动作音效",
  },
  voices: {
    id: "voices", label: "声音分身", icon: UserRound, tint: "#007AFF",
    desc: "导入一段录音，克隆出你的声音",
  },
  edit: {
    id: "edit", label: "录音后期", icon: SlidersHorizontal, tint: "#34C759",
    desc: "改情绪、风格、语速，降噪，去静音", localOnly: true,
  },
  meeting: {
    id: "meeting", label: "会议纪要", icon: NotebookPen, tint: "#F5A30A",
    desc: "转写录音、区分发言人，生成纪要和待办",
  },
  interpret: {
    id: "interpret", label: "同声传译", icon: Languages, tint: "#32ADE6",
    desc: "实时字幕和翻译字幕", planned: "第 4 阶段",
  },
  dubbing: {
    id: "dubbing", label: "翻译配音", icon: Clapperboard, tint: "#FF3B30",
    desc: "视频换成另一种语言，保留原声和背景音", planned: "第 4 阶段",
  },
  tasks: {
    id: "tasks", label: "任务中心", icon: ListChecks, tint: "#5856D6",
    desc: "所有生成任务按顺序排队执行",
  },
  models: {
    id: "models", label: "模型管理", icon: Boxes, tint: "#636366",
    desc: "本地模型、在线 API 和内存占用",
  },
  settings: { id: "settings", label: "设置", icon: Settings, tint: "#636366", desc: "" },
  about: { id: "about", label: "关于与许可", icon: Info, tint: "#636366", desc: "" },
};

export const NAV_GROUPS: { title: string | null; items: PageId[] }[] = [
  { title: null, items: ["home"] },
  { title: "创作", items: ["audiobook", "podcast", "music", "sfx"] },
  { title: "声音", items: ["voices", "edit"] },
  { title: "听写与视频", items: ["meeting", "interpret", "dubbing"] },
  { title: "系统", items: ["tasks", "models", "settings", "about"] },
];

/** 任务类型 → 显示名称和所属应用 */
export const TASK_KINDS: Record<string, { label: string; app: PageId }> = {
  "tts.quick": { label: "快速配音", app: "voices" },
  "sfx.generate": { label: "生成音效", app: "sfx" },
  "audio.edit": { label: "录音后期", app: "edit" },
  "voice.transcribe": { label: "音色文字稿转写", app: "voices" },
  "asr.transcribe": { label: "语音转文字", app: "meeting" },
  "meeting.summarize": { label: "会议纪要", app: "meeting" },
  "script.generate": { label: "生成剧本", app: "audiobook" },
  "audiobook.render": { label: "配音与混音", app: "audiobook" },
  "music.generate": { label: "生成音乐", app: "music" },
  "extension.install": { label: "安装扩展包", app: "models" },
};


/** 在页面之间跳转（首页卡片、任务链接等用） */
export const NavContext = createContext<(id: PageId) => void>(() => undefined);
export const useNavigate = () => useContext(NavContext);

// 从别的页面直接打开设置里的某个标签页（例如首页的“连接在线服务”）
let pendingSettingsTab: string | null = null;
export function openSettingsTab(tab: string) {
  pendingSettingsTab = tab;
}
export function takeSettingsTab(): string | null {
  const tab = pendingSettingsTab;
  pendingSettingsTab = null;
  return tab;
}
