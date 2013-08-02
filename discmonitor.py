#!/usr/bin/python

"""
Module for monitoring optical drives.
"""

import os, sys
import dbus
import gobject
import thread
from dbus.mainloop import glib

from common_util import Error, Warn, Msg, Babble, Die


###########################
# Device monitoring/dbus  #
###########################


def debugEvt(dev_name, dev_path, props):
    sep = ("=" * 60) + "\n"
    s = sep + "ChangedEvent from %s at %s received\n" % (dev_name, dev_path) + sep
    for k,v in props.iteritems():
        s += "%s : %s\n" % (k,v)
    return s


def convertDBusTypes(obj):
    """Convert DBus objects to native python formats.
    Because I like it better this way."""
    
    typ = type(obj).__name__.lower()
    if 'int' in typ or typ == 'byte':
        obj = int(obj)
    elif 'string' in typ:
        obj = str(obj)
    elif 'bool' in typ:
        obj = bool(obj)
    elif 'float' in typ:
        obj = float(obj)
    elif 'double' in typ:
        obj = float(obj)
    elif 'array' in typ:
        obj = [convertDBusTypes(x) for x in obj]
    elif 'dictionary' in typ:
        obj = dict([(convertDBusTypes(k), convertDBusTypes(v)) 
                    for k,v in obj.iteritems()])
    return obj


def deviceProperties(bus, dbus_dev_addr):
    """Return all properties of a device (given by its DBus address, e.g.
    '/org/freedesktop/UDisks/devices/sr0') as a dictionary."""
    
    dev_obj   = bus.get_object("org.freedesktop.UDisks", dbus_dev_addr)
    dev_props = dbus.Interface(dev_obj, "org.freedesktop.DBus.Properties")
    d = {}
    for prop in dev_props.GetAll(''):
        val = dev_props.Get('', prop)
        d[str(prop)] = convertDBusTypes(val)
    return d


def deviceChangedCallback(bus, dev_name, dev_path, ripper):
    """Called when (among other things) a disk is inserted."""
    devprops = deviceProperties(bus, dev_path)
    
    closed, avail, blank = [devprops[x] for x in (
                                'OpticalDiscIsClosed',
                                'DeviceIsMediaAvailable',
                                'OpticalDiscIsBlank')]
    disc_kind = devprops['DriveMedia']
    if closed and avail and not blank:
        # each rip job gets its own thread.
        # we do this so that we don't block the udev monitoring loop, and
        # also any exceptions in the rip thread will not terminate the main
        # daemon thread.
        
        discID = None
        if 'IdLabel' in devprops:
            discID = devprops['IdLabel']
        
        if 'optical_bd' in disc_kind:
            Msg('blu-ray inserted into %s' % dev_name)
            # call ripper.ripBluRay in a dedicated thread, passing dev_name
            thread.start_new_thread(ripper.ripBluRay, (dev_name, discID))
        elif 'optical_dvd' in disc_kind:
            Msg('dvd inserted into %s' % dev_name)
            thread.start_new_thread(ripper.ripDVD, (dev_name, discID))
        else:
            Msg('%s inserted into %s; not ripping' % (disc_kind, dev_name))


def monitorDevices(device_array, ripper):
    # this is needed to ensure threading works:
    import gobject
    gobject.threads_init()
    
    # this is needed to monitor for callbacks:
    glib.DBusGMainLoop(set_as_default=True)
    
    bus = dbus.SystemBus()
    udisks = bus.get_object('org.freedesktop.UDisks', 
                            '/org/freedesktop/UDisks')
    udisks = dbus.Interface(udisks, 'org.freedesktop.UDisks')
    
    for dev in device_array:
        try:
            dev_path = udisks.FindDeviceByDeviceFile(dev)
            dev_obj  = bus.get_object("org.freedesktop.UDisks", dev_path)
            dev_ifc  = dbus.Interface(dev_obj, 
                               "org.freedesktop.UDisks.Device")
            
            # We use a lambda to store the device name with the callback
            # so we can tell which drive was changed from inside the function
            cbak = lambda: deviceChangedCallback(bus, dev, dev_path, ripper)
            dev_ifc.connect_to_signal('Changed', cbak)
            Msg("Monitoring %s" % dev)
        except:
            Warn("Device %s not found; will not monitor events" % dev)
    
    # begin monitoring
    mainloop = gobject.MainLoop()
    try:
        mainloop.run()
    except KeyboardInterrupt:
        # For some reason, this is raised when the daemon terminates with a
        # SIGTERM. I don't understand why this happens.
        pass

