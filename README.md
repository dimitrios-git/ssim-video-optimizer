# SSIM Video Optimizer

This script intelligently re-encodes a video file using NVIDIA's NVENC encoder to find the smallest bitrate that retains high visual fidelity, measured using SSIM. It's designed to minimize file size without sacrificing perceptual quality.

## Features
- Automatically detects and converts non-AAC audio streams
- Estimates original bitrate and guides the encoding process
- Uses SSIM to determine quality loss across encoding iterations
- Deletes intermediate files, keeping only the best-quality result
- Customizable parameters: bitrate, GOP size, SSIM target, decrement step
- Fully logged output (`encoding.log`)

## Requirements
- `ffmpeg` with NVENC support
- `jq`
- `bc`

## Usage
```bash
./ssim-video-optimizer.sh <input_video>

