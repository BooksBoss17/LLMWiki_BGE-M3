#!/usr/bin/env python
"""Probe Bilibili video/audio CDN mirrors without exposing signed URLs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from bilibili_playurl_download import (
    DEFAULT_CDN_PROBE_BYTES,
    DEFAULT_CDN_PROBE_TIMEOUT_SEC,
    audio_candidates,
    fetch_playurl,
    network_mode_sequence,
    probe_stream_urls,
    redact_proxy_url,
    resolve_cid,
    sanitize_status_text,
    stream_urls,
    video_candidates,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bvid", required=True)
    parser.add_argument("--cid", type=int)
    parser.add_argument("--network-mode", choices=["direct", "system-proxy", "explicit-proxy"], default="direct")
    parser.add_argument("--network-fallback", choices=["system-proxy", "none"], default="system-proxy")
    parser.add_argument("--proxy-url")
    parser.add_argument("--probe-bytes", type=int, default=DEFAULT_CDN_PROBE_BYTES)
    parser.add_argument("--probe-timeout-sec", type=int, default=DEFAULT_CDN_PROBE_TIMEOUT_SEC)
    parser.add_argument("--work-dir", type=Path, default=Path("tmp/downloads/bilibili-cdn-probe"))
    args = parser.parse_args()
    if args.proxy_url and args.network_mode != "explicit-proxy":
        parser.error("--proxy-url is valid only with --network-mode explicit-proxy")
    args.work_dir.mkdir(parents=True, exist_ok=True)

    attempts = []
    for network in network_mode_sequence(args.network_mode, args.network_fallback, args.proxy_url):
        row = {"mode": network["mode"], "proxy": redact_proxy_url(network.get("proxy_url")), "ok": False}
        attempts.append(row)
        try:
            cid = args.cid or resolve_cid(args.bvid, network)
            playurl = fetch_playurl(args.bvid, cid, network)
            dash = playurl.get("dash") or {}
            video = video_candidates(dash)[0]
            audio = audio_candidates(dash)[0]
            _, video_probes = probe_stream_urls(
                stream_urls(video), bvid=args.bvid, network=network, work=args.work_dir,
                requested_bytes=args.probe_bytes, probe_timeout_sec=args.probe_timeout_sec, deadline=None,
            )
            _, audio_probes = probe_stream_urls(
                stream_urls(audio), bvid=args.bvid, network=network, work=args.work_dir,
                requested_bytes=args.probe_bytes, probe_timeout_sec=args.probe_timeout_sec, deadline=None,
            )
            for probe in video_probes:
                probe.update({"kind": "video", "stream_id": video.get("id"), "bandwidth": video.get("bandwidth")})
            for probe in audio_probes:
                probe.update({"kind": "audio", "stream_id": audio.get("id"), "bandwidth": audio.get("bandwidth")})
            row["cdn_probe"] = video_probes + audio_probes
            row["ok"] = any(item["ok"] for item in video_probes) and any(item["ok"] for item in audio_probes)
            if row["ok"]:
                break
            row["error"] = "no valid video or audio Range candidate"
        except Exception as exc:
            row["error"] = sanitize_status_text(exc, network.get("proxy_url"))

    result = {"bvid": args.bvid, "requested_mode": args.network_mode, "attempts": attempts}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if attempts and attempts[-1].get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
