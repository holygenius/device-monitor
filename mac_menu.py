"""Native macOS menu-bar integration, sharing Tk's main run loop."""
import objc
from AppKit import NSStatusBar, NSVariableStatusItemLength, NSMenu, NSMenuItem, NSWorkspace, NSWorkspaceDidWakeNotification
from Foundation import NSObject


class MenuActions(NSObject):
    @objc.python_method
    def attach(self, app):
        self.app = app
        return self

    def showDashboard_(self, sender):
        self.app.root.after(0, self.app.show_dashboard)

    def hideDashboard_(self, sender):
        self.app.root.after(0, self.app.hide_dashboard)

    def quitMonitor_(self, sender):
        self.app.root.after(0, self.app.close)

    def wake_(self, notification):
        self.app.sensor_reader.request_restart()


class MenuBar:
    def __init__(self, app):
        self.actions = MenuActions.alloc().init().attach(app)
        self.item = NSStatusBar.systemStatusBar().statusItemWithLength_(NSVariableStatusItemLength)
        self.item.button().setTitle_('DM · starting')
        self.item.button().setToolTip_('Device Monitor · CPU activity and CPU temperature')
        self.menu = NSMenu.alloc().init()
        self.menu.setAutoenablesItems_(False)
        self.readings = []
        for text in ['Device Monitor 1.0','CPU: —','GPU: —','Temperatures: —','Fans: —','Memory: —','Swap: —']:
            entry = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(text,None,'')
            entry.setEnabled_(False)
            self.menu.addItem_(entry)
            self.readings.append(entry)
        self.menu.addItem_(NSMenuItem.separatorItem())
        for title, action, key in [('Show dashboard','showDashboard:',''),
                                   ('Hide dashboard','hideDashboard:',''),
                                   ('Quit Device Monitor','quitMonitor:','q')]:
            entry = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title,action,key)
            entry.setTarget_(self.actions)
            entry.setEnabled_(True)
            self.menu.addItem_(entry)
        self.item.setMenu_(self.menu)
        NSWorkspace.sharedWorkspace().notificationCenter().addObserver_selector_name_object_(
            self.actions,'wake:',NSWorkspaceDidWakeNotification,None)

    def update(self, cpu, sample):
        pct = lambda value: f'{value:.0f}%' if value is not None else '—'
        temp = lambda value: f'{value:.0f}°C' if value is not None else '—'
        gib = lambda value: f'{value/1024**3:.1f} GiB' if value is not None else '—'
        self.item.button().setTitle_(f'CPU {pct(cpu)} · {temp(sample.get("cpu_temp"))}')
        texts = ['Device Monitor 1.0',f'CPU: {pct(cpu)}',f'GPU: {pct(sample.get("gpu"))}',
                 f'Temperatures: CPU {temp(sample.get("cpu_temp"))} / GPU {temp(sample.get("gpu_temp"))}',
                 f'Fans: {round(sample["fan_rpm"]):,} RPM' if sample.get('fan_rpm') is not None else 'Fans: no reading',
                 f'Memory: {gib(sample.get("ram_used"))}',f'Swap: {gib(sample.get("swap_used"))}']
        for entry,text in zip(self.readings,texts):
            entry.setTitle_(text)

    def close(self):
        NSWorkspace.sharedWorkspace().notificationCenter().removeObserver_(self.actions)
        NSStatusBar.systemStatusBar().removeStatusItem_(self.item)
        self.actions.app = None
