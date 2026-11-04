"""Local SSH connection profiles. No SSH passwords or private-key text in JSON."""
import base64
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import uuid


def validate_profile(data):
    if not isinstance(data,dict):raise ValueError('Cấu hình kết nối không hợp lệ.')
    result={k:str(data.get(k,'')).strip() for k in ('name','host','user','identity')}
    if not result['name'] or len(result['name'])>100:raise ValueError('Nhập tên kết nối, tối đa 100 ký tự.')
    host=result['host']
    try:ipaddress.ip_address(host)
    except ValueError:
        if len(host)>253 or not all(re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?',part) for part in host.split('.')):
            raise ValueError('Host/IP không hợp lệ.')
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.-]{0,63}',result['user']):raise ValueError('Tài khoản SSH không hợp lệ.')
    if '\0' in result['identity'] or len(result['identity'])>2000:raise ValueError('Đường dẫn SSH key không hợp lệ.')
    for key,default in (('port',22),('local_port',8765),('remote_port',8765)):
        value=data.get(key,default)
        if type(value) is not int or not 1<=value<=65535:raise ValueError('Cổng kết nối phải từ 1 đến 65535.')
        result[key]=value
    return result


class SSHProfiles:
    def __init__(self,directory):
        self.directory=Path(directory).resolve();self.directory.mkdir(parents=True,exist_ok=True)
        self.path=self.directory/'vps_connections.json'
        self.known_hosts=self.directory/'vps_known_hosts'

    def load(self):
        if not self.path.exists():return []
        value=json.loads(self.path.read_text(encoding='utf-8'))
        if not isinstance(value,list) or len(value)>100:raise ValueError('Danh sách kết nối không hợp lệ.')
        return [validate_profile(row) for row in value]

    def save(self,profiles):
        if len(profiles)>100:raise ValueError('Tối đa 100 kết nối.')
        profiles=[validate_profile(row) for row in profiles]
        temporary=self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(profiles,ensure_ascii=False,indent=2),encoding='utf-8')
        temporary.chmod(0o600);temporary.replace(self.path)

    def key_target(self):
        directory=self.directory/'ssh_keys';directory.mkdir(exist_ok=True)
        if os.name!='nt':directory.chmod(0o700)
        return directory/('mochi_'+uuid.uuid4().hex[:12]+'_ed25519')

    def import_key(self,path):
        source=Path(path).expanduser().resolve()
        if not source.is_file() or not 0<source.stat().st_size<=512*1024:raise ValueError('SSH key phải là file dưới 512 KB.')
        if b'PRIVATE KEY-----' not in source.read_bytes()[:256]:raise ValueError('Chọn private key OpenSSH/PEM, không phải file .pub hoặc PuTTY .ppk.')
        target=self.key_target();shutil.copyfile(source,target);target.chmod(0o600)
        public=source.with_name(source.name+'.pub')
        if public.is_file() and public.stat().st_size<=65536:shutil.copyfile(public,target.with_name(target.name+'.pub'))
        return target

    def ssh_arguments(self,profile):
        p=validate_profile(profile)
        if not p['identity'] or not Path(p['identity']).is_file():raise ValueError('Chọn SSH private key trước khi kết nối.')
        return ['-N','-T','-v','-p',str(p['port']),'-i',str(Path(p['identity']).resolve()),
            '-o','BatchMode=yes','-o','IdentitiesOnly=yes','-o','StrictHostKeyChecking=yes',
            '-o','ExitOnForwardFailure=yes','-o','ServerAliveInterval=30','-o','ServerAliveCountMax=3',
            '-o','UserKnownHostsFile="'+self.known_hosts.as_posix().replace('"','\\"')+'"','-L',f"127.0.0.1:{p['local_port']}:127.0.0.1:{p['remote_port']}",
            p['user']+'@'+p['host']]

    def scanned_key(self,profile,text):
        p=validate_profile(profile);host=p['host'] if p['port']==22 else f"[{p['host']}]:{p['port']}"
        for line in text.splitlines():
            parts=line.split()
            if len(parts)!=3 or parts[0]!=host or parts[1]!='ssh-ed25519':continue
            try:raw=base64.b64decode(parts[2],validate=True)
            except ValueError:continue
            if not 32<=len(raw)<=4096:continue
            fingerprint='SHA256:'+base64.b64encode(hashlib.sha256(raw).digest()).decode().rstrip('=')
            return line,fingerprint
        raise ValueError('Chưa đọc được host key ed25519 của VPS. Kiểm tra host/cổng SSH.')

    def trust_key(self,profile,line):
        line,_=self.scanned_key(profile,line)
        host=line.split()[0]
        old=self.known_hosts.read_text(encoding='utf-8') if self.known_hosts.exists() else ''
        kept=[row for row in old.splitlines() if not (row.split() and host in row.split()[0].split(',') and len(row.split())>1 and row.split()[1]=='ssh-ed25519')]
        temporary=self.known_hosts.with_suffix('.tmp')
        temporary.write_text('\n'.join(kept+[line])+'\n',encoding='utf-8');temporary.replace(self.known_hosts)

    def update_manager_url(self,profile):
        path=self.directory/'remote_connection.json'
        value=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
        value['url']='http://127.0.0.1:'+str(validate_profile(profile)['local_port'])
        temporary=path.with_suffix('.tmp')
        temporary.write_text(json.dumps(value),encoding='utf-8');temporary.chmod(0o600);temporary.replace(path)
