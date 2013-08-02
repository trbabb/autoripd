import subprocess as subp
import time
import thread
import errno
from common_util import Error, Babble

###########################
# Process Management      #
###########################

class SpawnLockedException(Exception):
    pass 


class ProcessManager:
    """Class for keeping track of child processes. All processes spawned from 
    this class will have their pids logged. This is used to ensure that all 
    child processes get properly terminated when the daemon recieves a SIGTERM 
    signal. 
    
    A process "start lock" is provided so that when the daemon recieves the 
    SIGTERM, it can safely prevent new child processes from spawning."""
    
    def __init__(self):
        self._threadlock = thread.allocate_lock()
        self._pids = set()
        self._startlock = False
    
    def Popen(self, *args, **kwargs):
        if self._startlock:
            sys.stdout.flush()
            raise SpawnLockedException("process spawning is locked")
        else:
            p = subp.Popen(*args, **kwargs)
            self.addProcess(p.pid)
            return PopenWrapper(p, self)
    
    def call(self, args):
        try:
            with self.Popen(args,
                            stdout=subp.PIPE, stderr=subp.PIPE) as pipe:
                sout, serr = pipe.communicate()
                Babble("%s output:\n%s\n%s\n" % (args[0], sout, serr))
                return pipe.returncode, sout, serr
        except OSError, err:
            if err.errno == errno.ENOENT:
                Error("%s could not be found. Is it installed?" % args[0])
            raise
    
    def addProcess(self, pid):
        with self._threadlock:
            self._pids.add(pid)
    
    def releaseProcess(self, pid):
        with self._threadlock:
            self._pids.remove(pid)
    
    def lockProcessStart(self):
        """Prevent any new processes from being launched."""
        with self._threadlock:
            self._startlock = True
    
    def unlockProcessStart(self):
        with self._threadlock:
            self._startlock = False
    
    def getActivePIDs(self):
        pidsCopy = None
        with self._threadlock:
            pidsCopy = set(self._pids)
        return pidsCopy


class PopenWrapper:
    """Wrapper for a subprocess.Popen object that keeps track of when the 
    process finishes, and reports this to its parent ProcessManager."""
    
    def __init__(self, pipe, procman, killtimeout=1.0):
        self._pipe = pipe
        self._procman = procman
        self._timeout = killtimeout
        self._released = False
        self.returncode = None
        self.stdout = pipe.stdout
        self.stdin = pipe.stdin
        self.stder = pipe.stderr
        self.pid = pipe.pid
    
    def poll(self):
        ret = self._pipe.poll()
        if ret is not None:
            #process has finished. log it as done.
            self.release()
        return ret
    
    def wait(self):
        ret = self._pipe.wait()
        self.release()
        return ret
    
    def communicate(self, input=None):
        out = self._pipe.communicate(input)
        self.release()
        return out
    
    def release(self):
        """Owned process is finished; report it so."""
        pid = self._pipe.pid
        self.returncode = self._pipe.returncode
        if not self._released:
            self._procman.releaseProcess(pid)
            self._released = True
    
    def __enter__(self):
        return self
    
    def __exit__(self, errtype, errval, tracebk):
        """Ensure that our child process is no longer running upon return"""
        pid = self._pipe.pid
        self.returncode = self._pipe.returncode
        if self._pipe.poll() is not None and not self._released:
            # process finished, but we haven't checked it until now.
            self.release()
        elif errtype is not None and not self._released:
            # inner code has thrown an exception.
            # terminate the child process now.
            
            try:
                self._pipe.terminate()
            except OSError, err:
                if err.errno == errno.ESRCH:
                    # process already dead
                    pass
                else:
                    raise
            
            # wait for child to finish cleaning up
            t_kill = time.time() + self._timeout
            while time.time() < t_kill and self._pipe.poll() is None:
                time.sleep(0.05)
            
            # out of time. kill it
            if self.poll() is None:
                try:
                    self._pipe.kill()
                except OSError, err:
                    if err.errno == errno.ESRCH:
                        # process already dead
                        pass
                    else:
                        raise
                self.release()
        else:
            # healthy exit of `with` stmt; but process still running.
            # wait for it to finish of its own accord.
            self._pipe.wait()
            self.release() 


DFT_MGR = ProcessManager()
