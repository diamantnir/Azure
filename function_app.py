import azure.functions as func
import logging
import os
import tempfile
import subprocess
import requests
import shutil
import json
import uuid
import random
from azure.storage.blob import BlobServiceClient, ContentSettings
from azure.core.exceptions import ResourceExistsError
import transcribe
from generate_paparazzi_video import generate_paparazzi_video
from collage import create_collage
#import generate_music_video

MIN_DURATION_REQUIREMENTS = {
    1: 10.0,    # maybe 0 if there's no lower bound
    2: 10.0,    # likewise
    3: 4.0,    # needs at least 4 seconds
    4: 8.0,    # needs at least 8 seconds
    5: 12.0,
    6: 16.0,
    7: 20.0,
    8: 32.0,
    9: 4.0,    # e.g. loudest 4 audio seconds
    10: 4.0,   # removing dupes + final shorten might need at least 4
    11: 20.0,   # boomerang best 20s x10
    12: 10.0,
    13: 8.0,
    14: 8.0,
    15: 10.0,
    16: 8.0,
    17: 4.0,
    18: 4.0,
    19: 4.0,
    20: 4.0,
    21: 5.0,
    22: 4.0,
    23: 4.0,
    101: 4
}


app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)

def get_video_duration(ffprobe_path, input_file):
    cmd_get_metadata = f"\"{ffprobe_path}\" -v quiet -print_format json -show_streams \"{input_file}\""
    result = subprocess.run(cmd_get_metadata, shell=True, capture_output=True, text=True)
    metadata = json.loads(result.stdout)

    # Find the video stream
    video_stream = next(stream for stream in metadata['streams'] if stream['codec_type'] == 'video')

    # Extract duration and fps
    duration = float(video_stream['duration'])
    width = int(video_stream['width'])
    height = int(video_stream['height'])
    fps = eval(video_stream['r_frame_rate'])
    return {"duration": duration, "fps": fps, "width": width, "height": height}

def shorten_video(ffmpeg_path, input_file, total_duration, fps, desired_duration=4.0, speed_up_audio=True):
    folder = os.path.dirname(input_file)
    filename = os.path.basename(input_file)
    name, ext = os.path.splitext(filename)

    speed_factor = total_duration / desired_duration
    output_file = os.path.join(folder, f"{name}_shortened{ext}")

    if speed_up_audio:
        cmd_ffmpeg = (
            f"\"{ffmpeg_path}\" -i \"{input_file}\" -hide_banner -loglevel error "
            f"-vf \"setpts=PTS/{speed_factor}\" -af \"atempo={speed_factor}\" -y \"{output_file}\""
        )
    else:
        cmd_ffmpeg = (
            f"\"{ffmpeg_path}\" -i \"{input_file}\" -hide_banner -loglevel error "
            f"-vf \"select=not(mod(n\\,{max(1, int((fps*total_duration)/(fps*desired_duration))) }))," 
            f"setpts=N/FRAME_RATE/TB\" -vsync vfr -map 0:v -y \"{output_file}\""
        )

    subprocess.run(cmd_ffmpeg, shell=True, check=True)
    return output_file

def concat_reverse(ffmpeg_path, input_file, output_file):
    folder = os.path.dirname(input_file)
    temp2r_path = os.path.join(folder, "temp2r.mp4")

    cmd_reverse = f"\"{ffmpeg_path}\" -i \"{input_file}\" -hide_banner -loglevel error -vf reverse -af areverse -y \"{temp2r_path}\""
    subprocess.run(cmd_reverse, shell=True, check=True)

    cmd_concat = (
        f"\"{ffmpeg_path}\" -i \"{input_file}\" -i \"{temp2r_path}\" "
        f"-filter_complex \"[0:v][1:v]concat=n=2:v=1[v]\" "
        f"-map \"[v]\" -hide_banner -loglevel error -y \"{output_file}\""
    )
    subprocess.run(cmd_concat, shell=True, check=True)

    os.remove(input_file)
    os.remove(temp2r_path)

def boomerang_video(ffmpeg_path, input_file, total_duration, fps):
    folder = os.path.dirname(input_file)
    filename = os.path.basename(input_file)
    name, ext = os.path.splitext(filename)

    desired_duration = 2
    total_frames = int(fps * total_duration)
    target_frames = int(fps * desired_duration)
    frame_skip = max(1, total_frames // target_frames)

    output_file = os.path.join(folder, f"{name}_boomerang{ext}")
    temp2_path = os.path.join(folder, "temp2.mp4")

    cmd_ffmpeg = (
        f"\"{ffmpeg_path}\" -i \"{input_file}\" -hide_banner -loglevel error "
        f"-vf \"select=not(mod(n\\,{frame_skip})),setpts=N/FRAME_RATE/TB\" "
        f"-vsync vfr -map 0:v -y \"{temp2_path}\""
    )
    subprocess.run(cmd_ffmpeg, shell=True, check=True)

    concat_reverse(ffmpeg_path, temp2_path, output_file)
    return output_file

def boomerang_video_best_2(ffmpeg_path, ffprobe_path, input_file, total_duration, fps):
    folder = os.path.dirname(input_file)
    filename = os.path.basename(input_file)
    name, ext = os.path.splitext(filename)

    output_file_mp4 = os.path.join(folder, f"{name}_boomerang_best_2_seconds.mp4")

    # Extract a 2-second segment from the original
    video2 = extract_best_4_seconds(ffmpeg_path, input_file, total_duration, fps, window_size=2)

    # Boomerang the 2s clip
    concat_reverse(ffmpeg_path, video2, output_file_mp4)

    return output_file_mp4

def zoom_in_out(ffmpeg_path, ffprobe_path, input_file, max_zoom=0.25):
    """
    Applies a zoom-in-then-out effect to the given input video, 
    based on its duration, fps, width, and height.
    """

    # 1) Retrieve video metadata
    response = get_video_duration(ffprobe_path, input_file)
    duration = response['duration']
    frame_rate = response['fps']
    width = response['width']
    height = response['height']

    folder = os.path.dirname(input_file)
    filename = os.path.basename(input_file)
    name, ext = os.path.splitext(filename)

    # 2) Calculate the midpoint (in frames) to switch zoom direction
    midpoint_frames = int((duration / 2) * frame_rate)

    # 3) Build the zoom formula
    # - In first half, zoom from 1x to 1 + max_zoom
    # - In second half, zoom back from 1 + max_zoom to 1x
    zoom_formula = (
        f"if(lte(on,{midpoint_frames}),"
        f"1+{max_zoom}/{midpoint_frames}*on,"
        f"1+{max_zoom}-{max_zoom}/{midpoint_frames}*(on-{midpoint_frames}))"
    )

    output_file = os.path.join(folder, f"{name}_zoom_effect{ext}")

    # 4) Construct and run the ffmpeg command
    command = [
        ffmpeg_path,
        "-i", input_file,
        "-vf",
        f"zoompan=z='{zoom_formula}':d=1:s={width}x{height}:fps={frame_rate},setsar=1",
        "-c:v", "libx264",
        "-t", str(duration),
        "-r", str(frame_rate),
        "-hide_banner",
        "-loglevel", "error",
        "-y",  # overwrite
        output_file
    ]

    subprocess.run(command, check=True)

    return output_file


def boomerang_video_best_20x10(ffmpeg_path, ffprobe_path, input_file, total_duration, fps):
    folder = os.path.dirname(input_file)
    filename = os.path.basename(input_file)
    name, ext = os.path.splitext(filename)

    # Output file path
    output_file = os.path.join(folder, f"{name}_boomerang_best_20_seconds{ext}")

    # Step 1: Extract best 20 seconds from the input
    video20 = extract_best_4_seconds(ffmpeg_path, input_file, total_duration, fps, window_size=20)

    # Step 2: Re-measure the resulting 20s clip
    response = get_video_duration(ffprobe_path, video20)
    total_duration_20 = response['duration']
    fps_20 = response['fps']

    # Step 3: Shorten that 20s clip down to 2 seconds
    short_filename = shorten_video(ffmpeg_path, video20, total_duration_20, fps_20, desired_duration=2.0)

    # Clean up the 20s intermediate
    os.remove(video20)

    # Step 4: Boomerang the 2s clip into 'output_file'
    concat_reverse(ffmpeg_path, short_filename, output_file)

    return output_file


def extract_best_4_seconds(ffmpeg_path, input_file, total_duration, fps, window_size=4.0):
    folder = os.path.dirname(input_file)
    filename = os.path.basename(input_file)
    name, ext = os.path.splitext(filename)

    # We'll store the scene log file in the same temp folder
    scene_log_file = os.path.join(folder, "temp_scene_log.txt")

    # Detect scenes using the ffmpeg_path
    cmd_scene_detect = (
        f"\"{ffmpeg_path}\" -i \"{input_file}\" -hide_banner -loglevel error "
        f"-vf \"select='gt(scene,0)',metadata=print:file={scene_log_file}\" "
        f"-y -an -f null -"
    )
    subprocess.run(cmd_scene_detect, shell=True, check=True)

    scene_changes = [0.0]  # Start from the beginning
    scene_scores = [0.0]

    # Parse scene changes and scores
    with open(scene_log_file, 'r') as log_file:
        for line in log_file:
            if "pts_time" in line:
                time_str = line.split('pts_time:')[1].strip()
                scene_changes.append(float(time_str))
            if "lavfi.scene_score" in line:
                score_str = line.split('lavfi.scene_score=')[1].strip()
                scene_scores.append(float(score_str))

    # Append the end of the video
    scene_changes.append(total_duration)
    scene_scores.append(0)

    best_start = 0.0
    best_score = 0.0

    # Slide a 4-second window across the timeline, sum up scene scores
    for i in range(len(scene_changes)):
        start_time = scene_changes[i]
        end_time = start_time + window_size

        if end_time > total_duration:
            break

        window_score = 0
        for j, change_time in enumerate(scene_changes):
            if start_time <= change_time < end_time:
                window_score += scene_scores[j]

        if window_score > best_score:
            best_score = window_score
            best_start = start_time

    # Extract the best 4-second segment
    output_file = os.path.join(folder, f"{name}_best_{str(window_size)}_seconds{ext}")
    cmd_ffmpeg = (
        f"\"{ffmpeg_path}\" -i \"{input_file}\" -hide_banner -loglevel error "
        f"-ss {best_start} -t {window_size} -map 0 -c:v libx264 -c:a aac -y \"{output_file}\""
    )

    subprocess.run(cmd_ffmpeg, shell=True, check=True)

    # Clean up
    os.remove(scene_log_file)

    return output_file

def extract_best_8_seconds_x2(ffmpeg_path, ffprobe_path, input_file, total_duration, fps):
    # First extract best 8 seconds
    video8 = extract_best_4_seconds(ffmpeg_path, input_file, total_duration, fps, window_size=8)

    # Get info for the 8-second clip
    response = get_video_duration(ffprobe_path, video8)
    total_duration_8 = response['duration']
    fps_8 = response['fps']

    # Shorten the 8-second clip down to 4 seconds
    short_filename = shorten_video(ffmpeg_path, video8, total_duration_8, fps_8, desired_duration=4.0)
    os.remove(video8)
    return short_filename


def extract_best_12_seconds_x3(ffmpeg_path, ffprobe_path, input_file, total_duration, fps):
    # First extract best 12 seconds
    video12 = extract_best_4_seconds(ffmpeg_path, input_file, total_duration, fps, window_size=12)

    # Get info for the 12-second clip
    response = get_video_duration(ffprobe_path, video12)
    total_duration_12 = response['duration']
    fps_12 = response['fps']

    # Shorten the 12-second clip down to 4 seconds
    short_filename = shorten_video(ffmpeg_path, video12, total_duration_12, fps_12, desired_duration=4.0)
    os.remove(video12)
    return short_filename


def extract_best_16_seconds_x4(ffmpeg_path, ffprobe_path, input_file, total_duration, fps):
    # First extract best 16 seconds
    video16 = extract_best_4_seconds(ffmpeg_path, input_file, total_duration, fps, window_size=16)

    # Get info for the 16-second clip
    response = get_video_duration(ffprobe_path, video16)
    total_duration_16 = response['duration']
    fps_16 = response['fps']

    # Shorten the 16-second clip down to 4 seconds
    short_filename = shorten_video(ffmpeg_path, video16, total_duration_16, fps_16, desired_duration=4.0)
    os.remove(video16)
    return short_filename


def extract_best_20_seconds_x5(ffmpeg_path, ffprobe_path, input_file, total_duration, fps):
    # First extract best 20 seconds
    video20 = extract_best_4_seconds(ffmpeg_path, input_file, total_duration, fps, window_size=20)

    # Get info for the 20-second clip
    response = get_video_duration(ffprobe_path, video20)
    total_duration_20 = response['duration']
    fps_20 = response['fps']

    # Shorten the 20-second clip down to 4 seconds
    short_filename = shorten_video(ffmpeg_path, video20, total_duration_20, fps_20, desired_duration=4.0)
    os.remove(video20)
    return short_filename


def extract_best_32_seconds_x8(ffmpeg_path, ffprobe_path, input_file, total_duration, fps):
    # First extract best 32 seconds
    video32 = extract_best_4_seconds(ffmpeg_path, input_file, total_duration, fps, window_size=32)

    # Get info for the 32-second clip
    response = get_video_duration(ffprobe_path, video32)
    total_duration_32 = response['duration']
    fps_32 = response['fps']

    # Shorten the 32-second clip down to 4 seconds
    short_filename = shorten_video(ffmpeg_path, video32, total_duration_32, fps_32, desired_duration=4.0)
    os.remove(video32)
    return short_filename

def extract_best_4_audio_seconds(ffmpeg_path, input_file, total_duration, fps):
    """
    Extract the loudest 4-second segment from 'input_file'.
    Returns the path to the newly created "loudest4" MP4 file.
    """

    import numpy as np  # Make sure numpy is available

    # Set parameters
    segment_duration = 4  # 4-second window for extraction

    # Define the output filename
    folder = os.path.dirname(input_file)
    filename = os.path.basename(input_file)
    name, ext = os.path.splitext(filename)
    loudest_4sec_filename = os.path.join(folder, f"{name}_loudest4{ext}")

    # Create an intermediary file with a lower audio sampling rate (e.g., 8kHz)
    low_sample_rate_file = os.path.join(folder, f"{name}_low_sample_rate{ext}")
    cmd_low_sample_rate = (
        f"\"{ffmpeg_path}\" -i \"{input_file}\" -ar 8000 -ac 1 "
        f"-c:a aac -c:v copy -hide_banner -loglevel error -y \"{low_sample_rate_file}\""
    )
    subprocess.run(cmd_low_sample_rate, shell=True, check=True)

    # Step 1: Extract audio loudness levels from the lower-sample-rate file
    cmd_loudness = (
        f"\"{ffmpeg_path}\" -i \"{low_sample_rate_file}\" "
        f"-af \"astats=metadata=1:reset=1,ametadata=mode=print:key=lavfi.astats.Overall.RMS_level\" "
        f"-f null - -hide_banner -loglevel error"
    )
    result = subprocess.run(cmd_loudness, shell=True, capture_output=True, text=True, check=True)

    # Parse the output to get loudness levels and timestamps
    loudness_levels = []
    timestamps = []
    for line in result.stderr.splitlines():
        if "lavfi.astats.Overall.RMS_level" in line:
            # Extract loudness level
            level_str = line.split('=')[-1].strip()
            level = float(level_str)
            if level == float('-inf'):
                level = -100.0  # Replace -inf with a low value to handle silent frames
            loudness_levels.append(level)
        elif "pts_time" in line:
            # Extract timestamp
            time_str = line.split('pts_time:')[1].strip()
            timestamps.append(float(time_str))

    if not loudness_levels or not timestamps:
        logging.warning("No loudness levels or timestamps found.")
        # Cleanup
        if os.path.exists(low_sample_rate_file):
            os.remove(low_sample_rate_file)
        return None

    # Step 2: Resample timestamps to match the video frame rate
    video_frame_times = np.arange(0, total_duration, 1 / fps)
    loudness_interpolated = np.interp(video_frame_times, timestamps, loudness_levels)

    # Step 3: Find the loudest 4-second window via cumulative sum
    num_video_frames_in_segment = int(segment_duration * fps)
    cumulative_sum = np.cumsum(loudness_interpolated)

    # We can't compute the difference for the first 'segment' in a simple manner without offset,
    # so we handle the boundary conditions carefully:
    #   - cumulative_sum[x + segment] - cumulative_sum[x]
    # for x in [0 ... len(...)-segment]
    if len(cumulative_sum) < num_video_frames_in_segment:
        logging.warning("Video shorter than 4 seconds, no window to extract.")
        # Cleanup
        os.remove(low_sample_rate_file)
        return None

    # max start frame in range
    loudest_start_frame = np.argmax(
        cumulative_sum[num_video_frames_in_segment:] - cumulative_sum[:-num_video_frames_in_segment]
    )
    best_start_time = loudest_start_frame / fps

    # Step 4: Extract the 4-second segment with the highest loudness
    cmd_extract_loudest = (
        f"\"{ffmpeg_path}\" -i \"{input_file}\" -ss {best_start_time:.2f} -t {segment_duration} "
        f"-map 0 -c:v libx264 -c:a aac -hide_banner -loglevel error -y \"{loudest_4sec_filename}\""
    )
    subprocess.run(cmd_extract_loudest, shell=True, check=True)

    # Clean up the intermediary file
    os.remove(low_sample_rate_file)

    return loudest_4sec_filename

def shorten_video_by_removing_dupes(ffmpeg_path, ffprobe_path, input_file, total_duration, fps):
    folder = os.path.dirname(input_file)
    filename = os.path.basename(input_file)
    name, ext = os.path.splitext(filename)

    intermediate_file = os.path.join(folder, f"{name}_remove-dupes{ext}")

    cmd_mpdecimate = (
        f"\"{ffmpeg_path}\" -i \"{input_file}\" -hide_banner -loglevel error "
        f"-vf \"decimate,setpts=N/FRAME_RATE/TB\" "
        f"-vsync vfr -map 0 -c:v libx264 -c:a aac -y \"{intermediate_file}\""
    )
    subprocess.run(cmd_mpdecimate, shell=True, check=True)

    # Now re-check the duration/fps of this intermediate file
    response = get_video_duration(ffprobe_path, intermediate_file)
    total_duration_intermediate = response['duration']
    fps_intermediate = response['fps']

    # Finally shorten the intermediate video (down to 4 seconds by default)
    short_filename = shorten_video(ffmpeg_path, intermediate_file, total_duration_intermediate, fps_intermediate)
    
    # Clean up
    os.remove(intermediate_file)
    
    return short_filename


def remove_duplicate_frames_by_scene(ffmpeg_path, input_file, fps, scene_threshold=0.003):
    folder = os.path.dirname(input_file)
    filename = os.path.basename(input_file)
    name, ext = os.path.splitext(filename)
    
    output_file = os.path.join(folder, f"{name}_remove-dupes2{ext}")

    def extract_frame_timestamps():
        result = subprocess.run(
            [
                ffmpeg_path,
                "-i", input_file,
                "-vf", f"select='gt(scene,{scene_threshold})',showinfo",
                "-f", "null", "-"
            ],
            stderr=subprocess.PIPE, text=True, check=True
        )
        
        output = result.stderr
        frame_timestamps = []
        for line in output.splitlines():
            if 'pts_time' in line:
                pts_time = float(line.split('pts_time:')[1].split()[0])
                frame_timestamps.append(pts_time)
        return frame_timestamps

    def process_video(frame_timestamps, scene_threshold=0.003):
        if not frame_timestamps:
            # If no timestamps found, just copy input to output
            shutil.copyfile(input_file, output_file)
            return

        original_frame_count = frame_timestamps[-1] * fps
        kept_frame_count = len(frame_timestamps)
        frames_removed = original_frame_count - kept_frame_count

        if frames_removed <= 0:
            # No frames removed, copy input to output
            shutil.copyfile(input_file, output_file)
            return

        time_removed = frames_removed / fps  
        original_duration = frame_timestamps[-1]
        new_duration = original_duration - time_removed

        subprocess.run([
            ffmpeg_path, 
            "-i", input_file,
            "-vf", f"select='gt(scene,{scene_threshold})',setpts=N/FRAME_RATE/TB",
            "-to", f"{new_duration:.3f}",
            "-vsync", "vfr",
            "-c:v", "libx264",
            "-c:a", "aac",
            "-af", "aresample=async=1",
            "-y", 
            "-hide_banner", 
            "-loglevel", "error", 
            output_file
        ], check=True)

    frame_timestamps = extract_frame_timestamps()
    process_video(frame_timestamps, scene_threshold)
    return output_file


def detect_video_movement(ffmpeg_path, ffprobe_path, input_file):
    folder = os.path.dirname(input_file)
    scene_log_file = os.path.join(folder, "tmp_scene_log.txt")

    # 1) Get the video's total duration using ffprobe
    response = get_video_duration(ffprobe_path, input_file)
    total_duration = response['duration']

    # 2) Detect scenes with ffmpeg and write metadata (scene scores) to a log file
    cmd_scene_detect = (
        f"\"{ffmpeg_path}\" -i \"{input_file}\" -hide_banner -loglevel error "
        f"-vf \"select='gte(scene,0)',metadata=print:file='{scene_log_file}'\" "
        f"-f null -"
    )
    subprocess.run(cmd_scene_detect, shell=True, check=True)

    # 3) Parse the log file to sum up the scene scores
    total_movement_score = 0.0
    frame_count = 0
    if os.path.exists(scene_log_file):
        with open(scene_log_file, 'r') as log_file:
            for line in log_file:
                if "lavfi.scene_score" in line:
                    score_str = line.split('lavfi.scene_score=')[1].strip()
                    total_movement_score += float(score_str)
                    frame_count += 1
        os.remove(scene_log_file)

    # 4) Compute average movement per second
    if total_duration > 0:
        average_movement_per_second = total_movement_score / total_duration
    else:
        average_movement_per_second = 0.0

    return average_movement_per_second


def get_audio_filename_by_movement_score(movement_score):
    json_file_path = os.path.join(os.getcwd(), 'bpm_results.json')

    with open(json_file_path, 'r') as json_file:
        bpm_data = json.load(json_file)

    min_movement_score = 0.0
    max_movement_score = 1.0
    min_bpm = 60.0
    max_bpm = 180.0

    # Clamp
    clamped_movement_score = max(min(movement_score, max_movement_score), min_movement_score)
    # Normalize
    normalized_score = (clamped_movement_score - min_movement_score) / (max_movement_score - min_movement_score)

    target_bpm = min_bpm + normalized_score * (max_bpm - min_bpm)

    closest_filename = None
    closest_id = None
    smallest_bpm_diff = float('inf')
    #chosen_bpm = float('inf')

    for item in bpm_data:
        bpm = item.get('bpm')
        filename = item.get('filename')
        item_id = item.get('id')
        bpm_diff = abs(bpm - target_bpm)
        if bpm_diff < smallest_bpm_diff:
            smallest_bpm_diff = bpm_diff
            closest_filename = filename
            closest_id = item_id
            #chosen_bpm = bpm

    return closest_id

def extract_best_clips(ffmpeg_path, ffprobe_path, input_file, total_duration, fps, window_size=4.0, clips=4):
    logging.info("inside the extract best clips function")
    folder = os.path.dirname(input_file)
    filename = os.path.basename(input_file)
    name, ext = os.path.splitext(filename)

    # Step 1: Detect scenes (write metadata to scene_log_file)
    scene_log_file = os.path.join(folder, "temp_scene_log.txt")
    cmd_scene_detect = (
        f"\"{ffmpeg_path}\" -i \"{input_file}\" -hide_banner -loglevel error "
        f"-vf \"select='gt(scene,0)',metadata=print:file={scene_log_file}\" "
        f"-y -an -f null -"
    )
    subprocess.run(cmd_scene_detect, shell=True, check=True)
    logging.info("finished running 1st cmd")

    scene_changes = [0.0]
    scene_scores = [0.0]

    with open(scene_log_file, 'r') as log_file:
        for line in log_file:
            if "pts_time" in line:
                time_str = line.split('pts_time:')[1].strip()
                scene_changes.append(float(time_str))
            if "lavfi.scene_score" in line:
                score_str = line.split('lavfi.scene_score=')[1].strip()
                scene_scores.append(float(score_str))

    scene_changes.append(total_duration)
    scene_scores.append(0.0)

    # Step 2: Compute window scores
    windows = []
    for i in range(len(scene_changes)):
        start_time = scene_changes[i]
        end_time = start_time + window_size
        if end_time > total_duration:
            break

        window_score = 0.0
        for j, change_time in enumerate(scene_changes):
            if start_time <= change_time < end_time:
                window_score += scene_scores[j]
        windows.append((start_time, end_time, window_score))

    # Sort by score descending
    windows.sort(key=lambda x: x[2], reverse=True)
    logging.info("sorted")
    # Helper to check overlap
    def is_overlapping(a, b):
        return not (a[1] <= b[0] or b[1] <= a[0])

    best_segments = []
    for w in windows:
        if len(best_segments) == clips:
            break
        # Check overlap with selected segments
        overlap_found = any(is_overlapping(w, s) for s in best_segments)
        if not overlap_found:
            best_segments.append(w)

    best_segments.sort(key=lambda x: x[0])

    # Step 3: Extract each segment
    temp_files = []
    for idx, (start_time, end_time, _) in enumerate(best_segments):
        duration = end_time - start_time
        output_temp = os.path.join(folder, f"{name}_clip_{idx}{ext}")
        cmd_ffmpeg = (
            f"\"{ffmpeg_path}\" -i \"{input_file}\" -hide_banner -loglevel error "
            f"-ss {start_time} -t {duration} -map 0 -c:v libx264 -c:a aac -y \"{output_temp}\""
        )
        subprocess.run(cmd_ffmpeg, shell=True, check=True)
        logging.info("cutting 1s video")
        temp_files.append(output_temp)

    # Step 4: Concatenate the selected segments
    #concat_list_path = os.path.join(folder, f"{name}_concat_list.txt")
    #with open(concat_list_path, 'w') as f:
    #    for tmp in temp_files:
    #        f.write(f"file '{tmp}'\n")

    final_output_file = os.path.join(folder, f"{name}_best_{clips}_clips_concat{ext}")
    
    num_inputs = len(temp_files)
    inputs_str = " ".join(f'-i "{fn}"' for fn in temp_files)
    streams_str = " ".join(f'[{i}:v][{i}:a]' for i in range(num_inputs))
    filter_complex_str = f'"{streams_str}concat=n={num_inputs}:v=1:a=1 [v][a]"'

    cmd_concat = (
        f'"{ffmpeg_path}" {inputs_str} '
        f'-filter_complex {filter_complex_str} '
        f'-map "[v]" -map "[a]" '
        f'-hide_banner -loglevel error -y "{final_output_file}"'
    )
    subprocess.run(cmd_concat, shell=True, check=True)
    logging.info("finished running 2nd cmd")
    
    #cmd_concat = (
    #    f"\"{ffmpeg_path}\" -f concat -safe 0 -i \"{concat_list_path}\" "
    #    f"-c copy -hide_banner -loglevel error -y \"{final_output_file}\""
    #)
    #subprocess.run(cmd_concat, shell=True, check=True)

    # Clean up
    os.remove(scene_log_file)
    #os.remove(concat_list_path)
    for tmp in temp_files:
        if os.path.exists(tmp):
            os.remove(tmp)

    return final_output_file    

def collage_videos(ffmpeg_path, filenames, total_duration, fps, window_size=4.0):
    """
    Creates a 2x2 mosaic (collage) of 4 videos, resulting in a 1080x1920 output video.
    
    Parameters:
        ffmpeg_path (str): Path to the ffmpeg executable.
        filenames (list of str): List of 4 video file paths.
        total_duration (float): Duration (in seconds) of the input videos.
        fps (float): Frames per second of the input videos.
        window_size (float, optional): The window size to use in extract_best_4_seconds (default is 4.0).
        
    Returns:
        str: The output file name (full path) of the collage video.
    """
    if len(filenames) != 4:
        raise ValueError("The 'filenames' list must contain exactly 4 video file paths.")

    # Process each video file to extract the best 4 seconds.
    processed_filenames = []
    for filename in filenames:
        best_4_filename = extract_best_4_seconds(ffmpeg_path, filename, total_duration, fps, window_size)
        processed_filenames.append(best_4_filename)

    # Construct the output file name based on the first file's path.
    folder = os.path.dirname(filenames[0])
    base_filename = os.path.basename(filenames[0])
    name, ext = os.path.splitext(base_filename)
    output_file = os.path.join(folder, f"{name}_collage{ext}")

    # Construct the FFmpeg command using the provided ffmpeg_path.
    cmd = (
        f"\"{ffmpeg_path}\" "  # Use ffmpeg_path here
        f"-i \"{processed_filenames[0]}\" "
        f"-i \"{processed_filenames[1]}\" "
        f"-i \"{processed_filenames[2]}\" "
        f"-i \"{processed_filenames[3]}\" "
        f"-filter_complex \""
        # Scale each input to 270x480 (adjust dimensions as needed)
        f"[0:v]scale=270:480[p1]; "
        f"[1:v]scale=270:480[p2]; "
        f"[2:v]scale=270:480[p3]; "
        f"[3:v]scale=270:480[p4]; "
        # Stack videos: create top and bottom rows then combine them vertically
        f"[p1][p2]hstack=inputs=2[top]; "
        f"[p3][p4]hstack=inputs=2[bottom]; "
        f"[top][bottom]vstack=inputs=2\" "
        # Force the final size to 540x960 (adjust as needed)
        f"-s 540x960 "
        f"-hide_banner -loglevel error -y \"{output_file}\""
    )

    # Execute the command.
    subprocess.run(cmd, shell=True, check=True)

    # Clean up temporary processed files.
    for pf in processed_filenames:
        if os.path.exists(pf):
            os.remove(pf)

    return output_file


#######################################################################################
def validate_request(req):
    """
    Validate and extract parameters from the request.
    Returns a tuple: (fileNames, containerName, funcId, detectAudio).
    Raises ValueError if a parameter is missing or invalid.
    """
    fileName = req.params.get('fileName')
    fileNames = fileName.split("|") if fileName else []
    containerName = req.params.get('containerName')
    funcId_str = req.params.get('funcId')
    detectAudio_str = req.params.get('detectAudio')

    # Check JSON body if needed.
    if not fileName or not containerName:
        try:
            req_body = req.get_json()
            if not fileName:
                fileName = req_body.get('fileName')
                fileNames = fileName.split("|") if fileName else []
            if not containerName:
                containerName = req_body.get('containerName')
            if not funcId_str:
                funcId_str = req_body.get('funcId')
            if detectAudio_str is None:
                detectAudio_str = req_body.get('detectAudio')
        except Exception:
            pass

    if not fileNames:
        raise ValueError("Please provide a 'fileName' parameter as a URL to an MP4 file.")
    if not containerName:
        raise ValueError("Please provide a 'containerName' parameter.")
    if not all(fn.startswith("http://") or fn.startswith("https://") for fn in fileNames):
        raise ValueError("Each 'fileName' must be a valid URL to an MP4 file.")

    funcId = None
    if funcId_str:
        try:
            funcId = int(funcId_str)
        except ValueError:
            raise ValueError("funcId must be an integer.")
    detectAudio = detectAudio_str.lower() == "true" if detectAudio_str else False

    return fileNames, containerName, funcId, detectAudio

def download_file(url, local_path):
    """
    Download a file from the given URL and save it at local_path.
    """
    logging.info(f"Attempting to download file from URL: {url}")
    
    # Check if the file exists before downloading
    if os.path.exists(local_path):
        logging.info(f"File already exists at {local_path} before download.")
    else:
        logging.info(f"No file found at {local_path} before download.")
    
    r = requests.get(url, stream=True)
    r.raise_for_status()
    
    with open(local_path, "wb") as f:
        for chunk in r.iter_content(chunk_size=8192):
            f.write(chunk)
    
    # Check if the file exists after downloading
    if os.path.exists(local_path):
        logging.info(f"File successfully downloaded and exists at {local_path}.")
    else:
        logging.error(f"Download failed; file does not exist at {local_path}.")


def generate_music_video(ffmpeg_path, input_file, song):
    """
    Generates a music video by:
    1. Resizing the input video to 540x960 pixels.
    2. Overlaying a WEBM named [song].webm.
    3. Replacing the audio with [song].mp3.
    
    Parameters:
        input_file (str): The input video file (mp4).
        song (str): The base name for the webm and mp3 files (without extension).
        ffmpeg_path (str): The full path to the ffmpeg executable.
        
    Returns:
        str: The filename of the generated output video.
    """
    logging.info("Starting generate_music_video with input_file=%s, song=%s", input_file, song)

    # Build file names for the webm and mp3
    webm_file = os.path.join(os.getcwd(), "lyrics", f"{song}.webm")
    mp3_file = os.path.join(os.getcwd(), "lyrics", f"{song}.mp3")
    
    # Check existence of webm_file
    if os.path.isfile(webm_file):
        logging.info("WEBM file found: %s", webm_file)
    else:
        logging.warning("WEBM file does NOT exist: %s", webm_file)

    # Check existence of mp3_file
    if os.path.isfile(mp3_file):
        logging.info("MP3 file found: %s", mp3_file)
    else:
        logging.warning("MP3 file does NOT exist: %s", mp3_file)
    
    # Create an output filename by appending the song name before the extension.
    base, ext = os.path.splitext(input_file)
    output_file = f"{base}_{song}{ext}"
    logging.info("Output video will be: %s", output_file)
    
    # Build the ffmpeg command.
    command = [
        ffmpeg_path,
        "-i", input_file,
        "-c:v", "libvpx-vp9",   # <--- Make sure you really want both this and libx264
        "-i", webm_file,
        "-i", mp3_file,
        "-filter_complex", "[0:v]scale=540:960[scaled];[scaled][1:v]overlay=0:0[out]",
        "-map", "[out]",
        "-map", "2:a",
        "-c:v", "libx264",
        "-c:a", "aac",
        "-shortest",
        output_file
    ]
    
    # Log the command before running
    logging.info("Running ffmpeg command: %s", " ".join(command))
    
    try:
        subprocess.run(command, check=True)
        logging.info("ffmpeg command completed successfully.")
    except subprocess.CalledProcessError as e:
        logging.error("An error occurred during the video generation process: %s", e)
        raise e
    
    # Verify the output file
    if os.path.isfile(output_file):
        logging.info("Output file was successfully created: %s", output_file)
    else:
        logging.warning("Output file does not exist: %s", output_file)
    
    return output_file


def process_single_file(ffmpeg_path, ffprobe_path, input_file, funcId):
    """
    Process a single file based on the provided funcId.
    Returns either the processed file path or, for funcId 0, a dict with possible function IDs.
    """
    video_info = get_video_duration(ffprobe_path, input_file)
    total_duration = video_info['duration']
    fps = video_info['fps']

    if funcId == 0:
        possible_funcs = []
        for possible_id, min_duration in MIN_DURATION_REQUIREMENTS.items():
            if total_duration >= min_duration:
                possible_funcs.append(possible_id)
        return {"possibleFuncs": possible_funcs}

    # Validate minimum duration requirement.
    if funcId in MIN_DURATION_REQUIREMENTS:
        required_duration = MIN_DURATION_REQUIREMENTS[funcId]
        if total_duration < required_duration:
            raise ValueError(
                f"This video is only {total_duration:.1f}s long, which is less than the required "
                f"{required_duration}s for funcId={funcId}."
            )
    else:
        raise ValueError(f"funcId={funcId} not recognized.")

    if funcId == 1:
        logging.info("Shorten video function starting")
        processed_file = shorten_video(ffmpeg_path, input_file, total_duration, fps,
                                       desired_duration=4.0, speed_up_audio=True)
        logging.info("Shorten video function ending")
    elif funcId == 2:
        logging.info("Boomerang video function starting")
        processed_file = boomerang_video(ffmpeg_path, input_file, total_duration, fps)
        logging.info("Boomerang video function ending")
    elif funcId == 3:
        logging.info("Extract best 4 seconds function starting")
        processed_file = extract_best_4_seconds(ffmpeg_path, input_file, total_duration, fps, window_size=4.0)
        logging.info("Extract best 4 seconds function ending")
    elif funcId == 4:
        logging.info("Extract best 8 seconds x2 function starting")
        processed_file = extract_best_8_seconds_x2(ffmpeg_path, ffprobe_path, input_file, total_duration, fps)
        logging.info("Extract best 8 seconds x2 function ending")
    elif funcId == 5:
        logging.info("Extract best 12 seconds x3 function starting")
        processed_file = extract_best_12_seconds_x3(ffmpeg_path, ffprobe_path, input_file, total_duration, fps)
        logging.info("Extract best 12 seconds x3 function ending")
        
    elif funcId == 6:
        logging.info("Extract best 16 seconds x4 function starting")
        processed_file = extract_best_16_seconds_x4(
            ffmpeg_path,
            ffprobe_path,
            input_file,
            total_duration,
            fps
        )
        logging.info("Extract best 16 seconds x4 function ending")

    elif funcId == 7:
        logging.info("Extract best 20 seconds x5 function starting")
        processed_file = extract_best_20_seconds_x5(
            ffmpeg_path,
            ffprobe_path,
            input_file,
            total_duration,
            fps
        )
        logging.info("Extract best 20 seconds x5 function ending")

    elif funcId == 8:
        logging.info("Extract best 32 seconds x8 function starting")
        processed_file = extract_best_32_seconds_x8(
            ffmpeg_path,
            ffprobe_path,
            input_file,
            total_duration,
            fps
        )
        logging.info("Extract best 32 seconds x8 function ending")    
    elif funcId == 9:
        logging.info("Extract best 4 audio seconds function starting")
        processed_file = extract_best_4_audio_seconds(
            ffmpeg_path,
            input_file,
            total_duration,
            fps
        )
        logging.info("Extract best 4 audio seconds function ending")           
    elif funcId == 10:
        logging.info("Shorten video by removing dupes function starting")
        processed_file = shorten_video_by_removing_dupes(
            ffmpeg_path,
            ffprobe_path,
            input_file,
            total_duration,
            fps
        )
        logging.info("Shorten video by removing dupes function ending")
        
    elif funcId == 11:
        logging.info("Boomerang best 20 seconds x10 function starting")
        processed_file = boomerang_video_best_20x10(
            ffmpeg_path,
            ffprobe_path,
            input_file,
            total_duration,
            fps
        )
        logging.info("Boomerang best 20 seconds x10 function ending")                 
    elif funcId == 12:
        logging.info("Boomerang + Zoom in/out function starting")

        # 1) Produce the boomerang version of the input file
        boomerang_file = boomerang_video(ffmpeg_path, input_file, total_duration, fps)

        # 2) Apply zoom in/out effect on the boomerang output
        logging.info("Now applying zoom_in_out effect on the boomerang output")
        processed_file = zoom_in_out(ffmpeg_path, ffprobe_path, boomerang_file, max_zoom=0.25)

        logging.info("Boomerang + Zoom in/out function ending")                    
        
    elif funcId == 13:
        logging.info("Boomerang best 2 seconds function starting")
        processed_file = boomerang_video_best_2(
            ffmpeg_path,
            ffprobe_path,
            input_file,
            total_duration,
            fps
        )
        logging.info("Boomerang best 2 seconds function ending")                    
    elif funcId == 14:
        logging.info("Boomerang best 2 seconds + Zoom in/out function starting")

        # Create the 2-second boomerang clip
        boomerang_file = boomerang_video_best_2(
            ffmpeg_path, 
            ffprobe_path, 
            input_file, 
            total_duration, 
            fps
        )

        # Then apply zoom in/out
        processed_file = zoom_in_out(ffmpeg_path, ffprobe_path, boomerang_file, max_zoom=0.25)

        logging.info("Boomerang best 2 seconds + Zoom in/out function ending")         
    elif funcId == 15:
        logging.info("Removing duplicate frames by scene function starting")
        processed_file = remove_duplicate_frames_by_scene(
            ffmpeg_path,
            input_file,
            fps,
            scene_threshold=0.003
        )
        logging.info("Removing duplicate frames by scene function ending")          
        
        logging.info("Extract best 4 seconds function starting")
        processed_file = extract_best_4_seconds(
            ffmpeg_path, processed_file, total_duration, fps, window_size=4.0
        )
        logging.info("Extract best 4 seconds function ending")                        
        
    elif funcId == 16:
        logging.info("Extract best clips function starting")
        processed_file = extract_best_clips(
            ffmpeg_path,
            ffprobe_path,
            input_file,
            total_duration,
            fps,
            window_size=1.0,
            clips=4
        )
        logging.info("Extract best clips function ending")

    elif funcId == 17:
        logging.info("Transcription")
        processed_file = extract_best_4_seconds(
            ffmpeg_path, input_file, total_duration, fps, window_size=4.0
        )
        processed_file = transcribe.transcribe_video(ffmpeg_path, processed_file)
        logging.info("Transcription function ending")
    elif funcId == 18:
        logging.info("Music video 1")
        processed_file = extract_best_4_seconds(
            ffmpeg_path, input_file, total_duration, fps, window_size=4.0
        )
        processed_file = generate_music_video(ffmpeg_path, processed_file, 'Diamonds')
        logging.info("Music video function ending")        
        
    elif funcId == 19:
        logging.info("Music video 1")
        processed_file = extract_best_4_seconds(
            ffmpeg_path, input_file, total_duration, fps, window_size=4.0
        )
        processed_file = generate_music_video(ffmpeg_path, processed_file, 'NewYork')
        logging.info("Music video function ending")        

    elif funcId == 20:
        logging.info("Music video 1")
        processed_file = extract_best_4_seconds(
            ffmpeg_path, input_file, total_duration, fps, window_size=4.0
        )
        processed_file = generate_music_video(ffmpeg_path, processed_file, 'ThatWay')
        logging.info("Music video function ending")        
    elif funcId == 21:
        logging.info("Music video paparazzi")
        processed_file = extract_best_4_seconds(
            ffmpeg_path, input_file, total_duration, fps, window_size=5.0
        )
        processed_file = generate_paparazzi_video(ffmpeg_path, processed_file, 'Paparazzi',[0, 1.540, 2.007, 2.607, 3.040, 3.607, 3.940])
        logging.info("Music video paparazzi function ending")       
    elif funcId == 22:
        logging.info("Auto-Collage")
        processed_file = create_collage(ffmpeg_path, input_file, 'Collage',total_duration)
        logging.info("Music video paparazzi function ending")                   
    elif funcId == 23:
        logging.info("Random lyric video")
        processed_file = extract_best_4_seconds(
            ffmpeg_path, input_file, total_duration, fps, window_size=4.0
        )
        processed_file = generate_music_video(ffmpeg_path, processed_file, random.choice(["ThatWay", "NewYork", "Diamonds"]))
        logging.info("Music video function ending")
        
    else:
        raise ValueError(f"funcId={funcId} not implemented for single file processing.")

    return processed_file

def process_multiple_files(ffmpeg_path, ffprobe_path, downloaded_files, funcId):
    """
    Process multiple files. For now, we assume that multi-file processing uses collage_videos.
    Returns the processed file.
    """
    if len(downloaded_files) < 2:
        raise ValueError("Multiple file processing requires at least 2 files.")

    logging.info("Getting video duration and fps with ffprobe for multiple files")
    # Assume the videos have similar properties; use the first file’s info.
    video_info = get_video_duration(ffprobe_path, downloaded_files[0])
    total_duration = video_info['duration']
    fps = video_info['fps']

    logging.info("Multi-file processing function starting")
    processed_file = collage_videos(ffmpeg_path, downloaded_files, total_duration, fps, window_size=4.0)
    logging.info("Multi-file processing function ending")
    return processed_file

def upload_to_blob(processed_file, containerName, conn_str):
    """
    Upload the processed file to Azure Blob Storage and return the blob URL.
    """
    from azure.storage.blob import BlobServiceClient, ContentSettings
    from azure.core.exceptions import ResourceExistsError

    blob_service_client = BlobServiceClient.from_connection_string(conn_str)
    container_client = blob_service_client.get_container_client(containerName)
    try:
        container_client.create_container()
    except ResourceExistsError:
        pass

    blob_name = f"AiOutputs/processed_{uuid.uuid4()}.mp4"
    blob_client = container_client.get_blob_client(blob_name)
    logging.info("Uploading processed video to blob storage")
    with open(processed_file, "rb") as data:
        blob_client.upload_blob(
            data,
            overwrite=True,
            content_settings=ContentSettings(content_type='video/mp4')
        )
    logging.info("Upload complete")
    return blob_client.url

#
# Main Azure Function
#
@app.route(route="func", methods=["GET", "POST"])
def func_handler(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("Azure Function triggered.")
    try:
        fileNames, containerName, funcId, detectAudio = validate_request(req)
    except ValueError as ve:
        return func.HttpResponse(str(ve), status_code=400)

    conn_str = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
    if not conn_str:
        return func.HttpResponse(
            "Azure Storage connection string not found in environment variables.",
            status_code=500
        )

    # Create a temporary directory (without a 'with' block).
    tmpdir = tempfile.mkdtemp()
    try:
        # Prepare ffmpeg and ffprobe executables in tmpdir.
        ffmpeg_src = os.path.join(os.getcwd(), 'ffmpeg')
        ffprobe_src = os.path.join(os.getcwd(), 'ffprobe')
        ffmpeg_path = os.path.join(tmpdir, 'ffmpeg')
        ffprobe_path = os.path.join(tmpdir, 'ffprobe')
        logging.info("Copying ffmpeg and ffprobe to temporary directory")
        shutil.copyfile(ffmpeg_src, ffmpeg_path)
        shutil.copyfile(ffprobe_src, ffprobe_path)
        os.chmod(ffmpeg_path, 0o755)
        os.chmod(ffprobe_path, 0o755)

        # Decide processing based on the number of files.
        if len(fileNames) > 1:
            # Multi-file processing.
            downloaded_files = []
            for idx, url in enumerate(fileNames):
                local_file = os.path.join(tmpdir, f"input_{idx}.mp4")
                download_file(url, local_file)
                downloaded_files.append(local_file)
            processed_file = process_multiple_files(ffmpeg_path, ffprobe_path, downloaded_files, funcId)
        else:
            # Single file processing.
            input_file = os.path.join(tmpdir, "input.mp4")
            download_file(fileNames[0], input_file)
            result = process_single_file(ffmpeg_path, ffprobe_path, input_file, funcId)
            # For funcId 0, result is a dict with possible processing options.
            if isinstance(result, dict):
                return func.HttpResponse(
                    json.dumps(result),
                    status_code=200,
                    mimetype="application/json"
                )
            processed_file = result

        # Run audio detection if enabled.
        audio_result = ""
        if detectAudio and processed_file:
            movement_score = detect_video_movement(ffmpeg_path, ffprobe_path, processed_file)
            audio_result = get_audio_filename_by_movement_score(movement_score)

        # Upload the processed file to Blob Storage.
        blob_url = upload_to_blob(processed_file, containerName, conn_str)

    except Exception as e:
        logging.error(f"Error processing video: {e}")
        return func.HttpResponse("Failed to process video.", status_code=500)
    finally:
        shutil.rmtree(tmpdir)

    response_json = {
        "output": blob_url,
        "audio": audio_result
    }
    return func.HttpResponse(
        json.dumps(response_json),
        status_code=200,
        mimetype="application/json"
    )
