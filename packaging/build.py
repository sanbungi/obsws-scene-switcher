"""Build frozen applications and OS packages from the locked environment."""
import argparse
import hashlib
import importlib.metadata as metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import tomllib

ROOT = Path(__file__).resolve().parents[1]
VERSION = tomllib.loads((ROOT / 'pyproject.toml').read_text())['project']['version']
NAME = 'obs-scene-switcher'


def licenses():
    target = ROOT / 'build' / 'licenses'
    target.mkdir(parents=True, exist_ok=True)
    distributions = ['obsws-python', 'websocket-client', 'PySide6', 'PySide6-Essentials',
                     'PySide6-Addons', 'shiboken6', 'pyinstaller', 'pyinstaller-hooks-contrib', 'setuptools', 'packaging', 'typing-extensions']
    if sys.platform == 'linux':
        distributions += ['python-xlib', 'six']
    manifest = []
    for name in distributions:
        dist = metadata.distribution(name)
        manifest.append({'name': name, 'version': dist.version,
                         'license': dist.metadata.get('License-Expression') or dist.metadata.get('License'),
                         'source': dist.metadata.get_all('Project-URL') or dist.metadata.get('Home-page')})
        for file in dist.files or []:
            if any(word in str(file).lower() for word in ('license', 'copying', 'copyright', 'notice')):
                source = Path(dist.locate_file(file))
                if source.is_file():
                    dest = target / name / str(file).replace('..', '_')
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, dest)
    # Qt wheels do not consistently ship their license texts. Collect notices
    # from the exact upstream source tags, including bundled third-party code.
    if sys.platform == 'linux':
        for source in Path('/usr/share/doc').glob('*/copyright'):
            dest = target / 'system' / source.parent.name / 'copyright'
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, dest)
        if Path('/usr/share/common-licenses').is_dir():
            shutil.copytree('/usr/share/common-licenses', target / 'common-licenses', dirs_exist_ok=True)
    qt_version = metadata.version('PySide6')
    for repository in ('qt/qtbase', 'qt/qtsvg', 'qt/qtwayland', 'pyside/pyside-setup'):
        archive = ROOT / 'build' / (repository.replace('/', '-') + '-' + qt_version + '.tar.gz')
        url = f'https://codeload.github.com/{repository}/tar.gz/refs/tags/v{qt_version}'
        if not archive.exists():
            partial = archive.with_suffix('.download')
            with urllib.request.urlopen(url, timeout=120) as response, partial.open('wb') as output:
                shutil.copyfileobj(response, output)
            partial.replace(archive)
        folder = target / repository.replace('/', '-')
        count = 0
        with tarfile.open(archive) as source:
            for member in source:
                parts = Path(member.name).parts[1:]
                if not parts or '..' in parts or not member.isfile():
                    continue
                if any(token in member.name.lower() for token in ('license', 'copying', 'copyright', 'notice', 'qt_attribution')):
                    dest = folder.joinpath(*parts)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    with source.extractfile(member) as data, dest.open('wb') as output:
                        shutil.copyfileobj(data, output)
                    count += 1
        if not count:
            raise RuntimeError(f'No upstream notices collected from {url}')
    # The interpreter's complete license includes bundled third-party notices.
    import builtins
    builtins.license._Printer__setup()
    (target / 'Python-LICENSE.txt').write_text('\n'.join(builtins.license._Printer__lines), encoding='utf-8')
    (target / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    shutil.copy2(ROOT / 'packaging' / 'THIRD_PARTY.md', target)
    return target


def icon():
    from PIL import Image, ImageDraw
    image = Image.new('RGBA', (256, 256), '#202632')
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((24, 40, 232, 188), radius=20, outline='#70d8c4', width=14)
    draw.polygon([(104, 78), (104, 152), (166, 115)], fill='#ffffff')
    draw.rectangle((74, 208, 182, 222), fill='#70d8c4')
    folder = ROOT / 'build'
    folder.mkdir(exist_ok=True)
    image.save(folder / 'icon.png')
    image.save(folder / 'icon.ico', sizes=[(16,16), (32,32), (48,48), (256,256)])


def freeze(onefile=False):
    license_dir = licenses()
    icon()
    cmd = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
           '--specpath', str(ROOT / 'build'),
           '--additional-hooks-dir', str(ROOT / 'packaging' / 'hooks'),
           '--name', NAME, '--windowed', '--onefile' if onefile else '--onedir',
           '--add-data', f'{license_dir}{os.pathsep}licenses',
           '--icon', str(ROOT / 'build' / 'icon.ico'),
           '--hidden-import', 'PySide6.QtCore']
    if sys.platform == 'linux':
        cmd += ['--hidden-import', 'Xlib.display']
    cmd += [str(ROOT / 'main.py')]
    subprocess.run(cmd, cwd=ROOT, check=True)


def deb():
    stage = ROOT / 'build' / 'deb'
    if stage.exists():
        shutil.rmtree(stage)
    app = stage / 'opt' / NAME
    shutil.copytree(ROOT / 'dist' / NAME, app)
    for plugin in ('libqxcb.so', 'libqwayland.so'):
        if not list(app.rglob(plugin)):
            raise RuntimeError(f'Missing Qt platform plugin: {plugin}')
    bindir = stage / 'usr' / 'bin'
    bindir.mkdir(parents=True)
    launcher = bindir / NAME
    launcher.write_text(f'#!/bin/sh\nexec /opt/{NAME}/{NAME} "$@"\n')
    launcher.chmod(0o755)
    desktops = stage / 'usr' / 'share' / 'applications'
    desktops.mkdir(parents=True)
    shutil.copy2(ROOT / 'packaging' / f'{NAME}.desktop', desktops)
    icons = stage / 'usr' / 'share' / 'icons' / 'hicolor' / '256x256' / 'apps'
    icons.mkdir(parents=True)
    shutil.copy2(ROOT / 'build' / 'icon.png', icons / f'{NAME}.png')
    control = stage / 'DEBIAN'
    control.mkdir()
    dependencies = ('libc6 (>= 2.39), libstdc++6, libgcc-s1, libglib2.0-0t64, libdbus-1-3, '
        'libfontconfig1, libfreetype6, libx11-6, libx11-xcb1, libxext6, libxrender1, libxi6, '
        'libxcb1, libxcb-cursor0, libxcb-icccm4, libxcb-image0, libxcb-keysyms1, '
        'libxcb-render0, libxcb-render-util0, libxcb-shape0, libxcb-shm0, libxcb-sync1, '
        'libxcb-randr0, libxcb-xfixes0, libxcb-xkb1, libxkbcommon0, libxkbcommon-x11-0, '
        'libwayland-client0, libwayland-cursor0, libwayland-egl1, libegl1, libgl1, zlib1g')
    (control / 'control').write_text(f'Package: {NAME}\nVersion: {VERSION}\nArchitecture: amd64\n'
        f'Maintainer: OBS Scene Switcher contributors\nDepends: {dependencies}\n'
        'Section: video\nPriority: optional\nDescription: Active-window OBS scene switcher\n')
    subprocess.run(['dpkg-deb', '--root-owner-group', '--build', str(stage),
                    str(ROOT / 'dist' / f'{NAME}-{VERSION}-linux-amd64.deb')], check=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('target', choices=['linux', 'windows', 'version', 'checksums'])
    args = parser.parse_args()
    os.chdir(ROOT)
    if args.target == 'version':
        print(VERSION)
    elif args.target == 'checksums':
        artifacts = sorted(p for p in (ROOT / 'dist').iterdir() if p.suffix in ('.deb', '.exe'))
        (ROOT / 'dist' / 'SHA256SUMS').write_text(''.join(
            f'{hashlib.file_digest(p.open("rb"), "sha256").hexdigest()}  {p.name}\n' for p in artifacts))
    elif args.target == 'linux':
        freeze()
        deb()
    else:
        freeze()
        # Keep the directory build for Inno Setup; onefile has a distinct filename.
        subprocess.run(['iscc', f'/DAppVersion={VERSION}', str(ROOT / 'packaging' / 'installer.iss')], check=True)
        freeze(onefile=True)
        (ROOT / 'dist' / f'{NAME}.exe').rename(ROOT / 'dist' / f'{NAME}-{VERSION}-windows-x64.exe')
