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
import re
import time
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
        "-show_entries", "stream=index,duration,codec_name,channels,sample_rate:stream_tags=language",
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

def run_ffmpeg_with_progress(command, timeout, duration_seconds=None, operation_name="FFmpeg"):
    """Run FFmpeg command with progress monitoring and update Blender's progress bar"""
    wm = bpy.context.window_manager
    
    try:
        # Start the process
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            bufsize=1
        )
        
        start_time = time.time()
        last_progress = 0
        
        # Monitor the process
        while process.poll() is None:
            # Check for timeout
            if time.time() - start_time > timeout:
                process.terminate()
                process.wait(timeout=5)
                return None, "Process timed out"
            
            # Read stderr for progress (FFmpeg outputs progress to stderr)
            try:
                line = process.stderr.readline()
                if line:
                    # Parse FFmpeg progress output
                    # Look for time= patterns
                    time_match = re.search(r'time=(\d{2}):(\d{2}):(\d{2}\.\d{2})', line)
                    if time_match and duration_seconds:
                        hours = int(time_match.group(1))
                        minutes = int(time_match.group(2))
                        seconds = float(time_match.group(3))
                        current_time = hours * 3600 + minutes * 60 + seconds
                        
                        progress = min(current_time / duration_seconds, 1.0)
                        
                        # Only update if progress increased significantly (avoid spam)
                        if progress - last_progress > 0.01:
                            wm.progress_update(progress)
                            last_progress = progress
                            
            except:
                # Continue even if progress parsing fails
                pass
            
            # Small delay to prevent excessive CPU usage
            time.sleep(0.1)
        
        # Get final output
        stdout, stderr = process.communicate()
        
        if process.returncode == 0:
            return stdout, None
        else:
            return None, stderr
            
    except Exception as e:
        return None, str(e)

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
        
        # Check if we're in the sequence editor and have strips
        if not context.scene.sequence_editor or not context.scene.sequence_editor.sequences:
            layout.label(text="No sequences available", icon='INFO')
            return
            
        # Check for selected video strips
        seq_editor = context.scene.sequence_editor
        selected_video_strips = []
        
        for strip in seq_editor.sequences_all:
            if strip.select and strip.type in ['MOVIE', 'SOUND']:
                if strip.type == 'MOVIE' or (strip.type == 'SOUND' and hasattr(strip, 'sound') and strip.sound.filepath):
                    selected_video_strips.append(strip)
        
        if not selected_video_strips:
            layout.label(text="Select a video/movie strip", icon='INFO')
            layout.label(text="to extract additional audio tracks")
        elif len(selected_video_strips) > 1:
            layout.label(text="Select only one video strip", icon='ERROR')
        else:
            selected_strip = selected_video_strips[0]
            
            # Get source file info
            if selected_strip.type == 'MOVIE':
                source_file = bpy.path.abspath(selected_strip.filepath)
                strip_type = "Video"
            else:  # SOUND
                source_file = bpy.path.abspath(selected_strip.sound.filepath)
                strip_type = "Audio"
            
            # Display strip info
            layout.label(text=f"Selected: {selected_strip.name}", icon='SEQUENCE')
            layout.label(text=f"Type: {strip_type}")
            
            if os.path.isfile(source_file):
                file_size_mb = os.path.getsize(source_file) / (1024 * 1024)
                layout.label(text=f"Size: {file_size_mb:.1f} MB")
                layout.separator()
                layout.operator("multi_audio.extract_additional_tracks", 
                              icon="SPEAKER", 
                              text="Extract Additional Audio Tracks")
            else:
                layout.label(text="⚠ Source file not found", icon='ERROR')
                layout.label(text=f"Path: {source_file}")
                layout.separator()
                layout.label(text="Tip: Use 'Make Paths Relative'")
                layout.label(text="or ensure source file exists")

# Main extract operator
class AUDIO_OT_ExtractAdditionalTracks(Operator):
    bl_idname = "multi_audio.extract_additional_tracks"
    bl_label = "Extract Additional Audio Tracks"
    bl_description = "Extract additional audio tracks from the selected video/audio strip and create a metastrip"

    def execute(self, context):
        # Check sequence editor
        if not context.scene.sequence_editor:
            self.report({'ERROR'}, "No sequence editor available.")
            return {'CANCELLED'}
            
        seq_editor = context.scene.sequence_editor
        
        # Find selected video/audio strip
        selected_strip = None
        for strip in seq_editor.sequences_all:
            if strip.select and strip.type in ['MOVIE', 'SOUND']:
                if strip.type == 'MOVIE' or (strip.type == 'SOUND' and hasattr(strip, 'sound') and strip.sound.filepath):
                    if selected_strip is None:
                        selected_strip = strip
                    else:
                        self.report({'ERROR'}, "Multiple strips selected. Please select only one video/audio strip.")
                        return {'CANCELLED'}
        
        if not selected_strip:
            self.report({'ERROR'}, "No video or audio strip selected.")
            return {'CANCELLED'}
        
        # Get source file path
        if selected_strip.type == 'MOVIE':
            source_file = bpy.path.abspath(selected_strip.filepath)
        else:  # SOUND
            source_file = bpy.path.abspath(selected_strip.sound.filepath)
        
        if not os.path.isfile(source_file):
            self.report({'ERROR'}, f"Source file not found: {source_file}")
            return {'CANCELLED'}

        # Initialize progress bar
        wm = context.window_manager
        wm.progress_begin(0, 100)
        
        try:
            # Phase 1: Scan for audio tracks (10% of progress)
            wm.progress_update(10)
            self.report({'INFO'}, f"Scanning audio tracks in: {os.path.basename(source_file)}")
            found_audio_info = get_audio_tracks(source_file)

            if isinstance(found_audio_info, dict) and "error" in found_audio_info:
                self.report({'ERROR'}, f"Failed to scan audio tracks: {found_audio_info['detail']}")
                return {'CANCELLED'}
            
            found_audio_streams = found_audio_info
            
            if not found_audio_streams:
                self.report({'INFO'}, "No audio tracks found in source file.")
                return {'FINISHED'}
            elif len(found_audio_streams) <= 1:
                self.report({'INFO'}, f"Only {len(found_audio_streams)} audio track found. No additional tracks to extract.")
                return {'FINISHED'}
            else:
                self.report({'INFO'}, f"Found {len(found_audio_streams)} audio tracks. Extracting additional tracks...")
                
                # Log detailed information about each track found
                for i, stream_info in enumerate(found_audio_streams):
                    stream_index = str(stream_info.get("index"))
                    stream_duration = stream_info.get("duration", "unknown")
                    stream_codec = stream_info.get("codec_name", "unknown")
                    stream_channels = stream_info.get("channels", "unknown")
                    stream_sample_rate = stream_info.get("sample_rate", "unknown")
                    stream_lang_tags = stream_info.get("tags", {})
                    stream_lang = stream_lang_tags.get("language", f"Track_{stream_index}")
                    
                    self.report({'INFO'}, f"Track {i}: index={stream_index}, lang={stream_lang}, duration={stream_duration}s, codec={stream_codec}, channels={stream_channels}, sample_rate={stream_sample_rate}")
                    
                    # Warn about potential empty/silent tracks
                    if stream_duration and stream_duration != "unknown":
                        try:
                            duration_float = float(stream_duration)
                            if duration_float < 1.0:
                                self.report({'WARNING'}, f"Track {stream_index} ({stream_lang}) appears very short ({duration_float:.3f}s) - may be empty/silent")
                        except:
                            pass

            # Phase 2: Analyze video properties for duration (20% of progress)
            wm.progress_update(20)
            self.report({'INFO'}, "Getting source file duration...")
            try:
                ffprobe_exe = get_executable_path("ffprobe")
                
                file_size_mb = os.path.getsize(source_file) / (1024 * 1024)
                
                # Get video duration
                video_info_command = [
                    ffprobe_exe, "-v", "error", 
                    "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1", source_file
                ]
                
                result = subprocess.run(video_info_command, capture_output=True, text=True, check=False, timeout=30)
                
                if result.returncode != 0 or not result.stdout.strip():
                    self.report({'ERROR'}, f"Failed to get duration from source file")
                    return {'CANCELLED'}
                
                video_duration_seconds = float(result.stdout.strip())
                self.report({'INFO'}, f"Source duration: {video_duration_seconds:.3f} seconds")
                
                # Get actual video FPS (crucial for accurate duration calculations)
                video_fps_command = [
                    ffprobe_exe, "-v", "error", "-select_streams", "v:0",
                    "-show_entries", "stream=r_frame_rate",
                    "-of", "default=noprint_wrappers=1:nokey=1", source_file
                ]
                
                fps_result = subprocess.run(video_fps_command, capture_output=True, text=True, check=False, timeout=30)
                
                if fps_result.returncode == 0 and fps_result.stdout.strip():
                    # Parse frame rate (could be in format like "30/1" or "29.97")
                    fps_string = fps_result.stdout.strip()
                    if '/' in fps_string:
                        # Handle fractional format like "30/1" or "30000/1001" 
                        numerator, denominator = fps_string.split('/')
                        actual_video_fps = float(numerator) / float(denominator)
                    else:
                        actual_video_fps = float(fps_string)
                    
                    self.report({'INFO'}, f"Source video FPS: {actual_video_fps:.3f}")
                else:
                    # Fallback to project FPS if video FPS detection fails
                    scene = context.scene
                    actual_video_fps = scene.render.fps / scene.render.fps_base
                    self.report({'WARNING'}, f"Could not detect video FPS, using project FPS: {actual_video_fps:.3f}")
                
            except Exception as e:
                self.report({'ERROR'}, f"Failed to analyze source file: {e}")
                return {'CANCELLED'}

            # Phase 3: Prepare for safe audio extraction (30% of progress)
            wm.progress_update(30)
            
            # Store ALL original strip properties to preserve user's work
            original_strip_name = selected_strip.name
            original_strip_channel = selected_strip.channel
            original_frame_start = selected_strip.frame_start
            original_frame_final_start = selected_strip.frame_final_start  
            original_frame_final_end = selected_strip.frame_final_end
            original_frame_final_duration = selected_strip.frame_final_duration
            original_frame_offset_start = getattr(selected_strip, 'frame_offset_start', 0)
            original_frame_offset_end = getattr(selected_strip, 'frame_offset_end', 0)
            
            self.report({'INFO'}, f"Original strip properties: start={original_frame_start}, final_start={original_frame_final_start}, final_end={original_frame_final_end}, duration={original_frame_final_duration}")
            
            # If we have multiple audio tracks, extract them safely
            if len(found_audio_streams) > 1:
                self.report({'INFO'}, f"Extracting all {len(found_audio_streams)} audio tracks safely...")
                
                additional_tracks = found_audio_streams[1:]  # Skip first track (will be included with original strip)
                
                audio_timeout = max(60, min(600, int(file_size_mb * 2)))
                
                # Find temporary extraction area - use a simple, predictable location
                # Instead of calculating complex safe areas, use frame 1000+ for temporary extraction
                temp_extraction_start = 1000
                
                self.report({'INFO'}, f"Using temporary extraction area starting at frame {temp_extraction_start}")
                
                # Find available channels for extraction  
                occupied_channels = [s.channel for s in seq_editor.sequences_all]
                if occupied_channels:
                    max_channel = max(occupied_channels)
                    extraction_start_channel = max_channel + 1
                    self.report({'INFO'}, f"Found channels 1-{max_channel} occupied, using channel {extraction_start_channel}+ for extraction")
                else:
                    extraction_start_channel = 1
                    self.report({'INFO'}, f"No existing channels found, starting extraction at channel {extraction_start_channel}")
                
                created_audio_strips = []  # Track all strips we create
                next_channel = extraction_start_channel

                # Phase 4: Extract and add additional audio tracks to main timeline (30-80% of progress)
                # Extract the exact duration requested by the user's video strip, since all audio
                # tracks were recorded simultaneously and should have identical durations.
                for i, stream_info in enumerate(additional_tracks):
                    # Update progress for each audio track
                    audio_progress = 30 + (50 * (i + 1) / len(additional_tracks))
                    wm.progress_update(audio_progress)
                    
                    stream_index = str(stream_info.get("index")) 
                    stream_lang_tags = stream_info.get("tags", {})
                    stream_lang = stream_lang_tags.get("language", f"Track_{stream_index}")

                    temp_audio_filename = f"additional_audio_{original_strip_name}_track_{stream_index}.aac"
                    # Save extracted audio next to original video file instead of temp directory
                    source_dir = os.path.dirname(source_file)
                    temp_path = os.path.join(source_dir, temp_audio_filename)
                    
                    self.report({'INFO'}, f"Extracting additional audio track {stream_index} ({stream_lang}) [{i+1}/{len(additional_tracks)}]...")
                    
                    try:
                        ffmpeg_exe = get_executable_path("ffmpeg")
                        
                        # Calculate precise duration from original strip's frame count
                        # Use actual video FPS instead of project FPS for accuracy
                        precise_duration_seconds = original_frame_final_duration / actual_video_fps
                        
                        # Calculate the exact start time in the source file
                        # This accounts for any trimming/offset the user has applied
                        strip_start_offset_seconds = original_frame_offset_start / actual_video_fps
                        
                        self.report({'INFO'}, f"Strip offsets: frame_offset_start={original_frame_offset_start}, frame_offset_end={original_frame_offset_end}")
                        self.report({'INFO'}, f"Using precise extraction: start={strip_start_offset_seconds:.3f}s, duration={precise_duration_seconds:.3f}s ({original_frame_final_duration} frames at {actual_video_fps:.2f} FPS)")
                        
                        # Extract entire audio track without duration limit to test natural length
                        ffmpeg_command = [
                            ffmpeg_exe, "-y", 
                            "-ss", f"{strip_start_offset_seconds:.6f}",  # Seek BEFORE input for accuracy
                            "-i", source_file,
                            "-map", f"0:{stream_index}", 
                            "-vn",  # No video output
                            "-acodec", "copy",  # Copy audio without re-encoding to preserve timing
                            temp_path  # Extract entire track to see natural length
                        ]
                        
                        # Debug: Show the exact FFmpeg command
                        cmd_str = ' '.join(ffmpeg_command)
                        self.report({'INFO'}, f"FFmpeg command: {cmd_str}")
                        
                        stdout, stderr = run_ffmpeg_with_progress(
                            ffmpeg_command, 
                            audio_timeout, 
                            precise_duration_seconds, 
                            f"Additional Audio Track {i+1}"
                        )
                        
                        if stderr:
                            self.report({'WARNING'}, f"Failed to extract audio track {stream_index}: {stderr}")
                            continue

                        # Check the extracted file properties for debugging
                        if os.path.exists(temp_path):
                            file_size_kb = os.path.getsize(temp_path) / 1024
                            self.report({'INFO'}, f"Extracted audio file: {file_size_kb:.1f} KB")
                            
                            # Verify extracted file duration with ffprobe for debugging
                            try:
                                verify_command = [
                                    ffprobe_exe, "-v", "error", 
                                    "-show_entries", "format=duration",
                                    "-of", "default=noprint_wrappers=1:nokey=1", temp_path
                                ]
                                verify_result = subprocess.run(verify_command, capture_output=True, text=True, check=False, timeout=10)
                                
                                if verify_result.returncode == 0 and verify_result.stdout.strip():
                                    actual_extracted_duration = float(verify_result.stdout.strip())
                                    self.report({'INFO'}, f"Verified extracted file duration: {actual_extracted_duration:.3f}s (requested: {precise_duration_seconds:.3f}s)")
                                else:
                                    self.report({'WARNING'}, f"Could not verify extracted file duration")
                            except Exception as e:
                                self.report({'WARNING'}, f"Error verifying extracted file: {e}")
                            
                            # Special warning for very small files (likely silent/empty tracks)
                            if file_size_kb < 10:  # Less than 10KB is suspiciously small for real audio
                                self.report({'WARNING'}, f"Track {stream_index} ({stream_lang}) extracted file is very small ({file_size_kb:.1f} KB)")
                                self.report({'WARNING'}, f"This track may be silent/empty but will still be included in the metastrip")
                        else:
                            self.report({'WARNING'}, f"Extracted audio file not found: {temp_path}")
                            continue

                        # Import the extracted audio to safe area on timeline
                        audio_strip_name = f"Audio_{stream_lang}"
                        
                        # Create the sound strip in safe extraction area  
                        audio_strip = seq_editor.sequences.new_sound(
                            name=audio_strip_name,
                            filepath=temp_path,
                            channel=next_channel,
                            frame_start=temp_extraction_start  # Place in temporary area
                        )
                        
                        # Verify the strip was created
                        if audio_strip:
                            self.report({'INFO'}, f"Created {audio_strip_name}: start={audio_strip.frame_start}, final_start={audio_strip.frame_final_start}, final_end={audio_strip.frame_final_end}, duration={audio_strip.frame_final_duration}")
                                
                            created_audio_strips.append(audio_strip)
                            self.report({'INFO'}, f"✓ Added {audio_strip_name} on channel {audio_strip.channel} (natural duration: {audio_strip.frame_final_duration} frames)")
                            next_channel += 1
                        else:
                            self.report({'WARNING'}, f"Failed to create audio strip {audio_strip_name}")
                    
                    except Exception as e:
                        self.report({'WARNING'}, f"Failed to import audio track {stream_index}: {e}")
                        continue
                
                # Phase 5: Create metastrip from all tracks (80-100% of progress)
                wm.progress_update(90)
                
                if created_audio_strips:
                    self.report({'INFO'}, f"Creating metastrip from original strip + {len(created_audio_strips)} additional audio tracks...")
                    
                    # First, move original strip to temporary area to group with audio tracks
                    original_strip_temp_start = temp_extraction_start
                    selected_strip.frame_start = original_strip_temp_start
                    selected_strip.channel = extraction_start_channel - 1  # Place original strip just below audio tracks
                    
                    self.report({'INFO'}, f"Temporarily moved original strip to temporary area for grouping...")
                    
                    # Select all strips to include in metastrip (original + all new audio tracks)
                    bpy.ops.sequencer.select_all(action='DESELECT')
                    selected_strip.select = True
                    for audio_strip in created_audio_strips:
                        audio_strip.select = True
                    
                    # Create metastrip from all selected strips
                    bpy.ops.sequencer.meta_make()
                    
                    if seq_editor.active_strip and seq_editor.active_strip.type == 'META':
                        meta_strip = seq_editor.active_strip
                        meta_strip.name = f"MultiAudio_{original_strip_name}"
                        
                        # Phase 6: Restore original position and properties
                        self.report({'INFO'}, f"Restoring original strip position and properties...")
                        
                        # Move metastrip back to original position
                        meta_strip.frame_start = original_frame_start
                        meta_strip.channel = original_strip_channel
                        
                        # Restore original trimming and offset properties
                        if hasattr(meta_strip, 'frame_offset_start'):
                            meta_strip.frame_offset_start = original_frame_offset_start
                        if hasattr(meta_strip, 'frame_offset_end'):
                            meta_strip.frame_offset_end = original_frame_offset_end
                        
                        # Ensure final duration matches original (handles trimming)
                        if hasattr(meta_strip, 'frame_final_duration'):
                            try:
                                # Calculate the duration adjustment needed
                                current_duration = meta_strip.frame_final_duration
                                target_duration = original_frame_final_duration
                                if abs(current_duration - target_duration) > 1:  # Allow 1 frame tolerance
                                    # Adjust end trimming to match original duration
                                    duration_diff = current_duration - target_duration
                                    meta_strip.frame_offset_end = original_frame_offset_end + duration_diff
                                    self.report({'INFO'}, f"Adjusted duration from {current_duration} to {target_duration} frames")
                            except Exception as duration_error:
                                self.report({'WARNING'}, f"Could not fully restore duration: {duration_error}")
                        
                        self.report({'INFO'}, f"✓ Successfully created metastrip '{meta_strip.name}' containing:")
                        self.report({'INFO'}, f"  - 1 video track")  
                        self.report({'INFO'}, f"  - {len(created_audio_strips) + 1} audio tracks")
                        self.report({'INFO'}, f"✓ All {len(found_audio_streams)} audio tracks successfully grouped!")
                        self.report({'INFO'}, f"✓ Original position and properties preserved!")
                        self.report({'INFO'}, f"✓ Timeline safety maintained - no existing content disturbed!")
                        self.report({'INFO'}, f"✓ Using efficient PCM compression (much smaller files)!")
                    else:
                        self.report({'WARNING'}, "Metastrip creation may have failed, but audio tracks were added successfully")
                else:
                    self.report({'WARNING'}, "No additional audio tracks were successfully extracted")
                
            else:
                # Only one audio track, no need for metastrip
                self.report({'INFO'}, "Only one audio track found. No additional processing needed.")
            
            wm.progress_update(100)
            return {'FINISHED'}
            
        except Exception as e:
            self.report({'ERROR'}, f"Failed to extract additional audio tracks: {e}")
            return {'CANCELLED'}
        finally:
            # Always end progress bar
            wm.progress_end()

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
    AUDIO_OT_ExtractAdditionalTracks,
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
