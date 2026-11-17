"""首次升级旧后台：仅供用户双击启动器调用，不由开发工具自动执行。"""
from pathlib import Path
import ctypes
import json
import os
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import time
import urllib.request
from datetime import datetime

ROOT = Path(__file__).resolve().parent.parent


def split_windows_command(command):
    count=ctypes.c_int()
    parse=ctypes.windll.shell32.CommandLineToArgvW
    parse.argtypes=[ctypes.c_wchar_p,ctypes.POINTER(ctypes.c_int)]
    parse.restype=ctypes.POINTER(ctypes.c_wchar_p)
    values=parse(command,ctypes.byref(count))
    if not values:raise RuntimeError('无法读取后台启动参数')
    try:return [values[i] for i in range(count.value)]
    finally:ctypes.windll.kernel32.LocalFree(ctypes.cast(values,ctypes.c_void_p))


def find_service():
    command="[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe'\" | Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"
    powershell=Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe'
    result=subprocess.run([str(powershell),'-NoProfile','-NonInteractive','-Command',command],
                          capture_output=True,check=True,creationflags=subprocess.CREATE_NO_WINDOW)
    rows=json.loads(result.stdout.decode('utf-8-sig') or '[]')
    if isinstance(rows,dict):rows=[rows]
    found=[]
    for row in rows:
        args=split_windows_command(row.get('CommandLine') or '')
        if len(args)<4 or args[1:3]!=['-m','host.server']:continue
        def option(name):return args[args.index(name)+1] if name in args else None
        if option('--host')=='192.168.50.200' and option('--port')=='8765':
            if Path(args[0]).resolve()!=Path(sys.executable).resolve():raise RuntimeError('后台Python程序与启动器不一致，未重启')
            found.append((int(row['ProcessId']),args))
    if len(found)!=1:raise RuntimeError('没有找到唯一的顺言8765后台，未修改任何进程')
    return found[0]


def restart():
    pid,args=find_service()
    certificate=Path(args[args.index('--trust-cert')+1])
    raw=certificate.read_bytes()
    ca=raw.decode('ascii') if b'BEGIN CERTIFICATE' in raw else ssl.DER_cert_to_PEM_cert(raw)
    context=ssl.create_default_context(cadata=ca)
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPSHandler(context=context))
    with opener.open('https://192.168.50.200:8765/api/v1/local-status',timeout=5) as response:
        state=json.load(response)['data']
    if state.get('voiceActive') or state.get('audio',{}).get('state')=='streaming':
        raise RuntimeError('正在录音，请结束并松开所有按键后重试')
    private=certificate.parent.parent
    if '--config-dir' in args:private=Path(args[args.index('--config-dir')+1])
    backup=private/'service-backups'/datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    backup.mkdir(parents=True)
    for name in ('config.json','paired-devices.json','codex-connection.json'):
        path=private/name
        if path.is_file():shutil.copy2(path,backup/name)
    # Recheck exact target immediately before this user-initiated replacement.
    if find_service()!=(pid,args):raise RuntimeError('后台身份已变化，未重启')
    os.kill(pid,signal.SIGTERM)
    for _ in range(100):
        with socket.socket() as check:
            if check.connect_ex(('192.168.50.200',8765))!=0:break
        time.sleep(.1)
    else:raise RuntimeError('旧后台尚未释放端口，未启动第二个实例')
    logs=ROOT/'work';logs.mkdir(exist_ok=True)
    with (logs/'manual-restart.out.log').open('ab') as out,(logs/'manual-restart.err.log').open('ab') as err:
        subprocess.Popen(args,cwd=ROOT,stdin=subprocess.DEVNULL,stdout=out,stderr=err,
                         creationflags=subprocess.CREATE_NO_WINDOW|subprocess.CREATE_NEW_PROCESS_GROUP)
    for _ in range(100):
        try:
            with opener.open('https://192.168.50.200:8765/api/v1/health',timeout=1) as response:
                if json.load(response)['data'].get('restartAvailable'):return
        except Exception:pass
        time.sleep(.3)
    raise RuntimeError('新服务尚未恢复，请查看顺言项目work目录中的manual-restart日志')


if __name__=='__main__':
    try:restart()
    except Exception as exc:
        ctypes.windll.user32.MessageBoxW(None,str(exc),'顺言重启未完成',0x10)
