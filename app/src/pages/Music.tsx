// 音乐：主题 → LLM 写歌词和风格 → 本地 ACE-Step 1.5 或在线服务（SenseAudio、ElevenLabs）生成
import { Music2, Sparkles } from "lucide-react";
import { useState } from "react";
import {
  Button, Callout, Card, Empty, Field, FitWarning, ModelSelect, Page, Player, Segmented,
  TaskProgress, resolveModel,
} from "../components";
import { useResource, useTask, type ModelInfo } from "../engine";

type Mode = "theme" | "custom" | "instrumental";

const STYLE_IDEAS = ["温暖的民谣，木吉他，女声", "Lo-fi 嘻哈，慵懒的钢琴", "热血摇滚，失真吉他，男声",
  "空灵的电子氛围，合成器"];

export function Music() {
  const models = useResource<ModelInfo[]>("/catalog");
  const [model, setModel] = useState("");
  const current = resolveModel(models.data, ["music"], model);
  const installed = current ? current.worker_installed : undefined;
  const [mode, setMode] = useState<Mode>("theme");
  const [theme, setTheme] = useState("");
  const [style, setStyle] = useState("");
  const [lyrics, setLyrics] = useState("");
  const [duration, setDuration] = useState(90);
  const [language, setLanguage] = useState("zh");
  const [batch, setBatch] = useState(2);
  const task = useTask();
  const result = task.task?.status === "succeeded" ? task.task.result : null;

  const ready = mode === "theme" ? Boolean(theme.trim()) : Boolean(style.trim());
  const submit = () => task.submit("music.generate", {
    theme: mode === "theme" ? theme : undefined,
    style,
    lyrics: mode === "custom" ? lyrics : "",
    instrumental: mode === "instrumental",
    duration, language, batch_size: batch, model: model || undefined,
  });

  return (
    <Page app="music">
      {installed === false && (
        <Callout>
          {current?.worker === "cloud"
            ? `还没有填写 ${current.name} 的 API Key，请到设置 → 在线 API 填写。`
            : "还没有安装音乐扩展包（ACE-Step）。可以在下方“模型”里换成在线服务，或按 workers/music/README.md 安装。"}
        </Callout>
      )}
      <div className="cols-main">
        <Card>
          <Field label="模型">
            <ModelSelect models={models.data} kinds={["music"]} value={model} onChange={setModel} />
          </Field>
          {installed !== false && <FitWarning model={current} />}
          <Field label="创作方式">
            <Segmented value={mode} onChange={setMode} block options={[
              { value: "theme", label: "给个主题" },
              { value: "custom", label: "自己写词" },
              { value: "instrumental", label: "纯音乐" },
            ]} />
          </Field>
          {mode === "theme" && (
            <Field label="主题" hint="LLM 会据此写歌词和风格描述">
              <input value={theme} onChange={(e) => setTheme(e.target.value)}
                placeholder="例如：毕业季，和好朋友在海边告别" />
            </Field>
          )}
          <Field label={mode === "theme" ? "风格（可选）" : "风格"}
            hint="曲风、乐器、人声、情绪">
            <input value={style} onChange={(e) => setStyle(e.target.value)} />
          </Field>
          <div className="chips" style={{ margin: "-6px 0 14px" }}>
            {STYLE_IDEAS.map((s) => (
              <button key={s} className={`chip ${s === style ? "on" : ""}`} onClick={() => setStyle(s)}>{s}</button>
            ))}
          </div>
          {mode === "custom" && (
            <Field label="歌词" hint="用 [verse] [chorus] 等标记分段，每行一句">
              <textarea rows={8} value={lyrics} onChange={(e) => setLyrics(e.target.value)}
                placeholder={"[verse]\n…\n\n[chorus]\n…"} />
            </Field>
          )}
          <div className="fields">
            <Field label={`时长：${duration} 秒`}>
              <input type="range" min={10} max={300} step={5} value={duration}
                onChange={(e) => setDuration(Number(e.target.value))} />
            </Field>
            {mode !== "instrumental" && (
              <Field label="歌词语言">
                <Segmented value={language} onChange={setLanguage} block options={[
                  { value: "zh", label: "中文" }, { value: "en", label: "英文" },
                  { value: "ja", label: "日文" },
                ]} />
              </Field>
            )}
            <Field label="一次生成几首">
              <Segmented value={batch} onChange={setBatch} block
                options={[1, 2, 3, 4].map((n) => ({ value: n, label: String(n) }))} />
            </Field>
          </div>
          <Button variant="primary" size="lg" icon={Sparkles} loading={task.busy}
            disabled={!ready || installed === false} onClick={submit}>
            生成音乐
          </Button>
          <TaskProgress task={task.task} error={task.submitError} />
        </Card>

        <Card title={result?.title || "作品"} sub={result?.style}>
          {result ? (
            <div className="stack">
              {result.files.map((f: string, i: number) => (
                <Player key={f} path={f} label={`版本 ${i + 1}`} />
              ))}
              {result.lyrics && <pre className="lyrics">{result.lyrics}</pre>}
            </div>
          ) : (
            <Empty icon={Music2} title="还没有作品">生成的歌曲会出现在这里。</Empty>
          )}
        </Card>
      </div>
    </Page>
  );
}
