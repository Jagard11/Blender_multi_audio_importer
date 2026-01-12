bl_info = {
    "name": "Multi-Audio Track Video Importer & Exporter",
    "author": "Jagard11 & Claude AI",
    "version": (3, 0, 1),
    "blender": (5, 0, 1),
    "location": "Video Sequence Editor > Sidebar > Multi-Audio",
    "description": "Import & export video with multiple audio tracks using FFmpeg. Import creates metastrips, export creates MKV files with multiple audio tracks. Auto-downloads static binaries.",
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

def _maio_vse_scene(context):
    """Return the scene currently displayed by the VSE editor (if available).

    Blender 5.x can run the VSE inside its own scene; `context.scene` may not
    always match the sequencer space's scene.
    """
    space = getattr(context, "space_data", None)
    space_scene = getattr(space, "scene", None)
    return space_scene or context.scene


def _maio_sequence_editor(scene):
    """Return the scene's sequence editor if present, else None."""
    return getattr(scene, "sequence_editor", None)


def _maio_seq_editor_strips(seq_editor):
    """Return an iterable of sequencer strips across Blender API versions."""
    if not seq_editor:
        return None

    # Blender historically exposed "sequences" collections; future versions may
    # prefer "strips". Support both names.
    for attr in ("sequences_all", "strips_all", "sequences", "strips"):
        strips = getattr(seq_editor, attr, None)
        if strips is not None:
            return strips

    return None


def _maio_seq_editor_collection(seq_editor):
    """Return the writable strip collection for creating new strips."""
    if not seq_editor:
        return None

    for attr in ("sequences", "strips"):
        collection = getattr(seq_editor, attr, None)
        if collection is not None:
            return collection

    return None


def _maio_seq_editor_new_sound(seq_editor, *, name, filepath, channel, frame_start):
    """Create a new sound strip across Blender API versions."""
    collection = _maio_seq_editor_collection(seq_editor)
    if collection is None:
        raise AttributeError("SequenceEditor has no sequences/strips collection")

    new_sound = getattr(collection, "new_sound", None)
    if new_sound is None:
        raise AttributeError("SequenceEditor collection has no new_sound()")

    return new_sound(
        name=name,
        filepath=filepath,
        channel=channel,
        frame_start=frame_start,
    )


def _maio_context_selected_strips(context):
    """Return selected strips in the current sequencer context, if possible."""
    for attr in ("selected_sequences", "selected_strips"):
        selected = getattr(context, attr, None)
        if selected is not None:
            return list(selected)

    scene = _maio_vse_scene(context)
    seq_editor = _maio_sequence_editor(scene)
    strips = _maio_seq_editor_strips(seq_editor) or []
    return [s for s in strips if getattr(s, "select", False)]


def _maio_strip_has_source(strip):
    """True if this strip has a usable source filepath."""
    strip_type = getattr(strip, "type", None)
    if strip_type == 'MOVIE':
        return bool(getattr(strip, "filepath", ""))
    if strip_type == 'SOUND':
        sound = getattr(strip, "sound", None)
        return bool(sound and getattr(sound, "filepath", ""))
    return False


def _maio_sequencer_temp_override(context, scene):
    """Context override for bpy.ops.sequencer.* in Blender 5.x VSE.

    In Blender 5.x the sequencer can display/edit a scene different from
    `context.scene`. Operators must be executed with an override so they act on
    the VSE scene.
    """
    override = {"scene": scene}
    if getattr(context, "window", None) is not None:
        override["window"] = context.window
    if getattr(context, "screen", None) is not None:
        override["screen"] = context.screen
    if getattr(context, "area", None) is not None:
        override["area"] = context.area

    # Panel/operator runs from the sidebar (UI region). Many sequencer ops need
    # the WINDOW region.
    region = None
    area = getattr(context, "area", None)
    if area is not None:
        for r in area.regions:
            if r.type == 'WINDOW':
                region = r
                break
    if region is None:
        region = getattr(context, "region", None)
    if region is not None:
        override["region"] = region

    space_data = getattr(context, "space_data", None)
    if space_data is not None:
        override["space_data"] = space_data

    return bpy.context.temp_override(**override)


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
        ffprobe_exe, "-v", "error",
        "-show_entries", "stream=index,codec_type,duration,codec_name,channels,sample_rate:stream_tags=language,title",
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
        streams = data.get("streams", []) or []
        audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
        audio_streams.sort(key=lambda s: s.get("index", 0))
        return audio_streams

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

# NEW: Property group for export track selection
class ExportAudioTrackItem(PropertyGroup):
    index: StringProperty(name="Stream Index")
    name: StringProperty(name="Track Name", default="Audio Track")
    language: StringProperty(name="Language", default="")
    channels: IntProperty(name="Channels", default=2)
    codec: StringProperty(name="Codec", default="")
    include: BoolProperty(name="Include in Export", default=True)

# NEW: Multi-track export UI panel
class SEQUENCER_PT_MultiAudioExport(Panel):
    bl_label = "Multi-Audio Export"
    bl_space_type = 'SEQUENCE_EDITOR'
    bl_region_type = 'UI'
    bl_category = 'Multi-Audio'
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        scene = _maio_vse_scene(context)
        props = scene.multi_audio_export_props
        
        # Check if we're in the sequence editor
        seq_editor = _maio_sequence_editor(scene)
        if not seq_editor:
            layout.label(text="No sequence editor available", icon='INFO')
            return
        
        all_strips = _maio_seq_editor_strips(seq_editor)
        if all_strips is None:
            layout.label(text="No sequences available", icon='INFO')
            return
        
        # Check for selected audio/video strips
        selected_strips = []
        for strip in _maio_context_selected_strips(context):
            if strip.type not in {'MOVIE', 'SOUND', 'META'}:
                continue
            if strip.type == 'META' or _maio_strip_has_source(strip):
                selected_strips.append(strip)
        
        if not selected_strips:
            layout.label(text="Select an audio, video strip, or", icon='INFO')
            layout.label(text="imported metastrip to export")
            return
        elif len(selected_strips) > 1:
            layout.label(text="Select only one strip", icon='ERROR')
            return
        
        selected_strip = selected_strips[0]
        
        # Get source file info and display appropriate info
        if selected_strip.type == 'META':
            if selected_strip.name.startswith("MultiAudio_"):
                strip_type = "Imported Metastrip"
                layout.label(text=f"Selected: {selected_strip.name}", icon='SEQUENCE')
                layout.label(text=f"Type: {strip_type}", icon='GROUP')
                source_file = None  # Will be determined inside metastrip
            else:
                layout.label(text="⚠ Unsupported metastrip type", icon='ERROR')
                layout.label(text="Only imported metastrips are supported")
                return
        elif selected_strip.type == 'MOVIE':
            source_file = bpy.path.abspath(selected_strip.filepath)
            strip_type = "Video"
            layout.label(text=f"Selected: {selected_strip.name}", icon='SEQUENCE')
            layout.label(text=f"Type: {strip_type}")
            
            if not os.path.isfile(source_file):
                layout.label(text="⚠ Source file not found", icon='ERROR')
                return
        else:  # SOUND
            source_file = bpy.path.abspath(selected_strip.sound.filepath) if selected_strip.sound else ""
            strip_type = "Audio"
            layout.label(text=f"Selected: {selected_strip.name}", icon='SEQUENCE')
            layout.label(text=f"Type: {strip_type}")
            
            if not os.path.isfile(source_file):
                layout.label(text="⚠ Source file not found", icon='ERROR')
                return
        
        # Analyze button for this specific strip
        if selected_strip.type == 'META':
            layout.operator("multi_audio.analyze_strip", icon="VIEWZOOM", text="Analyze Imported Tracks")
        else:
            layout.operator("multi_audio.analyze_strip", icon="VIEWZOOM", text="Analyze Audio Tracks")
        
        if len(props.export_tracks) == 0:
            if selected_strip.type == 'META':
                layout.label(text="Click 'Analyze Imported Tracks' to scan", icon='INFO')
                layout.label(text="tracks that were imported")
            else:
                layout.label(text="Click 'Analyze Audio Tracks' to scan", icon='INFO')
                layout.label(text="for tracks in this strip")
            return
        
        layout.separator()
        
        # Export settings
        col = layout.column(align=True)
        col.label(text="Export Settings:", icon='SETTINGS')
        col.prop(props, "output_path")
        col.prop(props, "output_filename")
        
        row = col.row(align=True)
        # Only show video codec if we have video
        if selected_strip.type in ['MOVIE', 'META']:
            row.prop(props, "video_codec", text="Video")
        row.prop(props, "audio_codec", text="Audio")
        
        layout.separator()
        
        # Audio track selection with better organization
        enabled_tracks = [t for t in props.export_tracks if t.include]
        unused_tracks = [t for t in props.export_tracks if not t.include]
        
        if enabled_tracks:
            layout.label(text=f"Enabled Tracks ({len(enabled_tracks)}):", icon='CHECKMARK')
            
            for track in enabled_tracks:
                box = layout.box()
                row = box.row()
                row.prop(track, "include", text="")
                
                col = row.column()
                col.prop(track, "name", text="")
                
                col = row.column()
                col.enabled = False
                col.label(text=f"{track.channels}ch")
                col.label(text=track.codec)
                
                if track.language:
                    row = box.row()
                    row.enabled = False
                    row.scale_y = 0.7
                    row.label(text=f"Language: {track.language}", icon='WORLD')
        
        if unused_tracks:
            layout.separator()
            unused_box = layout.box()
            unused_box.label(text=f"Unused Tracks ({len(unused_tracks)}):", icon='X')
            
            for track in unused_tracks:
                row = unused_box.row()
                row.scale_y = 0.8
                row.prop(track, "include", text="")
                
                col = row.column()
                if track.name == "[unused]":
                    col.enabled = False
                    if track.language:
                        col.label(text=f"Track {track.index} ({track.language})")
                    else:
                        col.label(text=f"Track {track.index}")
                else:
                    col.prop(track, "name", text="")
                
                col = row.column()
                col.enabled = False
                col.label(text=f"{track.channels}ch")
        
        layout.separator()
        
        # Export button
        selected_count = sum(1 for track in props.export_tracks if track.include)
        if selected_count == 0:
            layout.label(text="Select at least one audio track", icon='ERROR')
        else:
            if selected_strip.type in ['MOVIE', 'META']:
                export_text = f"Export Video + {selected_count} Audio Track"
            else:
                export_text = f"Export {selected_count} Audio Track"
            if selected_count > 1:
                export_text += "s"
            layout.operator("multi_audio.export_multitrack", icon="EXPORT", text=export_text)

# NEW: Analyze single strip operator  
class AUDIO_OT_AnalyzeStrip(Operator):
    bl_idname = "multi_audio.analyze_strip"
    bl_label = "Analyze Audio Tracks"
    bl_description = "Analyze the selected strip for individual audio tracks"

    def execute(self, context):
        scene = _maio_vse_scene(context)
        props = scene.multi_audio_export_props
        props.export_tracks.clear()
        
        seq_editor = _maio_sequence_editor(scene)
        if not seq_editor:
            self.report({'ERROR'}, "No sequence editor available")
            return {'CANCELLED'}
        
        # Find the selected strip
        selected_strip = None
        for strip in _maio_context_selected_strips(context):
            if strip.type not in {'MOVIE', 'SOUND', 'META'}:
                continue
            if strip.type != 'META' and not _maio_strip_has_source(strip):
                continue

            if selected_strip is None:
                selected_strip = strip
            else:
                self.report({'ERROR'}, "Multiple strips selected. Please select only one.")
                return {'CANCELLED'}
        
        if not selected_strip:
            self.report({'ERROR'}, "No audio, video, or metastrip selected")
            return {'CANCELLED'}
        
        # Handle imported metastrips (created by import functionality)
        if selected_strip.type == 'META' and selected_strip.name.startswith("MultiAudio_"):
            return self._analyze_imported_metastrip(context, selected_strip, props)
        
        # Handle regular strips
        return self._analyze_regular_strip(context, selected_strip, props)
    
    def _analyze_imported_metastrip(self, context, meta_strip, props):
        """Analyze a metastrip created by the import functionality"""
        self.report({'INFO'}, f"Analyzing imported metastrip: {meta_strip.name}")
        
        # Enter the metastrip to analyze its contents
        scene = _maio_vse_scene(context)
        seq_editor = _maio_sequence_editor(scene)
        if not seq_editor:
            self.report({'ERROR'}, "No sequence editor available")
            return {'CANCELLED'}

        original_active = seq_editor.active_strip
        seq_editor.active_strip = meta_strip
        try:
            with _maio_sequencer_temp_override(context, scene):
                bpy.ops.sequencer.meta_toggle()
        except Exception as e:
            self.report({'ERROR'}, f"Failed to enter metastrip: {e}")
            return {'CANCELLED'}
        
        try:
            seq_editor = _maio_sequence_editor(scene)
            
            # Find the original video/audio strip and additional audio strips
            video_strip = None
            audio_strips = []
            
            for strip in (_maio_seq_editor_strips(seq_editor) or []):
                if strip.type == 'MOVIE':
                    video_strip = strip
                elif strip.type == 'SOUND' and strip.name.startswith("Audio_"):
                    audio_strips.append(strip)
            
            if not video_strip:
                self.report({'ERROR'}, "No video strip found in metastrip")
                return {'CANCELLED'}
            
            # Get the original source file for analysis
            source_file = bpy.path.abspath(video_strip.filepath)
            
            if not os.path.isfile(source_file):
                self.report({'ERROR'}, f"Source file not found: {source_file}")
                return {'CANCELLED'}
            
            # Analyze all audio tracks in the source file
            try:
                audio_streams = get_audio_tracks(source_file)
                
                if isinstance(audio_streams, dict) and "error" in audio_streams:
                    self.report({'ERROR'}, f"Could not analyze file: {audio_streams['detail']}")
                    return {'CANCELLED'}
                
                if not audio_streams:
                    self.report({'INFO'}, "No audio tracks found in source file")
                    return {'FINISHED'}
                
                # Create export track entries
                imported_track_names = set()
                
                # First add the imported tracks (enabled)
                for i, stream_info in enumerate(audio_streams):
                    export_track = props.export_tracks.add()
                    export_track.index = str(stream_info.get("index", ""))
                    export_track.channels = stream_info.get("channels", 2)
                    export_track.codec = stream_info.get("codec_name", "unknown")
                    
                    stream_lang_tags = stream_info.get("tags", {})
                    stream_lang = stream_lang_tags.get("language", "")
                    export_track.language = stream_lang
                    
                    # Check if this track was imported
                    if i == 0:
                        # First track (main video track)
                        export_track.name = "Main Audio"
                        export_track.include = True
                        imported_track_names.add("Main Audio")
                    else:
                        # Check if there's a corresponding audio strip
                        found_imported = False
                        for audio_strip in audio_strips:
                            # Match by language or track pattern
                            if stream_lang and stream_lang in audio_strip.name:
                                export_track.name = stream_lang.capitalize()
                                export_track.include = True
                                imported_track_names.add(export_track.name)
                                found_imported = True
                                break
                        
                        if not found_imported:
                            # This track wasn't imported
                            export_track.name = "[unused]"
                            export_track.include = False
                
                total_tracks = len(props.export_tracks)
                imported_count = len([t for t in props.export_tracks if t.include])
                
                self.report({'INFO'}, f"Found {total_tracks} total tracks, {imported_count} were imported")
                
                # Set default output filename
                if not props.output_filename:
                    base_name = meta_strip.name.replace("MultiAudio_", "")
                    props.output_filename = f"{base_name}_export.mkv"
                
                return {'FINISHED'}
                
            except Exception as e:
                self.report({'ERROR'}, f"Error analyzing metastrip: {e}")
                return {'CANCELLED'}
        
        finally:
            # Exit the metastrip
            try:
                with _maio_sequencer_temp_override(context, scene):
                    bpy.ops.sequencer.meta_toggle()
            except Exception:
                pass
            if original_active:
                seq_editor = _maio_sequence_editor(scene)
                if seq_editor:
                    seq_editor.active_strip = original_active
    
    def _analyze_regular_strip(self, context, selected_strip, props):
        """Analyze a regular audio/video strip"""
        # Get source file
        if selected_strip.type == 'MOVIE':
            source_file = bpy.path.abspath(selected_strip.filepath)
        elif selected_strip.type == 'SOUND':
            source_file = bpy.path.abspath(selected_strip.sound.filepath)
        else:
            self.report({'ERROR'}, "Unsupported strip type")
            return {'CANCELLED'}
        
        if not os.path.isfile(source_file):
            self.report({'ERROR'}, f"Source file not found: {source_file}")
            return {'CANCELLED'}
        
        # Analyze audio tracks in this file
        try:
            audio_streams = get_audio_tracks(source_file)
            
            if isinstance(audio_streams, dict) and "error" in audio_streams:
                self.report({'ERROR'}, f"Could not analyze file: {audio_streams['detail']}")
                return {'CANCELLED'}
            
            if not audio_streams:
                self.report({'INFO'}, "No audio tracks found in this file")
                return {'FINISHED'}
            
            # Add each audio stream as an export option
            for i, stream_info in enumerate(audio_streams):
                export_track = props.export_tracks.add()
                export_track.index = str(stream_info.get("index", ""))
                export_track.channels = stream_info.get("channels", 2)
                export_track.codec = stream_info.get("codec_name", "unknown")
                
                # Generate track name
                stream_lang_tags = stream_info.get("tags", {})
                stream_lang = stream_lang_tags.get("language", "")
                export_track.language = stream_lang
                
                if i == 0:
                    # First track enabled by default
                    export_track.name = stream_lang.capitalize() if stream_lang else "Main Audio"
                    export_track.include = True
                else:
                    # Additional tracks disabled by default, marked as unused
                    export_track.name = "[unused]"
                    export_track.include = False
            
            total_tracks = len(props.export_tracks)
            self.report({'INFO'}, f"Found {total_tracks} audio track(s) in {selected_strip.name}")
            self.report({'INFO'}, f"Only first track enabled by default - check others to include them")
            
            # Set default output filename if not set
            if not props.output_filename:
                base_name = os.path.splitext(selected_strip.name)[0]
                props.output_filename = f"{base_name}_multitrack.mkv"
            
            return {'FINISHED'}
            
        except Exception as e:
            self.report({'ERROR'}, f"Error analyzing strip: {e}")
            return {'CANCELLED'}

# NEW: Multi-track export operator
class AUDIO_OT_ExportMultitrack(Operator):
    bl_idname = "multi_audio.export_multitrack"
    bl_label = "Export Multi-Track File"
    bl_description = "Export the selected strip with multiple audio tracks to MKV file"

    def execute(self, context):
        scene = _maio_vse_scene(context)
        props = scene.multi_audio_export_props
        
        # Validate settings
        if not props.output_path:
            self.report({'ERROR'}, "Please set output path")
            return {'CANCELLED'}
        
        if not props.output_filename:
            self.report({'ERROR'}, "Please set output filename")
            return {'CANCELLED'}
        
        selected_tracks = [track for track in props.export_tracks if track.include]
        if not selected_tracks:
            self.report({'ERROR'}, "Please select at least one audio track")
            return {'CANCELLED'}
        
        # Find the selected strip
        selected_strip = None
        for strip in _maio_context_selected_strips(context):
            if strip.type not in {'MOVIE', 'SOUND', 'META'}:
                continue
            if strip.type != 'META' and not _maio_strip_has_source(strip):
                continue
            selected_strip = strip
            break
        
        if not selected_strip:
            self.report({'ERROR'}, "No strip selected")
            return {'CANCELLED'}
        
        # Get source information - handle metastrips
        if selected_strip.type == 'META':
            if not selected_strip.name.startswith("MultiAudio_"):
                self.report({'ERROR'}, "Unsupported metastrip type")
                return {'CANCELLED'}
            
            # Get source file from within metastrip
            source_file, has_video = self._get_metastrip_source(context, selected_strip)
            if not source_file:
                self.report({'ERROR'}, "Could not find source file in metastrip")
                return {'CANCELLED'}
        else:
            # Regular strip
            if selected_strip.type == 'MOVIE':
                source_file = bpy.path.abspath(selected_strip.filepath)
                has_video = True
            else:  # SOUND
                source_file = bpy.path.abspath(selected_strip.sound.filepath) if selected_strip.sound else ""
                has_video = False
        
        # Initialize progress
        wm = context.window_manager
        wm.progress_begin(0, 100)
        
        try:
            return self._perform_export(context, props, selected_tracks, source_file, has_video, wm)
        except Exception as e:
            self.report({'ERROR'}, f"Export failed: {e}")
            return {'CANCELLED'}
        finally:
            wm.progress_end()
    
    def _get_metastrip_source(self, context, meta_strip):
        """Get the source file and video status from within a metastrip"""
        scene = _maio_vse_scene(context)
        seq_editor = _maio_sequence_editor(scene)
        if not seq_editor:
            return None, False

        original_active = seq_editor.active_strip
        seq_editor.active_strip = meta_strip
        with _maio_sequencer_temp_override(context, scene):
            bpy.ops.sequencer.meta_toggle()
        
        try:
            seq_editor = _maio_sequence_editor(scene)
            
            # Find the video strip
            for strip in (_maio_seq_editor_strips(seq_editor) or []):
                if strip.type == 'MOVIE':
                    source_file = bpy.path.abspath(strip.filepath)
                    if os.path.isfile(source_file):
                        return source_file, True
            
            # If no video, look for audio
            for strip in (_maio_seq_editor_strips(seq_editor) or []):
                if strip.type == 'SOUND' and getattr(strip, "sound", None):
                    source_file = bpy.path.abspath(strip.sound.filepath) if strip.sound else ""
                    if os.path.isfile(source_file):
                        return source_file, False
            
            return None, False
        
        finally:
            # Exit the metastrip
            try:
                with _maio_sequencer_temp_override(context, scene):
                    bpy.ops.sequencer.meta_toggle()
            except Exception:
                pass
            if original_active:
                seq_editor = _maio_sequence_editor(scene)
                if seq_editor:
                    seq_editor.active_strip = original_active
    
    def _perform_export(self, context, props, selected_tracks, source_file, has_video, wm):
        # Phase 1: Prepare temporary directory and get source
        wm.progress_update(10)
        self.report({'INFO'}, "Preparing export...")
        
        temp_dir = tempfile.mkdtemp(prefix="blender_multitrack_")
        temp_files = []
        
        try:
            # Get source file
            if not os.path.isfile(source_file):
                raise Exception(f"Source file not found: {source_file}")
            
            # Phase 2: Extract individual audio tracks
            wm.progress_update(20)
            audio_files = []
            
            for i, track in enumerate(selected_tracks):
                track_progress = 20 + (60 * i / len(selected_tracks))
                wm.progress_update(track_progress)
                
                audio_file = os.path.join(temp_dir, f"audio_track_{i}.wav")
                temp_files.append(audio_file)
                
                self.report({'INFO'}, f"Extracting audio track: {track.name}")
                
                # Extract specific audio stream using index
                ffmpeg_exe = get_executable_path("ffmpeg")
                audio_cmd = [
                    ffmpeg_exe, "-y", "-i", source_file,
                    "-map", f"0:{track.index}",
                    "-acodec", "pcm_s16le", "-ar", "48000",
                    audio_file
                ]
                
                stdout, stderr = run_ffmpeg_with_progress(audio_cmd, 180, None, f"Audio Track: {track.name}")
                
                if stderr:
                    self.report({'WARNING'}, f"Audio extraction failed for {track.name}: {stderr}")
                    continue
                
                if os.path.exists(audio_file):
                    audio_files.append((audio_file, track.name))
                else:
                    self.report({'WARNING'}, f"Audio file not created for {track.name}")
            
            # Phase 3: Combine into final MKV
            wm.progress_update(85)
            
            if not audio_files:
                raise Exception("No audio tracks were successfully extracted")
            
            output_path = bpy.path.abspath(props.output_path)
            if not os.path.isdir(output_path):
                os.makedirs(output_path, exist_ok=True)
            
            final_output = os.path.join(output_path, props.output_filename)
            
            self.report({'INFO'}, f"Creating final MKV with {len(audio_files)} audio tracks...")
            
            # Build FFmpeg command for final mux
            ffmpeg_exe = get_executable_path("ffmpeg")
            final_cmd = [ffmpeg_exe, "-y"]
            
            # Add source as first input (for video if it exists)
            final_cmd.extend(["-i", source_file])
            
            # Add individual audio track files
            for audio_file, _ in audio_files:
                final_cmd.extend(["-i", audio_file])
            
            # Map streams
            if has_video:
                final_cmd.extend(["-map", "0:v"])  # Video from source
                final_cmd.extend(["-c:v", props.video_codec])
            
            # Map each audio track
            for i in range(len(audio_files)):
                final_cmd.extend(["-map", f"{i + 1}:a"])  # Audio from extracted files
            
            final_cmd.extend(["-c:a", props.audio_codec])
            
            # Add metadata for audio track names
            for i, (_, track_name) in enumerate(audio_files):
                final_cmd.extend([f"-metadata:s:a:{i}", f"title={track_name}"])
            
            final_cmd.append(final_output)
            
            # Execute final mux
            stdout, stderr = run_ffmpeg_with_progress(final_cmd, 600, None, "Final Export")
            
            if stderr:
                raise Exception(f"Final mux failed: {stderr}")
            
            wm.progress_update(100)
            
            if os.path.exists(final_output):
                file_size_mb = os.path.getsize(final_output) / (1024 * 1024)
                self.report({'INFO'}, f"✓ Export successful! File: {final_output} ({file_size_mb:.1f} MB)")
                if has_video:
                    self.report({'INFO'}, f"✓ Video + {len(audio_files)} audio tracks exported")
                else:
                    self.report({'INFO'}, f"✓ {len(audio_files)} audio tracks exported")
                return {'FINISHED'}
            else:
                raise Exception("Output file was not created")
        
        finally:
            # Cleanup temporary files
            for temp_file in temp_files:
                try:
                    if os.path.exists(temp_file):
                        os.remove(temp_file)
                except:
                    pass
            try:
                os.rmdir(temp_dir)
            except:
                pass

# NEW: Export properties container
class MultiAudioExportProperties(PropertyGroup):
    export_tracks: CollectionProperty(type=ExportAudioTrackItem)
    active_track_index: IntProperty()
    
    output_path: StringProperty(
        name="Output Directory",
        description="Directory to save the exported file",
        subtype='DIR_PATH',
        default=""
    )
    
    output_filename: StringProperty(
        name="Filename",
        description="Name for the exported file",
        default=""
    )
    
    video_codec: bpy.props.EnumProperty(
        name="Video Codec",
        description="Video codec for export",
        items=[
            ('copy', 'Copy (No Re-encode)', 'Copy video stream without re-encoding'),
            ('libx264', 'H.264', 'H.264 codec for broad compatibility'),
            ('libx265', 'H.265', 'H.265 codec for better compression'),
            ('libvpx-vp9', 'VP9', 'VP9 codec for web use'),
        ],
        default='copy'
    )
    
    audio_codec: bpy.props.EnumProperty(
        name="Audio Codec",
        description="Audio codec for export",
        items=[
            ('aac', 'AAC', 'AAC codec for broad compatibility'),
            ('mp3', 'MP3', 'MP3 codec for smaller files'),
            ('pcm_s16le', 'PCM WAV', 'Uncompressed audio for highest quality'),
            ('flac', 'FLAC', 'Lossless compression'),
        ],
        default='aac'
    )

# UI panel in the Video Sequence Editor
class SEQUENCER_PT_MultiAudioImport(Panel):
    bl_label = "Multi-Audio Import"
    bl_space_type = 'SEQUENCE_EDITOR'
    bl_region_type = 'UI'
    bl_category = 'Multi-Audio'

    def draw(self, context):
        layout = self.layout
        
        # Check if we're in the sequence editor and have strips
        scene = _maio_vse_scene(context)
        seq_editor = _maio_sequence_editor(scene)
        all_strips = _maio_seq_editor_strips(seq_editor) if seq_editor else None
        if not seq_editor or all_strips is None or len(all_strips) == 0:
            layout.label(text="No sequences available", icon='INFO')
            return
            
        # Check for selected video strips
        selected_video_strips = []
        
        for strip in _maio_context_selected_strips(context):
            if strip.type not in {'MOVIE', 'SOUND'}:
                continue
            if _maio_strip_has_source(strip):
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
                source_file = bpy.path.abspath(selected_strip.sound.filepath) if selected_strip.sound else ""
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
        scene = _maio_vse_scene(context)
        seq_editor = _maio_sequence_editor(scene)
        if not seq_editor:
            self.report({'ERROR'}, "No sequence editor available.")
            return {'CANCELLED'}
        
        # Find selected video/audio strip
        selected_strip = None
        for strip in _maio_context_selected_strips(context):
            if strip.type in {'MOVIE', 'SOUND'} and _maio_strip_has_source(strip):
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
            source_file = bpy.path.abspath(selected_strip.sound.filepath) if selected_strip.sound else ""
        
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
                self.report({'INFO'}, f"Only {len(found_audio_streams)} audio stream found in this file. No additional tracks to extract.")
                if found_audio_streams:
                    s = found_audio_streams[0]
                    tags = s.get("tags", {}) or {}
                    lang = tags.get("language", "")
                    title = tags.get("title", "")
                    details = f"index={s.get('index')}, codec={s.get('codec_name')}, channels={s.get('channels')}"
                    if lang or title:
                        details += f", lang={lang}, title={title}"
                    self.report({'INFO'}, f"Detected stream: {details}")
                self.report({'INFO'}, "Note: This addon extracts additional *audio streams* (tracks). It does not split a single multi-channel stream into multiple tracks.")
                self.report({'INFO'}, "If you expected more tracks, verify the source file actually contains multiple audio streams (e.g. with ffprobe).")
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
                occupied_channels = [s.channel for s in (_maio_seq_editor_strips(seq_editor) or [])]
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
                    stream_codec = stream_info.get("codec_name", "unknown")

                    # Use WAV format for universal compatibility instead of AAC
                    temp_audio_filename = f"additional_audio_{original_strip_name}_track_{stream_index}.wav"
                    # Save extracted audio next to original video file instead of temp directory
                    source_dir = os.path.dirname(source_file)
                    temp_path = os.path.join(source_dir, temp_audio_filename)
                    
                    self.report({'INFO'}, f"Extracting additional audio track {stream_index} ({stream_lang}, {stream_codec}) [{i+1}/{len(additional_tracks)}]...")
                    
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
                        
                        # Extract audio track and convert to WAV PCM for universal compatibility
                        ffmpeg_command = [
                            ffmpeg_exe, "-y", 
                            "-ss", f"{strip_start_offset_seconds:.6f}",  # Seek BEFORE input for accuracy
                            "-i", source_file,
                            "-map", f"0:{stream_index}", 
                            "-vn",  # No video output
                            "-acodec", "pcm_s16le",  # Convert to 16-bit PCM for WAV compatibility
                            "-ar", "48000",  # Standard sample rate
                            temp_path
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
                        audio_strip = _maio_seq_editor_new_sound(
                            seq_editor,
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
                    
                    # Capture existing strips so we can reliably detect the newly-created metastrip.
                    before_ptrs = set()
                    for s in (_maio_seq_editor_strips(seq_editor) or []):
                        try:
                            before_ptrs.add(s.as_pointer())
                        except Exception:
                            pass
                    
                    # Select all strips to include in metastrip (original + all new audio tracks)
                    with _maio_sequencer_temp_override(context, scene):
                        bpy.ops.sequencer.select_all(action='DESELECT')
                    selected_strip.select = True
                    for audio_strip in created_audio_strips:
                        audio_strip.select = True
                    seq_editor.active_strip = selected_strip
                    
                    # Create metastrip from all selected strips
                    meta_strip = None
                    try:
                        with _maio_sequencer_temp_override(context, scene):
                            bpy.ops.sequencer.meta_make()
                    except Exception as e:
                        self.report({'ERROR'}, f"Failed to create metastrip: {e}")
                    else:
                        # Prefer a newly-created META strip; fall back to selected/active meta.
                        after_strips = list(_maio_seq_editor_strips(seq_editor) or [])
                        new_strips = []
                        for s in after_strips:
                            try:
                                if s.as_pointer() not in before_ptrs:
                                    new_strips.append(s)
                            except Exception:
                                continue
                        
                        new_meta = [s for s in new_strips if getattr(s, "type", None) == 'META']
                        if new_meta:
                            meta_strip = new_meta[0]
                        else:
                            selected_meta = [s for s in after_strips if getattr(s, "type", None) == 'META' and getattr(s, "select", False)]
                            if selected_meta:
                                meta_strip = selected_meta[0]
                            elif getattr(seq_editor, "active_strip", None) and seq_editor.active_strip.type == 'META':
                                meta_strip = seq_editor.active_strip
                    
                    if meta_strip and meta_strip.type == 'META':
                        seq_editor.active_strip = meta_strip
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
                        # Restore strip placement so the user can see the extracted audio strips.
                        try:
                            selected_strip.frame_start = original_frame_start
                            selected_strip.channel = original_strip_channel
                        except Exception:
                            pass
                        for audio_strip in created_audio_strips:
                            try:
                                audio_strip.frame_start = original_frame_start
                            except Exception:
                                pass
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
    ExportAudioTrackItem,
    SEQUENCER_PT_MultiAudioImport,
    AUDIO_OT_ExtractAdditionalTracks,
    MultiAudioProperties,
    SEQUENCER_PT_MultiAudioExport,
    AUDIO_OT_AnalyzeStrip,
    AUDIO_OT_ExportMultitrack,
    MultiAudioExportProperties,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.multi_audio_props = bpy.props.PointerProperty(type=MultiAudioProperties)
    bpy.types.Scene.multi_audio_export_props = bpy.props.PointerProperty(type=MultiAudioExportProperties)

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    del bpy.types.Scene.multi_audio_props
    del bpy.types.Scene.multi_audio_export_props

if __name__ == "__main__":
    register()
