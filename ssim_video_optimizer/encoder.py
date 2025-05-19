# encoder.py
import os
from .utils import run_cmd

def measure_full_ssim(input_file: str, encoded_file: str) -> float:
    res = run_cmd([
        'ffmpeg', '-i', input_file, '-i', encoded_file,
        '-filter_complex', 'ssim', '-f', 'null', '-'
    ], capture_output=True)
    for line in res.stderr.splitlines():
        if 'All:' in line:
            return float(line.split('All:')[1].split()[0])
    return 0.0


def encode_final(input_file: str, qp: int, audio_opts: list, raw_fr: float, return_ssim: bool=False) -> str:
    base, ext = os.path.splitext(input_file)
    final_file = f"{base} [h264_nvenc qp {qp}]{ext}"
    gop = max(1, int(round(raw_fr/2)))
    run_cmd([
        'ffmpeg', '-y', '-hwaccel', 'cuda', '-i', input_file,
        '-r', str(raw_fr), '-g', str(gop), '-bf', '2',
        '-pix_fmt', 'yuv420p', '-c:v', 'h264_nvenc',
        '-preset', 'p7', '-rc', 'constqp', '-qp', str(qp)
    ] + audio_opts + ['-c:s', 'copy', final_file])
    if return_ssim:
        full_ssim = measure_full_ssim(input_file, final_file)
        print(f"Full-file SSIM at QP {qp}: {full_ssim:.4f}")
    return final_file
