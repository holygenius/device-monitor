"""Local data collection for Device Monitor. No network calls or persistent history."""
import ctypes
from dataclasses import dataclass, field
import json
import math
import os
from pathlib import Path
import re
import selectors
import subprocess
import threading
import time

VERSION = '1.0'


def number(value, default=None):
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return float(value)
    return default


def normalize_sample(raw):
    if not isinstance(raw, dict):
        raise ValueError('Expected a sensor object')
    temps = raw.get('temp') or {}
    mem = raw.get('memory') or {}
    fans = []
    for f in raw.get('fans') or []:
        rpm = number(f.get('rpm')) if isinstance(f, dict) else None
        if rpm is not None and rpm >= 0:
            fans.append({'rpm': rpm, 'max_rpm': number(f.get('max_rpm'), 8000)})
    ct, gt = number(temps.get('cpu_temp_avg')), number(temps.get('gpu_temp_avg'))
    gpu = number(raw.get('gpu_active_ratio'))
    return {'cpu_temp': ct if ct is not None and ct > 0 else None,
            'gpu_temp': gt if gt is not None and gt > 0 else None,
            'gpu': min(100, max(0, gpu * 100)) if gpu is not None else None,
            'ram_used': number(mem.get('ram_usage')),
            'ram_total': number(mem.get('ram_total')),
            'swap_used': number(mem.get('swap_usage')),
            'swap_total': number(mem.get('swap_total')),
            'fans': fans,
            'fan_rpm': sum(f['rpm'] for f in fans)/len(fans) if fans else None,
            'fan_max': max([f['max_rpm'] for f in fans] + [8000])}


class CpuMeter:
    def __init__(self):
        self.lib = ctypes.CDLL('/usr/lib/libSystem.B.dylib')
        self.lib.mach_host_self.restype = ctypes.c_uint
        self.lib.host_statistics.argtypes = [ctypes.c_uint, ctypes.c_int,
                                             ctypes.POINTER(ctypes.c_uint),
                                             ctypes.POINTER(ctypes.c_uint)]
        self.port = self.lib.mach_host_self()
        self.previous = self.read()

    def read(self):
        ticks = (ctypes.c_uint * 4)()
        count = ctypes.c_uint(4)
        if self.lib.host_statistics(self.port, 3, ticks, ctypes.byref(count)):
            raise OSError('CPU statistics unavailable')
        return list(ticks)

    def usage(self):
        now = self.read()
        delta = [(a-b) & 0xffffffff for a,b in zip(now, self.previous)]
        self.previous = now
        total = sum(delta)
        return 100*(total-delta[2])/total if total else 0


class SensorReader:
    """Supervise macmon, including silent startup, stalled pipes, and restarts."""
    def __init__(self, executable, publish, startup_timeout=12, stale_timeout=7):
        self.executable, self.publish = str(executable), publish
        self.startup_timeout, self.stale_timeout = startup_timeout, stale_timeout
        self.stop = threading.Event()
        self.restart = threading.Event()
        self.lock = threading.Lock()
        self.process = None
        self.starts = 0
        self.thread = threading.Thread(target=self.run, daemon=True, name='sensors')

    def start(self):
        self.thread.start()

    def request_restart(self):
        self.restart.set()

    @staticmethod
    def terminate(proc):
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=1.5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=1.5)

    def close(self):
        self.stop.set()
        self.restart.set()
        with self.lock:
            proc = self.process
        self.terminate(proc)
        self.thread.join(timeout=3)

    def run(self):
        delay = 1
        while not self.stop.is_set():
            self.restart.clear()
            self.publish('sensor_state', 'Connecting to sensors…' if not self.starts else 'Reconnecting to sensors…')
            proc = None
            started = time.monotonic()
            try:
                proc = subprocess.Popen([self.executable, 'pipe', '-i', '1000'],
                                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
                with self.lock:
                    self.process = proc
                    self.starts += 1
                if self.stop.is_set():
                    break
                self.read_stream(proc)
            except (OSError, RuntimeError, ValueError) as error:
                if not self.stop.is_set():
                    self.publish('sensor_state', 'Sensor connection interrupted · retrying automatically')
            finally:
                self.terminate(proc)
                if proc and proc.stdout:
                    proc.stdout.close()
                with self.lock:
                    self.process = None
                if time.monotonic()-started > 10:
                    delay = 1
            if self.stop.is_set():
                break
            # A wake/retry request skips exponential backoff.
            self.restart.wait(delay)
            delay = min(15, delay*2)

    def read_stream(self, proc):
        buffer = b''
        last = time.monotonic()
        received = False
        with selectors.DefaultSelector() as selector:
            selector.register(proc.stdout, selectors.EVENT_READ)
            while not self.stop.is_set() and not self.restart.is_set():
                ready = selector.select(timeout=0.5)
                if ready:
                    chunk = os.read(proc.stdout.fileno(), 65536)
                    if not chunk:
                        raise RuntimeError('Sensor reader exited')
                    buffer += chunk
                    if len(buffer) > 1024*1024:
                        raise ValueError('Sensor response too large')
                    while b'\n' in buffer:
                        line, buffer = buffer.split(b'\n', 1)
                        try:
                            sample = normalize_sample(json.loads(line))
                        except (ValueError, TypeError, AttributeError):
                            continue
                        last, received = time.monotonic(), True
                        self.publish('sensor', (last, sample))
                timeout = self.stale_timeout if received else self.startup_timeout
                if time.monotonic()-last > timeout:
                    raise RuntimeError('Sensor timeout')


@dataclass
class Process:
    pid: int
    uid: int
    cpu: float
    memory: int  # Resident bytes, not virtual address space.
    name: str
    executable: str = field(repr=False)
    ports: tuple = ()

    @property
    def is_dev(self):
        runtime = bool(re.search(
            r'python|node|ollama|llama|mlx|uvicorn|gunicorn|php|ruby|java|docker|bun|deno|vllm|lm studio|lms',
            self.name, re.I))
        system_service = self.executable.startswith(('/System/','/usr/libexec/'))
        return runtime or (bool(self.ports) and not system_service)


def parse_processes(text, ports=None):
    rows = []
    for line in text.splitlines():
        parts = line.strip().split(None, 4)
        if len(parts) != 5:
            continue
        try:
            pid, uid, cpu, rss = int(parts[0]), int(parts[1]), float(parts[2]), int(parts[3])
            if not math.isfinite(cpu) or pid <= 0:
                continue
        except ValueError:
            continue
        name = Path(parts[4]).name
        rows.append(Process(pid, uid, max(0,cpu), max(0,rss)*1024, name, parts[4],
                            tuple((ports or {}).get(pid, ()))))
    return rows


def parse_ports(text):
    ports, pid = {}, None
    for line in text.splitlines():
        if line.startswith('p'):
            try:
                pid = int(line[1:])
            except ValueError:
                pid = None
        elif line.startswith('n') and pid is not None:
            match = re.search(r':(\d+)$', line)
            if match:
                ports.setdefault(pid, set()).add(int(match[1]))
    return {pid: tuple(sorted(values)) for pid,values in ports.items()}


class ProcessReader:
    def __init__(self, publish):
        self.publish = publish
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True, name='processes')
        self.ports = {}
        self.last_ports = 0

    def start(self):
        self.thread.start()

    def run(self):
        while not self.stop.is_set():
            try:
                now = time.monotonic()
                port_state = None
                if now-self.last_ports > 6:
                    self.last_ports = now
                    try:
                        result = subprocess.run(['/usr/sbin/lsof', '-nP', '-a', '-u', str(os.getuid()),
                                                 '-iTCP', '-sTCP:LISTEN', '-Fpn'],
                                                capture_output=True, text=True, timeout=3)
                        self.ports = parse_ports(result.stdout) if result.returncode in (0,1) else {}
                        if result.returncode not in (0,1):
                            port_state = 'Local ports unavailable'
                    except (subprocess.SubprocessError, OSError):
                        self.ports = {}
                        port_state = 'Local port scan timed out'
                result = subprocess.run(['/bin/ps', '-A', '-o', 'pid=,uid=,pcpu=,rss=,comm='],
                                        capture_output=True, text=True, timeout=3, check=True,
                                        env={**os.environ, 'LC_ALL':'C'})
                self.publish('processes', (time.monotonic(), parse_processes(result.stdout,self.ports),port_state))
            except (subprocess.SubprocessError, OSError):
                self.publish('process_error', 'Process list unavailable · retrying')
            self.stop.wait(3)

    def close(self):
        self.stop.set()
        self.thread.join(timeout=7)


class Timeline:
    def __init__(self, seconds=300):
        self.seconds = seconds
        self.samples = []
        self.events = []
        self.last_event = -math.inf

    def append(self, stamp, cpu, sensor, top=None):
        row = {'time': stamp, 'cpu': cpu, **sensor}
        self.samples.append(row)
        self.samples = [p for p in self.samples if stamp-p['time'] <= self.seconds]
        rpm = sensor.get('fan_rpm')
        recent = [p['fan_rpm'] for p in self.samples[:-1]
                  if 3 <= stamp-p['time'] <= 10 and p.get('fan_rpm') is not None]
        # Record a meaningful change across several samples; a marker is correlation, not diagnosis.
        if rpm is not None and len(recent) >= 2 and rpm-min(recent) >= 400 and stamp-self.last_event >= 20:
            self.last_event = stamp
            self.events.append({'time': stamp, 'wall':time.strftime('%H:%M:%S'), 'rpm':rpm,
                                'cpu':cpu, 'gpu':sensor.get('gpu'), 'top':top})
        self.events = [e for e in self.events if stamp-e['time'] <= self.seconds]
        return row
