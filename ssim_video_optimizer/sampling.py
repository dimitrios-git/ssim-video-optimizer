# sampling.py
import json
import os
import tempfile

from .probes import probe_video_duration
from .utils import run_cmd


def detect_scenes(input_file: str, threshold: float = 0.6) -> list:
    cmd = [
        'ffprobe', '-v', 'quiet', '-f', 'lavfi',
        f"movie={input_file},select='gt(scene,{threshold})'",
        '-show_entries', 'frame=best_effort_timestamp_time:frame_tags=lavfi.scene_score',
        '-of', 'json'
    ]
    data = json.loads(run_cmd(cmd, capture_output=True).stdout)
    times = []
    for frame in data.get('frames', []):
        tags = frame.get('tags', {})
        if 'lavfi.scene_score' in tags:
            ts = float(frame.get('best_effort_timestamp_time', 0.0))
            times.append(ts)
    return times

def detect_motion(input_file: str, top_n: int = 0) -> list:
    cmd = [
        'ffprobe', '-v', 'quiet', '-f', 'lavfi',
        f"movie={input_file},fps=1,signalstats,metadata=print:key=lavfi.signalstats.YDIF",
        '-show_entries', 'frame=pkt_pts_time:frame_tags=lavfi.signalstats.YDIF',
        '-of', 'json'
    ]
    data = json.loads(run_cmd(cmd, capture_output=True).stdout)
    scores = []
    for frame in data.get('frames', []):
        tags = frame.get('tags', {})
        score = tags.get('lavfi.signalstats.YDIF')
        if score is None:
            continue
        if 'pkt_pts_time' in frame:
            ts = float(frame['pkt_pts_time'])
        else:
            ts = float(frame.get('best_effort_timestamp_time', 0.0))
        scores.append((float(score), ts))
    scores.sort(reverse=True, key=lambda x: x[0])
    times = [t for _, t in scores]
    return times[:top_n] if top_n else times

def select_sample_times(input_file: str, mode: str, percent: float, count: int, clip_len: float = None) -> list:
    duration = probe_video_duration(input_file)
    if mode == 'scene':
        times = detect_scenes(input_file)
    elif mode == 'motion':
        times = detect_motion(input_file)
    else:
        times = []
    if mode == 'uniform' or not times:
        span = duration * percent / 100.0
        step = max((duration - span) / max(count - 1, 1), 0)
        times = [i * step for i in range(count)]
    if clip_len is None:
        clip_len = duration * percent / 100.0 / count
    filtered = []
    for t in times:
        if all(abs(t - prev) >= clip_len for prev in filtered):
            filtered.append(t)
            if len(filtered) == count:
                break
    return (filtered + times)[:count]

def extract_samples(input_file: str, mode: str, percent: float, count: int, sample_qp: int, audio_opts: list, raw_fr: float) -> list:
    duration = probe_video_duration(input_file)
    clip_len = duration * percent / 100.0 / count
    times = select_sample_times(input_file, mode, percent, count, clip_len)
    tmpdir = tempfile.mkdtemp(prefix="ssim_sample_")
    samples = []
    for idx, t in enumerate(times):
        seg = os.path.join(tmpdir, f"seg_{idx}{os.path.splitext(input_file)[1]}")
        sample_file = os.path.join(tmpdir, f"sample_{idx}{os.path.splitext(input_file)[1]}")
        run_cmd(['ffmpeg', '-y', '-i', input_file, '-ss', str(t), '-t', str(clip_len), '-c', 'copy', seg])
        run_cmd([
            'ffmpeg', '-y', '-hwaccel', 'cuda', '-i', seg,
            '-r', str(raw_fr), '-g', str(int(max(1, round(raw_fr/2)))),
            '-bf', '2', '-pix_fmt', 'yuv420p', '-c:v', 'h264_nvenc',
            '-preset', 'p7', '-rc', 'constqp', '-qp', str(sample_qp)
        ] + audio_opts + ['-c:s', 'copy', sample_file])
        samples.append(sample_file)
    return samples
