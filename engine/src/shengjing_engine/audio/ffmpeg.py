"""通过 ffmpeg 命令行解码、编码、转换音频。

发布版会在 App 里内置一个 LGPL 版本的 ffmpeg；开发时使用系统里的 ffmpeg。
"""

from __future__ import annotations

import re
import shutil
import subprocess
import wave
from pathlib import Path

import numpy as np

_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")


class FFmpegError(RuntimeError):
    pass


class FFmpeg:
    def __init__(self, binary: str | None = None) -> None:
        self.binary = binary or shutil.which("ffmpeg")

    @property
    def available(self) -> bool:
        return bool(self.binary)

    def _require(self) -> str:
        if not self.binary:
            raise FFmpegError("找不到 ffmpeg")
        return self.binary

    def _run(self, args: list[str], *, input_bytes: bytes | None = None) -> bytes:
        proc = subprocess.run(
            [self._require(), "-hide_banner", "-nostdin", "-v", "error", *args],
            input=input_bytes,
            capture_output=True,
            check=False,
        )
        if proc.returncode != 0:
            raise FFmpegError(proc.stderr.decode(errors="replace").strip()[-800:])
        return proc.stdout

    def decode(self, path: str | Path, *, sample_rate: int, channels: int) -> np.ndarray:
        """解码任意音视频文件，返回 float32 数组 [channels, samples]。"""
        raw = self._run(
            [
                "-i", str(path),
                "-vn",
                "-f", "f32le",
                "-acodec", "pcm_f32le",
                "-ac", str(channels),
                "-ar", str(sample_rate),
                "pipe:1",
            ]
        )
        data = np.frombuffer(raw, dtype="<f4")
        frames = data.size // channels
        return data[: frames * channels].reshape(frames, channels).T.copy()

    def encode(
        self,
        samples: np.ndarray,
        sample_rate: int,
        out_path: str | Path,
        *,
        loudness_lufs: float | None = None,
        bitrate: str = "192k",
    ) -> Path:
        """把 [channels, samples] 数组编码成文件，格式由扩展名决定（wav/mp3/m4a/flac）。"""
        if samples.ndim == 1:
            samples = samples[None, :]
        channels = samples.shape[0]
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        interleaved = np.ascontiguousarray(samples.T, dtype="<f4").tobytes()
        args = ["-y", "-f", "f32le", "-ar", str(sample_rate), "-ac", str(channels), "-i", "pipe:0"]
        if loudness_lufs is not None:
            args += ["-af", f"loudnorm=I={loudness_lufs}:TP=-1.5:LRA=11"]
        args += ["-ar", str(sample_rate)]
        suffix = out.suffix.lower()
        if suffix == ".wav":
            args += ["-c:a", "pcm_s16le"]
        elif suffix == ".mp3":
            args += ["-c:a", "libmp3lame", "-b:a", bitrate]
        elif suffix in (".m4a", ".m4b", ".aac"):
            args += ["-c:a", "aac", "-b:a", bitrate]
        elif suffix == ".flac":
            args += ["-c:a", "flac"]
        else:
            raise FFmpegError(f"不支持的导出格式：{suffix}")
        self._run([*args, str(out)], input_bytes=interleaved)
        return out

    def to_wav(
        self, src: str | Path, dst: str | Path, *, sample_rate: int = 16000, channels: int = 1
    ) -> Path:
        """转成 16 kHz 单声道 WAV（转写、说话人区分、时间戳都用这个格式）。"""
        dst = Path(dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        self._run(
            [
                "-y", "-i", str(src), "-vn",
                "-ac", str(channels), "-ar", str(sample_rate),
                "-c:a", "pcm_s16le", str(dst),
            ]
        )
        return dst

    def to_wav_native(self, src: str | Path, dst: str | Path) -> Path:
        """转成 WAV，保留原采样率和声道（保存在线服务返回的 mp3 等格式用）。"""
        dst = Path(dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        self._run(["-y", "-i", str(src), "-vn", "-c:a", "pcm_s16le", str(dst)])
        return dst

    def to_mp3(
        self,
        src: str | Path,
        dst: str | Path,
        *,
        sample_rate: int = 16000,
        bitrate: str = "64k",
        max_seconds: float | None = None,
    ) -> Path:
        """转成体积小的单声道 mp3（上传给在线服务用）。"""
        dst = Path(dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        args = ["-y", "-i", str(src), "-vn"]
        if max_seconds is not None:
            args += ["-t", str(max_seconds)]
        args += ["-ac", "1", "-ar", str(sample_rate), "-c:a", "libmp3lame", "-b:a", bitrate,
                 str(dst)]
        self._run(args)
        return dst

    def duration(self, path: str | Path) -> float:
        path = Path(path)
        if path.suffix.lower() == ".wav":
            try:
                return wav_duration(path)
            except (wave.Error, EOFError):
                pass
        proc = subprocess.run(
            [self._require(), "-hide_banner", "-nostdin", "-i", str(path)],
            capture_output=True,
            check=False,
        )
        match = _DURATION_RE.search(proc.stderr.decode(errors="replace"))
        if not match:
            raise FFmpegError(f"无法读取时长：{path}")
        h, m, s = match.groups()
        return int(h) * 3600 + int(m) * 60 + float(s)


def wav_duration(path: str | Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / float(w.getframerate())


def read_wav_mono(path: str | Path) -> tuple[np.ndarray, int]:
    """读取 16-bit PCM WAV 为单声道 float32（不依赖 ffmpeg）。"""
    with wave.open(str(path), "rb") as w:
        sr = w.getframerate()
        ch = w.getnchannels()
        width = w.getsampwidth()
        frames = w.readframes(w.getnframes())
    if width != 2:
        raise ValueError("只支持 16-bit PCM WAV")
    data = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    if ch > 1:
        data = data.reshape(-1, ch).mean(axis=1)
    return data, sr


def write_wav(path: str | Path, samples: np.ndarray, sample_rate: int) -> Path:
    """写 16-bit PCM WAV。samples 为 [samples] 或 [channels, samples]。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if samples.ndim == 1:
        samples = samples[None, :]
    pcm = (np.clip(samples.T, -1.0, 1.0) * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(samples.shape[0])
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm.tobytes())
    return path
