"""Render the recorded CLI transcript in Pluto's site palette; no invented output."""
from pathlib import Path
import json
import shutil
import textwrap
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / 'assets/readme'
FONTS = ROOT.parent / 'pluto-site/assets/fonts'
events = json.loads((ASSETS / 'demo.json').read_text())
mono = ImageFont.truetype(str(FONTS / 'ibm-plex-mono.ttf'), 18)
title = ImageFont.truetype(str(FONTS / 'bricolage-grotesque.ttf'), 42)
label = ImageFont.truetype(str(FONTS / 'manrope.ttf'), 17)
frames = []
durations = []
lines = []


def paint(active):
    image = Image.new('RGB', (1200, 750), '#f4f2e9')
    d = ImageDraw.Draw(image)
    d.text((38, 25), 'pluto.', font=title, fill='#172e3b')
    d.text((210, 44), 'FROM FIRST COMMAND TO FIRST RESPONSE', font=label, fill='#546168')
    d.line((38, 100, 1162, 100), fill='#cdd0c6', width=1)
    d.rounded_rectangle((38, 125, 1162, 680), radius=16, fill='#102632')
    for x, color in [(63, '#ed7849'), (85, '#546168'), (107, '#546168')]:
        d.ellipse((x, 145, x+10, 155), fill=color)
    d.text((140, 139), 'orbit / terminal', font=label, fill='#cdd0c6')
    visible = (lines + active)[-18:]
    for i, (line, color) in enumerate(visible):
        d.text((62, 188+i*25), line, font=mono, fill=color)
    d.text((40, 707), 'Actual CLI + HTTP response. Compile waits shortened.', font=label, fill='#546168')
    d.text((960, 707), 'plutoframework', font=label, fill='#be4323')
    return image

for event in events:
    command = '$ ' + event['command']
    command_lines = command.splitlines()
    for length in range(0, len(command)+1, 5):
        typed = command[:length].splitlines() or ['']
        active = [(line, '#ed7849') for line in typed]
        frames.append(paint(active))
        durations.append(55)
    lines += [(line, '#ed7849') for line in command_lines]
    frames.append(paint([]))
    durations.append(450)
    for line in event['output'].splitlines():
        for wrapped in textwrap.wrap(line, width=98, replace_whitespace=False) or ['']:
            lines.append((wrapped, '#f4f2e9'))
    if event['output']:
        frames.append(paint([]))
        durations.append(1100)
    lines.append(('', '#f4f2e9'))
poster = paint([])
poster.save(ASSETS / 'demo.png')
frames.append(poster)
durations.append(4500)
frames[0].save(ASSETS / 'demo.gif', save_all=True, append_images=frames[1:],
               duration=durations, optimize=True, disposal=1, loop=1)
for name in ('ibmplexmono-OFL.txt', 'bricolagegrotesque-OFL.txt'):
    shutil.copy2(FONTS / name, ASSETS / name)
print(f'Rendered {len(frames)} frames; GIF {(ASSETS / "demo.gif").stat().st_size:,} bytes')
