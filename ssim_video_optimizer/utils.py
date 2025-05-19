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
    for s in streams:
        i = s['index']
        codec = s.get('codec_name', '')
        br = int(s.get('bit_rate') or 0)
        ch = int(s.get('channels') or 0)
        if codec != 'aac' and br > 0:
            kb = br // 1000
            opts += [f'-c:a:{i}', 'aac', f'-b:a:{i}', f'{kb}k', f'-ac:{i}', str(ch)]
        else:
            opts += [f'-c:a:{i}', 'copy']
    return opts

def setup_logging(verbose: bool, log_file: str = None):
    handlers = []
    if log_file:
        handlers.append(logging.FileHandler(log_file))
    if verbose:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s', handlers=handlers)
