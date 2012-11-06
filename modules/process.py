# coding=utf-8

import socket
import subprocess
import time
import os
import errno
from singleton import Singleton
import tempfile
import shutil
import stat
import json
import psutil


HOME = os.environ.get('HOME')
HOSTNAME = os.environ.get('HOSTNAME', socket.gethostname())


class PortPool(Singleton):

    def __init__(self, min_port=1025, max_port=2000, port_sequence=None):
        """
        Args:
            min_port - min port number  (ignoring if 'port_sequence' is not None)
            max_port - max port number  (ignoring if 'port_sequence' is not None)
            port_sequence - iterate sequence which contains numbers of ports
        """
        if not hasattr(self, '_PortPool__ports'):  # magic singleton checker
            self.__init_range(min_port, max_port, port_sequence)
            self.refresh()

    def __init_range(self, min_port=1025, max_port=2000, port_sequence=None):
        if port_sequence:
            self.__ports = set(port_sequence)
        else:
            self.__ports = set(xrange(min_port, max_port))
        self.__closed = set()

    def __check_port(self, port):
        """check port status
        return True if port is free, False else
        """
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind((HOSTNAME, port))
            return True
        except socket.error:
            return False
        finally:
            s.close()

    def release_port(self, port):
        """release port"""
        if port in self.__closed:
            self.__closed.remove(port)
        self.__ports.add(port)

    def port(self, check=False):
        """return next opened port
        Args:
          check - check is port realy free
        """
        if len(self.__ports) == 0:  # refresh ports if sequence is empty
            self.refresh()

        try:
            port = self.__ports.pop()
            if check:
                while not self.__check_port(port):
                    self.release_port(port)
                    port = self.__ports.pop()
        except IndexError:
            raise IndexError("Could not find a free port")
        self.__closed.add(port)
        return port

    def refresh(self, only_closed=False):
        """refresh ports status
        Args:
          only_closed - check status only for closed ports
        """
        if only_closed:
            opened = filter(self.__check_port, self.__closed)
            self.__closed = self.__closed.difference(opened)
            self.__ports = self.__ports.union(opened)
        else:
            ports = self.__closed.union(self.__ports)
            self.__ports = set(filter(self.__check_port, ports))
            self.__closed = ports.difference(self.__ports)

    def change_range(self, min_port=1025, max_port=2000, port_sequence=None):
        """change Pool port range"""
        self.__init_range(min_port, max_port, port_sequence)
        self.refresh()


def wait_for(port_num, timeout):
    """waits while process starts.
    Args:
        port_num    - port number
        timeout     - specify how long, in seconds, a command can take before times out.
    return True if process started, return False if not
    """
    t_start = time.time()
    sleeps = 1
    while True:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            try:
                s.connect((HOSTNAME, port_num))
                return True
            except (IOError, socket.error):
                if time.time() - t_start < timeout:
                    time.sleep(sleeps)
                else:
                    return False
        finally:
            s.close()
    return False


def mprocess(name, config_path, port, timeout=180):
    """start 'name' process with params from config_path.
    Args:
        name - process name or path
        config_path - path to file where should be stored configuration
        params - specific process configuration
        timeout - specify how long, in seconds, a command can take before times out.
                  if timeout <=0 - doesn't wait for complete start process
    return tuple (pid, host) if process started, return (None, None) if not
    """
    port = port or PortPool().port(check=True)
    cmd = [name, "--config", config_path]
    host = HOSTNAME + ':' + str(port)
    print repr(cmd)
    try:
        proc = subprocess.Popen(cmd,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT)
    except (OSError, TypeError):
        raise OSError
    if timeout > 0 and wait_for(port, timeout):
        return (proc.pid, host)
    elif timeout > 0:
        proc.terminate()
        time.sleep(3)  # wait while process stoped
        raise OSError(errno.ETIMEDOUT, "could not connect to process during {timeout} seconds".format(timeout=timeout))
    return (proc.pid, host)


def kill_mprocess(pid, timeout=10):
    """kill process
    Args:
        pid - process pid
    """
    if pid and proc_alive(pid):
        psutil.Process(pid).terminate()
        t_start = time.time()
        while proc_alive(pid) and time.time() - t_start < timeout:
            time.sleep(1.5)
    return not proc_alive(pid)


def cleanup_mprocess(config_path, cfg):
    """remove all process's stuff
    Args:
       config_path - process's options file
       cfg - process's config
    """
    for key in ('keyFile', 'logPath', 'dbpath'):
        remove_path(cfg.get(key, None))
    remove_path(config_path)


def remove_path(path):
    """remove path from file system
    If path is None - do nothing"""

    onerror = lambda func, filepath, exc_info: (time.sleep(2), os.chmod(filepath, stat.S_IWUSR), func(filepath))
    if path is None or not os.path.exists(path):
        return
    if os.path.isdir(path):
        shutil.rmtree(path, onerror=onerror)
    if os.path.isfile(path):
        try:
            shutil.os.remove(path)
        except OSError:
            time.sleep(2)
            onerror(shutil.os.remove, path, None)
            # os.chmod(path,stat.S_IWUSR)
            # shutil.os.remove(path)


def write_config(params, auth_key=None, log=False):
    """write mongo's config file
    Args:
       params - options wich file contains
       auth_key - authorization key ()
       log - use logPath option with generation path if True
    Return config_path, cfg
    where config_path - path to mongo's options file
          cfg - all options as dictionary
    """
    cfg = {'dbpath': tempfile.mkdtemp(prefix="mongo-")}
    if auth_key:
        key_file = os.path.join(os.path.join(cfg['dbpath'], 'key'))
        open(key_file, 'w').write(auth_key)
        os.chmod(key_file, stat.S_IRUSR)
        cfg['keyFile'] = key_file
    cfg.update(params)
    if 'port' not in cfg:
        cfg['port'] = PortPool().port(check=True)
    config_path = tempfile.mktemp(prefix="mongo-")

    # fix boolean value
    for key, value in cfg.items():
        if isinstance(value, bool):
            cfg[key] = json.dumps(value)

    with open(config_path, 'w') as fd:
        data = reduce(lambda s, item: "{s}\n{key}={value}".format(s=s, key=item[0], value=item[1]), cfg.items(), '')
        fd.write(data)

    return config_path, cfg


def proc_alive(pid):
    """check if process with pid is alive
    Return True or False"""
    try:
        p = psutil.Process(pid)
    except (psutil.NoSuchProcess, TypeError):
        return False
    return p.status in (psutil.STATUS_RUNNING, psutil.STATUS_SLEEPING, psutil.STATUS_LOCKED)
