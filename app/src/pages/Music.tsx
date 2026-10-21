// 音乐：主题 → LLM 写歌词和风格 → 本地 ACE-Step 1.5 或在线服务（SenseAudio、ElevenLabs）生成
import { useState } from "react";
import {
  Field, ModelSelect, Page, Player, Section, TaskProgress, resolveModel,
} from "../components";
import { useResource, useTask, type ModelInfo } from "../engine";

export function Music() {
  const models = useResource<ModelInfo[]>("/catalog");
  const [model, setModel] = useState("");
  const current = resolveModel(models.data, ["music"], model);
  // 本地 ACE-Step 需要扩展包；在线 API 需要 API Key
  const installed = current ? current.worker_installed : undefined;
  const [theme, setTheme] = useState("");
  const [style, setStyle] = useState("");
  const [lyrics, setLyrics] = useState("");
  const [duration, setDuration] = useState(90);
  const [language, setLanguage] = useState("zh");
  const [instrumental, setInstrumental] = useState(false);
  const [batch, setBatch] = useState(2);
  const task = useTask();
  const result = task.task?.status === "succeeded" ? task.task.result : null;

  return (
    <Page title="音乐" subtitle="本地用 ACE-Step 1.5，也可以用 SenseAudio、ElevenLabs 的在线音乐服务。">
      {installed === false && (
        <p className="warn">
          {current?.worker === "cloud"
            ? `还没有填写 ${current.name} 的 API Key（在设置里填写）。`
            : "还没有安装音乐扩展包（开发时见 workers/music/README.md），也可以在设置里改用在线服务。"}
        </p>
      )}
      <Section>
        <Field label="模型">
          <ModelSelect models={models.data} kinds={["music"]} value={model} onChange={setModel} />
        </Field>
        <Field label="主题（可选）" hint="只填主题时，LLM 会写歌词和风格描述">
          <input value={theme} onChange={(e) => setTheme(e.target.value)} placeholder="例如：毕业季，和好朋友告别" />
        </Field>
        <Field label="风格描述" hint="曲风、乐器、人声、情绪，例如：温暖的民谣，木吉他，女声">
          <input value={style} onChange={(e) => setStyle(e.target.value)} />
        </Field>
        <label className="check">
          <input type="checkbox" checked={instrumental}
            onChange={(e) => setInstrumental(e.target.checked)} />
          纯音乐（不要人声）
        </label>
        {!instrumental && (
          <Field label="歌词（可选）" hint="用 [verse] [chorus] 等标记分段">
            <textarea rows={6} value={lyrics} onChange={(e) => setLyrics(e.target.value)} />
          </Field>
        )}
        <div className="grid">
          <Field label="时长（秒）">
            <input type="number" min={10} max={600} value={duration}
              onChange={(e) => setDuration(Number(e.target.value))} />
          </Field>
          <Field label="歌词语言">
            <select value={language} onChange={(e) => setLanguage(e.target.value)}>
              <option value="zh">中文</option>
              <option value="en">英文</option>
              <option value="ja">日文</option>
            </select>
          </Field>
          <Field label="一次生成几首">
            <input type="number" min={1} max={8} value={batch}
              onChange={(e) => setBatch(Number(e.target.value))} />
          </Field>
        </div>
        <button
          className="primary"
          disabled={task.busy || installed === false || (!theme && !style)}
          onClick={() =>
            task.submit("music.generate", {
              theme: theme || undefined, style, lyrics, duration, language, instrumental,
              batch_size: batch, model: model || undefined,
            })
          }
        >
          生成
        </button>
        <TaskProgress task={task.task} error={task.submitError} />
      </Section>
      {result && (
        <Section title={result.title || "结果"}>
          <p className="small muted">{result.style}</p>
          {result.files.map((f: string, i: number) => (
            <Player key={f} path={f} label={`版本 ${i + 1}`} />
          ))}
          {result.lyrics && <pre className="lyrics">{result.lyrics}</pre>}
        </Section>
      )}
    </Page>
  );
}
