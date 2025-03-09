import os
import subprocess

def generate_music_video(ffmpeg_path, input_file, song):
    """
    Generates a music video by:
    1. Resizing the input video to 540x960 pixels.
    2. Overlaying a GIF named [song].gif.
    3. Replacing the audio with [song].mp3.
    
    Parameters:
        input_file (str): The input video file (mp4).
        song (str): The base name for the gif and mp3 files (without extension).
        ffmpeg_path (str): The full path to the ffmpeg executable.
        
    Returns:
        str: The filename of the generated output video.
    """
    # Build file names for the gif and mp3
    webm_file = f"lyrics\{song}.webm"
    mp3_file = f"lyrics\{song}.mp3"
    
    # Create an output filename by appending the song name before the extension.
    base, ext = os.path.splitext(input_file)
    output_file = f"{base}_{song}{ext}"
    
    # Build the ffmpeg command.
    # The filter_complex first scales the video to 540x960 and then overlays the gif.
    command = [
        ffmpeg_path,
        "-i", input_file,
        "-c:v", "libvpx-vp9",
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
    
    try:
        # Run the ffmpeg command.
        subprocess.run(command, check=True)
        return output_file
    except subprocess.CalledProcessError as e:
        print("An error occurred during the video generation process.")
        raise e

def main():
    ffmpeg_path = "ffmpeg"
    input_file = "Video.mp4"
    song = "Diamonds"
    
    output_file = generate_music_video(ffmpeg_path, input_file, song)
    print("Generated video:", output_file)

if __name__ == "__main__":
    main()        

