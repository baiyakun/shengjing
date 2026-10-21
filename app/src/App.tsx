import { AudioWaveform, LoaderCircle, RotateCcw } from "lucide-react";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Button, Tile } from "./components";
import { EngineProvider, useEngineState, useResource, type SystemInfo } from "./engine";
import { activeDownloads } from "./downloads";
import { openExternal } from "./files";
import { APPS, NAV_GROUPS, NavContext, type PageId } from "./nav";
import { Edit } from "./pages/Edit";
import { Home } from "./pages/Home";
import { Meeting } from "./pages/Meeting";
import { Music } from "./pages/Music";
import { Planned, PLANNED } from "./pages/Planned";
import { Sfx } from "./pages/Sfx";
import { Studio } from "./pages/Studio";
import { About, Models, Settings, Tasks } from "./pages/System";
import { Voices } from "./pages/Voices";

const PAGES: Record<PageId, () => ReactNode> = {
  home: () => <Home />,
  audiobook: () => <Studio key="audiobook" style="audiobook" />,
  podcast: () => <Studio key="podcast" style="podcast" />,
  music: () => <Music />,
  sfx: () => <Sfx />,
  voices: () => <Voices />,
  edit: () => <Edit />,
  meeting: () => <Meeting />,
  interpret: () => <Planned spec={PLANNED.interpret} />,
  dubbing: () => <Planned spec={PLANNED.dubbing} />,
  tasks: () => <Tasks />,
  models: () => <Models />,
  settings: () => <Settings />,
  about: () => <About />,
};

const WORKER_LABEL: Record<string, string> = {
  speech: "语音", llm: "LLM", analysis: "分析", music: "音乐",
};

/** 侧边栏底部的本机状态：内存占用和正在运行的模型进程 */
function MachineCard({ onClick }: { onClick: () => void }) {
  const system = useResource<SystemInfo>("/system");
  useEffect(() => {
    const timer = setInterval(system.reload, 5000);
    return () => clearInterval(timer);
  }, [system.reload]);
  const { downloads } = useEngineState();
  const downloading = activeDownloads(downloads);
  const s = system.data;
  if (!s) return null;
  if (s.local.supported === false) {
    return (
      <div className="machine" onClick={onClick} title="打开模型管理">
        <div className="machine-head">
          <span>在线模式</span>
          <span>{s.local.apple_silicon ? "Intel 版本" : "Intel 芯片"}</span>
        </div>
        <div className="machine-workers small">
          <span className="faint">模型都在服务商那边运行</span>
        </div>
      </div>
    );
  }
  const used = s.scheduler.used_gb;
  const pct = Math.min(100, (used / Math.max(1, s.memory_budget_gb)) * 100);
  const running = s.scheduler.workers.filter((w) => w.running);
  return (
    <div className="machine" onClick={onClick} title="打开模型管理">
      <div className="machine-head">
        <span>模型内存</span>
        <span>{used.toFixed(1)} / {s.memory_budget_gb} GB</span>
      </div>
      <div className="gauge"><div style={{ width: `${pct}%` }} /></div>
      {downloading.length > 0 && (
        <div className="machine-dl">
          <div className="machine-head">
            <span>下载模型{downloading.length > 1 ? `（${downloading.length} 个）` : ""}</span>
            <span>{Math.round(downloading[0][1].fraction * 100)}%</span>
          </div>
          <div className="gauge downloading"><div style={{ width: `${downloading[0][1].fraction * 100}%` }} /></div>
        </div>
      )}
      <div className="machine-workers small">
        {running.length === 0 ? (
          <span className="faint">没有模型在运行</span>
        ) : (
          running.map((w) => (
            <span key={w.name} className="badge success">
              <span className="dot on" style={{ width: 6, height: 6 }} />
              {WORKER_LABEL[w.name] ?? w.name}
            </span>
          ))
        )}
      </div>
    </div>
  );
}

function Sidebar({ current, go }: { current: PageId; go: (id: PageId) => void }) {
  const { tasks } = useEngineState();
  const system = useResource<SystemInfo>("/system");
  const onlineOnly = system.data?.local.supported === false;
  const active = useMemo(
    () => Object.values(tasks).filter((t) => t.status === "queued" || t.status === "running").length,
    [tasks],
  );
  return (
    <nav className="sidebar">
      <div className="sidebar-drag" data-tauri-drag-region />
      <div className="brand" data-tauri-drag-region>
        <span className="brand-mark"><AudioWaveform size={17} strokeWidth={2.4} /></span>
        <div>
          <div className="brand-name">声境</div>
          <div className="brand-sub">本地 AI 声音工作室</div>
        </div>
      </div>
      <div className="nav">
        {NAV_GROUPS.map((g) => (
          <div key={g.title ?? "top"}>
            {g.title && <div className="nav-title">{g.title}</div>}
            {g.items.map((id) => {
              const meta = APPS[id];
              return (
                <button key={id} className={`nav-item ${id === current ? "active" : ""}`}
                  onClick={() => go(id)}>
                  <Tile app={id} />
                  <span className="nav-label">{meta.label}</span>
                  {id === "tasks" && active > 0 && <span className="nav-count">{active}</span>}
                  {meta.planned && <span className="nav-soon">开发中</span>}
                  {onlineOnly && meta.localOnly && <span className="nav-soon">需 M 芯片</span>}
                </button>
              );
            })}
          </div>
        ))}
      </div>
      <MachineCard onClick={() => go("models")} />
    </nav>
  );
}

function Splash() {
  const { info, restart } = useEngineState();
  const failed = info?.status === "failed";
  return (
    <div className="splash" data-tauri-drag-region>
      <div>
        <div className="brand-mark"><AudioWaveform size={32} strokeWidth={2.2} /></div>
        <h1>声境</h1>
        {failed ? (
          <>
            <p style={{ color: "var(--danger)", maxWidth: 460, margin: "0 auto 16px" }}>
              引擎启动失败：{info?.error}
            </p>
            <Button variant="primary" icon={RotateCcw} onClick={restart}>重试</Button>
          </>
        ) : (
          <p>
            <LoaderCircle size={14} className="spin" style={{ verticalAlign: -2, marginRight: 6 }} />
            {info?.status === "restarting" ? `引擎重启中（第 ${info.restarts} 次）…` : "正在启动引擎…"}
          </p>
        )}
      </div>
    </div>
  );
}

function Shell() {
  const { engine } = useEngineState();
  const [current, setCurrent] = useState<PageId>("home");
  // 页面里的网页链接（模型主页、接口文档、获取 API Key）交给系统浏览器打开
  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      const a = (e.target as HTMLElement | null)?.closest?.("a[href]");
      const href = a?.getAttribute("href") ?? "";
      if (/^https?:\/\//.test(href)) {
        e.preventDefault();
        openExternal(href);
      }
    };
    document.addEventListener("click", onClick);
    return () => document.removeEventListener("click", onClick);
  }, []);
  if (!engine) return <Splash />;
  return (
    <NavContext.Provider value={setCurrent}>
      <div className="app">
        <Sidebar current={current} go={setCurrent} />
        <main className="main">
          <div className="topbar" data-tauri-drag-region />
          <div className="scroll" key={current}>{PAGES[current]()}</div>
        </main>
      </div>
    </NavContext.Provider>
  );
}

export function App() {
  return (
    <EngineProvider>
      <Shell />
    </EngineProvider>
  );
}
