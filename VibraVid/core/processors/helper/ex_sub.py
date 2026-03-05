# 17.01.25

import os
import re
import shutil
import platform
from typing import Optional

from rich.console import Console

from .ex_font import FontManager

console = Console()


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
    
    # Report findings
    system = platform.system()
    if missing_fonts:
        for font in sorted(missing_fonts):
            console.print(f"[yellow][{system}] No font found for '{font}' in {os.path.basename(subtitle_path)}")
    
    if not found_fonts and not missing_fonts:
        console.print(f"[yellow]No Style definitions found in {os.path.basename(subtitle_path)}")


def detect_subtitle_format(subtitle_path: str) -> Optional[str]:
    """Detects the actual format of a subtitle file by examining its content."""
    try:
        # Check binary signatures first for formats like stpp in mp4/m4s
        with open(subtitle_path, 'rb') as f:
            header = f.read(32)

            # Check for MP4/M4S signatures (styp, ftyp, moof)
            if any(sig in header for sig in [b'styp', b'ftyp', b'moof']):
                return 'ttml'
                
        with open(subtitle_path, 'r', encoding='utf-8', errors='ignore') as f:
            first_lines = ''.join([f.readline() for _ in range(20)]).lower()
            
            if re.search(r'webvtt', first_lines, re.IGNORECASE):
                return 'vtt'
            
            if re.search(r'<tt\s', first_lines, re.IGNORECASE):
                return 'ttml'
            
            if re.search(r'\[script info\]', first_lines, re.IGNORECASE) or re.search(r'\[v4\+ styles\]', first_lines, re.IGNORECASE) or re.search(r'\[v4 styles\]', first_lines, re.IGNORECASE):
                if re.search(r'format:\s*name', first_lines, re.IGNORECASE) or re.search(r'format:\s*marked', first_lines, re.IGNORECASE):
                    return 'ass'
                return 'ssa'
            
            lines = first_lines.split('\n')
            for i, line in enumerate(lines):
                if re.match(r'^\d+$', line.strip()) and i + 1 < len(lines):
                    if '-->' in lines[i + 1]:
                        return 'srt'
            
            if re.search(r'-->', first_lines):
                return 'srt'
                
    except Exception as e:
        console.print(f"[red]Error detecting subtitle format for {subtitle_path}: {str(e)}")
    
    return None


def fix_subtitle_extension(subtitle_path: str) -> str:
    """Detects the actual subtitle format and renames the file with the correct extension."""
    detected_format = detect_subtitle_format(subtitle_path)
    
    if detected_format is None:
        console.print(f"[yellow]    Warning: Could not detect format for {subtitle_path}, keeping original extension")
        return subtitle_path
    
    # Get current extension
    base_name, current_ext = os.path.splitext(subtitle_path)
    current_ext = current_ext.lower().lstrip('.')
    
    # If extension is already correct, just process fonts for ASS/SSA
    if current_ext == detected_format:
        if detected_format in ['ass', 'ssa']:
            process_subtitle_fonts(subtitle_path)
        return subtitle_path
    
    # Create new path with correct extension
    new_path = f"{base_name}.{detected_format}"
    
    try:
        shutil.move(subtitle_path, new_path)
        console.print(f"[yellow]    Renamed subtitle: [cyan]{current_ext} [yellow]-> [cyan]{detected_format}")
        return_path = new_path
    except Exception as e:
        console.print(f"[red]    Error renaming subtitle: {str(e)}")
        return_path = subtitle_path
    
    if detected_format in ['ass', 'ssa']:
        process_subtitle_fonts(return_path)
    return return_path