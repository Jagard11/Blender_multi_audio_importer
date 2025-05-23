bl_info = {
    "name": "Multi-Audio Track Video Importer",
    "author": "Jagard11 & Claude AI",
    "version": (2, 0),
    "blender": (3, 0, 0),
    "location": "Video Sequence Editor > Sidebar > Multi-Audio",
    "description": "Import video with all its audio tracks into a metastrip using FFmpeg. Auto-downloads static binaries.",
    "category": "Sequencer",
    "warning": "Steam installs of Blender may not work with this addon due to the way steam segregates blender from the rest of the system. Manually installing static versions of ffmpeg and ffprobe into the addon directory is recommended.",
    "doc_url": "",
}

import bpy
import subprocess
import os
import tempfile
import json
import urllib.request
import tarfile
import shutil
from bpy.props import StringProperty, CollectionProperty, BoolProperty, IntProperty, PointerProperty
from bpy.types import Operator, Panel, PropertyGroup, AddonPreferences

class MultiAudioImporterPreferences(AddonPreferences):
    bl_idname = __name__

    def draw(self, context):
        layout = self.layout
        
        addon_dir = os.path.dirname(os.path.realpath(__file__))
        ffprobe_path = os.path.join(addon_dir, "ffprobe")
        ffmpeg_path = os.path.join(addon_dir, "ffmpeg")
        
        if os.path.isfile(ffprobe_path) and os.path.isfile(ffmpeg_path):
            layout.label(text="✓ FFmpeg static binaries are installed and ready", icon='CHECKMARK')
            layout.label(text=f"Location: {addon_dir}")
        else:
            layout.label(text="⚠ FFmpeg binaries not found", icon='ERROR')
            layout.operator("multi_audio.download_ffmpeg", icon="IMPORT")
        
        layout.separator()
        layout.operator("multi_audio.download_ffmpeg", text="Re-download FFmpeg Binaries", icon="FILE_REFRESH")

def download_ffmpeg_static():
    """Download and extract static FFmpeg binaries to addon directory"""
    addon_dir = os.path.dirname(os.path.realpath(__file__))
    
    # URLs for static builds (johnvansickle.com provides reliable static builds)
    ffmpeg_url = "https://johnvansickle.com/ffmpeg/builds/ffmpeg-git-amd64-static.tar.xz"
    
    try:
        # Download to temp file
        print("Downloading FFmpeg static binaries...")
        temp_file = os.path.join(tempfile.gettempdir(), "ffmpeg-static.tar.xz")
        
        with urllib.request.urlopen(ffmpeg_url) as response:
            with open(temp_file, 'wb') as f:
                shutil.copyfileobj(response, f)
        
        print("Extracting FFmpeg binaries...")
        
        # Extract ffprobe and ffmpeg
        with tarfile.open(temp_file, 'r:xz') as tar:
            # Find the ffmpeg and ffprobe files in the archive
            ffmpeg_member = None
            ffprobe_member = None
            
            for member in tar.getmembers():
                if member.name.endswith('/ffmpeg') and member.isfile():
                    ffmpeg_member = member
                elif member.name.endswith('/ffprobe') and member.isfile():
                    ffprobe_member = member
            
            if not ffmpeg_member or not ffprobe_member:
                raise Exception("Could not find ffmpeg or ffprobe in downloaded archive")
            
            # Extract to addon directory
            ffmpeg_member.name = "ffmpeg"
            ffprobe_member.name = "ffprobe"
            
            tar.extract(ffmpeg_member, addon_dir)
            tar.extract(ffprobe_member, addon_dir)
        
        # Make executable
        ffmpeg_path = os.path.join(addon_dir, "ffmpeg")
        ffprobe_path = os.path.join(addon_dir, "ffprobe")
        os.chmod(ffmpeg_path, 0o755)
        os.chmod(ffprobe_path, 0o755)
        
        # Clean up temp file
        os.remove(temp_file)
        
        print("FFmpeg static binaries installed successfully!")
        return True
        
    except Exception as e:
        print(f"Failed to download FFmpeg binaries: {e}")
        return False

class AUDIO_OT_DownloadFFmpeg(Operator):
    bl_idname = "multi_audio.download_ffmpeg"
    bl_label = "Download FFmpeg Static Binaries"
    bl_description = "Download static FFmpeg and FFprobe binaries (required for this addon)"

    def execute(self, context):
        self.report({'INFO'}, "Downloading FFmpeg static binaries...")
        
        if download_ffmpeg_static():
            self.report({'INFO'}, "FFmpeg binaries downloaded and installed successfully!")
        else:
            self.report({'ERROR'}, "Failed to download FFmpeg binaries. Check console for details.")
            
        return {'FINISHED'}

def get_executable_path(executable_name):
    """Get path to FFmpeg executable, downloading if necessary"""
    addon_dir = os.path.dirname(os.path.realpath(__file__))
    local_executable = os.path.join(addon_dir, executable_name)
    
    # Check if local copy exists and is executable
    if os.path.isfile(local_executable) and os.access(local_executable, os.X_OK):
        return local_executable
    
    # For ffmpeg/ffprobe, try to auto-download
    if executable_name in ["ffmpeg", "ffprobe"]:
        print(f"Local {executable_name} not found, attempting auto-download...")
        if download_ffmpeg_static():
            # Check again after download
            if os.path.isfile(local_executable) and os.access(local_executable, os.X_OK):
                return local_executable
        
        # If auto-download failed, show helpful error
        raise FileNotFoundError(f"Could not find or download {executable_name}. Please use the addon preferences to manually download FFmpeg binaries.")
    
    # For other executables, return as-is
    return executable_name

def get_audio_tracks(video_path):
    """Scan video file for audio tracks using ffprobe"""
    try:
        ffprobe_exe = get_executable_path("ffprobe")
    except FileNotFoundError as e:
        return {"error": "ffprobe_not_found", "detail": str(e)}
    
    command = [
        ffprobe_exe, "-v", "error", "-select_streams", "a",
        "-show_entries", "stream=index:stream_tags=language",
        "-of", "json", video_path
    ]

    try:
        result = subprocess.run(
            command,
            capture_output=True, text=True, check=False,
            timeout=30
        )
        
        if result.returncode != 0:
            error_detail = f"ffprobe failed (code {result.returncode}): {result.stderr.strip()}"
            return {"error": "ffprobe_failed", "detail": error_detail}

        if not result.stdout.strip():
            error_detail = "ffprobe returned no output. File may not contain audio tracks."
            return {"error": "ffprobe_empty_output", "detail": error_detail}

        data = json.loads(result.stdout)
        return data.get("streams", [])

    except json.JSONDecodeError as e:
        error_detail = f"Error parsing ffprobe output: {e}"
        return {"error": "json_decode_error", "detail": error_detail}
    except subprocess.TimeoutExpired:
        error_detail = "ffprobe timed out after 30 seconds"
        return {"error": "ffprobe_timeout", "detail": error_detail}
    except Exception as e:
        error_detail = f"Unexpected error running ffprobe: {e}"
        return {"error": "ffprobe_unexpected_error", "detail": error_detail}

# Property group for each audio track (kept for compatibility)
class AudioTrackItem(PropertyGroup):
    index: StringProperty(name="Index")
    language: StringProperty(name="Language")
    selected: BoolProperty(name="Import", default=False)

# UI panel in the Video Sequence Editor
class SEQUENCER_PT_MultiAudioImport(Panel):
    bl_label = "Multi-Audio Import"
    bl_space_type = 'SEQUENCE_EDITOR'
    bl_region_type = 'UI'
    bl_category = 'Multi-Audio'

    def draw(self, context):
        layout = self.layout
        props = context.scene.multi_audio_props

        layout.prop(props, "video_path")
        layout.operator("multi_audio.scan_and_import_all", icon="FILE_REFRESH", text="Import Video & All Audio (Meta)")

# Main import operator
class AUDIO_OT_ScanAndImportAll(Operator):
    bl_idname = "multi_audio.scan_and_import_all"
    bl_label = "Import Video and All Audio Tracks (Metastrip)"

    def execute(self, context):
        props = context.scene.multi_audio_props
        video_path = bpy.path.abspath(props.video_path)

        if not props.video_path:
            self.report({'ERROR'}, "Please select a video file first.")
            return {'CANCELLED'}

        if not os.path.isfile(video_path):
            self.report({'ERROR'}, f"Video file not found: {video_path}")
            return {'CANCELLED'}

        # Scan for audio tracks
        self.report({'INFO'}, "Scanning for audio tracks...")
        found_audio_info = get_audio_tracks(video_path)

        if isinstance(found_audio_info, dict) and "error" in found_audio_info:
            self.report({'ERROR'}, f"Failed to scan audio tracks: {found_audio_info['detail']}")
            return {'CANCELLED'}
        
        found_audio_streams = found_audio_info
        
        if not found_audio_streams:
            self.report({'INFO'}, "No audio tracks found. Importing video only.")
        else:
            self.report({'INFO'}, f"Found {len(found_audio_streams)} audio track(s). Proceeding with import.")

        # Get video properties (duration, framerate, frame count) directly from ffprobe
        self.report({'INFO'}, "Analyzing video properties...")
        try:
            ffprobe_exe = get_executable_path("ffprobe")
            
            # Get comprehensive video stream information
            video_info_command = [
                ffprobe_exe, "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=duration,r_frame_rate,nb_frames:format=duration",
                "-of", "json", video_path
            ]
            
            result = subprocess.run(video_info_command, capture_output=True, text=True, check=False, timeout=30)
            
            if result.returncode != 0:
                self.report({'ERROR'}, f"Failed to analyze video properties: {result.stderr.strip()}")
                return {'CANCELLED'}
            
            video_info = json.loads(result.stdout)
            
            # Extract video duration
            video_duration_seconds = None
            if 'streams' in video_info and video_info['streams']:
                stream = video_info['streams'][0]
                if 'duration' in stream:
                    video_duration_seconds = float(stream['duration'])
                    self.report({'INFO'}, f"Video stream duration: {video_duration_seconds:.3f} seconds")
            
            # Fallback to format duration if stream duration not available
            if video_duration_seconds is None and 'format' in video_info and 'duration' in video_info['format']:
                video_duration_seconds = float(video_info['format']['duration'])
                self.report({'INFO'}, f"Video format duration: {video_duration_seconds:.3f} seconds")
            
            # Extract video framerate
            video_framerate = None
            if 'streams' in video_info and video_info['streams']:
                stream = video_info['streams'][0]
                if 'r_frame_rate' in stream and stream['r_frame_rate'] != '0/0':
                    # Parse fractional framerate (e.g., "30000/1001" for 29.97fps)
                    fps_parts = stream['r_frame_rate'].split('/')
                    if len(fps_parts) == 2:
                        video_framerate = float(fps_parts[0]) / float(fps_parts[1])
                        self.report({'INFO'}, f"Video framerate: {video_framerate:.3f} fps")
            
            # Extract frame count if available
            video_frame_count = None
            if 'streams' in video_info and video_info['streams']:
                stream = video_info['streams'][0]
                if 'nb_frames' in stream:
                    video_frame_count = int(stream['nb_frames'])
                    self.report({'INFO'}, f"Video frame count: {video_frame_count} frames")
            
            # Calculate expected frame count if we have duration and framerate
            if video_duration_seconds is not None and video_framerate is not None:
                calculated_frames = int(round(video_duration_seconds * video_framerate))
                self.report({'INFO'}, f"Calculated frames from duration×fps: {calculated_frames} frames")
                
                # Use the more reliable frame count
                if video_frame_count is None:
                    video_frame_count = calculated_frames
                elif abs(video_frame_count - calculated_frames) > 2:  # Allow small rounding differences
                    self.report({'WARNING'}, f"Frame count mismatch: metadata={video_frame_count}, calculated={calculated_frames}")
            
            if video_duration_seconds is None:
                self.report({'ERROR'}, "Could not determine video duration from ffprobe")
                return {'CANCELLED'}
                
        except Exception as e:
            self.report({'ERROR'}, f"Failed to analyze video properties: {e}")
            return {'CANCELLED'}

        # Get project framerate for comparison
        scene = context.scene
        project_fps = scene.render.fps / scene.render.fps_base
        self.report({'INFO'}, f"Project framerate: {project_fps:.3f} fps")
        
        # Calculate expected frame duration in project time
        if video_framerate is not None and abs(video_framerate - project_fps) > 0.01:
            self.report({'WARNING'}, f"Framerate mismatch detected! Video: {video_framerate:.3f} fps, Project: {project_fps:.3f} fps")
            
            # Calculate the correct frame duration for the project framerate
            # This ensures the video plays at the correct speed in the project
            correct_frame_duration = int(round(video_duration_seconds * project_fps))
            self.report({'INFO'}, f"Adjusting video duration to {correct_frame_duration} frames for project framerate")
        else:
            # Framerates match or video framerate unknown, use duration-based calculation
            correct_frame_duration = int(round(video_duration_seconds * project_fps))
            self.report({'INFO'}, f"Using duration-based frame count: {correct_frame_duration} frames")

        # Set up sequence editor
        seq_editor = context.scene.sequence_editor
        if not seq_editor:
            context.scene.sequence_editor_create()
            seq_editor = context.scene.sequence_editor

        frame_start_val = 1
        
        # Find next available channel
        max_occupied_channel = 0
        if seq_editor.sequences_all:
            for s in seq_editor.sequences_all:
                num_chans_in_strip = getattr(s, 'channels', 1) if s.type == 'META' else 1
                strip_end_channel = s.channel + num_chans_in_strip - 1
                if strip_end_channel > max_occupied_channel:
                    max_occupied_channel = strip_end_channel
        current_channel_base = max_occupied_channel + 1

        video_strip_name_base = os.path.basename(video_path).rsplit('.', 1)[0]
        
        strips_for_meta = []
        meta_part_channel = 1

        # Import video
        self.report({'INFO'}, "Importing video...")
        try:
            # CRITICAL: Extract video-only first to prevent Blender from extending duration based on audio tracks
            video_only_filename = f"video_only_{video_strip_name_base}.mp4"
            video_only_path = os.path.join(tempfile.gettempdir(), video_only_filename)
            
            ffmpeg_exe = get_executable_path("ffmpeg")
            
            # Determine if we need framerate conversion
            needs_framerate_conversion = (video_framerate is not None and 
                                        abs(video_framerate - project_fps) > 0.01)
            
            if needs_framerate_conversion:
                self.report({'INFO'}, f"Converting video framerate from {video_framerate:.3f}fps to {project_fps:.3f}fps...")
                
                # Convert framerate to match project settings
                video_extract_command = [
                    ffmpeg_exe, "-y", "-i", video_path,
                    "-map", "0:v:0",  # Map only the first video stream
                    "-an",  # Explicitly no audio
                    "-vf", f"fps={project_fps:.6f}",  # Convert framerate using video filter
                    "-c:v", "libx264", "-preset", "medium", "-crf", "18",  # High-quality encode
                    "-avoid_negative_ts", "make_zero",  # Ensure clean timing
                    "-t", str(video_duration_seconds),  # Ensure exact duration
                    video_only_path
                ]
                
                self.report({'INFO'}, "Re-encoding video with framerate conversion...")
                result = subprocess.run(video_extract_command, capture_output=True, text=True, check=False, timeout=180)
                
            else:
                # No framerate conversion needed, try to copy first
                self.report({'INFO'}, "Extracting video without framerate conversion...")
                video_extract_command = [
                    ffmpeg_exe, "-y", "-i", video_path,
                    "-map", "0:v:0",  # Map only the first video stream
                    "-an",  # Explicitly no audio
                    "-c:v", "copy",  # Copy video without re-encoding to preserve exact timing
                    "-avoid_negative_ts", "make_zero",  # Ensure clean timing
                    "-t", str(video_duration_seconds),  # Ensure exact duration
                    video_only_path
                ]
                
                result = subprocess.run(video_extract_command, capture_output=True, text=True, check=False, timeout=60)
                
                if result.returncode != 0:
                    # Fallback: re-encode if copy fails, but preserve timing
                    self.report({'INFO'}, "Video copy failed, trying re-encode...")
                    video_extract_command = [
                        ffmpeg_exe, "-y", "-i", video_path,
                        "-map", "0:v:0",  # Map only the first video stream
                        "-an",  # Explicitly no audio
                        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",  # Fast, high-quality re-encode
                        "-avoid_negative_ts", "make_zero",
                        "-t", str(video_duration_seconds),  # Ensure exact duration
                        video_only_path
                    ]
                    result = subprocess.run(video_extract_command, capture_output=True, text=True, check=False, timeout=120)
            
            if result.returncode != 0:
                self.report({'ERROR'}, f"Failed to extract video-only: {result.stderr.strip()}")
                return {'CANCELLED'}
            
            # Now import the video-only file
            video_strip = seq_editor.sequences.new_movie(
                name=video_strip_name_base,
                filepath=video_only_path,
                channel=meta_part_channel,
                frame_start=frame_start_val
            )
            
            # Verify the imported duration
            imported_duration = video_strip.frame_final_duration
            self.report({'INFO'}, f"Blender imported video with {imported_duration} frames, expected {correct_frame_duration} frames")
            
            # With proper framerate conversion, the duration should now be correct
            if abs(imported_duration - correct_frame_duration) > 2:  # Allow 2-frame tolerance for encoding
                self.report({'WARNING'}, f"Duration mismatch persists after framerate conversion: imported={imported_duration}, expected={correct_frame_duration}")
                
                # Only try manual adjustment if the difference is significant
                if abs(imported_duration - correct_frame_duration) > 5:
                    self.report({'INFO'}, f"Attempting manual duration adjustment...")
                    try:
                        video_strip.frame_final_end = video_strip.frame_final_start + correct_frame_duration - 1
                        self.report({'INFO'}, f"Adjusted video strip duration to {video_strip.frame_final_duration} frames")
                    except Exception as e:
                        self.report({'WARNING'}, f"Could not adjust video strip duration: {e}")
            else:
                self.report({'INFO'}, f"✓ Video duration is correct: {imported_duration} frames")
            
            strips_for_meta.append(video_strip)
            meta_part_channel += 1
            
        except Exception as e:
            self.report({'ERROR'}, f"Failed to import video: {e}")
            return {'CANCELLED'}

        # Import audio tracks using exact duration from video analysis
        successfully_imported_audio_count = 0
        if found_audio_streams and video_strip:
            try:
                ffmpeg_exe = get_executable_path("ffmpeg")
            except FileNotFoundError as e:
                self.report({'ERROR'}, str(e))
                return {'CANCELLED'}
                
            for stream_info in found_audio_streams:
                stream_index = str(stream_info.get("index")) 
                stream_lang_tags = stream_info.get("tags", {})
                stream_lang = stream_lang_tags.get("language", f"Track_{stream_index}")

                temp_audio_filename = f"audio_{video_strip_name_base}_track_{stream_index}.wav"
                temp_path = os.path.join(tempfile.gettempdir(), temp_audio_filename)
                
                self.report({'INFO'}, f"Extracting audio track {stream_index} ({stream_lang}) with exact duration {video_duration_seconds:.3f}s...")
                try:
                    # Extract audio with exact duration matching video analysis
                    ffmpeg_command = [
                        ffmpeg_exe, "-y", "-i", video_path,
                        "-map", f"0:{stream_index}", 
                        "-vn",  # No video output
                        "-acodec", "pcm_s16le",  # Standard WAV codec
                        "-ar", "48000",  # Standard sample rate
                        "-ac", "2",  # Stereo output
                        "-t", str(video_duration_seconds),  # Exact duration from video analysis
                        "-avoid_negative_ts", "make_zero",  # Ensure timing starts at zero
                        temp_path
                    ]
                    
                    result = subprocess.run(ffmpeg_command, capture_output=True, text=True, check=False, timeout=60)
                    
                    if result.returncode != 0:
                        self.report({'WARNING'}, f"Failed to extract audio track {stream_index}: {result.stderr.strip()}")
                        continue

                    audio_strip_name = f"Audio_{stream_lang}_{video_strip_name_base}"
                    audio_strip = seq_editor.sequences.new_sound(
                        name=audio_strip_name,
                        filepath=temp_path,
                        channel=meta_part_channel,
                        frame_start=frame_start_val
                    )
                    
                    strips_for_meta.append(audio_strip)
                    meta_part_channel += 1
                    successfully_imported_audio_count += 1
                
                except Exception as e:
                    self.report({'WARNING'}, f"Failed to import audio track {stream_index}: {e}")
                    continue
        
        # Create metastrip if we have video + audio, otherwise just position video
        if video_strip and successfully_imported_audio_count > 0:
            self.report({'INFO'}, "Creating metastrip...")
            bpy.ops.sequencer.select_all(action='DESELECT')
            for s_meta in strips_for_meta:
                s_meta.select = True
            
            seq_editor.active_strip = video_strip
            
            # Adjust channel positions before creating meta
            min_channel_in_meta_parts = min(s.channel for s in strips_for_meta)
            channel_offset = current_channel_base - min_channel_in_meta_parts
            if channel_offset != 0:
                for s_meta in strips_for_meta:
                    s_meta.channel += channel_offset
            
            bpy.ops.sequencer.meta_make()
            
            if seq_editor.active_strip and seq_editor.active_strip.type == 'META':
                seq_editor.active_strip.name = f"Meta_{video_strip_name_base}"
                if seq_editor.active_strip.channel != current_channel_base:
                     seq_editor.active_strip.channel = current_channel_base
                
                # Final verification of durations
                final_video_duration = None
                final_audio_durations = []
                
                # Check the metastrip contents for duration verification
                bpy.ops.sequencer.meta_toggle()  # Enter meta
                for strip in seq_editor.sequences:
                    if strip.type == 'MOVIE':
                        final_video_duration = strip.frame_final_duration
                    elif strip.type == 'SOUND':
                        final_audio_durations.append(strip.frame_final_duration)
                bpy.ops.sequencer.meta_toggle()  # Exit meta
                
                if final_video_duration and final_audio_durations:
                    max_audio_duration = max(final_audio_durations)
                    duration_diff = abs(final_video_duration - max_audio_duration)
                    if duration_diff <= 1:  # 1-frame tolerance
                        self.report({'INFO'}, f"✓ Duration verification passed: Video={final_video_duration}, Audio={max_audio_duration} frames")
                    else:
                        self.report({'WARNING'}, f"⚠ Duration mismatch detected: Video={final_video_duration}, Audio={max_audio_duration} frames (diff: {duration_diff})")
                
                self.report({'INFO'}, f"Created metastrip '{seq_editor.active_strip.name}' with video and {successfully_imported_audio_count} audio track(s).")
            else:
                self.report({'WARNING'}, "Metastrip creation may have failed.")
        elif video_strip:
            # Only video imported
            video_strip.channel = current_channel_base
            msg = "Imported video only."
            if found_audio_streams and successfully_imported_audio_count == 0:
                msg += " All audio extractions failed."
            elif not found_audio_streams:
                 msg += " No audio tracks found."
            self.report({'INFO'}, msg)
        else: 
            self.report({'ERROR'}, "Video import failed.")
            return {'CANCELLED'}
            
        return {'FINISHED'}

# Property container
class MultiAudioProperties(PropertyGroup):
    video_path: StringProperty(
        name="Video File",
        description="Path to the video file to import",
        subtype='FILE_PATH'
    )
    tracks: CollectionProperty(type=AudioTrackItem)  # Kept for compatibility
    track_index: IntProperty()

# Register/unregister
classes = (
    MultiAudioImporterPreferences,
    AUDIO_OT_DownloadFFmpeg,
    AudioTrackItem,
    SEQUENCER_PT_MultiAudioImport,
    AUDIO_OT_ScanAndImportAll,
    MultiAudioProperties,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.multi_audio_props = bpy.props.PointerProperty(type=MultiAudioProperties)

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    del bpy.types.Scene.multi_audio_props

if __name__ == "__main__":
    register()
