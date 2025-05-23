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
            video_strip = seq_editor.sequences.new_movie(
                name=video_strip_name_base,
                filepath=video_path,
                channel=meta_part_channel,
                frame_start=frame_start_val
            )
            strips_for_meta.append(video_strip)
            meta_part_channel += 1
        except Exception as e:
            self.report({'ERROR'}, f"Failed to import video: {e}")
            return {'CANCELLED'}

        # Import audio tracks
        successfully_imported_audio_count = 0
        if found_audio_streams:
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
                
                self.report({'INFO'}, f"Extracting audio track {stream_index} ({stream_lang})...")
                try:
                    ffmpeg_command = [
                        ffmpeg_exe, "-y", "-i", video_path,
                        "-map", f"0:{stream_index}", 
                        "-vn", "-acodec", "pcm_s16le",
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
