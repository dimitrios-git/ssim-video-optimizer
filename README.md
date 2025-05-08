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

## Notes
1. Screen-capture a video at 60fps or match your screen refresh rate if higher
2. Trim the video at the end if necessary using:
```
ffmpeg -i "input.mkv" -to 02:00:48 -c copy -avoid_negative_ts make_zero trimmed.mkv"
```
Adjust the time as required.
3. Play the video to identify the cropping dimensions, using:
```
ffplay -i "trimmed.mkv" -vf "cropdetect=limit=24:round=8:reset=10:skip=5"
```
4. Crop the file using the dimension from the previous step and use decimate to get rid of the extra frames assuming a classic cinematic 24p as the source:
```
ffmpeg -hwaccel cuda -i "trimmed.mkv" \
-vf "decimate,fps=24000/1001,crop=1920:1032:0:26" \
-map 0 -map_metadata 0 \
-c:v h264_nvenc -pix_fmt yuv420p -bf 2 -g 12 -coder 1 \
-movflags +faststart -preset p7 -qp 16 \
-c:a aac -b:a $(echo "$(ffprobe -v error -select_streams a:0 -show_entries stream=channels -of default=noprint_wrappers=1:nokey=1 "trimmed.mkv") * 160" | bc)k -ac $(ffprobe -v error -select_streams a:0 -show_entries stream=channels -of default=noprint_wrappers=1:nokey=1 "trimmed.mkv") -c:s copy "cropped.mkv"
```
5. Use the script to find the optimal file size/quality:
```
~/Development/ssim-video-optimizer/ssim-video-optimizer.sh cropped.mkv
```




