// 还在开发阶段的应用：展示流程和所在阶段
import { Page, Section } from "../components";

export interface PlannedSpec {
  title: string;
  subtitle: string;
  phase: string;
  steps: string[];
  models: string[];
}

export const PLANNED: Record<string, PlannedSpec> = {
  dubbing: {
    title: "翻译配音",
    subtitle: "把视频配成另一种语言，保留原说话人的声音和背景音。",
    phase: "第 4 阶段",
    steps: [
      "从视频抽出音轨，分离人声和伴奏（HT-Demucs）",
      "转写、词级时间戳（Qwen3-ForcedAligner）、区分说话人（pyannote）",
      "LLM 翻译，并把每句译文控制在原句时长内",
      "用原说话人的声音按时长生成译文语音（Drama 3 / dots.tts）",
      "和伴奏混音，写回视频，导出双语字幕",
    ],
    models: ["htdemucs", "qwen3-asr-1.7b", "qwen3-forced-aligner", "pyannote-community-1",
      "fish-drama-3", "dots-tts-soar"],
  },
  interpret: {
    title: "同声传译",
    subtitle: "实时字幕和翻译字幕；语音播报作为可选项（语音合成接近实时，会有明显延迟）。",
    phase: "第 4 阶段",
    steps: [
      "麦克风实时转写（Confucius4-R2T2 / Nemotron）",
      "判断一句话说完后交给 LLM 翻译",
      "实时显示原文和译文字幕",
      "可选：用 dots.tts 流式朗读译文",
    ],
    models: ["confucius4-r2t2", "nemotron-asr-streaming", "sortformer-streaming-v2", "dots-tts-soar"],
  },
};

export function Planned({ spec }: { spec: PlannedSpec }) {
  return (
    <Page title={spec.title} subtitle={spec.subtitle}>
      <Section title={`开发中 · ${spec.phase}`}>
        <ol className="steps">
          {spec.steps.map((s) => (
            <li key={s}>{s}</li>
          ))}
        </ol>
        <p className="small muted">用到的模型：{spec.models.join("、")}</p>
      </Section>
    </Page>
  );
}
