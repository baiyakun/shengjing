// 音效库：一句需求 → （可选）LLM 扩写成多条具体描述 → 批量生成
import { Sparkles, Waves } from "lucide-react";
import { useState } from "react";
import {
  Button, Card, Empty, Field, ModelSelect, Page, Player, Segmented, TaskProgress, Toggle,
} from "../components";
import { useResource, useTask, type ModelInfo } from "../engine";

const IDEAS = ["雨夜，老旧木门被风吹开", "清晨公园，鸟鸣和远处的狗叫", "科幻飞船引擎低沉轰鸣",
  "咖啡馆里杯碟碰撞和人声", "雪地里缓慢的脚步声", "篝火噼啪作响"];

export function Sfx() {
  const [description, setDescription] = useState("");
  const [duration, setDuration] = useState(5);
  const [variants, setVariants] = useState(3);
  const [expand, setExpand] = useState(true);
  const [model, setModel] = useState("");
  const models = useResource<ModelInfo[]>("/catalog");
  const task = useTask();
  const items: { description: string; path: string; duration: number }[] =
    task.task?.status === "succeeded" ? task.task.result.items : [];

  return (
    <Page app="sfx">
      <Card>
        <Field label="想要什么声音">
          <textarea rows={3} value={description} onChange={(e) => setDescription(e.target.value)}
            placeholder="描述声源、环境和节奏，例如：空旷的走廊里，木门缓慢打开，铰链吱呀作响" />
        </Field>
        <div className="chips" style={{ margin: "-6px 0 16px" }}>
          {IDEAS.map((s) => (
            <button key={s} className={`chip ${s === description ? "on" : ""}`} onClick={() => setDescription(s)}>{s}</button>
          ))}
        </div>
        <div className="fields">
          <Field label="模型">
            <ModelSelect models={models.data} kinds={["sfx"]} value={model} onChange={setModel} />
          </Field>
          <Field label={`时长：约 ${duration} 秒`}>
            <input type="range" min={1} max={30} value={duration}
              onChange={(e) => setDuration(Number(e.target.value))} />
          </Field>
          <Field label="生成几条">
            <Segmented value={variants} onChange={setVariants} block
              options={[1, 2, 3, 4].map((n) => ({ value: n, label: String(n) }))} />
          </Field>
        </div>
        <Toggle checked={expand} onChange={setExpand} label="让 LLM 扩写描述"
          hint="把一句需求扩写成几条不同的具体描述，结果更多样（需要 LLM）" />
        <div className="btn-row" style={{ marginTop: 10 }}>
          <Button variant="primary" size="lg" icon={Sparkles} loading={task.busy}
            disabled={!description.trim()}
            onClick={() => task.submit("sfx.generate", {
              description, duration, variants, expand, model: model || undefined,
            })}>
            生成音效
          </Button>
        </div>
        <TaskProgress task={task.task} error={task.submitError} />
      </Card>

      {items.length > 0 ? (
        <div className="result-grid">
          {items.map((it, i) => (
            <Card key={it.path} title={`第 ${i + 1} 条`} sub={it.description}>
              <Player path={it.path} />
            </Card>
          ))}
        </div>
      ) : (
        !task.task && <Empty icon={Waves} title="还没有音效">从上面的例子开始试试。</Empty>
      )}
    </Page>
  );
}
