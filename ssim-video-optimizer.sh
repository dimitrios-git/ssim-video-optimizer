#!/bin/bash

log_file="encoding.log"
: > "$log_file"

log() {
    echo "$@"
    echo "$@" >> "$log_file"
}

if [ -z "$1" ]; then
    log "Usage: $0 <input_file>"
    exit 1
fi

input_file="$1"
file_ext="${input_file##*.}"
valid_exts=("mkv" "mp4" "mov" "MKV" "MP4" "MOV")

if [[ ! " ${valid_exts[@]} " =~ " ${file_ext} " ]]; then
    log "Error: Unsupported file format. Only MKV, MP4, and MOV are allowed."
    exit 1
fi

if ! command -v jq &> /dev/null; then
    log "Error: jq is required but not installed. Please install jq first."
    exit 1
fi

audio_json=$(ffprobe -v quiet -select_streams a -show_entries stream=codec_name,bit_rate,channels -of json "$input_file")
stream_count=$(echo "$audio_json" | jq '.streams | length')
audio_streams=()

for ((i=0; i<stream_count; i++)); do
    codec=$(echo "$audio_json" | jq -r ".streams[$i].codec_name")
    bitrate=$(echo "$audio_json" | jq -r ".streams[$i].bit_rate // \"0\"")
    channels=$(echo "$audio_json" | jq -r ".streams[$i].channels // \"0\"")
    [[ ! "$bitrate" =~ ^[0-9]+$ ]] && bitrate=128000
    audio_streams+=("$codec,$bitrate,$channels")
done

audio_options=()
for index in "${!audio_streams[@]}"; do
    IFS=, read -r codec bitrate channels <<< "${audio_streams[$index]}"
    if [[ "$codec" != "aac" ]]; then
        bitrate_kbps=$((bitrate / 1000))
        audio_options+=("-c:a:$index" "aac" "-b:a:$index" "${bitrate_kbps}k" "-ac:a:$index" "$channels")
        log "Converting audio stream $index from $codec to AAC (${bitrate_kbps}k, ${channels} channels)"
    else
        audio_options+=("-c:a:$index" "copy")
        log "Copying existing AAC stream $index"
    fi
done

framerate=$(ffprobe -v error -select_streams v:0 -show_entries stream=r_frame_rate -of default=noprint_wrappers=1:nokey=1 "$input_file")
framerate=$(echo "scale=2; $framerate" | bc)
framerate_rounded=$(echo "scale=0; ($framerate + 0.5) / 1" | bc)
gop_size=$(echo "scale=0; $framerate_rounded / 2" | bc)
(( $(echo "$gop_size < 1" | bc -l) )) && gop_size=1

log "Detected source framerate: ${framerate} fps"
log "Rounded framerate: ${framerate_rounded} fps"
log "Calculated GOP size: ${gop_size}"

recommended_ssim_target=0.99
read -e -p "Enter the desired SSIM (similarity) (as a decimal, recommended <= ${recommended_ssim_target}) [default: ${recommended_ssim_target}]: " ssim_target
ssim_target=${ssim_target:-$recommended_ssim_target}

default_qp=19
read -e -p "Enter the starting QP (0=best quality, 51=worst) [default: $default_qp]: " start_qp
start_qp=${start_qp:-$default_qp}

while ! [[ "$start_qp" =~ ^[0-9]+$ ]] || (( start_qp < 0 || start_qp > 51 )); do
    log "Error: QP must be an integer between 0 and 51."
    read -e -p "Enter the starting QP (0=best, 51=worst) [default: $default_qp]: " start_qp
    start_qp=${start_qp:-$default_qp}
done

output_dir="encoded_outputs"
mkdir -p "$output_dir"

current_qp=$start_qp
max_qp=40
prev_ssim=1.0
best_qp=$start_qp
created_files=()
current_file=""
previous_file=""

log "Starting encoding iterations using constant QP..."

while [ "$current_qp" -le "$max_qp" ]; do
    output_file="${output_dir}/$(basename "$input_file" .${file_ext}) [h264_nvenc qp ${current_qp}].${file_ext}"
    created_files+=("$output_file")

    log "Encoding with QP: $current_qp..."

    ffmpeg -hwaccel cuda -i "$input_file" \
      -r 24000/1001 \
      -map 0 -map_metadata 0 \
      -c:v h264_nvenc -pix_fmt yuv420p -bf 2 -g "$gop_size" -coder 1 \
      -movflags +faststart -preset p7 -qp "$current_qp" \
      "${audio_options[@]}" -c:s copy "$output_file"

    if [ -n "$previous_file" ] && [ -f "$previous_file" ]; then
        log "Deleting previous file: $previous_file"
        rm "$previous_file"
    fi

    previous_file="$current_file"
    current_file="$output_file"

    ssim_value=$(ffmpeg -i "$input_file" -i "$output_file" -filter_complex ssim -f null - 2>&1 | \
                 grep "All:" | sed -E 's/.*All:([0-9.]+).*/\1/')

    log "SSIM for QP ${current_qp}: $ssim_value"

    if (( $(echo "$ssim_value < $ssim_target" | bc -l) )); then
        log "SSIM dropped below threshold at QP ${current_qp}, stopping."
        break
    fi

    prev_ssim=$ssim_value
    best_qp=$current_qp
    (( current_qp++ ))
done

best_output_file="${output_dir}/$(basename "$input_file" .${file_ext}) [h264_nvenc qp ${best_qp}].${file_ext}"

for file in "${created_files[@]}"; do
    if [ "$file" != "$best_output_file" ] && [ -f "$file" ]; then
        log "Deleting intermediate file: $file"
        rm "$file"
    fi

done

log "Best QP determined: ${best_qp}"
log "Optimized file: $best_output_file"

