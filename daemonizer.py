import os, sys
import errno
import signal
import atexit
import resource
import time

"""
"daemonizer" module

For converting a python process into a daemon.

Use class `Daemon` for lower level control, and to become a daemon process 
yourself. 

For higher-level control, and to start/stop daemon processes, use a 
DaemonController.

Some of the code for this module has been taken and modified from PEP 3143's 
python-daemon library implementation. Modifications are mostly to allow for 
running the daemon with reduced privileges.

Current implementation responsibilities:
  - kill all child processes via `on_terminate` callback.
""" 


class DaemonError(Exception):
    pass

class PidfileLocked(Exception):
    pass

class DaemonStopError(Exception):
    pass

class DaemonStartFailure(Exception):
    pass

class Daemon:
    """Class for detaching from the terminal and running code as a daemon 
    process.""" 
    
    def __init__(
            self,
            working_dir='/',
            umask=0,
            uid=None,
            gid=None,
            prevent_core=True,
            detach_process=None, # default: detach unless already detached
            files_preserve=None,
            pidfile_path=None,
            stdin=None,
            stdout=None, # may be a file object or a file descriptor
            stderr=None,
            on_terminate=None  # no-arg function to call when process is killed
            ):
        
        self.wdir = working_dir
        self.umask = umask
        self.uid = uid
        self.gid = gid
        self.prevent_core = prevent_core
        self.detach_process = detach_process
        self.files_preserve = files_preserve
        self.pidfile_path = pidfile_path
        self.stdin = stdin
        self.stdout = stdout
        self.stderr = stderr
        self.on_terminate = on_terminate
        
        if uid is None:
            uid = os.getuid()
        self.uid = uid
        if gid is None:
            gid = os.getgid()
        self.gid = gid
        
        self._is_started = False
    
    
    def isStarted(self):
        return self._is_started
    
    
    def start(self):
        """Become a daemon. Raise a PidfileLocked if an existing pidfile 
        prevented us from starting."""
        
        if self._is_started:
            return True
        
        own_pidfile = False
        
        try:
            # redirect asap so any errors are logged.
            redirect_stream(sys.stdin,  self.stdin)
            redirect_stream(sys.stdout, self.stdout)
            redirect_stream(sys.stderr, self.stderr)
            
            # detach if requested, or if unspecified and not already detached
            # (i.e. parent is init)
            if self.detach_process or \
              (self.detach_process is None and os.getppid() != 1):
                detach_process_context()
            
            # pidfile must be acquired after detach, so PID is correct.
            if self.pidfile_path is not None:
                if not self._acquire_pidfile():
                    raise PidfileLocked("could not acquire pidfile")
                own_pidfile = True
            
            if self.prevent_core:
                prevent_core_dump()
            
            os.setgid(self.gid)
            os.setuid(self.uid)
            
            os.umask(self.umask)
            os.chdir(self.wdir)
            
            # self._close_files()
            atexit.register(self.stop)
            signal.signal(signal.SIGTERM, self._signal_callback)
            
        except Exception, e:
            if self.pidfile_path is not None and own_pidfile:
                self._release_pidfile()
            raise DaemonError("Error while entering daemon context [%s]" % e)
        
        self._is_started = True
        return True
    
    
    def stop(self):
        """Release ownership of the daemon pidfile if we have it. Will be 
        called before exit."""
        if not self._is_started:
            return
        
        if self.pidfile_path is not None:
            self._release_pidfile()
        
        self._is_started = False
    
    
    def __enter__(self):
        success = self.start()
        if success:
            return self
        else:
            return None
    
    
    def __exit__(self, exc_type, exc_value, traceback):
        self.stop()
    
    
    def terminate(self):
        if self.on_terminate is not None:
            ret = self.on_terminate()
        if not sys.stdout.closed:
            sys.stdout.flush()
        if not sys.stderr.closed:
            sys.stderr.flush() 
        
        self.stop()
        
        if ret is None:
            ret_i = 0
        else:
            try:
                ret_i = int(ret)
            except:
                ret_i = 1
        
        sys.exit(ret_i)
    
    
    def _signal_callback(self, sig, stack):
        if sig == signal.SIGTERM:
            self.terminate()
    
    
    def _close_files(self):
        """Close all open files, except those file descriptors which are
        being used by stdin, stdout, or stderr, and those on the user-supplied 
        preserve list."""
        
        preserve = [] if self.files_preserve is None else self.files_preserve
        preserve.extend((self.stdout, self.stderr, self.stdin))
        preserve_fds = set()
        
        for stream in preserve:
            if hasattr(stream, 'fileno') and not stream.closed:
                # a file object
                preserve_fds.add(stream.fileno())
            elif isinstance(stream, int):
                # a file descriptor
                preserve_fds.add(stream)
        
        for openFd in range(get_max_fd()):
            if openFd in preserve_fds:
               continue
            try:
                os.close(openFd)
            except OSError, e:
                if e.errno == errno.EBADF:
                    # File descriptor was not open
                    pass
                else:
                    raise DaemonError(
                            "Failed to close file descriptor %s (%s)" % \
                            (openFd, e))
    
    
    def _release_pidfile(self):
        """Release the pidfile, raising an exception if we don't own the 
        lock, or otherwise cannot release it."""
        pid = os.getpid()
        path = self.pidfile_path
        if os.path.isfile(path):
            found_pid = read_pidfile(path)
            if found_pid == pid:
                # we own this lock
                try:
                    os.remove(path)
                except OSError, e:
                    if e == errno.ENOENT:
                        pass
                    else:
                        raise
            elif found_pid is None:
                raise DaemonError("Unable to read pidfile for release")
            else:
                raise DaemonError(
                    "Cannot release pidfile; belongs to another process")
        else:
           raise DaemonError("Cannot release pidfile; pidfile does not exist")
    
    
    def _acquire_pidfile(self):
        """Create a pidfile and record our PID if no pidfile exists, returning
        True. If a pidfile exists and the owner process is still running, do 
        nothing and return False. If the previous owner has died, then break the 
        lock and create a new pidfile in our own name. If the pidfile cannot be 
        read for any other reason, raise a `DaemonError`."""
        
        pid = os.getpid()
        path = self.pidfile_path
        create = True
        if os.path.isfile(path):
            create = False
            read_pid = read_pidfile(path)
            if read_pid == pid:
                return True
            elif read_pid is None:
                # permissions error, most likely
                raise DaemonError("Could not read pidfile %s" % path)
            else:
                # testing /proc/ is preferable to os.kill (a method used by
                # some other daemon libraries) because os.kill may fail if the
                # current process does not have sufficient permissions.
                if os.path.exists("/proc/%d" % read_pid):
                    # another running process has the pidfile
                    return False
                else:
                    # other process has died. break the lock.
                    os.unlink(path)
                    create = True
        if create:
            # pidfile doesn't exist. Let's make one
            pidfd = open_writeable_file(path, self.uid, self.gid, 
                                         othersCanRead=True, exclusive=True)
            with os.fdopen(pidfd, 'a') as pidfile:
                pidfile.write('%d\n' % pid)
            return True
        
        return False

##########################
# Daemon Controller      #
##########################


class DaemonController:
    """A class for starting/stopping a daemon process. Daemon behavior is 
    defined by subclassing DaemonController and overriding the self.run() 
    method."""
    
    def __init__(self, 
            daemon_name=None,
            pid_timeout=5,
            **daemon_settings):
        """Create a new daemon controller, with the arguments to the 
        Daemon object constructor given as keyword arguments.
        
        'pid_timeout' is the time in seconds to wait for existing daemons to 
        clean up and release their lockfile before giving up. It is also the 
        number of seconds to wait for a lockfile to appear (and remain in place) 
        when the daemon is spawned, before giving up and declaring the start a 
        failure.
        
        If 'pidfile_path' is not given, a pidfile will be created in a directory 
        under /var/run/ using the daemon name.
        
        If 'stdout' or 'stderr' are not given, then a logfile will be created in 
        /var/log/ using the daemon name. If they strings, 'stdout' and 'stderr' 
        will be interpreted as paths, and the daemon will attempt to open 
        writeable files at those locations, creating intermediate directories if 
        necessary. If stdout or stderr are to be closed, pass None.
        
        If 'on_terminate' is not provided, then self.on_terminate will be run 
        when the daemon recieves a SIGTERM. If no termination hook is to be run, 
        pass None."""
        
        if daemon_name is None:
            self.daemon_name = os.path.basename(sys.argv[0])
        else:
            self.daemon_name = daemon_name
        
        self._daemon_settings = daemon_settings
        self._timeout = pid_timeout
        
        if 'pidfile' not in daemon_settings or \
                daemon_settings['pidfile'] is None:
            # use default pidfile location
            pid_loc = '/var/run/%s/%s.pid' % (self.daemon_name, self.daemon_name)
            self._daemon_settings['pidfile_path'] = pid_loc
        if 'on_terminate' not in self._daemon_settings:
            # use class termination method
            self._daemon_settings['on_terminate'] = self.on_terminate
        self.daemon_obj = Daemon(**self._daemon_settings)
    
    
    def on_terminate(self):
        """Action to perform on daemon termination. Override this
        in your subclass."""
        print "%s terminated" % self.daemon_name
    
    
    def start(self, forkAndReturn=True):
        """Start the daemon process. If `forkAndReturn` is True, then this 
        process will fork off the daemon and wait for it to acquire the 
        lockfile, then return success or failure. Otherwise, this method will 
        enter a daemon state and never finish (unless self.run() returns).
        """
        settings = self._daemon_settings
        dmn = self.daemon_obj 
        if self.is_pidfile_locked(self._timeout):
            # we check ahead of time, because if we rely on the Daemon object to
            # do the check, we'll have already detached the process and
            # redirected stdout/stderr, and the user will get no notifcation
            # of the problem. (Technically this is a race condition, but the 
            # only consequence is that the user doesn't get a terminal complaint).
            raise PidfileLocked("%s locked" % dmn.pidfile_path)
        else:
            # create any necessary logfiles
            logfd = None 
            for stream in ('stdout', 'stderr'): 
                if stream not in settings:
                    # user did not provide a stream. make a log for them.
                    if logfd is None:
                        logloc = '/var/log/%s.log' % self.daemon_name
                        logfd = open_writeable_file(logloc, dmn.uid, dmn.gid)
                    setattr(dmn, stream, logfd)
                elif isinstance(settings[stream], str) or \
                     isinstance(settings[stream], unicode):
                    # user provided a filename to be opened
                    newfd = open_writeable_file(settings[stream], dmn.uid, dmn.gid)
                    setattr(dmn, stream, newfd)
                else:
                    # user passed None or a file descriptor; the daemon
                    # will handle it the right way.
                    pass
            
            print "starting %s" % self.daemon_name
            
            if (not forkAndReturn) or (os.fork() == 0):
                # we're the child / daemon process. Become a daemon
                with dmn as dmn_context:
                    self.run()
                if forkAndReturn:
                    raise SystemExit()
            else:
                # we're the parent.
                # wait for the pidfile to appear
                ppath = self.daemon_obj.pidfile_path
                timeout_time = time.time() + self._timeout
                flag_started = False
                flag_read_fail = None
                while True:
                    if os.path.isfile(ppath):
                        pid = read_pidfile(ppath)
                        if pid is not None and os.path.exists('/proc/%d' % pid):
                            flag_started = True
                            flag_read_fail = False
                        else:
                            flag_read_fail = True
                    else:
                        flag_read_fail = True
                    if flag_read_fail and flag_started:
                        # pidfile was there, but disappeared. deamon must have 
                        # died.
                        msg = "process start failed; lock released within timeout"
                        raise DaemonStartFailure(msg)
                    
                    if time.time() >= timeout_time:
                        break
                    else:
                        time.sleep(0.125)
                if flag_started:
                    return True
                else:
                    raise DaemonStartFailure("lock not established within timeout")
    
    
    def stop(self):
        if self.is_pidfile_locked(self._timeout):
            pid = read_pidfile(self.daemon_obj.pidfile_path)
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError, e:
                if e.errno == errno.EPERM:
                    emsg = "don't have permissions to kill running daemon " + \
                           "(pid=%s)" % pid
                    raise DaemonStopError(emsg)
                else:
                    raise
        else:
            raise DaemonStopError("daemon not running")
    
    
    def restart(self):
        self.stop()
        self.start() 
    
    
    def do_command(self, *cmd_args):
        """Do the command named by the string passed via `cmd_args`,
        and return the success as a bool, and a string message describing the
        outcome. Possible commands: start, stop, restart.""" 
        
        progname = os.path.basename(sys.argv[0])
        usage_ok = False
        name = self.daemon_name
        usage = "usage: %s [start|stop|restart]" % progname
        
        if len(cmd_args) != 1:
            return False, usage
        else:
            cmd = cmd_args[0]
        
        # if 'restart': stop, then start. 
        if cmd == "stop" or cmd == "restart":
            usage_ok = True
            try:
                self.stop()
                if cmd != "restart":
                    return True, "%s stopped." % name
            except DaemonStopError, e:
                return False, "%s not stopped; %s" % (name, str(e))
        
        if cmd == "start" or cmd == "restart":
            usage_ok = True
            try:
                # fork not necessary if init is already our parent
                dofork = os.getppid() != 1
                self.start(dofork)
                return True, "%s started" % name
            except PidfileLocked, e:
                return False, "%s not started; daemon already running" % name
            except DaemonStartFailure, e:
                return False, "%s not started; %s" % (name, str(e))
            except OSError, e:
                if e.errno == errno.EACCES:
                    # probably no access to log
                    return False, "%s: %s" % (e.strerror, e.filename)
                else:
                    raise
        
        if not usage_ok:
            return False, usage
    
    
    def run(self):
        raise NotImplementedError("implement in subclass")
    
    
    def is_pidfile_locked(self, timeout_seconds=5):
        pid_loc = self.daemon_obj.pidfile_path
        t = time.time()
        t_exit = t + timeout_seconds
        
        while True:
            if not os.path.exists(pid_loc):
                return False
            else:
                pid = read_pidfile(pid_loc)
            
            if pid is None or os.path.exists('/proc/%d' % pid):
                # pidfile is owned by a running process, or isn't readable. 
                # wait awhile for it to die/release the pidfile
                time.sleep(0.125)
            else:
                # pidfile is abandoned
                os.remove(pid_loc)
                return False
            
            if timeout_seconds <= 0 or time.time() > t_exit:
                break
        
        return True


##########################
# Helper functions       #
##########################


def get_max_fd(defaultmax=2048):
    """Get the number of the largest possible file descriptor."""
    limits = resource.getrlimit(resource.RLIMIT_NOFILE)
    result = limits[1]
    if result == resource.RLIM_INFINITY:
        result = defaultmax
    return result


def read_pidfile(path):
    """Get the PID from the named pidfile. Return None if the file does not 
    exist or does not contain an int."""
    try:
        with open(path, 'r') as f:
            txt = f.readline().strip()
            pid = int(txt)
            return pid
    except Exception, e:
        return None


def open_writeable_file(path, uid, gid, othersCanRead=False, exclusive=False):
    """Open a readable file and create its parent directories if necessary. If 
    the file did not previously exist, its ownership will be given to `uid` and 
    `gid`. If the file exists and `exclusive` is set, the open will fail.""" 
    
    dir, file = os.path.split(path)
    if not os.path.exists(dir):
        os.makedirs(dir)
        os.chmod(dir, 0o751) # rwxr-x--x
        os.chown(dir, uid, gid)
    
    existed = os.path.isfile(path)
    flags   = os.O_CREAT|os.O_WRONLY|os.O_APPEND|(os.O_EXCL if exclusive else 0)
    mode    = 0o644 if othersCanRead else 0o640   # rw-r--?--
    fd      = os.open(path, flags, mode)
    
    if exclusive or not existed:
        os.chmod(path, mode)
        os.chown(path, uid, gid)
    
    return fd


def redirect_stream(system_stream, target_stream):
    """Redirect a system stream to a specified file or file descriptor."""
    if target_stream is None:
        target_fd = os.open(os.devnull, os.O_RDWR)
    elif isinstance(target_stream, int):
        target_fd = target_stream
    else:
        target_fd = target_stream.fileno()
    os.dup2(target_fd, system_stream.fileno())


def detach_process_context():
    """ Detach the process context from parent and session.
    Detach from the parent process and session group, allowing the parent to 
    exit while this process continues running."""
    
    def fork_then_exit_parent(error_message):
        """ Fork a child process, then exit the parent process.
        
        If the fork fails, raise a ``DaemonError`` with ``error_message``."""
        try:
            pid = os.fork()
            if pid > 0:
                os._exit(0)
        except OSError, exc:
            exc_errno = exc.errno
            exc_strerror = exc.strerror
            error = DaemonError(
                u"%(error_message)s: [%(exc_errno)d] %(exc_strerror)s" % vars())
            raise error

    fork_then_exit_parent(error_message=u"Failed first fork")
    os.setsid()
    fork_then_exit_parent(error_message=u"Failed second fork")


def prevent_core_dump():
    """Prevent this process from generating a core dump.
    
    Sets the soft and hard limits for core dump size to zero. On Unix, this 
    prevents the process from creating core dump altogether. 
    """
    core_resource = resource.RLIMIT_CORE
    
    try:
        # Ensure the resource limit exists on this platform, by requesting
        # its current value
        core_limit_prev = resource.getrlimit(core_resource)
    except ValueError, exc:
        error = DaemonError(
            u"System does not support RLIMIT_CORE resource limit (%(exc)s)"
            % vars())
        raise error
    
    # Set hard and soft limits to zero, i.e. no core dump at all
    core_limit = (0, 0)
    resource.setrlimit(core_resource, core_limit)
