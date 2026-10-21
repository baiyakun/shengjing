// 音效库：一句需求 → （可选）LLM 扩写成多条具体描述 → 批量生成
import { useState } from "react";
import { Field, Page, Player, Section, TaskProgress } from "../components";
import { useTask } from "../engine";

export function Sfx() {
  const [description, setDescription] = useState("雨夜，老旧木门被风吹开");
  const [duration, setDuration] = useState(5);
  const [variants, setVariants] = useState(3);
  const [expand, setExpand] = useState(true);
  const task = useTask();
  const items: { description: string; path: string; duration: number }[] =
    task.task?.status === "succeeded" ? task.task.result.items : [];

  return (
    <Page title="音效库" subtitle="用文字生成环境声和动作音效（MOSS-SoundEffect，不适合做音乐）。">
      <Section>
        <Field label="想要的音效">
          <textarea rows={2} value={description} onChange={(e) => setDescription(e.target.value)} />
        </Field>
        <div className="grid">
          <Field label="时长（秒）" hint="大致时长，不是精确值">
            <input type="number" min={1} max={30} value={duration}
              onChange={(e) => setDuration(Number(e.target.value))} />
          </Field>
          <Field label="生成几条">
            <input type="number" min={1} max={8} value={variants}
              onChange={(e) => setVariants(Number(e.target.value))} />
          </Field>
        </div>
        <label className="check">
          <input type="checkbox" checked={expand} onChange={(e) => setExpand(e.target.checked)} />
          让 LLM 把需求扩写成多条不同的具体描述
        </label>
        <button
          className="primary"
          disabled={!description.trim() || task.busy}
          onClick={() => task.submit("sfx.generate", { description, duration, variants, expand })}
        >
          生成
        </button>
        <TaskProgress task={task.task} error={task.submitError} />
      </Section>
      {items.length > 0 && (
        <Section title="结果">
          {items.map((it) => (
            <Player key={it.path} path={it.path} label={it.description} />
          ))}
        </Section>
      )}
    </Page>
  );
}
