import os
import subprocess
import math
import logging
from faster_whisper import WhisperModel

def extract_audio(ffmpeg_path, input_file, output_audio):
    """
    Extracts the audio track from the input video and saves it as a WAV file.
    """
    cmd = f'"{ffmpeg_path}" -i "{input_file}" -vn -y "{output_audio}"'
    subprocess.run(cmd, shell=True, check=True)

def transcribe(audio):
    """
    Transcribes the given audio file using the Whisper model and returns the detected language and segments.
    """
    model = WhisperModel("small", device="cpu")
    logging.info('Before model transcribe')
    segments, info = model.transcribe(audio)
    
    language = info.language
    logging.info("Transcription language: %s", language)
    segments = list(segments)
    for segment in segments:
        logging.info("[%.2fs -> %.2fs] %s", segment.start, segment.end, segment.text)
    return language, segments

def format_time(seconds):
    """
    Converts seconds to an SRT timestamp format (HH:MM:SS,mmm).
    """
    hours = math.floor(seconds / 3600)
    seconds %= 3600
    minutes = math.floor(seconds / 60)
    seconds %= 60
    milliseconds = round((seconds - math.floor(seconds)) * 1000)
    seconds = math.floor(seconds)
    formatted_time = f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"
    return formatted_time

def generate_subtitle_file(subtitle_file, language, segments):
    """
    Generates an SRT subtitle file using the transcription segments.
    """
    text = ""
    for index, segment in enumerate(segments):
        segment_start = format_time(segment.start)
        segment_end = format_time(segment.end)
        text += f"{index+1}\n"
        text += f"{segment_start} --> {segment_end}\n"
        text += f"{segment.text}\n\n"
    with open(subtitle_file, "w", encoding="utf-8") as f:
        f.write(text)
    logging.info("Subtitle file generated at: %s", subtitle_file)

def add_subtitle_to_video(ffmpeg_path, input_file, subtitle_file, language, output_video, soft_subtitle=False):
    """
    Adds subtitles to the video. If soft_subtitle is True, the subtitle track is added as a separate stream;
    otherwise, the subtitles are burned into the video.
    """
    if soft_subtitle:
        # Use soft subtitles by embedding the subtitle file as a separate stream.
        subtitle_track_title = os.path.splitext(os.path.basename(subtitle_file))[0]
        cmd = (
            f'"{ffmpeg_path}" -i "{input_file}" -i "{subtitle_file}" '
            f'-c copy -c:s mov_text -metadata:s:s:0 language={language} '
            f'-metadata:s:s:0 title="{subtitle_track_title}" -y "{output_video}"'
        )
    else:
        # Burn subtitles into the video.
        cmd = f'"{ffmpeg_path}" -i "{input_file}" -vf "subtitles=\'{subtitle_file}\'" -y "{output_video}"'
    
    logging.info("Running ffmpeg command for adding subtitles: %s", cmd)
    subprocess.run(cmd, shell=True, check=True)

def transcribe_video(ffmpeg_path, input_file):
    """
    Main function that:
      1. Extracts audio from the input video.
      2. Transcribes the audio.
      3. Generates an SRT subtitle file.
      4. Burns the subtitles into the video.
      
    Returns the path to the output video file.
    """
    # Determine file names and directories
    video_dir = os.path.dirname(input_file)
    video_basename = os.path.basename(input_file)
    video_name, ext = os.path.splitext(video_basename)

    # Construct output file paths
    extracted_audio = os.path.join(video_dir, f"audio-{video_name}.wav")
    subtitle_file = os.path.join(video_dir, f"sub-{video_name}.srt")
    output_video = os.path.join(video_dir, f"output-{video_name}.mp4")

    logging.info('About to extract audio')
    # Extract the audio track from the video
    extract_audio(ffmpeg_path, input_file, extracted_audio)

    logging.info('About to start transcribing')
    # Transcribe the extracted audio
    language, segments = transcribe(extracted_audio)

    logging.info('About to generate subtitle file')
    # Generate the subtitle file using the transcription segments
    generate_subtitle_file(subtitle_file, language, segments)

    # Verify the existence of the subtitles file before calling ffmpeg
    if os.path.exists(subtitle_file):
        logging.info("Subtitle file exists: %s", subtitle_file)
    else:
        logging.warning("Subtitle file does not exist: %s", subtitle_file)
    
    logging.info('About to add subtitle to video')
    # Add (burn) the subtitles into the video. Set soft_subtitle to True to add them as a separate track.
    add_subtitle_to_video(ffmpeg_path, input_file, subtitle_file, language, output_video, soft_subtitle=False)

    return output_video
