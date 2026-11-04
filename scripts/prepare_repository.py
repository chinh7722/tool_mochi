"""Build a reviewable source archive; never include the extracted distribution."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINTS = ('main_mochi_gui.py','main_mochi_server.py','main_mochi_web.py','main_vps_manager.py','cli_mochi.py',
    'Mo_Giao_Dien_Quan_Ly_VPS.bat',
    'Launch_HoshinoTool.bat','Launch_MochiTool.bat','Launch_VPS_Manager.bat','Launch_Web_VPS_Manager.bat','Open_VPS_Tunnel.bat',
    '.gitignore','.gitattributes','requirements.txt','requirements-core.txt')


def source_files(root=ROOT):
    selected = {root/name for name in ENTRYPOINTS if (root/name).is_file()}
    selected.update(root.glob('*.md'))
    for directory in ('core','gui','src','web','tests','scripts','deploy','docs','.github','examples'):
        for path in (root/directory).rglob('*'):
            if not path.is_file() or path.is_symlink():
                continue
            relative = path.relative_to(root)
            if any(part in ('__pycache__','android','data','node_modules','.venv') for part in relative.parts):
                continue
            allowed = ('.py','.md','.sh','.service','.yml','.yaml','.txt','.svg','.png','.jpg','.ico')
            if directory=='web':
                allowed += ('.html','.css','.js')
            if path.suffix.lower() in allowed or path.name=='Dockerfile':
                selected.add(path)
    return sorted(selected)


def check_mirrors(root=ROOT):
    mismatches = []
    for tree in ('core','gui'):
        for path in (root/tree).rglob('*.py'):
            if 'android' in path.relative_to(root).parts:
                continue
            mirror = root/'src'/path.relative_to(root)
            if not mirror.is_file() or path.read_bytes()!=mirror.read_bytes():
                mismatches.append(str(path.relative_to(root)))
    if mismatches:
        raise ValueError('Root/src differ: '+', '.join(mismatches))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output',default=str(ROOT/'.tmp'/'github-source.zip'))
    args = parser.parse_args()
    check_mirrors()
    files = source_files()
    output = Path(args.output).resolve()
    if output in files:
        raise ValueError('Output must be outside source paths.')
    output.parent.mkdir(parents=True,exist_ok=True)
    manifest = {}
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            name = path.relative_to(ROOT).as_posix()
            content = path.read_bytes()
            manifest[name] = hashlib.sha256(content).hexdigest()
            archive.writestr(name,content)
    output.with_suffix('.manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    print(f'{len(files)} source files; {output.stat().st_size} bytes; {output}')


if __name__=='__main__':
    main()
