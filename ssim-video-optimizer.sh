#!/bin/bash

# Define log file location
log_file="encoding.log"

# Clear existing log file
: > "$log_file"

# Custom logging function
log() {
    echo "$@"
    echo "$@" >> "$log_file"
}

# Check if a file is provided
if [ -z "$1" ]; then
    log "Usage: $0 <input_file>"
    exit 1
fi

input_file="$1"
file_ext="${input_file##*.}"  # Extract file extension
valid_exts=("mkv" "mp4" "mov" "MKV" "MP4" "MOV")

# Validate file extension
if [[ ! " ${valid_exts[@]} " =~ " ${file_ext} " ]]; then
    log "Error: Unsupported file format. Only MKV, MP4, and MOV are allowed."
    exit 1
fi

# Check if jq is installed
if ! command -v jq &> /dev/null; then
    log "Error: jq is required but not installed. Please install jq first."
    exit 1
fi

# Get audio stream information using JSON format
audio_json=$(ffprobe -v quiet -select_streams a -show_entries stream=codec_name,bit_rate,channels -of json "$input_file")

# Parse JSON to extract audio stream information
stream_count=$(echo "$audio_json" | jq '.streams | length')
audio_streams=()

for ((i=0; i<stream_count; i++)); do
    codec=$(echo "$audio_json" | jq -r ".streams[$i].codec_name")
    bitrate=$(echo "$audio_json" | jq -r ".streams[$i].bit_rate // \"0\"")
    channels=$(echo "$audio_json" | jq -r ".streams[$i].channels // \"0\"")
    
    # Validate and convert bitrate
    if [[ ! "$bitrate" =~ ^[0-9]+$ ]] || [[ "$bitrate" -eq 0 ]]; then
        log "Warning: Could not detect bitrate for audio stream $i. Using fallback value."
        bitrate=128000  # Fallback bitrate
    fi
    
    audio_streams+=("$codec,$bitrate,$channels")
done

# Process audio streams to build encoding parameters
audio_options=()
for index in "${!audio_streams[@]}"; do
    IFS=, read -r codec bitrate channels <<< "${audio_streams[$index]}"
    
    if [[ "$codec" != "aac" ]]; then
        # Convert to AAC with original bitrate and channels
        bitrate_kbps=$((bitrate / 1000))
        audio_options+=("-c:a:$index" "aac" "-b:a:$index" "${bitrate_kbps}k" "-ac:a:$index" "$channels")
        log "Converting audio stream $index from $codec to AAC (${bitrate_kbps}k, ${channels} channels)"
    else
        # Copy existing AAC stream
        audio_options+=("-c:a:$index" "copy")
        log "Copying existing AAC stream $index"
    fi
done

# Get original file max bitrate using ffprobe
original_maxrate=$(ffprobe -v error -select_streams v:0 -show_entries format=bit_rate -of default=noprint_wrappers=1:nokey=1 "$input_file")

# If original_maxrate is empty or not valid, try to calculate it manually
if [ -z "$original_maxrate" ] || [ "$original_maxrate" = "N/A" ]; then
    duration=$(ffprobe -v error -select_streams v:0 -show_entries stream=duration -of default=noprint_wrappers=1:nokey=1 "$input_file")
    filesize=$(stat -c %s "$input_file")
    
    if [ -n "$duration" ] && [ "$duration" != "N/A" ] && [ "$filesize" -gt 0 ]; then
        original_maxrate=$((filesize / duration / 1000))
        log "Estimated original max bitrate: ${original_maxrate} kbps"
    else
        log "Error: Could not determine original max bitrate. Exiting."
        exit 1
    fi
else
    original_maxrate=$((original_maxrate / 1000))
    log "Detected original max bitrate: ${original_maxrate} kbps"
fi

# Get source video framerate using ffprobe
framerate=$(ffprobe -v error -select_streams v:0 -show_entries stream=r_frame_rate -of default=noprint_wrappers=1:nokey=1 "$input_file")
framerate=$(echo "scale=2; $framerate" | bc)  # Convert fraction to decimal
framerate_rounded=$(echo "scale=0; ($framerate + 0.5) / 1" | bc)  # Round to nearest integer
gop_size=$(echo "scale=0; $framerate_rounded / 2" | bc)  # Calculate GOP size as half the rounded framerate

# Ensure GOP size is at least 1
if (( $(echo "$gop_size < 1" | bc -l) )); then
    gop_size=1
fi

log "Detected source framerate: ${framerate} fps"
log "Rounded framerate: ${framerate_rounded} fps"
log "Calculated GOP size: ${gop_size}"

# Calculate recommended starting bitrate (20% less than original_maxrate, rounded to nearest integer)
recommended_bitrate=$(echo "scale=0; ($original_maxrate * 0.6 + 0.5) / 1" | bc)

# Ask user for starting bitrate, with recommended_bitrate as default
read -e -p "Enter the starting bitrate (in kbps, recommended <= ${recommended_bitrate}k) [default: ${recommended_bitrate}k]: " start_bitrate
start_bitrate=${start_bitrate:-$recommended_bitrate}  # Use default if input is empty

# Ask user for minimum decrement step, with a default value (e.g., 199)
default_min_decrement=199
read -e -p "Enter the minimum decrement step (in kbps, must be > 0) [default: ${default_min_decrement}k]: " min_decrement
min_decrement=${min_decrement:-$default_min_decrement}  # Use default if input is empty

# Remove 'k' suffix if present
min_decrement=$(echo "$min_decrement" | tr -d 'kK')

# Validate minimum decrement step
while [[ ! "$min_decrement" =~ ^[0-9]+$ ]] || (( min_decrement <= 0 )); do
    log "Error: Minimum decrement step must be a positive integer."
    read -e -p "Enter the minimum decrement step (in kbps, must be > 0) [default: ${default_min_decrement}k]: " min_decrement
    min_decrement=${min_decrement:-$default_min_decrement}  # Use default if input is empty
    min_decrement=$(echo "$min_decrement" | tr -d 'kK')
done

# Ensure starting bitrate is within valid range
if [ "$start_bitrate" -ge "$original_maxrate" ]; then
    log "Warning: Starting bitrate is higher than or equal to original max bitrate."
fi

# Set recommended SSIM target
recommended_ssim_target=0.99

# Ask user for desired SSIM (similarity), with recommended_0.99 as default
read -e -p "Enter the desired SSIM (similarity) (as a decimal, recommended <= ${recommended_ssim_target}k) [default: ${recommended_ssim_target}]: " ssim_target
ssim_target=${ssim_target:-$recommended_ssim_target}  # Use default if input is empty


output_dir="encoded_outputs"
mkdir -p "$output_dir"

prev_ssim=1.0  # Assume perfect quality for the first encoding
# Array to track all created files
created_files=()

best_bitrate=$start_bitrate
current_file=""
previous_file=""

log "Starting encoding iterations..."
while [ "$start_bitrate" -gt 0 ]; do
    maxrate=$((start_bitrate * 2))
    bufsize=$maxrate

    output_file="${output_dir}/$(basename "$input_file" .${file_ext}) [h264_nvenc ${start_bitrate}k].${file_ext}"
    created_files+=("$output_file")

    log "Encoding with bitrate: ${start_bitrate}k..."

    ffmpeg -hwaccel cuda -i "$input_file" -map 0 -map_metadata 0 \
        -r 24000/1001 \
        -c:v h264_nvenc -pix_fmt yuv420p -bf 2 -g "$gop_size" -coder 1 \
        -movflags +faststart -preset slow -b:v "${start_bitrate}k" -maxrate "${maxrate}k" \
        -bufsize "${bufsize}k" "${audio_options[@]}" -c:s copy "$output_file"

    # Delete the previous file if it exists
    if [ -n "$previous_file" ] && [ -f "$previous_file" ]; then
        log "Deleting previous file: $previous_file"
        rm "$previous_file"
    fi

    # Update previous and current file pointers
    previous_file="$current_file"
    current_file="$output_file"

    ssim_value=$(ffmpeg -i "$input_file" -i "$output_file" -filter_complex ssim -f null - 2>&1 | \
                 grep "All:" | sed -E 's/.*All:([0-9.]+).*/\1/')

    log "SSIM for ${start_bitrate}k: $ssim_value"

    ssim_change=$(echo "scale=6; $prev_ssim - $ssim_value" | bc)
    ssim_target_diff=$(echo "scale=6; $ssim_value - $ssim_target" | bc)

    if (( $(echo "$ssim_value < $ssim_target" | bc -l) )); then
        if (( $(echo "$decrement_step > $min_decrement" | bc -l) )); then
            log "Quality deteriorated at ${start_bitrate}k, but decrement_step > min_decrement. Setting decrement_step to min_decrement and encoding once more."
            start_bitrate=$((start_bitrate + decrement_step))
            decrement_step=$min_decrement
            start_bitrate=$((start_bitrate - decrement_step))
            continue
        else
            log "Quality deteriorated at ${start_bitrate}k, stopping."
            break
        fi
    fi

    # Handle division by zero (stagnant SSIM)
    if (( $(echo "$ssim_change == 0" | bc -l) )); then
        log "SSIM did not change. Using minimum decrement step."
        decrement_step=$min_decrement
    else
        # Calculate decrement factor and step
        decrement_factor=$(echo "scale=6; $ssim_target_diff / $ssim_change" | bc | awk '{printf "%.6f", $0}')
        decrement_step=$(echo "$decrement_factor * $min_decrement" | bc | awk '{printf "%d", $0}')
        
        # Cap the decrement_step to a reasonable multiple of min_decrement (e.g., 10x)
        max_decrement=$((min_decrement * 10))
        if [ "$decrement_step" -gt "$max_decrement" ]; then
            log "Decrement step capped to ${max_decrement}k (10x min_decrement)."
            decrement_step=$max_decrement
        fi

        # Ensure decrement_step is at least min_decrement
        if [ "$decrement_step" -lt "$min_decrement" ]; then
            decrement_step=$min_decrement
        fi
    fi

    prev_ssim=$ssim_value
    best_bitrate=$start_bitrate

    start_bitrate=$((start_bitrate - decrement_step))
done

log "Best bitrate determined: ${best_bitrate}k"

# Construct correct best output filename with 'h264_nvenc'
best_output_file="${output_dir}/$(basename "$input_file" .${file_ext}) [h264_nvenc ${best_bitrate}k].${file_ext}"

# Delete only created files except the best one, checking existence first
for file in "${created_files[@]}"; do
    if [ "$file" != "$best_output_file" ] && [ -f "$file" ]; then
        log "Deleting intermediate file: $file"
        rm "$file"
    fi
done

