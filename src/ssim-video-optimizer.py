#!/usr/bin/env python3
import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from statistics import mean

def run_cmd(cmd, capture_output=False):
    """
    Run a command. If capture_output is True, return subprocess.CompletedProcess with stdout/stderr.
    If not, suppress output unless VERBOSE is set.
    """
    if capture_output:
        return subprocess.run(cmd, check=True,
                              stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE,
                              text=True)
    else:
        if not VERBOSE:
            return subprocess.run(cmd, check=True,
                                  stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL)
        else:
            return subprocess.run(cmd, check=True)

def probe_audio_streams(input_file):
    cmd = [
        'ffprobe', '-v', 'quiet', '-select_streams', 'a',
        '-show_entries', 'stream=index,codec_name,bit_rate,channels',
        '-of', 'json', input_file
    ]
    return json.loads(run_cmd(cmd, capture_output=True).stdout)['streams']

def probe_video_duration(input_file):
    cmd = [
        'ffprobe', '-v', 'quiet',
        '-show_entries', 'format=duration',
        '-of', 'default=nokey=1:noprint_wrappers=1', input_file
    ]
    return float(run_cmd(cmd, capture_output=True).stdout.strip())

def probe_video_framerate(input_file):
    cmd = [
        'ffprobe', '-v', 'quiet', '-select_streams', 'v:0',
        '-show_entries', 'stream=r_frame_rate',
        '-of', 'default=nokey=1:noprint_wrappers=1', input_file
    ]
    num, den = run_cmd(cmd, capture_output=True).stdout.strip().split('/')
    return float(num) / float(den)

def detect_scenes(input_file, threshold=0.6):
    """
    Detect scene changes via FFprobe 'scene' filter.
    Returns list of timestamps.
    """
    print(f"Detecting scene changes (threshold={threshold})...")
    logging.info(f"Detecting scene changes (threshold={threshold})...")
    cmd = [
        'ffprobe', '-v', 'quiet', '-f', 'lavfi',
        f"movie={input_file},select='gt(scene,{threshold})'",  
        '-show_entries', 'frame=best_effort_timestamp_time:frame_tags=lavfi.scene_score',
        '-of', 'json'
    ]
    data = json.loads(run_cmd(cmd, capture_output=True).stdout)
    times = []
    for idx, frame in enumerate(data.get('frames', [])):
        tags = frame.get('tags', {})
        score = tags.get('lavfi.scene_score')
        if 'best_effort_timestamp_time' in frame:
            ts = float(frame['best_effort_timestamp_time'])
        elif 'pkt_pts_time' in frame:
            ts = float(frame['pkt_pts_time'])
        else:
            ts = float(idx)
        if score is not None:
            times.append(ts)
    return times

def detect_motion(input_file, top_n=0):
    """
    Detect motion peaks using signalstats YDIF at 1fps.
    Returns list of timestamps.
    """
    print("Detecting motion peaks via signalstats (1fps)...")
    logging.info("Detecting motion peaks via signalstats (1fps)...")
    cmd = [
        'ffprobe', '-v', 'quiet', '-f', 'lavfi',
        f"movie={input_file},fps=1,signalstats,metadata=print:key=lavfi.signalstats.YDIF",
        '-show_entries', 'frame=pkt_pts_time:frame_tags=lavfi.signalstats.YDIF',
        '-of', 'json'
    ]
    data = json.loads(run_cmd(cmd, capture_output=True).stdout)
    scores = []
    for idx, frame in enumerate(data.get('frames', [])):
        tags = frame.get('tags', {})
        score = tags.get('lavfi.signalstats.YDIF')
        if 'best_effort_timestamp_time' in frame:
            timestamp = float(frame['best_effort_timestamp_time'])
        elif 'pkt_pts_time' in frame:
            timestamp = float(frame['pkt_pts_time'])
        else:
            timestamp = float(idx)
        if score is not None:
            scores.append((float(score), timestamp))
    if not scores:
        return []
    scores.sort(reverse=True, key=lambda x: x[0])
    times = [t for _, t in scores]
    return times[:top_n] if top_n > 0 else times

def build_audio_options(streams):
    opts = []
    for s in streams:
        i = s['index']
        codec = s.get('codec_name', '')
        br = int(s.get('bit_rate') or 0)
        ch = int(s.get('channels') or 0)
        if codec != 'aac' and br > 0:
            kb = br // 1000
            opts += [f'-c:a:{i}', 'aac', f'-b:a:{i}', f'{kb}k', f'-ac:{i}', str(ch)]
            print(f"Audio: convert stream {i} to AAC {kb}k,{ch}ch")
            logging.info(f"Audio: convert stream {i} to AAC {kb}k,{ch}ch")
        else:
            opts += [f'-c:a:{i}', 'copy']
            print(f"Audio: copy stream {i} (codec={codec})")
            logging.info(f"Audio: copy stream {i} (codec={codec})")
    return opts

def select_sample_times(input_file, mode, percent, count, clip_len=None):
    duration = probe_video_duration(input_file)
    if mode == 'scene':
        times = detect_scenes(input_file)
        if not times:
            msg = "No scene changes; fallback to uniform."
            print(msg); logging.warning(msg)
    elif mode == 'motion':
        times = detect_motion(input_file)
        if not times:
            msg = "No motion peaks; fallback to uniform."
            print(msg); logging.warning(msg)
    else:
        times = []
    if mode == 'uniform' or not times:
        span = duration * percent / 100.0
        step = max((duration - span) / (count - 1), 0)
        times = [i * step for i in range(count)]
        print(f"Using uniform sampling: {len(times)} segments over {duration:.1f}s")
        logging.info(f"Using uniform sampling: {len(times)} segments over {duration:.1f}s")
    if clip_len:
        filtered = []
        for t in times:
            if all(abs(t - prev) >= clip_len for prev in filtered):
                filtered.append(t)
                if len(filtered) == count:
                    break
        if len(filtered) < count:
            extra = [t for t in times if t not in filtered]
            filtered += extra[:count - len(filtered)]
        times = filtered
    return times[:count]

def extract_samples(input_file, tmpdir, mode, percent, count, sample_qp, audio_opts, raw_fr, gop):
    print(f"Sampling using '{mode}' mode: {count} segs, {percent}%")
    logging.info(f"Sampling using '{mode}' mode: {count} segs, {percent}%")
    duration = probe_video_duration(input_file)
    clip_len = duration * percent / 100.0 / count
    times = select_sample_times(input_file, mode, percent, count, clip_len)
    if not times:
        logging.error("No sample timestamps; aborting.")
        sys.exit(1)
    samples = []
    for idx, t in enumerate(times):
        msg = f"Extract seg {idx} at {t:.1f}s (len {clip_len:.1f}s)"
        print(msg); logging.info(msg)
        seg = os.path.join(tmpdir, f'seg_{idx}' + os.path.splitext(input_file)[1])
        sample_file = os.path.join(tmpdir, f'sample_{idx}' + os.path.splitext(input_file)[1])
        cmd_ext = ['ffmpeg','-y','-i',input_file,'-ss',str(t),'-t',str(clip_len),'-c','copy','-avoid_negative_ts','make_zero','-copyts',seg]
        if VERBOSE:
            print('Run:', ' '.join(cmd_ext)); logging.info('Run: '+' '.join(cmd_ext))
        run_cmd(cmd_ext)
        msg2 = f"Re-encode sample {idx} QP={sample_qp}"; print(msg2); logging.info(msg2)
        cmd_enc = ['ffmpeg','-y','-hwaccel','cuda','-i',seg,'-r',str(raw_fr),'-g',str(gop),'-bf','2','-pix_fmt','yuv420p','-c:v','h264_nvenc','-preset','p7','-rc','constqp','-qp',str(sample_qp)] + audio_opts + ['-c:s','copy',sample_file]
        if VERBOSE:
            print('Run:', ' '.join(cmd_enc)); logging.info('Run: '+' '.join(cmd_enc))
        run_cmd(cmd_enc)
        samples.append(sample_file)
    return samples

def measure_ssim_on_sample(sample_file, qp, raw_fr, gop, audio_opts):
    print(f"Measuring SSIM for {sample_file} at QP={qp}...")
    logging.info(f"Measuring SSIM for {sample_file} at QP={qp}...")
    ext = os.path.splitext(sample_file)[1]
    out = sample_file.replace(ext, f'_enc{ext}')
    cmd_enc = ['ffmpeg','-y','-hwaccel','cuda','-i',sample_file,'-r',str(raw_fr),'-g',str(gop),'-bf','2','-pix_fmt','yuv420p','-c:v','h264_nvenc','-preset','p7','-rc','constqp','-qp',str(qp)] + audio_opts + ['-c:s','copy',out]
    run_cmd(cmd_enc)
    cmd_ssim = ['ffmpeg','-i',sample_file,'-i',out,'-filter_complex','ssim','-f','null','-']
    res = run_cmd(cmd_ssim, capture_output=True)
    for line in res.stderr.splitlines():
        if 'All:' in line:
            res_ssim = float(line.split('All:')[1].split()[0])
            print(f"SSIM for {sample_file}: {res_ssim}")
            logging.info(f"SSIM for {sample_file}: {res_ssim}")
            return res_ssim
    return 0.0

def measure_ssim(qp, samples, raw_fr, gop, audio_opts, metric):
    if not samples:
        logging.error("No sample clips for SSIM.")
        sys.exit(1)
    vals=[measure_ssim_on_sample(s,qp,raw_fr,gop,audio_opts) for s in samples]
    avg, mn, mx = mean(vals), min(vals), max(vals)
    print(f"Sample results at QP={qp}: SSIMs={vals} avg={avg:.4f} min={mn:.4f} max={mx:.4f}")
    logging.info(f"Sample results at QP={qp}: SSIMs={vals} avg={avg:.4f} min={mn:.4f} max={mx:.4f}")
    return {'min':mn,'avg':avg,'max':mx}[metric]


def measure_full_ssim(input_file,encoded_file):
    cmd=['ffmpeg','-i',input_file,'-i',encoded_file,'-filter_complex','ssim','-f','null','-']
    res=run_cmd(cmd,capture_output=True)
    for line in res.stderr.splitlines():
        if 'All:' in line:
            res_ssim = float(line.split('All:')[1].split()[0])
            print(f"SSIM for {input_file}: {res_ssim}")
            logging.info(f"SSIM for {input_file}: {res_ssim}")
            return res_ssim
    return 0.0

def main():
    global VERBOSE
    parser = argparse.ArgumentParser(
        description='Optimize video quality via SSIM and QP binary search.',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument('input', help='Source video file')
    parser.add_argument('--ssim', type=float, default=0.99, help='Target SSIM')
    parser.add_argument('--min-qp', dest='min_qp', type=int, default=19, help='Min QP')
    parser.add_argument('--max-qp', dest='max_qp', type=int, default=32, help='Max QP')
    parser.add_argument('--sample-percent', type=float, default=15, help='Percent to sample')
    parser.add_argument('--sample-count', type=int, default=3, help='Num samples')
    parser.add_argument('--sample-qp', type=int, default=16, help='Sample re-encode QP')
    parser.add_argument('--sampling-mode', choices=['uniform','scene','motion'], default='motion', help='Sampling strategy')
    parser.add_argument('--metric', choices=['avg','min','max'], default='avg', help='SSIM metric')
    parser.add_argument('--log-file', help='Log file path')
    parser.add_argument('-v','--verbose', action='store_true', help='Verbose console and FFmpeg output')
    args = parser.parse_args()
    VERBOSE = args.verbose
    handlers = []
    if args.log_file: handlers.append(logging.FileHandler(args.log_file))
    if args.verbose: handlers.append(logging.StreamHandler(sys.stdout))
    if not handlers: handlers.append(logging.NullHandler())
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s', handlers=handlers, force=True)

    if not os.path.isfile(args.input): logging.error('Input not found'); sys.exit(1)
    ext = args.input.rsplit('.',1)[-1].lower()
    if ext not in ('mkv','mp4','mov'): logging.error('Unsupported format'); sys.exit(1)

    base = os.path.splitext(os.path.basename(args.input))[0]
    tmpdir = tempfile.mkdtemp(prefix=base+'_')
    streams = probe_audio_streams(args.input)
    audio_opts = build_audio_options(streams)
    raw_fr = probe_video_framerate(args.input)
    gop = max(1, int(round(raw_fr/2)))
    print(f"Video: framerate={raw_fr:.2f}fps gop={gop}")
    logging.info(f"Video: framerate={raw_fr:.2f}fps gop={gop}")

    print("Starting sampling and SSIM-based QP search...")
    logging.info("Starting sampling and SSIM-based QP search...")
    samples = extract_samples(
        args.input, tmpdir, args.sampling_mode,
        args.sample_percent, args.sample_count,
        args.sample_qp, audio_opts, raw_fr, gop
    )

    low, high = args.min_qp, args.max_qp
    print(f"Performing binary search: QP range [{low}..{high}]")
    logging.info(f"Performing binary search: QP range [{low}..{high}]")
    best_qp = high if measure_ssim(high, samples, raw_fr, gop, audio_opts, args.metric) >= args.ssim else low
    while high - low > 1:
        mid = (low + high) // 2
        print(f"Testing QP={mid}...")
        if measure_ssim(mid, samples, raw_fr, gop, audio_opts, args.metric) >= args.ssim:
            low, best_qp = mid, mid
        else:
            high = mid

    while True:
        print(f"Encoding final at QP={best_qp}...")
        logging.info(f"Encoding final at QP={best_qp}...")

        final = f"{base} [h264_nvenc qp {best_qp}].{ext}"
        run_cmd([
            'ffmpeg','-y','-hwaccel','cuda','-i',args.input,
            '-r',str(raw_fr),'-g',str(gop),'-bf','2','-pix_fmt','yuv420p',
            '-c:v','h264_nvenc','-preset','p7','-rc','constqp','-qp',str(best_qp)
        ] + audio_opts + ['-c:s','copy',final])
        full_ssim = measure_full_ssim(args.input, final)
        print(f"Full-file SSIM at QP {best_qp}: {full_ssim:.4f}")
        logging.info(f"Full-file SSIM at QP {best_qp}: {full_ssim:.4f}")
        if full_ssim >= args.ssim or best_qp <= 0:
            break
        best_qp -= 1
        os.remove(final)

    shutil.rmtree(tmpdir)
    print(f"Optimized file: {final}")
    logging.info(f"Optimized file: {final}")

if __name__ == '__main__':
    main()

