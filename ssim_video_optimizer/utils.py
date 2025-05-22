# utils.py
import subprocess
import logging


def run_cmd(cmd, capture_output=False):
    return subprocess.run(
        cmd, check=True,
        stdout=(subprocess.PIPE if capture_output else subprocess.DEVNULL),
        stderr=(subprocess.PIPE if capture_output else subprocess.DEVNULL),
        text=True
    )

def build_audio_options(streams: list) -> list:
    opts = []
    target_bitrate_kbps = 192
    for s in streams:
        i = s['index']
        codec = s.get('codec_name', '')
        # Always re-encode non-AAC streams at 192 kbps per channel
        if codec != 'aac':
            # Use the stream's channel count to maintain layout
            ch = int(s.get('channels') or 2)
            opts += [
                f'-c:a:{i}', 'aac',
                f'-b:a:{i}', f'{target_bitrate_kbps}k',
                f'-ac:{i}', str(ch)
            ]
        else:
            # Copy existing AAC streams
            opts += [
                f'-c:a:{i}', 'copy'
            ]
    return opts

def setup_logging(verbose: bool, log_file: str = None):
    handlers = []
    if log_file:
        handlers.append(logging.FileHandler(log_file))
    if verbose:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s', handlers=handlers)
