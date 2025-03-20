#!/usr/bin/env python3
import argparse
import random
import subprocess
import os

def get_video_duration(input_file):
    """Uses ffprobe to get the video duration (in seconds)."""
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        input_file
    ]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        raise Exception("Error running ffprobe: " + result.stderr)
    try:
        return float(result.stdout.strip())
    except ValueError:
        raise Exception("Could not parse duration from ffprobe output.")

def run_command(cmd):
    """Runs a command and raises an exception if it fails."""
    print("Running command:", " ".join(cmd))
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        print("Error output:", result.stderr)
        raise Exception("Command failed: " + " ".join(cmd))
    return result.stdout

def cleanup(files):
    """Deletes a list of files."""
    for file in files:
        try:
            os.remove(file)
            print(f"Deleted temporary file: {file}")
        except OSError as e:
            print(f"Error deleting file {file}: {e}")

def create_collage(ffmpeg_path, input_file, song, duration):
    # Determine the directory and base name of the input file
    input_abs = os.path.abspath(input_file)
    input_dir = os.path.dirname(input_abs)
    base_name, ext = os.path.splitext(os.path.basename(input_file))
    
    # Build file names for the webm and mp3 (assuming these live in a "lyrics" folder in cwd)
    webm_file = os.path.join(os.getcwd(), "lyrics", f"{song}.webm")
    mp3_file = os.path.join(os.getcwd(), "lyrics", f"{song}.mp3")
    
    temp_files = []  # List to hold intermediate file paths for later cleanup

    # Step 1: Resize the input video to 540x960.
    resized_file = os.path.join(input_dir, f"{base_name}_resized{ext}")
    temp_files.append(resized_file)
    cmd_resize = [
        ffmpeg_path, "-y", "-i", input_file,
        "-vf", "scale=540:960",
        resized_file
    ]
    run_command(cmd_resize)
    
    # Helper: Build input options for a given number of 0.5s segments from the resized video.
    def build_inputs(n):
        inputs = []
        for _ in range(n):
            start = random.uniform(0, duration - 0.5)
            inputs.extend(["-ss", str(start), "-t", "0.5", "-i", resized_file])
        return inputs

    # Step 2: Create an extra clip (clip0) to appear at the very beginning.
    clip0 = os.path.join(input_dir, f"{base_name}_clip0{ext}")
    temp_files.append(clip0)
    start = random.uniform(0, duration - 0.5)
    cmd_clip0 = [
        ffmpeg_path, "-y",
        "-i", resized_file,
        "-ss", str(start), "-t", "0.5",
        "-c:v", "libx264", "-c:a", "copy",
        clip0
    ]
    run_command(cmd_clip0)
    
    # Collage 1: 2 inputs, vertical layout, black and white.
    collage1 = os.path.join(input_dir, f"{base_name}_collage1{ext}")
    temp_files.append(collage1)
    inputs = build_inputs(2)
    filter_complex = (
        "[0:v]crop=540:480:0:240[v0];"
        "[1:v]crop=540:480:0:240[v1];"
        "[v0][v1]xstack=inputs=2:layout=0_0|0_480, hue=s=0[out]"
    )
    cmd_collage1 = [ffmpeg_path, "-y"] + inputs + [
        "-filter_complex", filter_complex,
        "-map", "[out]",
        collage1
    ]
    run_command(cmd_collage1)
    
    # Collage 2: 3 inputs, vertical stack, color.
    collage2 = os.path.join(input_dir, f"{base_name}_collage2{ext}")
    temp_files.append(collage2)
    inputs = build_inputs(3)
    filter_complex = (
        "[0:v]crop=540:320:0:320[v0];"
        "[1:v]crop=540:320:0:320[v1];"
        "[2:v]crop=540:320:0:320[v2];"
        "[v0][v1][v2]xstack=inputs=3:layout=0_0|0_320|0_640[out]"
    )
    cmd_collage2 = [ffmpeg_path, "-y"] + inputs + [
        "-filter_complex", filter_complex,
        "-map", "[out]",
        collage2
    ]
    run_command(cmd_collage2)
    
    # Collage 3: 4 inputs, 2x2 grid, black and white.
    collage3 = os.path.join(input_dir, f"{base_name}_collage3{ext}")
    temp_files.append(collage3)
    inputs = build_inputs(4)
    filter_complex = (
        "[0:v]scale=270:480[v0];"
        "[1:v]scale=270:480[v1];"
        "[2:v]scale=270:480[v2];"
        "[3:v]scale=270:480[v3];"
        "[v0][v1][v2][v3]xstack=inputs=4:layout=0_0|270_0|0_480|270_480, hue=s=0[out]"
    )
    cmd_collage3 = [ffmpeg_path, "-y"] + inputs + [
        "-filter_complex", filter_complex,
        "-map", "[out]",
        collage3
    ]
    run_command(cmd_collage3)
    
    # Collage 4: 6 inputs, 2 columns x 3 rows, color.
    collage4 = os.path.join(input_dir, f"{base_name}_collage4{ext}")
    temp_files.append(collage4)
    inputs = build_inputs(6)
    filter_complex = (
        "[0:v]crop=270:320:135:320[v0];"
        "[1:v]crop=270:320:135:320[v1];"
        "[2:v]crop=270:320:135:320[v2];"
        "[3:v]crop=270:320:135:320[v3];"
        "[4:v]crop=270:320:135:320[v4];"
        "[5:v]crop=270:320:135:320[v5];"
        "[v0][v1][v2][v3][v4][v5]xstack=inputs=6:layout=0_0|270_0|0_320|270_320|0_640|270_640[out]"
    )
    cmd_collage4 = [ffmpeg_path, "-y"] + inputs + [
        "-filter_complex", filter_complex,
        "-map", "[out]",
        collage4
    ]
    run_command(cmd_collage4)
    
    # Collage 5: 2 inputs, vertical layout, black and white.
    collage5 = os.path.join(input_dir, f"{base_name}_collage5{ext}")
    temp_files.append(collage5)
    inputs = build_inputs(2)
    filter_complex = (
        "[0:v]crop=540:480:0:240[v0];"
        "[1:v]crop=540:480:0:240[v1];"
        "[v0][v1]xstack=inputs=2:layout=0_0|0_480, hue=s=0[out]"
    )
    cmd_collage5 = [ffmpeg_path, "-y"] + inputs + [
        "-filter_complex", filter_complex,
        "-map", "[out]",
        collage5
    ]
    run_command(cmd_collage5)
    
    # Collage 6: 3 inputs, vertical stack, color.
    collage6 = os.path.join(input_dir, f"{base_name}_collage6{ext}")
    temp_files.append(collage6)
    inputs = build_inputs(3)
    filter_complex = (
        "[0:v]crop=540:320:0:320[v0];"
        "[1:v]crop=540:320:0:320[v1];"
        "[2:v]crop=540:320:0:320[v2];"
        "[v0][v1][v2]xstack=inputs=3:layout=0_0|0_320|0_640[out]"
    )
    cmd_collage6 = [ffmpeg_path, "-y"] + inputs + [
        "-filter_complex", filter_complex,
        "-map", "[out]",
        collage6
    ]
    run_command(cmd_collage6)
    
    # Collage 7: 4 inputs, 2x2 grid, black and white.
    collage7 = os.path.join(input_dir, f"{base_name}_collage7{ext}")
    temp_files.append(collage7)
    inputs = build_inputs(4)
    filter_complex = (
        "[0:v]scale=270:480[v0];"
        "[1:v]scale=270:480[v1];"
        "[2:v]scale=270:480[v2];"
        "[3:v]scale=270:480[v3];"
        "[v0][v1][v2][v3]xstack=inputs=4:layout=0_0|270_0|0_480|270_480, hue=s=0[out]"
    )
    cmd_collage7 = [ffmpeg_path, "-y"] + inputs + [
        "-filter_complex", filter_complex,
        "-map", "[out]",
        collage7
    ]
    run_command(cmd_collage7)
    
    # Step 3: Concatenate the extra clip and all 7 collages.
    concat_list = os.path.join(input_dir, f"{base_name}_concat_list.txt")
    with open(concat_list, "w") as f:
        f.write(f"file '{clip0}'\n")
        f.write(f"file '{collage1}'\n")
        f.write(f"file '{collage2}'\n")
        f.write(f"file '{collage3}'\n")
        f.write(f"file '{collage4}'\n")
        f.write(f"file '{collage5}'\n")
        f.write(f"file '{collage6}'\n")
        f.write(f"file '{collage7}'\n")
    temp_files.append(concat_list)
    
    final_file = os.path.join(input_dir, f"{base_name}_final{ext}")
    cmd_concat = [
        ffmpeg_path, "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", concat_list,
        "-c", "copy",
        final_file
    ]
    run_command(cmd_concat)
    print("Final video created:", final_file)
    
    # Step 4: (Optional) Cleanup intermediate files.
    cleanup(temp_files)
    
    # Step 5: Overlay the transparent WEBM and replace audio.
    final_output = os.path.join(input_dir, f"{base_name}_final_with_overlay_audio{ext}")
    cmd_overlay_audio = [
        ffmpeg_path, "-y",
        "-i", final_file,           # our final video from previous steps
        "-c:v", "libvpx-vp9",
        "-i", webm_file,            # transparent VP9 overlay
        "-i", mp3_file,             # new audio track
        "-filter_complex", "[0:v]scale=540:960[scaled];[scaled][1:v]overlay=0:0[out]",
        "-map", "[out]",           # use the overlaid video stream
        "-map", "2:a",             # use the audio from the mp3 file
        "-c:v", "libx264",         # encode video using H.264
        "-c:a", "aac",             # encode audio using AAC
        "-shortest",               # end when the shortest stream ends
        final_output
    ]
    run_command(cmd_overlay_audio)
    print("Final video with overlay and new audio created:", final_output)
    return final_output

if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description="Generate a collage video from random 0.5s segments of a resized video, then overlay a transparent WEBM and add new audio."
    )
    parser.add_argument(
        "input_file",
        nargs="?",
        default="Video.mp4",
        help="Input video file (default: Video.mp4)"
    )
    args = parser.parse_args()
    duration = get_video_duration(args.input_file)
    create_collage("ffmpeg", args.input_file, "Collage", duration)
