"""
common_util

Common snippets that all libraries make use of.
"""

import os, sys
import datetime

progname = os.path.basename(sys.argv[0])
verbose = False

def nowtime():
    return datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

def Babble(s):
    if verbose:
        print "%s (%s): %s" % (progname, nowtime(), s)

def Msg(s):
    print "%s (%s): %s" % (progname, nowtime(), s)

def Warn(s):
    print >>sys.stderr, "%s (%s) WARNING: %s" % (progname, nowtime(), s)

def Error(s):
    print >>sys.stderr, "%s (%s) ERROR: %s" % (progname, nowtime(), s)

def Die(msg):
    print >> sys.stderr, "%s (%s) FATAL ERROR: %s" % (progname, nowtime(), msg)
    sys.exit(1)


def uniquePath(p):
    """Uniquify the file path given by <p>, i.e. try to ensure that <p> does not 
    already exist by adding digits if neccessary. Technically, this method has a 
    race condition, as other processes may interfere after the existence tests 
    have finished and the method returns. Does not create the file."""
    
    path, name = os.path.split(p)
    base, ext = os.path.splitext(name)
    n = 0
    while os.path.exists(p):
        p  = os.path.join(path, "%s.%d%s" % (base, n, ext))
        n += 1
    return p
