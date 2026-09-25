#!/usr/bin/env python
"""Download Bilibili DASH media with consistent network routing and integrity gates."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Iterable

from video_path_utils import normalize_queue_data

SHARED_SCRIPTS = Path(__file__).resolve().parents[3] / "_shared" / "scripts"
if str(SHARED_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SHARED_SCRIPTS))
from media_tools import media_command


UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36"
MIN_IMPORT_AUDIO_BANDWIDTH = 96_000
DEFAULT_MAX_DURATION_SEC = 3_000
DEFAULT_PER_VIDEO_TIMEOUT_SEC = 0
DEFAULT_CDN_PROBE_BYTES = 2 * 1024 * 1024
DEFAULT_CDN_PROBE_TIMEOUT_SEC = 8
FFMPEG_DECODE_TIMEOUT_SEC = 900
FFMPEG_MERGE_TIMEOUT_SEC = 900
DEFAULT_ASCII_STAGING_ROOT = Path("tmp/downloads/video-download-ascii-staging")
URL_RE = re.compile(r"https?://[^\s'\"<>]+", re.IGNORECASE)
CONTENT_RANGE_RE = re.compile(r"content-range:\s*bytes\s+(\d+)-(\d+)/(\d+|\*)", re.IGNORECASE)


class PerVideoDeadlineExceeded(RuntimeError):
    def __init__(self, stage: str):
        super().__init__(f"per-video download deadline exceeded during {stage}")
        self.stage = stage


class CdnProbeError(RuntimeError):
    def __init__(self, kind: str, probes: list[dict]):
        super().__init__(f"all {kind} CDN mirrors failed Range probing")
        self.kind = kind
        self.probes = probes


def headers(bvid: str) -> dict[str, str]:
    return {
        "User-Agent": UA,
        "Referer": f"https://www.bilibili.com/video/{bvid}",
        "Origin": "https://www.bilibili.com",
    }


def remaining_seconds(deadline: float | None, stage: str, cap: float | None = None) -> float | None:
    if deadline is None:
        return cap
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise PerVideoDeadlineExceeded(stage)
    return min(remaining, cap) if cap is not None else remaining


def system_proxy_url() -> str | None:
    proxies = urllib.request.getproxies()
    return proxies.get("https") or proxies.get("http")


def redact_proxy_url(proxy_url: str | None) -> str | None:
    if not proxy_url:
        return None
    parsed = urllib.parse.urlsplit(proxy_url)
    host = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port else ""
    return urllib.parse.urlunsplit((parsed.scheme, host + port, parsed.path, "", ""))


def redact_url(url: str) -> str:
    try:
        parsed = urllib.parse.urlsplit(url)
        host = parsed.hostname or ""
        port = f":{parsed.port}" if parsed.port else ""
        return urllib.parse.urlunsplit((parsed.scheme, host + port, parsed.path, "", ""))
    except ValueError:
        return "<redacted-url>"


def sanitize_status_text(value: object, proxy_url: str | None = None) -> str:
    text = str(value)
    if proxy_url:
        text = text.replace(proxy_url, redact_proxy_url(proxy_url) or "<redacted-proxy>")
    return URL_RE.sub(lambda match: redact_url(match.group(0)), text)


def resolve_network_context(mode: str, explicit_proxy_url: str | None = None) -> dict:
    if mode == "direct":
        return {"mode": mode, "proxy_url": None}
    if mode == "system-proxy":
        proxy_url = system_proxy_url()
        if not proxy_url:
            raise RuntimeError("system-proxy requested but no HTTP/HTTPS system proxy was found")
        return {"mode": mode, "proxy_url": proxy_url}
    if mode == "explicit-proxy":
        if not explicit_proxy_url:
            raise RuntimeError("explicit-proxy requires --proxy-url")
        return {"mode": mode, "proxy_url": explicit_proxy_url}
    raise ValueError(f"unknown network mode: {mode}")


def network_mode_sequence(mode: str, fallback: str, explicit_proxy_url: str | None = None) -> list[dict]:
    contexts = [resolve_network_context(mode, explicit_proxy_url)]
    if fallback == "system-proxy" and mode != "system-proxy":
        try:
            fallback_context = resolve_network_context("system-proxy")
            if fallback_context.get("proxy_url") != contexts[0].get("proxy_url"):
                contexts.append(fallback_context)
        except RuntimeError:
            pass
    return contexts


def urllib_proxy_mapping(network: dict) -> dict[str, str]:
    proxy_url = network.get("proxy_url")
    return {"http": proxy_url, "https": proxy_url} if proxy_url else {}


def curl_network_args(network: dict) -> list[str]:
    proxy_url = network.get("proxy_url")
    return ["--proxy", proxy_url] if proxy_url else ["--noproxy", "*"]


def get_json(url: str, bvid: str, network: dict, deadline: float | None = None) -> dict:
    req = urllib.request.Request(url, headers=headers(bvid))
    opener = urllib.request.build_opener(urllib.request.ProxyHandler(urllib_proxy_mapping(network)))
    timeout = remaining_seconds(deadline, "api", 25) or 25
    deadline_limited = deadline is not None and timeout < 25
    try:
        with opener.open(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except TimeoutError:
        if deadline_limited:
            raise PerVideoDeadlineExceeded("api")
        raise


def resolve_view_data(bvid: str, network: dict, deadline: float | None = None) -> dict:
    url = "https://api.bilibili.com/x/web-interface/view?" + urllib.parse.urlencode({"bvid": bvid})
    data = get_json(url, bvid, network, deadline)
    if data.get("code") != 0:
        raise RuntimeError(f"view failed for {bvid}: code={data.get('code')} message={data.get('message')}")
    return data["data"]


def resolve_cid(bvid: str, network: dict, deadline: float | None = None) -> int:
    return int(resolve_view_data(bvid, network, deadline)["cid"])


def fetch_playurl(
    bvid: str,
    cid: int,
    network: dict,
    qn: int = 80,
    fourk: int = 1,
    deadline: float | None = None,
) -> dict:
    params = {"bvid": bvid, "cid": cid, "qn": qn, "fnval": 4048, "fourk": fourk}
    url = "https://api.bilibili.com/x/player/playurl?" + urllib.parse.urlencode(params)
    data = get_json(url, bvid, network, deadline)
    if data.get("code") != 0:
        raise RuntimeError(f"playurl failed for {bvid}: code={data.get('code')} message={data.get('message')}")
    return data["data"]


def stream_urls(stream: dict) -> list[str]:
    urls = [stream.get("base_url") or stream.get("baseUrl")]
    urls.extend(stream.get("backup_url") or stream.get("backupUrl") or [])
    return list(dict.fromkeys(url for url in urls if url))


def video_candidates(dash: dict, prefer_low_bitrate: bool = False) -> list[dict]:
    videos = dash.get("video") or []
    avc1 = [video for video in videos if "avc1" in (video.get("codecs") or "")]
    pool = avc1 or videos
    if not pool:
        raise RuntimeError("no video streams")
    return sorted(pool, key=lambda video: video.get("bandwidth") or 0, reverse=not prefer_low_bitrate)


def audio_stream_summary(dash: dict) -> list[dict]:
    return [
        {"id": audio.get("id"), "bandwidth": audio.get("bandwidth"), "codecs": audio.get("codecs")}
        for audio in (dash.get("audio") or [])
    ]


def audio_candidates(
    dash: dict,
    min_bandwidth: int = MIN_IMPORT_AUDIO_BANDWIDTH,
    allow_low_audio: bool = False,
) -> list[dict]:
    audios = dash.get("audio") or []
    if not audios:
        raise RuntimeError("no audio streams")
    ordered = sorted(audios, key=lambda audio: audio.get("bandwidth") or 0, reverse=True)
    if allow_low_audio:
        return ordered
    high_quality = [audio for audio in ordered if (audio.get("bandwidth") or 0) >= min_bandwidth]
    if not high_quality:
        raise RuntimeError(
            f"no import-quality audio streams >= {min_bandwidth} bps; "
            f"available={audio_stream_summary(dash)}; --allow-low-audio is diagnostic-only"
        )
    return high_quality


def item_duration_sec(item: dict) -> float | None:
    value = item.get("duration_sec", item.get("duration"))
    if value is None or value == "":
        return None
    try:
        if isinstance(value, (int, float)):
            return float(value)
        text = str(value).strip()
        if ":" in text:
            total = 0
            for part in text.split(":"):
                total = total * 60 + int(part)
            return float(total)
        return float(text)
    except (TypeError, ValueError):
        return None


def is_codex_runtime() -> bool:
    return bool(os.environ.get("CODEX_SHELL") or os.environ.get("CODEX_THREAD_ID"))


def has_non_ascii_path(path: Path | str) -> bool:
    return any(ord(char) > 127 for char in str(path))


def should_use_ascii_staging(
    mode: str,
    out_dir: Path,
    work_dir: Path,
    *,
    platform_name: str | None = None,
    codex_runtime: bool | None = None,
) -> bool:
    if mode == "on":
        return True
    if mode == "off":
        return False
    if mode != "auto":
        raise ValueError(f"unknown ASCII staging mode: {mode}")
    platform_name = platform_name or os.name
    codex_runtime = is_codex_runtime() if codex_runtime is None else codex_runtime
    return platform_name == "nt" and codex_runtime and (
        has_non_ascii_path(out_dir) or has_non_ascii_path(work_dir)
    )


# Backward-compatible import surface for older callers.
should_use_codex_ascii_staging = should_use_ascii_staging


def make_staging_dir(root: Path) -> Path:
    stamp = time.strftime("%Y%m%d_%H%M%S")
    path = root / f"{stamp}_{uuid.uuid4().hex[:8]}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def clear_bvid_intermediate_media(work: Path, bvid: str) -> list[str]:
    """Prevent a fallback network mode from merging streams from an earlier mode."""
    removed: list[str] = []
    for path in work.glob(f"{bvid}*.m4s"):
        if path.is_file():
            path.unlink(missing_ok=True)
            removed.append(path.name)
    return removed


def run(cmd: list[str], timeout: float | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)


def run_for_stage(
    cmd: list[str],
    *,
    deadline: float | None,
    stage: str,
    cap: float | None = None,
) -> subprocess.CompletedProcess[str]:
    timeout = remaining_seconds(deadline, stage, cap)
    deadline_limited = deadline is not None and (cap is None or (timeout is not None and timeout < cap))
    try:
        return run(cmd, timeout=timeout)
    except subprocess.TimeoutExpired:
        if deadline_limited:
            raise PerVideoDeadlineExceeded(stage)
        raise


def probe_response_is_valid(
    http_status: int,
    content_range: str | None,
    downloaded_bytes: int,
    requested_bytes: int,
) -> bool:
    if http_status != 206 or not content_range:
        return False
    match = CONTENT_RANGE_RE.search(content_range)
    if not match:
        return False
    start, end = int(match.group(1)), int(match.group(2))
    expected = end - start + 1
    return start == 0 and 0 < expected <= requested_bytes and downloaded_bytes == expected


def probe_cdn_url(
    url: str,
    *,
    bvid: str,
    network: dict,
    work: Path,
    requested_bytes: int = DEFAULT_CDN_PROBE_BYTES,
    probe_timeout_sec: int = DEFAULT_CDN_PROBE_TIMEOUT_SEC,
    deadline: float | None = None,
    url_index: int = 0,
) -> dict:
    host = urllib.parse.urlsplit(url).hostname or ""
    token = uuid.uuid4().hex
    body_path = work / f"probe_{token}.bin"
    header_path = work / f"probe_{token}.headers"
    allowed = remaining_seconds(deadline, "cdn_probe", float(probe_timeout_sec)) or float(probe_timeout_sec)
    deadline_limited = deadline is not None and allowed < float(probe_timeout_sec)
    curl_limit = max(0.1, allowed - 0.2)
    cmd = [
        "curl", "-L", "--silent", "--show-error", "--range", f"0-{requested_bytes - 1}",
        "--connect-timeout", str(min(5.0, curl_limit)), "--max-time", str(curl_limit),
        "-D", str(header_path), "-o", str(body_path),
        "-H", f"User-Agent: {UA}", "-H", f"Referer: https://www.bilibili.com/video/{bvid}",
        "-H", "Origin: https://www.bilibili.com",
        *curl_network_args(network),
        "--write-out", "__PROBE__%{http_code}\t%{remote_ip}\t%{size_download}\t%{time_total}",
        url,
    ]
    record = {
        "host": host,
        "remote_ip": None,
        "url_index": url_index,
        "http_status": 0,
        "bytes": 0,
        "elapsed_sec": 0.0,
        "speed_bytes_sec": 0.0,
        "range_valid": False,
        "ok": False,
        "selected": False,
    }
    try:
        process = run(cmd, timeout=allowed)
        marker = re.search(r"__PROBE__(\d+)\t([^\t]*)\t([0-9.]+)\t([0-9.]+)", process.stdout or "")
        header_text = header_path.read_text(encoding="iso-8859-1", errors="replace") if header_path.exists() else ""
        ranges = CONTENT_RANGE_RE.findall(header_text)
        content_range = None
        if ranges:
            last = ranges[-1]
            content_range = f"Content-Range: bytes {last[0]}-{last[1]}/{last[2]}"
        if marker:
            record["http_status"] = int(marker.group(1))
            record["remote_ip"] = marker.group(2) or None
            record["bytes"] = int(float(marker.group(3)))
            record["elapsed_sec"] = round(float(marker.group(4)), 4)
        record["range_valid"] = probe_response_is_valid(
            record["http_status"], content_range, record["bytes"], requested_bytes
        )
        record["ok"] = process.returncode == 0 and record["range_valid"]
        if record["elapsed_sec"] > 0:
            record["speed_bytes_sec"] = round(record["bytes"] / record["elapsed_sec"], 2)
        if not record["ok"]:
            record["error"] = sanitize_status_text(process.stderr[-500:], network.get("proxy_url"))
    except subprocess.TimeoutExpired:
        if deadline_limited:
            raise PerVideoDeadlineExceeded("cdn_probe")
        record["error"] = f"probe timeout after {allowed:.1f}s"
    finally:
        body_path.unlink(missing_ok=True)
        header_path.unlink(missing_ok=True)
    return record


def probe_stream_urls(
    urls: list[str],
    *,
    bvid: str,
    network: dict,
    work: Path,
    requested_bytes: int,
    probe_timeout_sec: int,
    deadline: float | None,
) -> tuple[list[str], list[dict]]:
    pairs: list[tuple[str, dict]] = []
    for index, url in enumerate(urls):
        record = probe_cdn_url(
            url,
            bvid=bvid,
            network=network,
            work=work,
            requested_bytes=requested_bytes,
            probe_timeout_sec=probe_timeout_sec,
            deadline=deadline,
            url_index=index,
        )
        pairs.append((url, record))
    valid = sorted((pair for pair in pairs if pair[1].get("ok")), key=lambda pair: pair[1]["speed_bytes_sec"], reverse=True)
    if valid:
        valid[0][1]["selected"] = True
    return [url for url, _ in valid], [record for _, record in pairs]


def curl_download(
    urls: list[str],
    out: Path,
    bvid: str,
    network: dict,
    retry: int = 6,
    deadline: float | None = None,
) -> tuple[bool, str]:
    last = ""
    for url in urls:
        out.unlink(missing_ok=True)
        cmd = [
            "curl", "-L", "--fail", "--retry", str(retry), "--retry-delay", "2", "--retry-all-errors",
            "--connect-timeout", "20", "--speed-limit", "1024", "--speed-time", "120",
            "-H", f"User-Agent: {UA}", "-H", f"Referer: https://www.bilibili.com/video/{bvid}",
            "-H", "Origin: https://www.bilibili.com", *curl_network_args(network), "-o", str(out),
        ]
        timeout = remaining_seconds(deadline, "media_download")
        if timeout is not None:
            cmd.extend(["--max-time", str(max(0.1, timeout - 0.2))])
        cmd.append(url)
        try:
            process = run(cmd, timeout=timeout)
            last = sanitize_status_text((process.stdout + process.stderr)[-2000:], network.get("proxy_url"))
            if process.returncode == 0 and out.exists() and out.stat().st_size > 1024:
                return True, last
        except subprocess.TimeoutExpired:
            if deadline is not None:
                raise PerVideoDeadlineExceeded("media_download")
            last = "curl process timeout"
    return False, last


def stream_decodes_clean(
    path: Path,
    kind: str,
    deadline: float | None = None,
    stage: str = "decode",
) -> tuple[bool, str]:
    args = [media_command("ffmpeg"), "-v", "error", "-xerror", "-i", str(path)]
    if kind == "audio":
        args += ["-map", "0:a:0"]
    elif kind == "video":
        args += ["-map", "0:v:0"]
    else:
        raise ValueError(kind)
    args += ["-f", "null", "-"]
    process = run_for_stage(args, deadline=deadline, stage=stage, cap=FFMPEG_DECODE_TIMEOUT_SEC)
    if process.returncode == 0 and not process.stderr.strip():
        return True, ""
    if kind == "video" and "non monotonically increasing dts" in process.stderr.lower():
        retry_args = [media_command("ffmpeg"), "-v", "error", "-fflags", "+genpts", "-i", str(path), "-map", "0:v:0", "-f", "null", "-"]
        retry = run_for_stage(retry_args, deadline=deadline, stage=stage, cap=FFMPEG_DECODE_TIMEOUT_SEC)
        if retry.returncode == 0:
            return True, "accepted after +genpts DTS fallback\n" + retry.stderr[-1000:]
    return False, process.stderr[-2000:]


def download_checked(
    candidates: Iterable[dict],
    out: Path,
    bvid: str,
    kind: str,
    network: dict,
    work: Path,
    probe_bytes: int,
    probe_timeout_sec: int,
    reuse_existing: bool = True,
    deadline: float | None = None,
) -> tuple[dict, list[dict], list[dict]]:
    attempts: list[dict] = []
    probes: list[dict] = []
    if reuse_existing and out.exists() and out.stat().st_size > 1024:
        ok, log = stream_decodes_clean(out, kind, deadline, f"{kind}_decode")
        attempts.append({"kind": kind, "id": "existing", "ok": ok, "size": out.stat().st_size, "log": log[-500:]})
        if ok:
            return {"id": "existing"}, attempts, probes
    last_log = ""
    had_valid_probe = False
    for stream in candidates:
        ranked_urls, stream_probes = probe_stream_urls(
            stream_urls(stream),
            bvid=bvid,
            network=network,
            work=work,
            requested_bytes=probe_bytes,
            probe_timeout_sec=probe_timeout_sec,
            deadline=deadline,
        )
        for probe in stream_probes:
            probe.update({"kind": kind, "stream_id": stream.get("id"), "bandwidth": stream.get("bandwidth")})
        probes.extend(stream_probes)
        if not ranked_urls:
            attempts.append({"kind": kind, "id": stream.get("id"), "ok": False, "error": "no valid CDN Range probe"})
            continue
        had_valid_probe = True
        ok_download, log = curl_download(ranked_urls, out, bvid, network, deadline=deadline)
        ok_decode, decode_log = stream_decodes_clean(out, kind, deadline, f"{kind}_decode") if ok_download else (False, log)
        last_log = decode_log or log
        attempts.append({
            "kind": kind,
            "id": stream.get("id"),
            "codecs": stream.get("codecs"),
            "bandwidth": stream.get("bandwidth"),
            "ok": ok_decode,
            "size": out.stat().st_size if out.exists() else 0,
            "log": last_log[-500:],
        })
        if ok_decode:
            return stream, attempts, probes
        time.sleep(1)
    if not had_valid_probe:
        raise CdnProbeError(kind, probes)
    raise RuntimeError(f"{kind} download/decode failed for {bvid}: {last_log[-1000:]}")


def ffprobe_duration(path: Path, deadline: float | None = None) -> tuple[float, float, float]:
    process = run_for_stage(
        [media_command("ffprobe"), "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)],
        deadline=deadline,
        stage="final_probe",
        cap=60,
    )
    data = json.loads(process.stdout or "{}")
    fmt = float(data.get("format", {}).get("duration") or 0)
    videos = [stream for stream in data.get("streams", []) if stream.get("codec_type") == "video"]
    audios = [stream for stream in data.get("streams", []) if stream.get("codec_type") == "audio"]
    video_duration = float(videos[0].get("duration") or 0) if videos else 0.0
    audio_duration = float(audios[0].get("duration") or 0) if audios else 0.0
    return fmt, video_duration, audio_duration


def try_download_profile(
    item: dict,
    out_dir: Path,
    work: Path,
    fallback_up: str | None,
    profile: str,
    min_audio_bandwidth: int,
    allow_low_audio: bool,
    network: dict,
    probe_bytes: int,
    probe_timeout_sec: int,
    deadline: float | None = None,
) -> dict:
    bvid = item["bvid"]
    up = item.get("up") or fallback_up or "UP主"
    cid = int(item.get("cid") or resolve_cid(bvid, network, deadline))
    lowrate = profile == "lowrate"
    suffix = "_lowrate" if lowrate else ""
    video_file = work / f"{bvid}{suffix}_video.m4s"
    audio_file = work / f"{bvid}{suffix}_audio.m4s"
    out = out_dir / f"{up}_{bvid}.mp4"
    all_probes: list[dict] = []
    all_attempts: list[dict] = []
    for playurl_attempt in range(2):
        data = fetch_playurl(
            bvid,
            cid,
            network,
            qn=64 if lowrate else 80,
            fourk=0 if lowrate else 1,
            deadline=deadline,
        )
        dash = data.get("dash") or {}
        try:
            video_stream, video_attempts, video_probes = download_checked(
                video_candidates(dash, prefer_low_bitrate=lowrate),
                video_file,
                bvid,
                "video",
                network,
                work,
                probe_bytes,
                probe_timeout_sec,
                reuse_existing=not lowrate,
                deadline=deadline,
            )
            all_attempts.extend(video_attempts)
            all_probes.extend(video_probes)
            audio_stream, audio_attempts, audio_probes = download_checked(
                audio_candidates(dash, min_bandwidth=min_audio_bandwidth, allow_low_audio=allow_low_audio),
                audio_file,
                bvid,
                "audio",
                network,
                work,
                probe_bytes,
                probe_timeout_sec,
                reuse_existing=False,
                deadline=deadline,
            )
            all_attempts.extend(audio_attempts)
            all_probes.extend(audio_probes)
            break
        except CdnProbeError as exc:
            all_probes.extend(exc.probes)
            if playurl_attempt == 0:
                continue
            exc.probes = all_probes
            raise
    else:
        raise RuntimeError("unreachable playurl retry state")

    audio_policy = {
        "min_bandwidth": min_audio_bandwidth,
        "allow_low_audio": allow_low_audio,
        "manual_low_audio_not_import_quality": True,
        "available_streams": audio_stream_summary(dash),
    }
    out.unlink(missing_ok=True)
    merge_cmd = [media_command("ffmpeg"), "-y", "-v", "error", "-fflags", "+genpts", "-i", str(video_file), "-i", str(audio_file), "-c", "copy", str(out)]
    merge = run_for_stage(merge_cmd, deadline=deadline, stage="merge", cap=FFMPEG_MERGE_TIMEOUT_SEC)
    if merge.returncode != 0 and "non monotonically increasing dts" in merge.stderr.lower():
        merge_cmd = [
            media_command("ffmpeg"), "-y", "-v", "error", "-fflags", "+genpts", "-i", str(video_file), "-i", str(audio_file),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-c:a", "copy", str(out),
        ]
        merge = run_for_stage(merge_cmd, deadline=deadline, stage="merge", cap=FFMPEG_MERGE_TIMEOUT_SEC)
    if merge.returncode != 0:
        raise RuntimeError("merge failed: " + merge.stderr[-1000:])
    fmt, video_duration, audio_duration = ffprobe_duration(out, deadline)
    expected = item_duration_sec(item) or fmt
    clean, decode_log = stream_decodes_clean(out, "audio", deadline, "final_audio_decode")
    row = {
        "bvid": bvid,
        "output": str(out),
        "ok": True,
        "profile": profile,
        "format_duration": fmt,
        "video_duration": video_duration,
        "audio_duration": audio_duration,
        "duration_sec": item_duration_sec(item),
        "audio_decode_clean": clean,
        "audio_quality_policy": audio_policy,
        "audio_quality_acceptable": (audio_stream.get("bandwidth") or 0) >= min_audio_bandwidth,
        "video_stream_id": video_stream.get("id"),
        "audio_stream_id": audio_stream.get("id"),
        "audio_stream_bandwidth": audio_stream.get("bandwidth"),
        "attempts": all_attempts,
        "cdn_probe": all_probes,
    }
    if abs(fmt - expected) > 3 or abs(video_duration - expected) > 3 or abs(audio_duration - expected) > 3 or not clean:
        row["ok"] = False
        row["error"] = f"duration/decode gate failed; decode_log={decode_log[-500:]}"
    if not row["audio_quality_acceptable"]:
        diagnostic_out = work / f"{bvid}{suffix}_diagnostic_low_audio.mp4"
        out.replace(diagnostic_out)
        row["output"] = str(diagnostic_out)
        row["diagnostic_output"] = str(diagnostic_out)
        row["ok"] = False
        row["error"] = (
            f"audio stream below import-quality floor: id={audio_stream.get('id')} "
            f"bandwidth={audio_stream.get('bandwidth')} < {min_audio_bandwidth}"
        )
    return row


def staging_status(staging_root: Path, reason: str) -> dict:
    return {"enabled": True, "reason": reason, "staging_root": str(staging_root)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--videos", required=True, help="JSON object or array of video objects")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--up", default=None, help="fallback UP prefix for output MP4")
    parser.add_argument("--status", default="download_status.json")
    parser.add_argument("--no-auto-fallback", action="store_true", help="disable low-video-rate profile fallback")
    parser.add_argument("--min-audio-bandwidth", type=int, default=MIN_IMPORT_AUDIO_BANDWIDTH)
    parser.add_argument("--allow-low-audio", action="store_true", help="diagnostic only; never import low-audio output")
    parser.add_argument("--max-duration-sec", type=float, default=DEFAULT_MAX_DURATION_SEC)
    parser.add_argument("--per-video-timeout-sec", type=int, default=DEFAULT_PER_VIDEO_TIMEOUT_SEC)
    parser.add_argument("--allow-long-video", action="store_true")
    parser.add_argument("--network-mode", choices=["direct", "system-proxy", "explicit-proxy"], default="direct")
    parser.add_argument("--network-fallback", choices=["system-proxy", "none"], default="system-proxy")
    parser.add_argument("--proxy-url", default=None, help="used only with --network-mode explicit-proxy")
    parser.add_argument("--cdn-probe-bytes", type=int, default=DEFAULT_CDN_PROBE_BYTES)
    parser.add_argument("--cdn-probe-timeout-sec", type=int, default=DEFAULT_CDN_PROBE_TIMEOUT_SEC)
    parser.add_argument(
        "--ascii-staging", "--codex-ascii-staging", dest="ascii_staging",
        choices=["auto", "on", "off"], default="auto",
        help="ASCII media staging; auto enables only for Codex on Windows with non-ASCII paths",
    )
    parser.add_argument(
        "--staging-root", "--codex-staging-root", dest="staging_root",
        default=str(DEFAULT_ASCII_STAGING_ROOT),
    )
    args = parser.parse_args()
    if args.proxy_url and args.network_mode != "explicit-proxy":
        parser.error("--proxy-url is valid only with --network-mode explicit-proxy")
    if args.cdn_probe_bytes <= 0 or args.cdn_probe_timeout_sec <= 0:
        parser.error("CDN probe byte and timeout values must be positive")

    videos = normalize_queue_data(json.loads(Path(args.videos).read_text(encoding="utf-8-sig")))
    requested_out_dir = Path(args.out_dir)
    requested_work = Path(args.work_dir)
    requested_out_dir.mkdir(parents=True, exist_ok=True)
    requested_work.mkdir(parents=True, exist_ok=True)
    use_ascii_staging = should_use_ascii_staging(args.ascii_staging, requested_out_dir, requested_work)
    staging_root = make_staging_dir(Path(args.staging_root)) if use_ascii_staging else None
    out_dir = staging_root / "out" if staging_root else requested_out_dir
    work = staging_root / "work" if staging_root else requested_work
    out_dir.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    status_path = Path(args.status)
    status: list[dict] = []
    contexts = network_mode_sequence(args.network_mode, args.network_fallback, args.proxy_url)

    for source_item in videos:
        item = dict(source_item)
        bvid = item["bvid"]
        up = item.get("up") or args.up or "UP主"
        final_out = requested_out_dir / f"{up}_{bvid}.mp4"
        max_duration_sec = 0 if args.allow_long_video else args.max_duration_sec
        duration_sec = item_duration_sec(item)
        deadline = time.monotonic() + args.per_video_timeout_sec if args.per_video_timeout_sec > 0 else None
        row = {
            "bvid": bvid,
            "title": item["original_title"],
            "original_title": item["original_title"],
            "storage_title": item["storage_title"],
            "import_title": item["import_title"],
            "wiki_title": item["wiki_title"],
            "output": str(final_out),
            "duration_sec": duration_sec,
            "max_duration_sec": max_duration_sec or None,
            "audio_quality_policy": {
                "min_bandwidth": args.min_audio_bandwidth,
                "allow_low_audio": args.allow_low_audio,
                "manual_low_audio_not_import_quality": True,
            },
            "network_policy": {
                "requested_mode": args.network_mode,
                "fallback_mode": args.network_fallback,
                "actual_mode": None,
                "fallback_used": False,
                "attempts": [],
            },
        }
        if use_ascii_staging:
            reason = "Codex auto mode with non-ASCII Windows media path" if args.ascii_staging == "auto" else "explicit --ascii-staging=on"
            generic_status = staging_status(staging_root, reason)
            row["ascii_staging"] = generic_status
            row["codex_ascii_staging"] = dict(generic_status)
        all_probes: list[dict] = []
        try:
            for network_index, network in enumerate(contexts):
                network_attempt = {
                    "mode": network["mode"],
                    "proxy": redact_proxy_url(network.get("proxy_url")),
                    "ok": False,
                    "profile_failures": [],
                }
                row["network_policy"]["attempts"].append(network_attempt)
                network_item = dict(item)
                try:
                    if network_index > 0:
                        network_attempt["cleared_intermediate_media"] = clear_bvid_intermediate_media(work, bvid)
                    if duration_sec is None or not network_item.get("cid"):
                        view_data = resolve_view_data(bvid, network, deadline)
                        if duration_sec is None:
                            duration_sec = float(view_data.get("duration") or 0) or None
                            row["duration_sec"] = duration_sec
                            if duration_sec is not None:
                                network_item["duration_sec"] = duration_sec
                        if not network_item.get("cid") and view_data.get("cid"):
                            network_item["cid"] = int(view_data["cid"])
                    if max_duration_sec and duration_sec is not None and duration_sec > max_duration_sec:
                        row.update({
                            "ok": False,
                            "skipped": True,
                            "error": f"skipped_long_video: duration {duration_sec:.0f}s exceeds max_duration_sec {max_duration_sec:.0f}s",
                        })
                        row["network_policy"]["actual_mode"] = network["mode"]
                        network_attempt["ok"] = True
                        break
                    profiles = ["standard"] if args.no_auto_fallback else ["standard", "lowrate"]
                    for profile in profiles:
                        try:
                            download_item = dict(network_item)
                            if use_ascii_staging:
                                download_item["up"] = "video"
                            result = try_download_profile(
                                download_item,
                                out_dir,
                                work,
                                "video" if use_ascii_staging else args.up,
                                profile,
                                args.min_audio_bandwidth,
                                args.allow_low_audio,
                                network,
                                args.cdn_probe_bytes,
                                args.cdn_probe_timeout_sec,
                                deadline,
                            )
                            all_probes.extend(result.get("cdn_probe") or [])
                            if result.get("ok"):
                                preserved = {
                                    key: row[key]
                                    for key in ("title", "original_title", "storage_title", "import_title", "wiki_title", "network_policy")
                                }
                                if "ascii_staging" in row:
                                    preserved["ascii_staging"] = row["ascii_staging"]
                                    preserved["codex_ascii_staging"] = row["codex_ascii_staging"]
                                row.update(result)
                                row.update(preserved)
                                row.setdefault("duration_sec", duration_sec)
                                row.setdefault("max_duration_sec", max_duration_sec or None)
                                if use_ascii_staging:
                                    staged_output = Path(row["output"])
                                    final_out.unlink(missing_ok=True)
                                    shutil.copy2(staged_output, final_out)
                                    row["staging_output"] = str(staged_output)
                                    row["output"] = str(final_out)
                                network_attempt["ok"] = True
                                row["network_policy"]["actual_mode"] = network["mode"]
                                row["network_policy"]["fallback_used"] = network_index > 0
                                break
                            failure = {"profile": profile, "error": result.get("error", "quality gate failed")}
                            network_attempt["profile_failures"].append(failure)
                        except CdnProbeError as exc:
                            all_probes.extend(exc.probes)
                            network_attempt["profile_failures"].append({"profile": profile, "error": str(exc)})
                            break
                        except PerVideoDeadlineExceeded:
                            raise
                        except Exception as exc:
                            network_attempt["profile_failures"].append({
                                "profile": profile,
                                "error": sanitize_status_text(repr(exc), network.get("proxy_url")),
                            })
                    if row.get("ok") or row.get("skipped"):
                        break
                    network_attempt["error"] = "all download profiles failed"
                except PerVideoDeadlineExceeded:
                    raise
                except Exception as exc:
                    network_attempt["error"] = sanitize_status_text(repr(exc), network.get("proxy_url"))
            if not row.get("ok") and not row.get("skipped"):
                row["ok"] = False
                row["error"] = "all network modes and download profiles failed"
        except PerVideoDeadlineExceeded as exc:
            row.update({"ok": False, "error": str(exc), "timeout_stage": exc.stage})
        except Exception as exc:
            proxy_url = contexts[-1].get("proxy_url") if contexts else None
            row.update({
                "ok": False,
                "error": sanitize_status_text(repr(exc), proxy_url),
                "traceback": sanitize_status_text(traceback.format_exc()[-3000:], proxy_url),
            })
        row["cdn_probe"] = all_probes or row.get("cdn_probe", [])
        selected = [probe for probe in row["cdn_probe"] if probe.get("selected")]
        row["network_policy"]["cdn_hosts"] = sorted({probe["host"] for probe in selected if probe.get("host")})
        row["network_policy"]["remote_ips"] = sorted({probe["remote_ip"] for probe in selected if probe.get("remote_ip")})
        status.append(row)
        status_path.parent.mkdir(parents=True, exist_ok=True)
        status_path.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
        time.sleep(1)

    print(json.dumps({"ok_count": sum(1 for item in status if item.get("ok")), "status": status}, ensure_ascii=False, indent=2))
    return 0 if all(item.get("ok") for item in status) else 1


if __name__ == "__main__":
    raise SystemExit(main())
