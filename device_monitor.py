#!/usr/bin/env python3
"""Device Monitor 1.0 — a local-only dashboard for models, servers, and fan spikes."""
import datetime
import json
import os
from pathlib import Path
import queue
import signal
import sys
import time
import tkinter as tk
from tkinter import font as tkfont

from monitor_core import VERSION, CpuMeter, SensorReader, ProcessReader, Timeline

HERE = Path(__file__).resolve().parent
BG, CARD, FG, MUTED = '#0c111b', '#171f2d', '#f5f8ff', '#a6b4ca'
BLUE, GREEN, PURPLE, PINK, AMBER = '#51b6ff', '#45d9b3', '#b9a0ff', '#ff91b7', '#ffcb83'


class Monitor:
    def __init__(self, root):
        self.root = root
        self.events = queue.Queue(maxsize=64)
        self.closed = False
        self.cpu = CpuMeter()
        self.usage = None
        self.loads = os.getloadavg()
        self.cores = os.cpu_count() or 1
        self.sample = {}
        self.last_sensor = None
        self.sensor_state = 'Connecting to sensors…'
        self.processes = []
        self.last_processes = None
        self.process_error = None
        self.port_state = None
        self.process_filter = 'all'
        self.process_sort = 'cpu'
        self.timeline = Timeline()
        self.scale, self.offset = 1, (0,0)
        self.hits, self.fonts = [], {}
        self.help_window = None
        self.tray = None
        self.menu_error = None
        self.last_tick = time.monotonic()
        self.root.title('Device Monitor')
        width = min(1080, root.winfo_screenwidth()-60)
        height = min(860, root.winfo_screenheight()-110)
        root.geometry(f'{width}x{height}')
        root.minsize(900, 720)
        root.configure(bg=BG)
        self.canvas = tk.Canvas(root, bg=BG, highlightthickness=0)
        self.canvas.pack(fill='both', expand=True)
        self.canvas.bind('<Configure>', lambda event: self.draw())
        self.canvas.bind('<Button-1>', self.click)
        self.canvas.bind('<Motion>', self.pointer)
        root.bind('<Command-i>', lambda event: self.help())
        root.bind('<Command-q>', lambda event: self.close())
        root.protocol('WM_DELETE_WINDOW', self.hide_dashboard)
        root.createcommand('tk::mac::Quit', self.close)
        root.createcommand('tk::mac::ReopenApplication', self.show_dashboard)
        self.build_menu()
        self.sensor_reader = SensorReader(HERE / 'macmon', self.publish)
        self.process_reader = ProcessReader(self.publish)
        self.sensor_reader.start()
        self.process_reader.start()
        try:
            from mac_menu import MenuBar
            self.tray = MenuBar(self)
        except (ImportError, RuntimeError) as error:
            self.menu_error = 'Menu bar unavailable · use the dashboard'
        root.after(1000, self.tick)
        self.draw()

    def build_menu(self):
        menu = tk.Menu(self.root)
        apple = tk.Menu(menu, name='apple', tearoff=False)
        apple.add_command(label='About Device Monitor', command=self.help)
        apple.add_separator()
        menu.add_cascade(label='Device Monitor', menu=apple)
        view = tk.Menu(menu, tearoff=False)
        view.add_command(label='Show dashboard', command=self.show_dashboard)
        view.add_command(label='Hide dashboard', command=self.hide_dashboard)
        view.add_separator()
        view.add_command(label='Reconnect sensors', command=self.reconnect)
        view.add_command(label='Fan event history', command=self.fan_events)
        menu.add_cascade(label='View', menu=view)
        help_menu = tk.Menu(menu, tearoff=False)
        help_menu.add_command(label='Understanding the readings', command=self.help)
        menu.add_cascade(label='Help', menu=help_menu)
        self.root.configure(menu=menu)

    def publish(self, kind, value):
        if self.closed:
            return
        try:
            self.events.put_nowait((kind,value))
        except queue.Full:
            # Drop the oldest reading; do not let a paused UI accumulate stale data.
            try:
                self.events.get_nowait()
                self.events.put_nowait((kind,value))
            except (queue.Empty,queue.Full):
                pass

    @property
    def sensors_live(self):
        return self.last_sensor is not None and time.monotonic()-self.last_sensor < 7

    @property
    def live_sample(self):
        return self.sample if self.sensors_live else {}

    @property
    def processes_live(self):
        return self.last_processes is not None and time.monotonic()-self.last_processes < 12

    def tick(self):
        if self.closed:
            return
        now = time.monotonic()
        # A large gap may be sleep or a blocked run loop. Reconnect rather than show old sensor data.
        if now-self.last_tick > 10:
            self.last_sensor = None
            self.sensor_reader.request_restart()
        self.last_tick = now
        try:
            self.usage = self.cpu.usage()
            self.loads = os.getloadavg()
        except OSError:
            self.usage = None
        while True:
            try:
                kind,value = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == 'sensor':
                self.last_sensor, self.sample = value
                self.sensor_state = 'Live'
            elif kind == 'sensor_state':
                self.sensor_state = value
                self.last_sensor = None
            elif kind == 'processes':
                self.last_processes, self.processes, self.port_state = value
                self.process_error = None
            elif kind == 'process_error':
                self.process_error = value
                self.last_processes = None
            elif kind == 'project_hint':
                window,folder = value
                try:
                    if window.winfo_exists():
                        window.content.configure(state='normal')
                        window.content.insert('end','\n\nProject folder hint: '+folder)
                        window.content.configure(state='disabled')
                except tk.TclError:
                    pass
        rows = sorted(self.processes, key=lambda p:p.cpu, reverse=True) if self.processes_live else []
        top = f'{rows[0].name} (PID {rows[0].pid})' if rows else None
        self.timeline.append(now,self.usage,self.live_sample,top)
        if self.tray:
            self.tray.update(self.usage,self.live_sample)
        if self.root.state() != 'withdrawn':
            self.draw()
        self.root.after(1000,self.tick)

    def reconnect(self):
        self.last_sensor = None
        self.sensor_state = 'Reconnecting to sensors…'
        self.sensor_reader.request_restart()
        self.draw()

    def show_dashboard(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        self.draw()

    def hide_dashboard(self):
        if self.tray:
            self.root.withdraw()
        else:
            self.close()

    def rect(self,x,y,w,h,fill):
        s,(ox,oy) = self.scale,self.offset
        return self.canvas.create_rectangle(ox+x*s,oy+y*s,ox+(x+w)*s,oy+(y+h)*s,
                                             fill=fill,outline='')

    def text(self,x,y,content,size=12,color=FG,bold=False,anchor='nw',maxwidth=None):
        s,(ox,oy) = self.scale,self.offset
        pixels = max(9,round(size*s))
        key = (pixels,bold)
        if key not in self.fonts:
            self.fonts[key] = tkfont.Font(family='Helvetica Neue',size=-pixels,weight='bold' if bold else 'normal')
        font = self.fonts[key]
        content = str(content)
        if maxwidth is not None:
            limit = maxwidth*s
            if font.measure(content)>limit:
                while content and font.measure(content+'…')>limit:
                    content = content[:-1]
                content += '…'
        return self.canvas.create_text(ox+x*s,oy+y*s,text=content,anchor=anchor,fill=color,font=font)

    def line(self,coords,color,width=1,dash=None):
        s,(ox,oy) = self.scale,self.offset
        points = [(ox+v*s) if i%2==0 else (oy+v*s) for i,v in enumerate(coords)]
        opts = {'fill':color,'width':width*s}
        if dash:
            opts['dash'] = dash
        return self.canvas.create_line(*points,**opts)

    def button(self,x,y,w,h,label,action,active=False):
        self.rect(x,y,w,h,'#25425a' if active else '#263247')
        self.text(x+w/2,y+h/2,label,11,BLUE if active else FG,True,anchor='center')
        self.hits.append((x,y,x+w,y+h,action))

    def bar(self,x,y,w,ratio,color):
        self.rect(x,y,w,3,'#2b3749')
        if ratio is not None and ratio>0:
            self.rect(x,y,w*min(1,ratio),3,color)

    @staticmethod
    def percent(value):
        return f'{value:.1f}%' if value is not None else '—'

    @staticmethod
    def temperature(value):
        return f'{value:.1f}°C' if value is not None else '—'

    @staticmethod
    def gib(value):
        return f'{value/1024**3:.1f} GiB' if value is not None else '—'

    def tile(self,x,title,value,caption,color,ratio=None):
        self.rect(x,102,246,126,CARD)
        self.rect(x,102,246,3,color)
        self.text(x+16,119,title,11,MUTED,True)
        self.text(x+16,140,value,34,FG,True,maxwidth=215)
        self.text(x+16,186,caption,11,MUTED,maxwidth=216)
        if ratio is not None:
            self.bar(x+16,213,214,ratio,color)

    def draw(self):
        if self.closed:
            return
        c = self.canvas
        c.delete('all')
        self.hits = []
        w,h = max(c.winfo_width(),1),max(c.winfo_height(),1)
        self.scale = min(w/1080,h/860)
        self.offset = ((w-1080*self.scale)/2,(h-860*self.scale)/2)
        self.text(28,23,'device monitor',29,FG,True)
        self.text(29,65,'MODELS. SERVERS. A QUICK LOOK AT YOUR MAC.',11,MUTED,True)
        self.rect(804,29,122,28,'#15342e' if self.sensors_live else '#293044')
        self.text(865,43,'●  LIVE' if self.sensors_live else '●  RECONNECTING' if self.sensor_reader.starts else '●  CONNECTING',
                  10,GREEN if self.sensors_live else AMBER,True,anchor='center')
        self.button(938,29,54,28,'Help',self.help)
        self.button(1002,29,50,28,'Hide',self.hide_dashboard)
        sample = self.live_sample
        gpu = sample.get('gpu')
        self.tile(28,'CPU USAGE',self.percent(self.usage),'Across all logical CPU cores',BLUE,
                  self.usage/100 if self.usage is not None else 0)
        self.tile(286,'GPU ACTIVITY',self.percent(gpu),'Whole GPU · active time',PURPLE,
                  gpu/100 if gpu is not None else 0)
        self.tile(544,'CPU TEMPERATURE',self.temperature(sample.get('cpu_temp')),'Average sensor temperature',GREEN)
        self.tile(802,'GPU TEMPERATURE',self.temperature(sample.get('gpu_temp')),'Average sensor temperature',PINK)
        self.draw_summaries(sample)
        self.draw_timeline()
        self.draw_processes()
        self.draw_loads()
        status = self.menu_error or ('On-device only · Close window to keep monitoring in the menu bar' if self.sensors_live else self.sensor_state)
        self.text(29,836,status,10,MUTED if self.sensors_live else AMBER,maxwidth=780)
        self.text(1052,836,datetime.datetime.now().strftime('%H:%M:%S')+' · v'+VERSION,10,MUTED,anchor='ne')

    def draw_summaries(self,sample):
        for x in (28,374,720):
            self.rect(x,242,332,80,CARD)
        used,total = sample.get('ram_used'),sample.get('ram_total')
        self.text(44,255,'MEMORY',10,MUTED,True)
        self.text(44,274,self.gib(used),24,FG,True)
        self.text(196,280,f'of {total/1024**3:.0f} GiB' if total else 'No sensor data',11,MUTED)
        self.text(390,255,'SWAP USED',10,MUTED,True)
        swap = sample.get('swap_used')
        self.text(390,274,self.gib(swap),24,FG,True)
        self.text(549,280,'No swap in use' if swap==0 else 'Disk-backed memory' if swap is not None else 'No sensor data',11,MUTED)
        self.text(736,255,'FAN SPEED',10,MUTED,True)
        fans = sample.get('fans',[])
        rpm = sample.get('fan_rpm')
        self.text(736,274,f'{rpm:,.0f} RPM' if rpm is not None else '—',24,FG,True)
        if fans:
            label = ' · '.join(f'F{i+1} {f["rpm"]:,.0f}' for i,f in enumerate(fans[:2]))
        else:
            label = 'No reading / fanless'
        self.text(905,280,label,11,MUTED,maxwidth=134)

    def draw_timeline(self):
        self.rect(28,336,1024,190,CARD)
        self.text(44,350,'ACTIVITY TIMELINE',11,MUTED,True)
        self.text(227,350,'LAST 5 MINUTES',10,MUTED)
        self.button(927,345,109,24,f'Fan events ({len(self.timeline.events)})',self.fan_events)
        now = time.monotonic()
        start,end = 208,1034
        fan_max = max([p.get('fan_max',8000) for p in self.timeline.samples]+[8000])
        temp_max = max(120,max([p.get(k) or 0 for p in self.timeline.samples for k in ('cpu_temp','gpu_temp')]+[0]))
        lanes = [(380,414,100,('cpu','gpu'),(BLUE,PURPLE),'CPU / GPU','0–100%'),
                 (424,458,temp_max,('cpu_temp','gpu_temp'),(GREEN,PINK),'TEMPERATURE',f'0–{temp_max:.0f}°C'),
                 (468,500,fan_max,('fan_rpm',),(AMBER,),'FANS',f'0–{fan_max/1000:.0f}k RPM')]
        for top,bottom,maximum,keys,colors,label,axis in lanes:
            self.text(44,top,label,10,MUTED,True)
            self.text(44,top+15,axis,10,MUTED)
            self.line([start,bottom,end,bottom],'#2a3546')
            self.line([start,top,end,top],'#202c3d')
            for key,color in zip(keys,colors):
                segments,points,last = [],[],None
                for p in self.timeline.samples:
                    value = p.get(key)
                    if value is None or (last is not None and p['time']-last>4):
                        if len(points)>=4:
                            segments.append(points)
                        points = []
                    if value is not None:
                        x = start+max(0,min(1,1-(now-p['time'])/300))*(end-start)
                        points.extend((x,bottom-min(maximum,max(0,value))/maximum*(bottom-top)))
                    last = p['time']
                if len(points)>=4:
                    segments.append(points)
                for pts in segments:
                    self.line(pts,color,1.6)
        for event in self.timeline.events:
            x = start+max(0,1-(now-event['time'])/300)*(end-start)
            self.line([x,378,x,502],AMBER,1,dash=(2,3))
        self.text(208,507,'−5 min',9,MUTED)
        self.text(1034,507,'now',9,MUTED,anchor='ne')
        self.text(474,350,'CPU',10,BLUE)
        self.text(514,350,'GPU',10,PURPLE)
        self.text(567,350,'CPU °C',10,GREEN)
        self.text(634,350,'GPU °C',10,PINK)
        self.text(705,350,'Fan RPM',10,AMBER)

    def visible_processes(self):
        rows = self.processes if self.processes_live else []
        if self.process_filter=='dev':
            rows = [p for p in rows if p.is_dev]
        return sorted(rows,key=lambda p:p.cpu if self.process_sort=='cpu' else p.memory,reverse=True)[:5]

    def choose_processes(self,field,value):
        setattr(self,field,value)
        self.draw()

    def draw_processes(self):
        self.rect(28,540,1024,196,CARD)
        self.text(44,552,'WHAT’S RUNNING',11,MUTED,True)
        self.text(44,571,'Click a row for details · updates every 3 seconds',10,MUTED)
        self.button(656,550,59,24,'CPU ↓',lambda:self.choose_processes('process_sort','cpu'),self.process_sort=='cpu')
        self.button(721,550,59,24,'RAM ↓',lambda:self.choose_processes('process_sort','memory'),self.process_sort=='memory')
        self.button(795,550,54,24,'All',lambda:self.choose_processes('process_filter','all'),self.process_filter=='all')
        self.button(855,550,181,24,'Models & servers',lambda:self.choose_processes('process_filter','dev'),self.process_filter=='dev')
        for x,title in [(44,'PROCESS'),(402,'PID'),(506,'CPU*'),(644,'RAM'),(790,'LOCAL TCP PORTS')]:
            self.text(x,593,title,10,MUTED,True)
        rows = self.visible_processes()
        for i,p in enumerate(rows):
            y = 614+i*21
            if i%2==0:
                self.rect(36,y-2,1008,21,'#1c2737')
            self.text(44,y,p.name,11,FG,maxwidth=340)
            self.text(402,y,p.pid,11,MUTED)
            self.text(506,y,f'{p.cpu:.1f}%',11,BLUE)
            memory = f'{p.memory/1024**3:.2f} GiB' if p.memory>=1024**3 else f'{p.memory/1024**2:.0f} MiB'
            self.text(644,y,memory,11,FG)
            self.text(790,y,', '.join(str(port) for port in p.ports) or '—',11,GREEN if p.ports else MUTED,maxwidth=238)
            self.hits.append((36,y-2,1044,y+19,lambda proc=p:self.process_details(proc)))
        if not rows:
            self.text(44,632,self.process_error or ('No matching processes' if self.processes_live else 'Reading local processes…'),12,MUTED)
        hint = self.port_state or '* Process CPU: one core = 100%; multi-core tasks can exceed 100%. Ports shown for your user only.'
        self.text(44,719,hint,9,MUTED,maxwidth=985)

    def draw_loads(self):
        self.rect(28,744,1024,76,CARD)
        self.text(44,755,'CPU DEMAND',10,MUTED,True)
        self.text(44,775,f'{self.cores} logical cores',11,MUTED)
        self.text(44,798,'Running / waiting tasks',9,MUTED)
        for i,(label,value) in enumerate(zip(('1 MIN','5 MIN','15 MIN'),self.loads)):
            x = 273+i*253
            self.text(x,755,label,10,BLUE,True)
            self.text(x,772,f'{value:.2f}',23,FG,True)
            self.text(x+100,780,f'{value/self.cores:.0%} of cores',10,MUTED)
            self.bar(x,806,215,value/self.cores,BLUE)

    def click(self,event):
        x,y = (event.x-self.offset[0])/self.scale,(event.y-self.offset[1])/self.scale
        for left,top,right,bottom,action in reversed(self.hits):
            if left<=x<=right and top<=y<=bottom:
                action()
                return

    def pointer(self,event):
        x,y = (event.x-self.offset[0])/self.scale,(event.y-self.offset[1])/self.scale
        hover = any(a<=x<=c and b<=y<=d for a,b,c,d,_ in self.hits)
        self.canvas.configure(cursor='pointinghand' if hover else '')

    def text_window(self,title,content):
        window = tk.Toplevel(self.root)
        window.title(title)
        window.geometry('680x560')
        window.minsize(520,380)
        window.configure(bg=BG)
        tk.Label(window,text=title,bg=BG,fg=FG,font=('Helvetica Neue',21,'bold')).pack(anchor='w',padx=22,pady=20)
        body = tk.Frame(window,bg=BG)
        body.pack(fill='both',expand=True,padx=22,pady=(0,20))
        scroll = tk.Scrollbar(body)
        scroll.pack(side='right',fill='y')
        text = tk.Text(body,wrap='word',bg=CARD,fg=FG,relief='flat',font=('Helvetica Neue',13),
                       padx=18,pady=16,spacing3=7,yscrollcommand=scroll.set,highlightthickness=0)
        text.pack(fill='both',expand=True)
        scroll.configure(command=text.yview)
        text.insert('1.0',content)
        text.configure(state='disabled')
        window.content = text
        window.bind('<Escape>',lambda event:window.destroy())
        window.lift()
        window.focus_force()
        return window

    def help(self):
        if self.help_window and self.help_window.winfo_exists():
            self.help_window.lift()
            return
        self.help_window = self.text_window('Understanding your Mac',
            'Device Monitor 1.0\nFor the models and dev servers you leave running.\n\n'
            'CPU and GPU\nCPU usage covers all logical cores in the last sample. GPU activity is the GPU active-time '
            'ratio reported by macmon, not a percentage of theoretical compute throughput.\n\n'
            'Temperatures and fans\nTemperatures are CPU/GPU sensor averages in Celsius, not peak hot spots. '
            'Fan speeds are RPM; zero means stopped. Fanless Macs may have no fan readings.\n\n'
            'Activity timeline\nFive minutes of CPU/GPU activity, temperatures, and fan speed on a shared time axis. '
            'Each lane has its own units and scale. Gaps mean readings were unavailable. '
            'An amber marker records a fan rise of at least 400 RPM over recent samples. '
            'Fan events show the top CPU process observed around that time; this is correlation, not proof of cause.\n\n'
            'Processes and local ports\nCPU is macOS ps’s recent per-process CPU estimate; 100% is one core. '
            'This differs from the whole-device CPU tile. RAM is resident memory (RSS). '
            'Local TCP listening ports are scanned for your user only and may be unavailable for other users’ processes. '
            'The Models & servers filter matches familiar runtime names and listening ports; it does not identify model names. '
            'Click a row to see its executable and a project-folder hint when accessible. '
            'No command arguments, environment variables, or process controls are collected.\n\n'
            'Memory and swap\nMemory is reported in GiB. macOS uses RAM for caches too; this is not a memory-pressure indicator. '
            'Swap shows disk-backed memory currently in use, not a count of all memory traffic.\n\n'
            '1 / 5 / 15 minutes\nThese are macOS load averages: tasks running or waiting. '
            'The bars compare demand with logical core count and are not CPU usage gauges.\n\n'
            'Menu bar and quitting\nThe menu bar shows CPU activity and CPU temperature. Click it for a compact summary, '
            'to show/hide the dashboard, or to quit. Closing the dashboard keeps monitoring running in the menu bar. '
            'Quit Device Monitor (or Command-Q) stops all readers.\n\n'
            'Privacy\nNo accounts, analytics, network requests, or saved monitoring history. '
            'Process lists and five-minute activity stay in memory until you quit. Sensor readings use the bundled open-source macmon helper.')

    def fan_events(self):
        lines = ['Fan rises observed in the last five minutes. These are time correlations, not a diagnosis.']
        for e in reversed(self.timeline.events):
            lines.append(f'\n{e["wall"]}  ·  {e["rpm"]:,.0f} RPM\nCPU {self.percent(e["cpu"])} · GPU {self.percent(e["gpu"])}\nTop CPU then: {e["top"] or "Unavailable"}')
        if not self.timeline.events:
            lines.append('\nNo fan rises recorded yet. A rise of at least 400 RPM over recent samples creates a marker.')
        self.text_window('Fan events','\n'.join(lines))

    def process_details(self,proc):
        content = (f'{proc.name}\n\nPID: {proc.pid}\nCPU: {proc.cpu:.1f}% (one core = 100%)\n'
                   f'Resident RAM: {self.gib(proc.memory)}\n'
                   f'Listening TCP ports: {", ".join(map(str,proc.ports)) or "None visible"}\n\n'
                   f'Executable:\n{proc.executable}\n\n'
                   'This is a snapshot. The process may have exited or changed since the last refresh. '
                   'Other users’ ports may not be accessible. No arguments or environment variables are read.')
        # CWD is resolved on demand, not polled; show only its basename to avoid exposing full project paths.
        window = self.text_window(f'Process · {proc.name}',content)
        import threading
        def worker():
            import subprocess
            try:
                r = subprocess.run(['/usr/sbin/lsof','-a','-p',str(proc.pid),'-d','cwd','-Fn'],
                                   capture_output=True,text=True,timeout=2)
                folder = next((Path(l[1:]).name for l in r.stdout.splitlines() if l.startswith('n')),None)
            except (OSError,subprocess.SubprocessError):
                folder = None
            if folder:
                self.publish('project_hint',(window,folder))
        # The core dashboard intentionally avoids shell command strings and argument inspection.
        # Project hints are shown in the window title through a main-thread queue drain.
        threading.Thread(target=worker,daemon=True).start()
        return window

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.tray:
            self.tray.close()
        self.sensor_reader.close()
        self.process_reader.close()
        self.root.destroy()


def layout_check(app):
    results = []
    for size in ('900x720','1080x860','1280x940'):
        app.root.geometry(size)
        app.root.update_idletasks()
        app.draw()
        w,h = app.canvas.winfo_width(),app.canvas.winfo_height()
        clipped = []
        for item in app.canvas.find_all():
            box = app.canvas.bbox(item)
            if box and (box[0]<-2 or box[1]<-2 or box[2]>w+2 or box[3]>h+2):
                clipped.append(app.canvas.itemcget(item,'text') if app.canvas.type(item)=='text' else str(item))
        results.append({'size':size,'clipped':clipped})
    return results


def self_test(app,path,attempt=0):
    if attempt<8 and (not app.sensors_live or not app.processes_live):
        app.root.after(1000,lambda:self_test(app,path,attempt+1))
        return
    sample = app.live_sample
    report = {'version':VERSION,'sensor_live':bool(app.sensors_live),'cpu':app.usage,
              'gpu':sample.get('gpu'),'swap':sample.get('swap_used'),
              'process_count':len(app.processes),'local_ports_detected':sum(bool(p.ports) for p in app.processes),
              'menu_bar':bool(app.tray),'layouts':layout_check(app)}
    details = app.process_details(next((p for p in app.processes if p.pid==os.getpid()),app.processes[0]))
    report['details_window'] = bool(details.winfo_exists() and details.title().startswith('Process'))
    details.destroy()
    app.help()
    report['help_window'] = bool(app.help_window.winfo_exists())
    app.help_window.destroy()
    # Exercise Cocoa target/action selectors, not only the Python view methods.
    if app.tray:
        app.tray.menu.performActionForItemAtIndex_(9)
        app.root.update()
    else:
        app.hide_dashboard()
    report['hide_works'] = app.root.state()=='withdrawn'
    if app.tray:
        app.tray.menu.performActionForItemAtIndex_(8)
        app.root.update()
    else:
        app.show_dashboard()
    report['show_works'] = app.root.state()!='withdrawn'
    app.close()
    report['readers_stopped'] = not app.sensor_reader.thread.is_alive() and not app.process_reader.thread.is_alive()
    report['passed'] = bool(report['sensor_live'] and report['cpu'] is not None and report['gpu'] is not None
                            and report['process_count'] and report['menu_bar'] and report['hide_works']
                            and report['show_works'] and report['readers_stopped']
                            and report['details_window'] and report['help_window']
                            and not any(x['clipped'] for x in report['layouts']))
    Path(path).write_text(json.dumps(report,indent=2))


def main():
    root = tk.Tk()
    app = Monitor(root)
    signal.signal(signal.SIGTERM,lambda *_:root.after(0,app.close))
    signal.signal(signal.SIGINT,lambda *_:root.after(0,app.close))
    if len(sys.argv)==3 and sys.argv[1]=='--self-test':
        root.after(6500,lambda:self_test(app,sys.argv[2]))
    root.mainloop()


if __name__=='__main__':
    main()
