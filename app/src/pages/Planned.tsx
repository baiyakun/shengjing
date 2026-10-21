// 还在开发阶段的应用：展示流程和所在阶段
import { Badge, Card, Page } from "../components";
import type { PageId } from "../nav";

export interface PlannedSpec {
  app: PageId;
  phase: string;
  steps: string[];
  models: string[];
}

export const PLANNED: Record<"dubbing" | "interpret", PlannedSpec> = {
  dubbing: {
    app: "dubbing",
    phase: "第 4 阶段",
    steps: [
      "从视频抽出音轨，分离人声和伴奏（HT-Demucs）",
      "转写、词级时间戳（Qwen3-ForcedAligner）、区分说话人（pyannote）",
      "LLM 翻译，并把每句译文控制在原句时长内",
      "用原说话人的声音按时长生成译文语音（Drama 3 / dots.tts）",
      "和伴奏混音，写回视频，导出双语字幕",
    ],
    models: ["HT-Demucs", "Qwen3-ASR", "Qwen3-ForcedAligner", "pyannote", "Fish Drama 3", "dots.tts"],
  },
  interpret: {
    app: "interpret",
    phase: "第 4 阶段",
    steps: [
      "麦克风实时转写（Confucius4-R2T2 / Nemotron）",
      "判断一句话说完后交给 LLM 翻译",
      "实时显示原文和译文字幕",
      "可选：用 dots.tts 流式朗读译文（语音合成接近实时，会有延迟）",
    ],
    models: ["Confucius4-R2T2", "Nemotron Streaming", "Streaming Sortformer", "dots.tts"],
  },
};

export function Planned({ spec }: { spec: PlannedSpec }) {
  return (
    <Page app={spec.app} narrow actions={<Badge tone="accent">开发中 · {spec.phase}</Badge>}>
      <Card title="工作流程" sub="这些能力在引擎和模型进程里已经备好接口，流程还在开发">
        <ol className="timeline">
          {spec.steps.map((s, i) => <li key={s} data-n={i + 1}>{s}</li>)}
        </ol>
      </Card>
      <Card title="会用到的模型">
        <div className="badges">{spec.models.map((m) => <Badge key={m}>{m}</Badge>)}</div>
      </Card>
    </Page>
  );
}
