import os
import subprocess
import logging

def generate_paparazzi_video(ffmpeg_path, ffprobe_path, input_file, song, cut_points):
    """
    Generates a paparazzi video by:
      1. Pre-scaling the input video to 540x960.
      2. Cutting the (scaled) video into segments based on provided cut_points.
         - The first segment (from cut_points[0] to cut_points[1]) is kept in full color.
         - For subsequent segments, a gap of 0.20s is applied and the video is converted to black & white.
         - The final segment is taken from (cut_points[-1] + gap) to the end (converted to B/W).
      3. Merging the segments using a filter_complex that applies an xfade transition of type "fadewhite"
         between each clip (duration 0.07s).
      4. Blending the merged video with a WEBM overlay (using lighten blend) and replacing its audio with an MP3.
      5. Overlaying a second WEBM (webm2_file) on top of that result.
      6. Running ffprobe to get the duration and trimming the final output.
      
    Parameters:
      ffmpeg_path (str): Path to the ffmpeg executable.
      input_file (str): The input video file.
      song (str): Base name for the associated WEBM and MP3 files.
      cut_points (list of float): Timestamps (in seconds) marking the boundaries of segments.
      
    Returns:
      str: The filename of the generated output video.
    """
    # Parameters
    gap = 0.20
    transition_duration = 0.07
    fps = "30"
    
    logging.info("Starting generate_paparazzi_video with input_file=%s, song=%s", input_file, song)
    print("Starting generate_paparazzi_video with input_file={}, song={}".format(input_file, song))
    
    # Determine input file directory and base name.
    input_abs = os.path.abspath(input_file)
    input_dir = os.path.dirname(input_abs)
    base_name, ext = os.path.splitext(os.path.basename(input_file))
    
    # Pre-scale the input video to 540x960 for efficiency.
    scaled_input = os.path.join(input_dir, f"{base_name}_scaled{ext}")
    scale_cmd = [
        ffmpeg_path, "-hide_banner", "-loglevel", "error",
        "-i", input_file,
        "-vf", "scale=540:960",
        "-c:v", "libx264",
        "-preset", "fast",
        "-an",
        "-y",
        scaled_input
    ]
    logging.info("Pre-scaling input video to 540x960: %s", scaled_input)
    print("Pre-scaling input video to 540x960: {}".format(scaled_input))
    try:
        subprocess.run(scale_cmd, check=True)
    except subprocess.CalledProcessError as e:
        logging.error("Error pre-scaling input video: %s", e)
        raise e

    # Build paths for the WEBM and MP3 files (assumed to be in a "lyrics" folder).
    webm_file = os.path.join(os.getcwd(), "lyrics", f"{song}.webm")
    webm2_file = os.path.join(os.getcwd(), "lyrics", f"{song}2.webm")
    mp3_file = os.path.join(os.getcwd(), "lyrics", f"{song}.mp3")
    
    if os.path.isfile(webm_file):
        logging.info("WEBM file found: %s", webm_file)
        print("WEBM file found: {}".format(webm_file))
    else:
        logging.warning("WEBM file does NOT exist: %s", webm_file)
        
    if os.path.isfile(mp3_file):
        logging.info("MP3 file found: %s", mp3_file)
        print("MP3 file found: {}".format(mp3_file))
    else:
        logging.warning("MP3 file does NOT exist: %s", mp3_file)
    
    # --- Extract segments from the pre-scaled input ---
    segment_files = []
    num_cuts = len(cut_points)
    
    # Process all segments except the final one.
    for i in range(num_cuts - 1):
        if i == 0:
            # First segment: original color.
            start = cut_points[0]
            end = cut_points[1]
            segment_filename = os.path.join(input_dir, f"{base_name}_segment_{i}{ext}")
            cmd = [
                ffmpeg_path, "-hide_banner", "-loglevel", "error",
                "-ss", str(start),
                "-to", str(end),
                "-i", scaled_input,
                "-r", fps,
                "-c:v", "libx264",
                "-preset", "fast",
                "-an",
                "-y",
                segment_filename
            ]
        else:
            # Subsequent segments: add gap and convert to B/W.
            start = cut_points[i] + gap
            end = cut_points[i + 1]
            segment_filename = os.path.join(input_dir, f"{base_name}_segment_{i}{ext}")
            cmd = [
                ffmpeg_path, "-hide_banner", "-loglevel", "error",
                "-ss", str(start),
                "-to", str(end),
                "-i", scaled_input,
                "-vf", "hue=s=0",
                "-r", fps,
                "-c:v", "libx264",
                "-preset", "fast",
                "-an",
                "-y",
                segment_filename
            ]
        logging.info("Extracting segment %d: start=%s, end=%s", i, start, end)
        print("Extracting segment {}: start={}, end={}".format(i, start, end))
        try:
            subprocess.run(cmd, check=True)
        except subprocess.CalledProcessError as e:
            logging.error("Error extracting segment %d: %s", i, e)
            raise e
        segment_files.append(segment_filename)
        
    # Extract the final segment: from (cut_points[-1] + gap) to end, in B/W.
    final_start = cut_points[-1] + gap
    final_segment = os.path.join(input_dir, f"{base_name}_segment_final{ext}")
    cmd = [
        ffmpeg_path, "-hide_banner", "-loglevel", "error",
        "-ss", str(final_start),
        "-i", scaled_input,
        "-vf", "hue=s=0",
        "-r", fps,
        "-c:v", "libx264",
        "-preset", "fast",
        "-an",
        "-y",
        final_segment
    ]
    logging.info("Extracting final segment: start=%s to end of video", final_start)
    print("Extracting final segment: start={} to end of video".format(final_start))
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        logging.error("Error extracting final segment: %s", e)
        raise e
    segment_files.append(final_segment)
    
    # --- Build filter_complex for merging with crossfade transitions (fadewhite) ---
    defined_durations = []
    if num_cuts >= 2:
        d0 = cut_points[1] - cut_points[0]
        defined_durations.append(d0)
    for i in range(1, num_cuts - 1):
        d = cut_points[i+1] - (cut_points[i] + gap)
        defined_durations.append(d)
        
    total_segments = len(segment_files)
    num_transitions = total_segments - 1
    offsets = []
    cumulative = 0.0
    prev_offset = -1.0
    for i in range(num_transitions):
        if i < len(defined_durations):
            cumulative += defined_durations[i]
        computed = cumulative - (i+1) * transition_duration
        if computed <= prev_offset:
            computed = prev_offset + 0.01
        offsets.append(round(computed, 3))
        prev_offset = computed
        
    filter_lines = []
    for idx in range(total_segments):
        filter_lines.append(f"[{idx}:v]setpts=PTS-STARTPTS,format=yuv420p,fps=30[v{idx}];")
    chain_label = "v0"
    for i in range(1, total_segments):
        offset = offsets[i-1]
        next_label = "".join(["v"] + [str(j) for j in range(i+1)])
        filter_lines.append(f"[{chain_label}][v{i}]xfade=transition=fadewhite:duration={transition_duration}:offset={offset}[{next_label}];")
        chain_label = next_label
    filter_complex = "".join(filter_lines)
    if filter_complex.endswith(";"):
        filter_complex = filter_complex[:-1]
        
    logging.info("Filter_complex for merging: %s", filter_complex)
    print("Filter_complex for merging: {}".format(filter_complex))
    
    merged_file = os.path.join(input_dir, f"{base_name}_merged{ext}")
    merge_cmd = [ffmpeg_path, "-hide_banner", "-loglevel", "error"]
    for seg in segment_files:
        merge_cmd.extend(["-i", seg])
    merge_cmd.extend([
        "-filter_complex", filter_complex,
        "-map", f"[{chain_label}]",
        "-y",
        merged_file
    ])
    logging.info("Merging segments with transitions into %s", merged_file)
    print("Merging segments with transitions into {}".format(merged_file))
    try:
        subprocess.run(merge_cmd, check=True)
    except subprocess.CalledProcessError as e:
        logging.error("Error merging segments: %s", e)
        raise e
        
    # Remove segment files as they are no longer needed.
    for seg in segment_files:
        if os.path.exists(seg):
            print("Deleted temporary segment file:", seg)
            os.remove(seg)
    logging.info("Deleted temporary segment files")
    print("Deleted temporary segment files")
    
    # --- Final processing: first, blend merged video with webm_file and replace audio with mp3 ---
    output_file = os.path.join(input_dir, f"{base_name}_{song}{ext}")
    temp_output = os.path.join(input_dir, f"{base_name}_temp_output{ext}")
    
    final_cmd = [
        ffmpeg_path, "-hide_banner", "-loglevel", "error",
        "-i", merged_file,
        "-i", webm_file,
        "-i", mp3_file,
        "-filter_complex", "[0:v][1:v]blend=all_mode=lighten:all_opacity=1.0[out]",
        "-map", "[out]",
        "-map", "2:a",
        "-c:v", "libx264",
        "-c:a", "aac",
        "-shortest",
        "-y",
        temp_output
    ]
    logging.info("Running final ffmpeg command (webm overlay): %s", " ".join(final_cmd))
    print("Running final ffmpeg command (webm overlay): {}".format(" ".join(final_cmd)))
    try:
        subprocess.run(final_cmd, check=True)
    except subprocess.CalledProcessError as e:
        logging.error("Error during final video generation (webm overlay): %s", e)
        raise e

    # --- New step: overlay webm2_file on top of temp_output ---
    temp_output2 = os.path.join(input_dir, f"{base_name}_temp_output2{ext}")
    second_cmd = [
        ffmpeg_path, "-hide_banner", "-loglevel", "error",
        "-i", temp_output,
        "-i", webm2_file,
        "-filter_complex", "[0:v][1:v]blend=all_mode=lighten:all_opacity=1.0[out]",
        "-map", "[out]",
        "-map", "0:a",
        "-c:v", "libx264",
        "-c:a", "aac",
        "-shortest",
        "-y",
        temp_output2
    ]
    logging.info("Running second ffmpeg command (webm2 overlay): %s", " ".join(second_cmd))
    print("Running second ffmpeg command (webm2 overlay): {}".format(" ".join(second_cmd)))
    try:
        subprocess.run(second_cmd, check=True)
    except subprocess.CalledProcessError as e:
        logging.error("Error during second video generation (webm2 overlay): %s", e)
        raise e

    # --- After both overlays, run ffprobe and trimming ---
    ffprobe_cmd = [
        ffprobe_path, "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        temp_output2
    ]
    try:
        result = subprocess.run(ffprobe_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        duration_final = float(result.stdout.decode().strip())
    except Exception as e:
        logging.error("Error getting duration of %s: %s", temp_output2, e)
        raise e

    trim_cmd = [
        ffmpeg_path, "-hide_banner", "-loglevel", "error",
        "-i", temp_output2,
        "-t", str(duration_final),
        "-c", "copy",
        "-y",
        output_file
    ]
    logging.info("Running trim command: %s", " ".join(trim_cmd))
    print("Running trim command: {}".format(" ".join(trim_cmd)))
    try:
        subprocess.run(trim_cmd, check=True)
    except subprocess.CalledProcessError as e:
        logging.error("Error trimming the final video: %s", e)
        raise e

    if os.path.isfile(output_file):
        logging.info("Output video created successfully: %s", output_file)
        print("Output video created successfully: {}".format(output_file))
    else:
        logging.warning("Output video was not created: %s", output_file)

    # Clean up temporary files.
    for temp in [merged_file, temp_output, temp_output2, scaled_input]:
        if os.path.exists(temp):
            os.remove(temp)
            logging.info("Deleted temporary file: %s", temp)
            print("Deleted temporary file: {}".format(temp))

    return output_file


def main():
    logging.basicConfig(level=logging.INFO)
    ffmpeg_path = "ffmpeg"  # or full path to ffmpeg
    ffprobe_path = "ffprobe"
    input_file = "Video.mp4"
    song = "paparazzi"
    # Example cut_points: [0, 1.540, 2.007, 2.607, 3.040, 3.607, 3.940]
    cut_points = [0, 1.540, 2.007, 2.607, 3.040, 3.607, 3.940]
    
    output_file = generate_paparazzi_video(ffmpeg_path, ffprobe_path, input_file, song, cut_points)
    print("Generated video:", output_file)

if __name__ == "__main__":
    main()
