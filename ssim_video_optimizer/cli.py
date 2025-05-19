# cli.py
import argparse
import logging
import os
from .probes import probe_audio_streams, probe_video_framerate
from .utils import build_audio_options, setup_logging
from .sampling import extract_samples
from .ssim_search import find_best_qp
from .encoder import encode_final, measure_full_ssim

def main():
    parser = argparse.ArgumentParser(
        description='Optimize video quality via SSIM and QP binary search.'
    )
    parser.add_argument('input', help='Source video file')
    parser.add_argument('--ssim', type=float, default=0.99, help='Target SSIM')
    parser.add_argument('--min-qp', dest='min_qp', type=int, default=19, help='Min QP')
    parser.add_argument('--max-qp', dest='max_qp', type=int, default=32, help='Max QP')
    parser.add_argument('--sample-percent', type=float, default=15, help='Percent to sample')
    parser.add_argument('--sample-count', type=int, default=3, help='Num samples')
    parser.add_argument('--sample-qp', type=int, default=16, help='Sample re-encode QP')
    parser.add_argument('--sampling-mode', choices=['uniform', 'scene', 'motion'], default='motion', help='Sampling strategy')
    parser.add_argument('--metric', choices=['avg', 'min', 'max'], default='avg', help='SSIM metric')
    parser.add_argument('--log-file', help='Log file path')
    parser.add_argument('-v', '--verbose', action='store_true', help='Verbose output')
    args = parser.parse_args()

    setup_logging(args.verbose, args.log_file)

    if not os.path.isfile(args.input):
        logging.error('Input file not found: %s', args.input)
        return

    # Probe streams and frame rate
    streams = probe_audio_streams(args.input)
    audio_opts = build_audio_options(streams)
    raw_fr = probe_video_framerate(args.input)

    # Extract sample clips
    samples = extract_samples(
        args.input,
        mode=args.sampling_mode,
        percent=args.sample_percent,
        count=args.sample_count,
        sample_qp=args.sample_qp,
        audio_opts=audio_opts,
        raw_fr=raw_fr
    )

    # Compute GOP size
    gop = max(1, int(round(raw_fr / 2)))

    # Find best QP via sample-based SSIM
    best_qp = find_best_qp(
        samples,
        min_qp=args.min_qp,
        max_qp=args.max_qp,
        target_ssim=args.ssim,
        metric=args.metric,
        audio_opts=audio_opts,
        raw_fr=raw_fr,
        gop=gop
    )

    # Final encode: drop QP until full SSIM meets target
    final_qp = best_qp
    while final_qp >= args.min_qp:
        final_file = encode_final(
            input_file=args.input,
            qp=final_qp,
            audio_opts=audio_opts,
            raw_fr=raw_fr,
            return_ssim=True
        )
        full_ssim = measure_full_ssim(args.input, final_file)
        if full_ssim >= args.ssim:
            print(f"Final full-file SSIM {full_ssim:.4f} meets target; using QP={final_qp}")
            break
        final_qp -= 1
    else:
        logging.warning(
            "Could not meet SSIM target on full file; keeping sample-based QP=%d", best_qp
        )
        final_qp = best_qp
        final_file = encode_final(
            input_file=args.input,
            qp=final_qp,
            audio_opts=audio_opts,
            raw_fr=raw_fr,
            return_ssim=False
        )

    print(f"Optimized file: {final_file} (QP={final_qp})")

if __name__ == '__main__':
    main()
