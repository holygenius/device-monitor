#!/usr/bin/env python3
"""Build a self-contained Apple Silicon app and compressed drag-to-install DMG.
Run with the build environment's Python. Optional: DEVICE_MONITOR_SIGN_IDENTITY
must be a Developer ID Application identity. Default builds are ad-hoc previews.
"""
from pathlib import Path
import os
import plistlib
import shutil
import subprocess
import sys
from PIL import Image, ImageDraw
from monitor_core import VERSION

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
ASSETS = ROOT / 'assets'
ASSETS.mkdir(exist_ok=True)
# Geometric icon drawn at Retina resolution; no external artwork or fonts.
im = Image.new('RGBA', (1024, 1024))
d = ImageDraw.Draw(im)
d.rounded_rectangle((60, 60, 964, 964), radius=190, fill='#111a29')
colors = ['#1689d1', '#149b83', '#7855bc', '#c64e7c']
for (x,y), color in zip([(160,160),(528,160),(160,528),(528,528)],colors):
    d.rounded_rectangle((x,y,x+336,y+336), radius=24, fill=color)
d.line([(207,690),(299,690),(347,593),(395,780),(443,690),
        (570,690),(617,690),(667,585),(713,765),(764,690),(815,690)],
       fill='white',width=19,joint='curve')
im.save(ASSETS / 'DeviceMonitor.icns', format='ICNS')
im.save(ASSETS / 'icon.png')

identity = os.environ.get('DEVICE_MONITOR_SIGN_IDENTITY', '')
if identity and not identity.startswith('Developer ID Application:'):
    raise SystemExit('Use a Developer ID Application identity for distribution.')
cmd = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--windowed',
       '--onedir', '--name', 'Device Monitor', '--target-arch', 'arm64',
       '--osx-bundle-identifier', 'app.devicemonitor.desktop',
       '--icon', str(ASSETS / 'DeviceMonitor.icns'),
       '--add-binary', f'{ROOT / "macmon"}:.',
       '--add-data', f'{ROOT / "macmon-LICENSE"}:licenses',
       '--add-data', f'{ROOT / "LICENSE"}:licenses',
       '--add-data', f'{ASSETS / "PYTHON-LICENSE.txt"}:licenses',
       '--add-data', f'{ASSETS / "TCL-LICENSE.txt"}:licenses',
       '--add-data', f'{ASSETS / "TK-LICENSE.txt"}:licenses',
       '--add-data', f'{ASSETS / "PYOBJC-LICENSE.txt"}:licenses',
       '--add-data', f'{ASSETS / "macmon-dependencies.html"}:licenses',
       '--add-data', f'{ROOT / "THIRD-PARTY-NOTICES.txt"}:licenses']
if identity:
    cmd.extend(['--codesign-identity', identity])
cmd.append(str(ROOT / 'device_monitor.py'))
subprocess.run(cmd, check=True)
app = ROOT / 'dist' / 'Device Monitor.app'
info = app / 'Contents' / 'Info.plist'
with info.open('rb') as f:
    data = plistlib.load(f)
data.update(CFBundleShortVersionString=VERSION, CFBundleVersion='100',
            NSHighResolutionCapable=True, LSMinimumSystemVersion='11.0',
            NSHumanReadableCopyright='Copyright 2026 Device Monitor contributors. MIT license.')
with info.open('wb') as f:
    plistlib.dump(data, f)
# Re-seal after setting version metadata. All nested code is signed by PyInstaller.
args = ['codesign', '--force', '--sign', identity or '-']
if identity:
    args += ['--options', 'runtime', '--timestamp']
subprocess.run(args + [str(app)], check=True)
subprocess.run(['codesign', '--verify', '--deep', '--strict', str(app)], check=True)

stage = ROOT / 'build' / 'dmg-root'
if stage.exists():
    shutil.rmtree(stage)
stage.mkdir(parents=True)
subprocess.run(['ditto', str(app), str(stage / app.name)], check=True)
(stage / 'Applications').symlink_to('/Applications', target_is_directory=True)
shutil.copy(ROOT / 'INSTALL.txt', stage / 'Install & Read Me.txt')
shutil.copy(ROOT / 'THIRD-PARTY-NOTICES.txt', stage)
name = f'Device-Monitor-{VERSION}-Apple-Silicon.dmg'
dmg = ROOT / name
subprocess.run(['hdiutil', 'create', '-volname', 'Device Monitor', '-srcfolder', str(stage),
                '-format', 'UDZO', '-ov', str(dmg)], check=True)
subprocess.run(['hdiutil', 'verify', str(dmg)], check=True)
print(f'\nCreated: {dmg}\nSigning: {identity or "Ad-hoc preview; not notarized"}')
