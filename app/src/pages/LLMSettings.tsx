// 设置 → LLM 的云端部分：DeepSeek 一键填好，其他 OpenAI 兼容接口手动填，可以测试连接
import { CircleCheck, CircleX, ExternalLink, Plug } from "lucide-react";
import { useEffect, useState } from "react";
import { Button, Field, Segmented, Toggle } from "../components";
import { useEngine } from "../engine";

export interface CloudLLM {
  cloud_base_url: string;
  cloud_model: string;
  cloud_thinking: boolean;
}

export const DEEPSEEK_URL = "https://api.deepseek.com";
const DEEPSEEK_KEY_URL = "https://platform.deepseek.com/api_keys";
// DeepSeek 当前的模型名（2026 年 10 月核对）
const DEEPSEEK_MODELS = [
  { value: "deepseek-flash", label: "DeepSeek Flash", hint: "速度快、费用低，日常够用" },
  { value: "deepseek-v4-pro", label: "DeepSeek V4 Pro", hint: "更强，适合长篇剧本改编" },
];

export function isDeepSeek(url: string): boolean {
  try {
    const host = new URL(url.trim()).hostname.toLowerCase();
    return host === "deepseek.com" || host.endsWith(".deepseek.com");
  } catch {
    return false;
  }
}

type TestResult = { ok: true; reply: string; seconds: number; model: string }
  | { ok: false; error: string };

export function CloudLLMFields({ value, onChange, apiKey, setApiKey, keySaved }: {
  value: CloudLLM;
  onChange: (next: CloudLLM) => void;
  apiKey: string;
  setApiKey: (v: string) => void;
  keySaved: boolean;
}) {
  const engine = useEngine();
  const deepseek = isDeepSeek(value.cloud_base_url);
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<TestResult | null>(null);
  const knownModel = DEEPSEEK_MODELS.some((m) => m.value === value.cloud_model);

  // 还没填过云端 LLM 时，先按 DeepSeek 填好（保存后才生效）
  useEffect(() => {
    if (!value.cloud_base_url.trim() && !value.cloud_model.trim()) {
      onChange({ ...value, cloud_base_url: DEEPSEEK_URL, cloud_model: DEEPSEEK_MODELS[0].value });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const pick = (preset: "deepseek" | "custom") => {
    setResult(null);
    if (preset === "deepseek") {
      onChange({ ...value, cloud_base_url: DEEPSEEK_URL,
        cloud_model: knownModel ? value.cloud_model : DEEPSEEK_MODELS[0].value });
    } else if (deepseek) {
      onChange({ ...value, cloud_base_url: "", cloud_model: "" });
    }
  };

  const test = async () => {
    setTesting(true);
    setResult(null);
    try {
      setResult(await engine.post<TestResult>("/llm/test", {
        base_url: value.cloud_base_url, model: value.cloud_model,
        api_key: apiKey || undefined, thinking: value.cloud_thinking,
      }));
    } catch (err) {
      setResult({ ok: false, error: err instanceof Error ? err.message : String(err) });
    } finally {
      setTesting(false);
    }
  };

  const keyField = (
    <Field label="API Key" hint={
      <>
        {keySaved ? "已保存在钥匙串，留空不修改" : "保存在系统钥匙串"}
        {deepseek && (
          <> · <a href={DEEPSEEK_KEY_URL} target="_blank" rel="noreferrer">
            获取 DeepSeek API Key <ExternalLink size={11} /></a></>
        )}
      </>
    }>
      <input type="password" value={apiKey} placeholder={keySaved ? "已保存" : "sk-…"}
        onChange={(e) => setApiKey(e.target.value)} />
    </Field>
  );

  return (
    <>
      <Field label="服务商">
        <Segmented block value={deepseek ? "deepseek" : "custom"} onChange={pick} options={[
          { value: "deepseek", label: "DeepSeek" },
          { value: "custom", label: "其他 OpenAI 兼容接口" },
        ]} />
      </Field>

      {deepseek ? (
        <>
          <Field label="模型">
            <div className="choices">
              {DEEPSEEK_MODELS.map((m) => (
                <button key={m.value} type="button"
                  className={`choice ${m.value === value.cloud_model ? "on" : ""}`}
                  onClick={() => onChange({ ...value, cloud_model: m.value })}>
                  <b>{m.label}</b>
                  <small>{m.hint}</small>
                </button>
              ))}
            </div>
          </Field>
          {!knownModel && value.cloud_model && (
            <p className="small muted" style={{ margin: "-6px 0 12px" }}>
              现在填的是“{value.cloud_model}”，DeepSeek 已经换了模型名，建议选上面的模型。
            </p>
          )}
          <Toggle checked={value.cloud_thinking}
            onChange={(cloud_thinking) => onChange({ ...value, cloud_thinking })}
            label="深度思考" hint="先思考再回答，长篇改编更稳，但更慢、费用更高。关闭时更快更省" />
          <div style={{ marginTop: 12 }}>{keyField}</div>
        </>
      ) : (
        <>
          <Field label="接口地址"
            hint="任意 OpenAI 兼容的对话接口，例如通义千问、Kimi、硅基流动、OpenAI，填到 /v1 为止">
            <input value={value.cloud_base_url} placeholder="https://…/v1"
              onChange={(e) => onChange({ ...value, cloud_base_url: e.target.value })} />
          </Field>
          <div className="fields">
            <Field label="模型名">
              <input value={value.cloud_model}
                onChange={(e) => onChange({ ...value, cloud_model: e.target.value })} />
            </Field>
            {keyField}
          </div>
        </>
      )}

      <div className="btn-row" style={{ marginTop: 4 }}>
        <Button icon={Plug} loading={testing}
          disabled={!value.cloud_base_url.trim() || !value.cloud_model.trim()} onClick={test}>
          测试连接
        </Button>
        {result?.ok === true && (
          <span className="test-ok">
            <CircleCheck size={14} />连接成功 · {result.model} · {result.seconds} 秒
          </span>
        )}
      </div>
      {result?.ok === false && (
        <div className="task-error" style={{ marginTop: 10 }}>
          <CircleX size={15} /><span>{result.error}</span>
        </div>
      )}
    </>
  );
}
