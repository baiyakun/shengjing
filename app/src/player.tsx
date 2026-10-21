// 带波形的音频播放器。波形在浏览器里解码音频算出来，过长的音频（超过 15 分钟）只显示进度条。
import { Check, Copy, FolderOpen, Pause, Play } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { inTauri, useEngine } from "./engine";
import { revealFile } from "./files";

const MAX_WAVE_SECONDS = 900;
const peaksCache = new Map<string, number[]>();
let current: HTMLAudioElement | null = null;

function fmt(t: number) {
  if (!Number.isFinite(t)) return "0:00";
  const m = Math.floor(t / 60);
  const s = Math.floor(t % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

async function computePeaks(url: string, bars: number): Promise<number[]> {
  const resp = await fetch(url);
  const data = await resp.arrayBuffer();
  const Ctx = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext })
    .webkitAudioContext;
  const ctx = new Ctx();
  try {
    const buf = await ctx.decodeAudioData(data);
    const ch = buf.getChannelData(0);
    const step = Math.max(1, Math.floor(ch.length / bars));
    const peaks: number[] = [];
    let top = 0;
    for (let i = 0; i < bars; i++) {
      let m = 0;
      const end = Math.min(ch.length, (i + 1) * step);
      for (let j = i * step; j < end; j += 4) {
        const v = Math.abs(ch[j]);
        if (v > m) m = v;
      }
      peaks.push(m);
      if (m > top) top = m;
    }
    return peaks.map((p) => (top > 0 ? p / top : 0));
  } finally {
    ctx.close().catch(() => undefined);
  }
}

export function Player({ path, label, large }: { path: string; label?: string; large?: boolean }) {
  const engine = useEngine();
  const src = engine.fileUrl(path);
  const bars = large ? 140 : 72;
  const key = `${src}#${bars}`;
  const audio = useRef<HTMLAudioElement>(null);
  const [playing, setPlaying] = useState(false);
  const [time, setTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [peaks, setPeaks] = useState<number[] | null>(peaksCache.get(key) ?? null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    setPeaks(peaksCache.get(key) ?? null);
    setTime(0);
    setPlaying(false);
  }, [key]);

  const loadPeaks = (d: number) => {
    if (peaksCache.has(key) || !d || d > MAX_WAVE_SECONDS) return;
    computePeaks(src, bars)
      .then((p) => {
        peaksCache.set(key, p);
        setPeaks(p);
      })
      .catch(() => undefined);
  };

  const toggle = () => {
    const el = audio.current;
    if (!el) return;
    if (el.paused) {
      if (current && current !== el) current.pause();
      current = el;
      el.play().catch(() => undefined);
    } else {
      el.pause();
    }
  };

  const seek = (e: React.MouseEvent<HTMLDivElement>) => {
    const el = audio.current;
    if (!el || !duration) return;
    const r = e.currentTarget.getBoundingClientRect();
    el.currentTime = ((e.clientX - r.left) / r.width) * duration;
    setTime(el.currentTime);
  };

  // 没有波形时用均匀的短条代替，进度照常显示
  const shape = useMemo(() => peaks ?? Array.from({ length: bars }, () => 0.18), [peaks, bars]);
  const progress = duration ? time / duration : 0;

  return (
    <div className={`player ${large ? "lg" : ""}`}>
      {label && <div className="player-label">{label}</div>}
      <div className="player-row">
        <button type="button" className="play-btn" onClick={toggle} aria-label={playing ? "暂停" : "播放"}>
          {playing ? <Pause size={15} fill="currentColor" /> : <Play size={15} fill="currentColor" />}
        </button>
        <div className="wave" onClick={seek}>
          {shape.map((p, i) => (
            <span key={i} className={i / shape.length < progress ? "on" : ""}
              style={{ height: `${Math.max(8, p * 100)}%` }} />
          ))}
        </div>
        <span className="player-time">{fmt(time)} / {fmt(duration)}</span>
        <button type="button" className="btn ghost icon sm" title={`复制文件路径：${path}`}
          onClick={() => {
            navigator.clipboard?.writeText(path).then(() => {
              setCopied(true);
              setTimeout(() => setCopied(false), 1200);
            });
          }}>
          {copied ? <Check size={14} /> : <Copy size={14} />}
        </button>
        {inTauri && (
          <button type="button" className="btn ghost icon sm" title="在访达中显示"
            onClick={() => revealFile(path)}>
            <FolderOpen size={14} />
          </button>
        )}
        <audio
          ref={audio}
          src={src}
          preload="metadata"
          onLoadedMetadata={(e) => {
            const d = e.currentTarget.duration;
            setDuration(d);
            loadPeaks(d);
          }}
          onTimeUpdate={(e) => setTime(e.currentTarget.currentTime)}
          onPlay={() => setPlaying(true)}
          onPause={() => setPlaying(false)}
          onEnded={() => setPlaying(false)}
        />
      </div>
    </div>
  );
}
