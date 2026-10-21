// 与主引擎通信：连接信息、HTTP 请求、任务事件推送。
import { invoke } from "@tauri-apps/api/core";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

export const inTauri = typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;

export type TaskStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled";

export interface Task {
  id: string;
  kind: string;
  status: TaskStatus;
  params: Record<string, unknown>;
  // 各任务的结果结构不同，由各页面自己解读
  result: any;
  error: string | null;
  progress: number;
  message: string;
  created_at: number;
  updated_at: number;
}

export interface Voice {
  id: string;
  name: string;
  audio_path: string;
  transcript: string;
  language: string;
  tags: string[];
  source: string;
  created_at: number;
}

export interface ModelInfo {
  id: string;
  name: string;
  kind: string;
  worker: string;
  url: string;
  license: string;
  commercial: "yes" | "restricted" | "no";
  memory_gb: number;
  measured: boolean;
  languages: string[];
  capabilities: string[];
  license_note: string;
  notes: string;
  needs_conversion: boolean;
  allowed: boolean;
  default: boolean;
  worker_installed: boolean;
}

export interface WorkerStatus {
  name: string;
  installed: boolean;
  unavailable_reason: string;
  running: boolean;
  loaded: string[];
  pinned: string[];
}

export interface SystemInfo {
  chip: string;
  memory_total_gb: number;
  memory_budget_gb: number;
  memory_tier: number;
  edition: "personal" | "commercial";
  ffmpeg: boolean;
  llm_default: string;
  scheduler: { budget_gb: number; used_gb: number; workers: WorkerStatus[] };
}

interface EngineInfo {
  status: "starting" | "ready" | "restarting" | "failed" | "stopped";
  baseUrl: string | null;
  token: string;
  error: string | null;
  restarts: number;
}

export class EngineError extends Error {}

export class Engine {
  constructor(
    readonly baseUrl: string,
    private readonly token: string,
  ) {}

  async request<T>(method: string, path: string, body?: unknown): Promise<T> {
    const resp = await fetch(this.baseUrl + path, {
      method,
      headers: {
        Authorization: `Bearer ${this.token}`,
        ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
    const text = await resp.text();
    const data = text ? JSON.parse(text) : null;
    if (!resp.ok) {
      const detail = data?.detail ?? data?.error?.message ?? `HTTP ${resp.status}`;
      throw new EngineError(typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    return data as T;
  }

  get<T>(path: string) {
    return this.request<T>("GET", path);
  }
  post<T>(path: string, body: unknown = {}) {
    return this.request<T>("POST", path, body);
  }
  put<T>(path: string, body: unknown) {
    return this.request<T>("PUT", path, body);
  }
  del<T>(path: string) {
    return this.request<T>("DELETE", path);
  }

  submit(kind: string, params: Record<string, unknown>) {
    return this.post<Task>("/tasks", { kind, params });
  }

  fileUrl(path: string) {
    return `${this.baseUrl}/files?path=${encodeURIComponent(path)}&token=${this.token}`;
  }

  eventsUrl() {
    return `${this.baseUrl.replace(/^http/, "ws")}/events?token=${this.token}`;
  }
}

async function fetchEngineInfo(): Promise<EngineInfo> {
  if (inTauri) {
    return invoke<EngineInfo>("engine_info");
  }
  // 不在 Tauri 里（只用 pnpm dev 调界面）：从环境变量读手动启动的引擎地址
  const baseUrl = import.meta.env.VITE_ENGINE_URL as string | undefined;
  const token = (import.meta.env.VITE_ENGINE_TOKEN as string | undefined) ?? "";
  return {
    status: baseUrl ? "ready" : "failed",
    baseUrl: baseUrl ?? null,
    token,
    error: baseUrl ? null : "没有 VITE_ENGINE_URL，请在 Tauri 里运行，或手动启动引擎",
    restarts: 0,
  };
}

interface EngineState {
  engine: Engine | null;
  info: EngineInfo | null;
  tasks: Record<string, Task>;
  restart: () => Promise<void>;
}

const EngineContext = createContext<EngineState | null>(null);

export function EngineProvider({ children }: { children: ReactNode }) {
  const [info, setInfo] = useState<EngineInfo | null>(null);
  const [tasks, setTasks] = useState<Record<string, Task>>({});

  // 轮询外壳报告的引擎状态（引擎崩溃时外壳会自动重启它，端口和令牌可能变化）
  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        const next = await fetchEngineInfo();
        if (alive) {
          setInfo((prev) =>
            prev &&
            prev.status === next.status &&
            prev.baseUrl === next.baseUrl &&
            prev.token === next.token &&
            prev.error === next.error
              ? prev
              : next,
          );
        }
      } catch (err) {
        if (alive) {
          setInfo({ status: "failed", baseUrl: null, token: "", error: String(err), restarts: 0 });
        }
      }
    };
    tick();
    const timer = setInterval(tick, 1500);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  const engine = useMemo(
    () => (info?.status === "ready" && info.baseUrl ? new Engine(info.baseUrl, info.token) : null),
    [info?.status, info?.baseUrl, info?.token],
  );

  // 任务列表：先拉一次，之后靠 WebSocket 推送更新
  useEffect(() => {
    if (!engine) return;
    let ws: WebSocket | null = null;
    let closed = false;
    let retry: ReturnType<typeof setTimeout> | undefined;
    engine
      .get<Task[]>("/tasks?limit=200")
      .then((list) => setTasks(Object.fromEntries(list.map((t) => [t.id, t]))))
      .catch(() => undefined);
    const connect = () => {
      ws = new WebSocket(engine.eventsUrl());
      ws.onmessage = (msg) => {
        const event = JSON.parse(msg.data);
        if (event.type === "task") {
          setTasks((prev) => ({ ...prev, [event.task.id]: event.task }));
        }
      };
      ws.onclose = () => {
        if (!closed) retry = setTimeout(connect, 2000);
      };
    };
    connect();
    return () => {
      closed = true;
      if (retry) clearTimeout(retry);
      ws?.close();
    };
  }, [engine]);

  const restart = useCallback(async () => {
    if (inTauri) await invoke("restart_engine");
  }, []);

  return (
    <EngineContext.Provider value={{ engine, info, tasks, restart }}>
      {children}
    </EngineContext.Provider>
  );
}

export function useEngineState(): EngineState {
  const ctx = useContext(EngineContext);
  if (!ctx) throw new Error("EngineProvider 缺失");
  return ctx;
}

/** 在已连接引擎的页面里使用。 */
export function useEngine(): Engine {
  const { engine } = useEngineState();
  if (!engine) throw new Error("引擎未就绪");
  return engine;
}

/** 提交任务并跟踪它的状态。 */
export function useTask() {
  const engine = useEngine();
  const { tasks } = useEngineState();
  const [taskId, setTaskId] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);

  const submit = useCallback(
    async (kind: string, params: Record<string, unknown>) => {
      setSubmitError(null);
      try {
        const task = await engine.submit(kind, params);
        setTaskId(task.id);
        return task;
      } catch (err) {
        setSubmitError(err instanceof Error ? err.message : String(err));
        return null;
      }
    },
    [engine],
  );

  const task = taskId ? (tasks[taskId] ?? null) : null;
  const busy = task !== null && (task.status === "queued" || task.status === "running");
  return {
    task,
    busy,
    submit,
    submitError,
    /** 跟踪一个由引擎自己创建的任务。 */
    track: (id: string) => setTaskId(id),
    reset: () => setTaskId(null),
  };
}

/** 加载一个接口的数据，提供 reload。 */
export function useResource<T>(path: string | null) {
  const engine = useEngine();
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const seq = useRef(0);

  const reload = useCallback(async () => {
    if (!path) return;
    const mine = ++seq.current;
    try {
      const value = await engine.get<T>(path);
      if (mine === seq.current) {
        setData(value);
        setError(null);
      }
    } catch (err) {
      if (mine === seq.current) setError(err instanceof Error ? err.message : String(err));
    }
  }, [engine, path]);

  useEffect(() => {
    reload();
  }, [reload]);

  return { data, error, reload, setData };
}
