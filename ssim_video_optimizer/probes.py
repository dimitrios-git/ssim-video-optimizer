# probes.py
import json
from .utils import run_cmd

def probe_audio_streams(input_file: str) -> list:
    cmd = [
        'ffprobe', '-v', 'quiet', '-select_streams', 'a',
        '-show_entries', 'stream=index,codec_name,bit_rate,channels',
        '-of', 'json', input_file
    ]
    out = run_cmd(cmd, capture_output=True).stdout
    return json.loads(out)['streams']

def probe_video_duration(input_file: str) -> float:
    cmd = [
        'ffprobe', '-v', 'quiet',
        '-show_entries', 'format=duration',
        '-of', 'default=nokey=1:noprint_wrappers=1', input_file
    ]
    return float(run_cmd(cmd, capture_output=True).stdout.strip())

def probe_video_framerate(input_file: str) -> float:
    cmd = [
        'ffprobe', '-v', 'quiet', '-select_streams', 'v:0',
        '-show_entries', 'stream=r_frame_rate',
        '-of', 'default=nokey=1:noprint_wrappers=1', input_file
    ]
    num, den = run_cmd(cmd, capture_output=True).stdout.strip().split('/')
    return float(num) / float(den)
