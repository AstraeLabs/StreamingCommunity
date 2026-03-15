# 17.01.25

import os
import re
import json
import logging
import platform
import subprocess
import xml.etree.ElementTree as et
from typing import Optional, List
from pathlib import Path

from rich.console import Console
import ttconv.imsc.reader as imsc_reader
import ttconv.srt.writer as srt_writer
import ttconv.vtt.writer as vtt_writer
from ttconv.srt.config import SRTWriterConfiguration
from ttconv.vtt.config import VTTWriterConfiguration

from VibraVid.setup import get_ffprobe_path, get_ffmpeg_path
from .font import FontManager

# suppress ttconv logging (Merging ISD paragraphs/regions)
logging.getLogger("ttconv").setLevel(logging.WARNING)


console = Console()
logger = logging.getLogger(__name__)


def extract_font_name_from_style(style_line: str) -> Optional[str]:
    """
    Extract font name from ASS/SSA Style line.
    """
    try:
        if not style_line.startswith('Style:'):
            return None
        
        # Split by comma and get fields
        parts = style_line[6:].split(',')  # Skip 'Style:'
        
        if len(parts) < 2:
            return None
        
        # Font name is the second field (index 1)
        font_name = parts[1].strip()
        
        if not font_name:
            return None
            
        return font_name
        
    except Exception as e:
        console.print(f"[red]Error extracting font name from line: {style_line.strip()}: {str(e)}")
        return None


def convert_ttml_to_format(ttml_path: str, output_path: Optional[str] = None, target_format: str = 'srt') -> bool:
    """
    Convert TTML file or .m4s fragment containing TTML to SRT or VTT format.

    Args:
        ttml_path (str): Path to the TTML or .m4s file.
        output_path (Optional[str]): Path where to save the converted file. If None, uses same name as ttml_path but with target extension.
        target_format (str): The target format ('srt' or 'vtt').

    Returns:
        bool: True if conversion was successful, False otherwise.
    """
    if not os.path.exists(ttml_path):
        console.print(f"[red]File {ttml_path} does not exist")
        return False

    target_format = target_format.lower()
    if target_format not in ['srt', 'vtt']:
        console.print(f"[red]Unsupported target format for TTML conversion: {target_format}")
        return False

    if output_path is None:
        output_path = str(Path(ttml_path).with_suffix(f'.{target_format}'))

    try:
        with open(ttml_path, 'rb') as f:
            data = f.read()

        # Extract all TTML blocks (works for both plain TTML files and .m4s fragments)
        ttml_blocks = re.findall(br'<\?xml.*?</tt>', data, re.DOTALL)

        if not ttml_blocks:
            # Try to see if it's a plain TTML without the XML declaration or just one block
            try:
                text_content = data.decode('utf-8')
                if '<tt' in text_content and '</tt>' in text_content:
                    match = re.search(r'<tt.*?</tt>', text_content, re.DOTALL)
                    if match:
                        ttml_blocks = [match.group(0).encode('utf-8')]
            except Exception:
                pass

        if not ttml_blocks:
            console.print(f"[red]No valid TTML blocks found in {ttml_path}")
            return False

        all_captions: List[str] = []
        
        for block in ttml_blocks:
            try:
                # Decode and parse TTML
                ttml_str = block.decode('utf-8')
                root = et.fromstring(ttml_str)
                tree = et.ElementTree(root)

                # Convert TTML to internal model
                model = imsc_reader.to_model(tree)

                if model is not None:
                    if target_format == 'srt':
                        srt_config = SRTWriterConfiguration()
                        content = srt_writer.from_model(model, srt_config)
                    else:  # vtt
                        vtt_config = VTTWriterConfiguration()
                        content = vtt_writer.from_model(model, vtt_config)
                    
                    if content.strip():
                        all_captions.append(content.strip())

            except Exception as e:
                console.print(f"[yellow]Warning: Failed to process TTML block: {e}")
                continue

        if not all_captions:
            console.print(f"[red]No valid TTML blocks processed from {ttml_path}")
            return False

        # Combine output
        delimiter = "\n\n" if target_format == 'srt' else "\n"
        output_content = delimiter.join(all_captions)
        
        # Add VTT header if needed
        if target_format == 'vtt' and not output_content.startswith("WEBVTT"):
            output_content = "WEBVTT\n\n" + output_content

        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(output_content)

        # Sanitize based on format
        if target_format == 'srt':
            sanitize_srt_file(output_path)
        elif target_format == 'vtt':
            sanitize_vtt_file(output_path)

        console.print(f"[yellow]    - [green]Converted TTML to {target_format.upper()}: [red]{os.path.basename(output_path)}")
        return True

    except Exception as e:
        console.print(f"[red]Error during TTML to {target_format.upper()} conversion: {e}")
        return False
    

def extract_srt_from_m4s(m4s_file_path: str, output_srt_path: Optional[str] = None) -> str:
    """
    Compatibility wrapper for the user requested function name.
    """
    if convert_ttml_to_format(m4s_file_path, output_srt_path):
        if output_srt_path is None:
            output_srt_path = str(Path(m4s_file_path).with_suffix('.srt'))
        with open(output_srt_path, 'r', encoding='utf-8') as f:
            return f.read()
    else:
        raise ValueError("Failed to extract SRT from m4s")


def process_subtitle_fonts(subtitle_path: str):
    """Process fonts in subtitle files (ASS/SSA), warn if not found."""
    format = detect_subtitle_format(subtitle_path)
    if format not in ['ass', 'ssa']:
        return
    
    font_manager = FontManager()
    installed_fonts = font_manager.get_installed_fonts()
    
    if not installed_fonts:
        console.print("[red]Error: No fonts detected on system. Cannot process subtitle fonts.")
        return
    
    installed_fonts_lower = [f.lower() for f in installed_fonts]
    
    try:
        with open(subtitle_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    except Exception as e:
        console.print(f"[red]Error reading subtitle file {subtitle_path}: {str(e)}")
        return
    
    missing_fonts = set()
    found_fonts = set()
    
    for i, line in enumerate(lines):
        if line.startswith('Style:'):
            font_name = extract_font_name_from_style(line)
            
            if font_name is None:
                console.print(f"[yellow]Warning: Could not parse Style line {i+1}: {line.strip()}")
                continue
            
            # Check if font is installed
            if font_name.lower() in installed_fonts_lower:
                found_fonts.add(font_name)
            else:
                missing_fonts.add(font_name)
    
    system = platform.system()
    if missing_fonts:
        for font in sorted(missing_fonts):
            console.print(f"[yellow][{system}] No font found for '{font}' in {os.path.basename(subtitle_path)}")
    
    if not found_fonts and not missing_fonts:
        console.print(f"[yellow]No Style definitions found in {os.path.basename(subtitle_path)}")


def detect_subtitle_format(subtitle_path: str) -> Optional[str]:
    """Detects the actual format of a subtitle file using ffprobe and fallbacks."""
    try:
        cmd = [
            get_ffprobe_path(),
            "-v", "error",
            "-show_entries", "stream=codec_name",
            "-of", "json",
            subtitle_path
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            data = json.loads(result.stdout)
            streams = data.get("streams", [])
            if streams:
                codec = streams[0].get("codec_name", "").lower()
                
                # Map common ffmpeg subtitle codec names to our target extensions
                codec_map = {
                    "subrip": "srt",
                    "webvtt": "vtt",
                    "ass": "ass",
                    "ssa": "ssa",
                    "mov_text": "srt",
                    "ttml": "ttml",
                    "stpp": "ttml"
                }
                
                if codec in codec_map:
                    return codec_map[codec]

        # 2. Fallback: Check binary signatures for formats like stpp in mp4/m4s or raw TTML
        with open(subtitle_path, 'rb') as f:
            header = f.read(1024)
            # Check for MP4/M4S atoms or TTML content
            if any(sig in header for sig in [b'styp', b'ftyp', b'moof', b'moov', b'stpp']):
                return 'ttml'
            
            # Direct check for TTML tags
            if b'<tt' in header and b'http://www.w3.org/ns/ttml' in header:
                return 'ttml'
                
        # 3. Fallback: Manual regex checks for text formats
        with open(subtitle_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read(4096).lower()
            
            if 'webvtt' in content:
                return 'vtt'
            
            if '<tt ' in content or '<tt>' in content:
                return 'ttml'
            
            if '[script info]' in content or '[v4+ styles]' in content:
                return 'ass'
            
            if '-->' in content:
                return 'srt'
                
    except Exception as e:
        console.print(f"[red]Error detecting subtitle format for {subtitle_path}: {str(e)}")
    
    return None


def _clean_srt_tag(m: re.Match) -> str:
    """Return the tag unchanged if it is an allowed bare SRT tag, else remove it."""
    tag = m.group(2).lower()
    attrs = (m.group(3) or '').strip()
    if tag in {'i', 'b', 'u', 's'} and not attrs:
        return m.group(0)
    return ''


def sanitize_srt_file(subtitle_path: str) -> str:
    """Sanitize SRT subtitle files by removing invalid HTML tags. SRT only allows <i>, <b>, <u>, <s> without attributes."""
    try:
        with open(subtitle_path, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()
        
        logger.info(f"Sanitizing SRT: {os.path.basename(subtitle_path)}")
        sanitized_content = re.compile(r'<(/?)([a-zA-Z][a-zA-Z0-9]*)(\s[^>]*)?>').sub(_clean_srt_tag, content)
        
        if sanitized_content != content:
            with open(subtitle_path, 'w', encoding='utf-8') as f:
                f.write(sanitized_content)
            logger.info(f"SRT sanitized: {os.path.basename(subtitle_path)}")
        
        return subtitle_path
    except Exception as e:
        logger.warning(f"Could not sanitize SRT file {os.path.basename(subtitle_path)}: {str(e)}")
        return subtitle_path


def sanitize_vtt_file(subtitle_path: str) -> str:
    """Sanitize VTT subtitle files by replacing unmatched '<' symbols with '-'."""
    try:
        with open(subtitle_path, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()
        
        # Replace unmatched '<' symbols (not followed by closing '>') with '- '
        logger.debug(f"Sanitizing VTT: {os.path.basename(subtitle_path)}")
        sanitized_content = re.sub(r'<(?![^>]*>)', '- ', content)
        
        if sanitized_content != content:
            with open(subtitle_path, 'w', encoding='utf-8') as f:
                f.write(sanitized_content)
            logger.debug(f"VTT sanitized: {os.path.basename(subtitle_path)}")
        
        return subtitle_path
    except Exception as e:
        logger.warning(f"Could not sanitize VTT file {os.path.basename(subtitle_path)}: {str(e)}")
        return subtitle_path


def fix_subtitle_extension(subtitle_path: str) -> str:
    """Detects the actual subtitle format and renames the file with the correct extension."""
    detected_format = detect_subtitle_format(subtitle_path)
    
    if detected_format is None:
        console.print(f"[yellow]    Warning: Could not detect format for {os.path.basename(subtitle_path)}, keeping original extension")
        return subtitle_path
    
    # Get current extension
    base_name, current_ext = os.path.splitext(subtitle_path)
    current_ext = current_ext.lower().lstrip('.')
    
    # If extension is already correct, just process fonts for ASS/SSA
    if current_ext == detected_format:
        if detected_format in ['ass', 'ssa']:
            process_subtitle_fonts(subtitle_path)
        elif detected_format == 'vtt':
            sanitize_vtt_file(subtitle_path)
        return subtitle_path
    
    # Create new path with correct extension
    new_path = f"{base_name}.{detected_format}"
    
    try:
        if os.path.exists(new_path):
            os.remove(new_path)
        os.rename(subtitle_path, new_path)
        console.print(f"[yellow]    - [cyan]Detected [red]{current_ext} [cyan]but it is [red]{detected_format}[cyan], renamed: [green]{os.path.basename(new_path)}")
        return_path = new_path
    except Exception as e:
        console.print(f"[red]    Error renaming subtitle: {str(e)}")
        return_path = subtitle_path
    
    if detected_format in ['ass', 'ssa']:
        process_subtitle_fonts(return_path)
    elif detected_format == 'vtt':
        sanitize_vtt_file(return_path)

    return return_path


def convert_subtitle(subtitle_path: str, target_format: str) -> Optional[str]:
    """Converts a subtitle file to the target format using FFmpeg.

    Supported target formats:
      - 'vtt', 'srt', 'ass': convert to specified container format
      - 'auto': detect format and either rename or convert as needed
      - 'copy': leave the file untouched (no conversion or sanitization)
    """
    # no-op when user requests copy
    if target_format == 'copy':
        return subtitle_path

    if target_format == 'auto':
        detected_format = detect_subtitle_format(subtitle_path)
        
        # If it's TTML, we MUST convert it because most players/containers don't support raw TTML
        if detected_format == 'ttml':
            output_path = f"{os.path.splitext(subtitle_path)[0]}.srt"
            if convert_ttml_to_format(subtitle_path, output_path, 'srt'):
                return output_path
            return None
            
        # Otherwise, just ensure extension is correct
        return fix_subtitle_extension(subtitle_path)
        
    current_format = detect_subtitle_format(subtitle_path)
    if current_format == target_format:
        return subtitle_path
        
    output_path = f"{os.path.splitext(subtitle_path)[0]}.{target_format}"
    
    # Special high-fidelity converter for TTML -> (SRT, VTT)
    if current_format == 'ttml':
        if target_format in ['srt', 'vtt']:
            if convert_ttml_to_format(subtitle_path, output_path, target_format):
                return output_path
        elif target_format == 'ass':
            tmp_srt = f"{os.path.splitext(subtitle_path)[0]}_tmp.srt"
            if convert_ttml_to_format(subtitle_path, tmp_srt, 'srt'):
                res = convert_subtitle(tmp_srt, 'ass')
                try: 
                    os.remove(tmp_srt)
                except Exception: 
                    pass
                return res
            
        return None

    try:
        cmd = [get_ffmpeg_path(), "-v", "error", "-i", subtitle_path, output_path, "-y"]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            console.print(f"[yellow]    Converted subtitle to [cyan]{target_format}: [green]{os.path.basename(output_path)}")
            return output_path
        else:
            console.print(f"[red]    Failed to convert subtitle to {target_format}: {result.stderr}")
            return None
        
    except Exception as e:
        console.print(f"[red]    Error converting subtitle: {str(e)}")
        return None