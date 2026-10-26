"""Behavior checks for public-build collection, reconnection, and fan events."""
from pathlib import Path
import os
import queue
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from monitor_core import normalize_sample, parse_processes, parse_ports, SensorReader, ProcessReader, Timeline


def wait_for(predicate,timeout=10):
    until = time.monotonic()+timeout
    while time.monotonic()<until:
        if predicate():
            return True
        time.sleep(.1)
    return False


class ParsingTests(unittest.TestCase):
    def test_missing_and_nonfinite_readings_are_not_zeroes(self):
        s = normalize_sample({'temp':{'cpu_temp_avg':float('nan'),'gpu_temp_avg':0},
                              'gpu_active_ratio':float('inf'), 'fans':[{'rpm':float('nan')}],
                              'memory':{'swap_usage':0}})
        self.assertIsNone(s['cpu_temp'])
        self.assertIsNone(s['gpu_temp'])
        self.assertIsNone(s['gpu'])
        self.assertIsNone(s['fan_rpm'])
        self.assertEqual(s['swap_used'],0)
        self.assertIsNone(s['ram_used'])

    def test_ports_deduplicate_ipv4_ipv6_and_map_to_pid(self):
        ports = parse_ports('p123\nn127.0.0.1:8000\nn[::1]:8000\nn*:3000\np456\nn*:11434\n')
        rows = parse_processes('123 501 250.5 2048 /path with spaces/Python\n456 501 0.1 1024 /Applications/ollama\ninvalid',ports)
        self.assertEqual(rows[0].ports,(3000,8000))
        self.assertEqual(rows[0].memory,2048*1024)
        self.assertEqual(rows[0].name,'Python')
        self.assertEqual(rows[0].cpu,250.5)  # Per-process CPU may exceed 100%.
        self.assertTrue(rows[1].is_dev)

    def test_fan_rise_records_correlated_process_and_expires(self):
        t = Timeline()
        for i in range(7):
            t.append(i,10,{'fan_rpm':1000,'gpu':40},'Python (PID 123)')
        t.append(7,70,{'fan_rpm':1550,'gpu':80},'Python (PID 123)')
        t.append(8,70,{'fan_rpm':1600,'gpu':80},'Python (PID 123)')
        self.assertEqual(len(t.events),1)
        self.assertEqual(t.events[0]['top'],'Python (PID 123)')
        t.append(400,0,{'fan_rpm':0})
        self.assertEqual(t.events,[])
        self.assertEqual(len(t.samples),1)

    def test_small_fan_fluctuations_and_unavailable_sensor_do_not_trigger(self):
        t = Timeline()
        for i in range(25):
            t.append(i,3,{'fan_rpm':1000+(i%3)*20})
        t.append(26,3,{})
        self.assertEqual(t.events,[])

    def test_models_filter_skips_system_listeners_but_keeps_custom_servers(self):
        rows = parse_processes('12 501 0 1024 /System/Library/ControlCenter\n13 501 1 2048 /projects/custom-server',
                               {12:(5000,),13:(8000,)})
        self.assertFalse(rows[0].is_dev)
        self.assertTrue(rows[1].is_dev)


class ReaderTests(unittest.TestCase):
    def test_stalled_stream_reconnects_after_first_reading(self):
        with tempfile.TemporaryDirectory() as directory:
            helper = Path(directory)/'stalled-helper'
            helper.write_text('#!/usr/bin/env python3\nimport time,json\nprint(json.dumps({"gpu_active_ratio":0.1}),flush=True)\ntime.sleep(30)\n')
            helper.chmod(0o755)
            events = []
            reader = SensorReader(helper,lambda k,v:events.append((k,v)),stale_timeout=.4)
            reader.start()
            try:
                self.assertTrue(wait_for(lambda:reader.starts>=2,5))
                self.assertTrue(any(k=='sensor' for k,v in events))
                self.assertTrue(any(k=='sensor_state' and 'retrying' in v for k,v in events))
            finally:
                reader.close()
            self.assertFalse(reader.thread.is_alive())

    def test_silent_startup_times_out_retries_and_shuts_down(self):
        with tempfile.TemporaryDirectory() as directory:
            helper = Path(directory)/'silent-helper'
            helper.write_text('#!/usr/bin/env python3\nimport time\ntime.sleep(30)\n')
            helper.chmod(0o755)
            events = []
            reader = SensorReader(helper,lambda k,v:events.append((k,v)),startup_timeout=.4)
            reader.start()
            try:
                self.assertTrue(wait_for(lambda:reader.starts>=2,5))
                self.assertTrue(any(k=='sensor_state' and 'retrying' in v for k,v in events))
            finally:
                reader.close()
            self.assertFalse(reader.thread.is_alive())
            self.assertIsNone(reader.process)

    def test_real_sensor_restart_recovers_readings(self):
        events = queue.Queue()
        reader = SensorReader(Path(__file__).resolve().parents[1]/'macmon',lambda k,v:events.put((k,v)))
        reader.start()
        def readings():
            found = False
            while not events.empty():
                k,v = events.get_nowait()
                found |= k=='sensor' and v[1]['gpu'] is not None
            return found
        try:
            self.assertTrue(wait_for(readings,8))
            old_pid = reader.process.pid
            reader.request_restart()  # Same path as the wake notification and Reconnect command.
            self.assertTrue(wait_for(lambda:reader.process is not None and reader.process.pid!=old_pid,5))
            self.assertTrue(wait_for(readings,8))
        finally:
            reader.close()
        self.assertFalse(reader.thread.is_alive())

    def test_real_local_server_appears_with_its_tcp_port(self):
        code = ('from http.server import HTTPServer,BaseHTTPRequestHandler; '
                's=HTTPServer(("127.0.0.1",0),BaseHTTPRequestHandler); '
                'print(s.server_port,flush=True); s.serve_forever()')
        server = subprocess.Popen([sys.executable,'-u','-c',code],stdout=subprocess.PIPE,text=True)
        port = int(server.stdout.readline())
        events = queue.Queue()
        reader = ProcessReader(lambda k,v:events.put((k,v)))
        reader.start()
        def found():
            while not events.empty():
                kind,value = events.get_nowait()
                if kind=='processes':
                    row = next((p for p in value[1] if p.pid==server.pid),None)
                    if row and port in row.ports and row.memory>0:
                        return True
            return False
        try:
            self.assertTrue(wait_for(found,12))
        finally:
            reader.close()
            server.terminate()
            server.wait(timeout=3)
            server.stdout.close()
        self.assertFalse(reader.thread.is_alive())


if __name__=='__main__':
    unittest.main(verbosity=2)
