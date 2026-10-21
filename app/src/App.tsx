import { useEffect, useState, type ReactNode } from "react";
import { EngineProvider, useEngineState, useResource, type SystemInfo } from "./engine";
import { Edit } from "./pages/Edit";
import { Meeting } from "./pages/Meeting";
import { Music } from "./pages/Music";
import { Planned, PLANNED } from "./pages/Planned";
import { Sfx } from "./pages/Sfx";
import { Studio } from "./pages/Studio";
import { About, Models, Settings, Tasks } from "./pages/System";
import { Voices } from "./pages/Voices";

interface NavItem {
  id: string;
  label: string;
  render: () => ReactNode;
  planned?: boolean;
}

const NAV: { group: string; items: NavItem[] }[] = [
  {
    group: "创作",
    items: [
      { id: "audiobook", label: "有声书", render: () => <Studio key="audiobook" style="audiobook" /> },
      { id: "podcast", label: "AI 播客", render: () => <Studio key="podcast" style="podcast" /> },
      { id: "music", label: "音乐", render: () => <Music /> },
      { id: "sfx", label: "音效库", render: () => <Sfx /> },
    ],
  },
  {
    group: "声音",
    items: [
      { id: "voices", label: "声音分身", render: () => <Voices /> },
      { id: "edit", label: "录音后期", render: () => <Edit /> },
    ],
  },
  {
    group: "听写",
    items: [
      { id: "meeting", label: "会议纪要", render: () => <Meeting /> },
      { id: "interpret", label: "同声传译", planned: true,
        render: () => <Planned spec={PLANNED.interpret} /> },
    ],
  },
  {
    group: "视频",
    items: [
      { id: "dubbing", label: "翻译配音", planned: true,
        render: () => <Planned spec={PLANNED.dubbing} /> },
    ],
  },
  {
    group: "系统",
    items: [
      { id: "tasks", label: "任务中心", render: () => <Tasks /> },
      { id: "models", label: "模型管理", render: () => <Models /> },
      { id: "settings", label: "设置", render: () => <Settings /> },
      { id: "about", label: "关于与许可", render: () => <About /> },
    ],
  },
];

const ALL = NAV.flatMap((g) => g.items);

function StatusBar() {
  const system = useResource<SystemInfo>("/system");
  // 进程会按需启动和关闭，定时刷新内存和进程状态
  useEffect(() => {
    const timer = setInterval(system.reload, 5000);
    return () => clearInterval(timer);
  }, [system.reload]);
  const { tasks } = useEngineState();
  const running = Object.values(tasks).filter((t) => t.status === "running").length;
  const queued = Object.values(tasks).filter((t) => t.status === "queued").length;
  const s = system.data;
  return (
    <div className="statusbar small">
      {s && (
        <>
          <span>{s.edition === "commercial" ? "商用版" : "个人版"}</span>
          <span>
            内存 {s.scheduler.used_gb.toFixed(1)} / {s.memory_budget_gb} GB
          </span>
          <span>
            进程：{s.scheduler.workers.filter((w) => w.running).map((w) => w.name).join("、") || "无"}
          </span>
        </>
      )}
      <span>
        任务：{running} 进行中 · {queued} 排队
      </span>
    </div>
  );
}

function Shell() {
  const { engine, info, restart } = useEngineState();
  const [current, setCurrent] = useState("audiobook");

  if (!engine) {
    return (
      <div className="splash">
        <h1>声境</h1>
        {info?.status === "failed" ? (
          <>
            <p className="error">引擎启动失败：{info.error}</p>
            <button onClick={restart}>重试</button>
          </>
        ) : (
          <p className="muted">
            {info?.status === "restarting" ? `引擎重启中（第 ${info.restarts} 次）…` : "引擎启动中…"}
          </p>
        )}
      </div>
    );
  }

  const page = ALL.find((i) => i.id === current) ?? ALL[0];
  return (
    <div className="app">
      <nav className="sidebar">
        <div className="brand">声境</div>
        {NAV.map((g) => (
          <div key={g.group} className="nav-group">
            <div className="nav-title">{g.group}</div>
            {g.items.map((item) => (
              <button
                key={item.id}
                className={`nav-item ${item.id === current ? "active" : ""}`}
                onClick={() => setCurrent(item.id)}
              >
                {item.label}
                {item.planned && <span className="soon">开发中</span>}
              </button>
            ))}
          </div>
        ))}
      </nav>
      <main className="main">
        {page.render()}
        <StatusBar />
      </main>
    </div>
  );
}

export function App() {
  return (
    <EngineProvider>
      <Shell />
    </EngineProvider>
  );
}
