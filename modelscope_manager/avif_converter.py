from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .storage import APP_DIR


@dataclass(frozen=True)
class AvifOptions:
    quality: int = 70
    speed: int = 5
    chroma: str = "auto"
    bit_depth: str = "auto"
    keep_metadata: bool = True

    def validate(self) -> None:
        if not 1 <= self.quality <= 100:
            raise ValueError("AVIF 质量必须在 1 到 100 之间")
        if not 0 <= self.speed <= 10:
            raise ValueError("AVIF 编码速度必须在 0 到 10 之间")
        if self.chroma not in {"auto", "444", "422", "420"}:
            raise ValueError("AVIF 色度采样无效")
        if self.bit_depth not in {"auto", "8", "10", "12"}:
            raise ValueError("AVIF 位深无效")


def find_ffmpeg() -> Path | None:
    bundled = APP_DIR / "embedded-tools" / "ffmpeg" / "ffmpeg.exe"
    if bundled.is_file():
        return bundled
    executable = shutil.which("ffmpeg")
    return Path(executable).resolve() if executable else None


def quality_to_crf(quality: int) -> int:
    if quality >= 100:
        return 0
    return min(63, max(0, round((100 - quality) * 23 / 30)))


def pixel_format(options: AvifOptions) -> str:
    chroma = "420" if options.chroma == "auto" else options.chroma
    depth = "10" if options.bit_depth == "auto" else options.bit_depth
    return f"yuv{chroma}p" if depth == "8" else f"yuv{chroma}p{depth}le"


def build_ffmpeg_command(ffmpeg: Path, source: Path, output: Path, options: AvifOptions) -> list[str]:
    options.validate()
    command = [
        str(ffmpeg), "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        "-threads", "1", "-filter_threads", "1", "-filter_complex_threads", "1",
        "-i", str(source), "-frames:v", "1", "-an", "-sn", "-dn",
        "-map_metadata", "0" if options.keep_metadata else "-1",
        "-vf", "scale=in_range=pc:out_range=pc:out_color_matrix=bt709",
        "-pix_fmt", pixel_format(options), "-c:v", "libaom-av1",
        "-usage", "allintra", "-still-picture", "1", "-cpu-used", str(options.speed),
        "-crf", str(quality_to_crf(options.quality)), "-b:v", "0", "-lag-in-frames", "0",
        "-row-mt", "1", "-aom-params", "tune=iq", "-color_range", "pc",
        "-color_primaries", "bt709", "-color_trc", "iec61966-2-1",
        "-colorspace", "bt709", "-f", "avif", str(output),
    ]
    return command


def convert_to_avif(source: Path, ffmpeg: Path, options: AvifOptions, timeout: int = 600) -> Path:
    options.validate()
    if source.suffix.lower() == ".avif":
        return source
    with tempfile.NamedTemporaryFile(prefix="modelscope-avif-", suffix=".avif", delete=False) as handle:
        output = Path(handle.name).resolve()
    output.unlink(missing_ok=True)
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        completed = subprocess.run(
            build_ffmpeg_command(ffmpeg, source, output, options),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            creationflags=creationflags,
        )
        if completed.returncode != 0 or not output.is_file() or output.stat().st_size == 0:
            message = completed.stderr.strip() or f"FFmpeg 退出码 {completed.returncode}"
            raise RuntimeError(f"AVIF 转换失败：{message}")
        return output
    except Exception:
        output.unlink(missing_ok=True)
        raise
