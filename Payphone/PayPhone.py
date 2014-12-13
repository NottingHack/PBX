#!/usr/bin/env python
# -*- coding: utf-8 -*-

# Nottingham Hackspace payphone client
#
# Auth: Matt Lloyd
#
# The MIT License (MIT)
#
# Copyright (c) 2014 Matt Lloyd
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
#

import sys
from time import time, sleep, gmtime, strftime
import os
import signal
import errno
import Queue
import argparse
import ConfigParser
import serial
import threading
import socket
import select
import logging
import re
if sys.platform == 'win32':
    pass
else:
    from daemon import DaemonContext, pidlockfile
    import lockfile
import pjsua as pj

"""
    Big TODO list
    
    queue's amd flags needed by tSerial
    
    SIP
        callback classes
        
    
    
"""

class PayPhoneAccountCallback(pj.AccountCallback):
    def __init__(self, phone):
        self._phone = phone
        pj.AccountCallback.__init__(self, self._phone.getAccount())

    # Notification on incoming call
    def on_incoming_call(self, call):
        global current_call 
        if current_call:
            call.answer(486, "Busy")
            return
            
        print "Incoming call from ", call.info().remote_uri
        print "Press 'a' to answer"

        current_call = call

        call_cb = MyCallCallback(current_call)
        current_call.set_callback(call_cb)

        current_call.answer(180)

class PayPhoneCallCallback(pj.CallCallback):
    def __init__(self, phone):
        self._phone = phone
        pj.CallCallback.__init__(self, self._phone.getCall())

    # Notification when call state has changed
    def on_state(self):
        global current_call
        print "Call with", self.call.info().remote_uri,
        print "is", self.call.info().state_text,
        print "last code =", self.call.info().last_code, 
        print "(" + self.call.info().last_reason + ")"
        
        if self.call.info().state == pj.CallState.DISCONNECTED:
            current_call = None
            print 'Current call is', current_call

    # Notification when call's media state has changed.
    def on_media_state(self):
        if self.call.info().media_state == pj.MediaState.ACTIVE:
            # Connect the call to sound device
            call_slot = self.call.info().conf_slot
            pj.Lib.instance().conf_connect(call_slot, 0)
            pj.Lib.instance().conf_connect(0, call_slot)
            print "Media is now active"
        else:
            print "Media is inactive"

class PayPhone():
    _configFile = "./PayPhone.cfg"
    _pidFile = None
    _pidFilePath = "./PayPhone.pid"
    _pidFileTimeout = 5
    _background = False
    
    _SerialFailCount = 0
    _SerialFailCountLimit = 3
    _serialTimeout = 1     # serial port time out setting
    
    _version = 0.01
    
    _dailDigits = "1234567890*#"
    _ringStart = 'R'
    _ringStop = 'r'
    _onHook = 'H'
    _offHook = 'h'
    _followKey = 'F'
    
    _state = ""
    RUNNING = "RUNNING"
    ERROR = "ERROR"

    _ActionHelp = """
start = Starts as a background daemon/service
stop = Stops a daemon/service if running
restart = Restarts the daemon/service
status = Check if a PayPhone serveice is running
If none of the above are given and no daemon/service
is running then run in the current terminal
"""

    def __init__(self, logger=None):
        """Instantiation
            
        Setup basic transport, Queue's, Threads etc
        """
        if hasattr(sys,'frozen'): # only when running in py2exe this exists
            self._path = sys.prefix
        else: # otherwise this is a regular python script
            self._path = os.path.dirname(os.path.realpath(__file__))
        
        self._signalMap = {
                           signal.SIGTERM: self._cleanUp,
                           signal.SIGHUP: self.terminate,
                           signal.SIGUSR1: self._reloadProgramConfig,
                          }
        
        self.tMainStop = threading.Event()
#        self.qServer = Queue.Queue()

        # setup initial Logging
        logging.getLogger().setLevel(logging.NOTSET)
        self.logger = logging.getLogger('PayPhone')
        self._ch = logging.StreamHandler()
        self._ch.setLevel(logging.WARN)    # this should be WARN by default
        self._formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        self._ch.setFormatter(self._formatter)
        self.logger.addHandler(self._ch)
     
    def __del__(self):
        """Destructor
            
        Close any open threads, and transports
        """
        # TODO: shut down anything we missed
        pass
    
    def start(self):
        """Start by check in the args and sorting out run context foreground/service/daemon
           This is the main entry point for most start conditions
        """
        self.logger.info("Start")
        
        self._checkArgs()           # pull in the command line options
        
        if not self._checkDaemon(): # base on the command line argument stop|stop|restart as a daemon
            self.logger.debug("Exiting")
            return
        self.run()
        
        
        if not self._background:
            if not sys.platform == 'win32':
                try:
                    self.logger.info("Removing Lock file")
                    self._pidFile.release()
                except:
                    pass

    def _checkArgs(self):
        """Parse the command line options
        """
        parser = argparse.ArgumentParser(description='PayPhone',
                                         formatter_class=argparse.RawTextHelpFormatter)
        parser.add_argument('action', nargs = '?',
                            choices=('start', 'stop', 'restart', 'status'),
                            help =self._ActionHelp)
        #parser.add_argument('-u', '--noupdate',
        #                    help='disable checking for update',
        #                    action='store_false')
        parser.add_argument('-d', '--debug',
                            help='Enable debug output to console, overrides PayPhone.cfg setting',
                            action='store_true')
        parser.add_argument('-l', '--log',
                            help='Override the console debug logging level, DEBUG, INFO, WARNING, ERROR, CRITICAL'
                            )
                            
        self.args = parser.parse_args()
    
    def _checkDaemon(self):
        """ Based on the current os and command line arguments handle running as
            a background daemon or service
            returns 
                True if we should continue running
                False if we are done and should exit
        """
        if sys.platform == 'win32':
            # need a way to check if we are already running on win32
            self._background = False
            return True
        else:
            # must be *nix based, right?
            
            #setup pidfile checking
            self._pidFile = self._makePidlockfile(os.path.join(self._path, self._pidFilePath),
                                                  self._pidFileTimeout)
            
            if self.args.action == None:
                # run in foreground unless a daemon is all ready running
                
                # check for valid or stale pid file, if there is already a
                # copy running somewhere we don't want to start again
                if self._isPidfileStale(self._pidFile):
                    self._pidFile.break_lock()
                    self.logger.debug("Removed Stale Lock")
                
                # create and lock a new pid file
                self.logger.info("Acquiring Lock file")
                try:
                    self._pidFile.acquire()
                except lockfile.LockTimeout:
                    self.logger.critical("Already running, exiting")
                    return False
                else:
                    # register our own signal handlers
                    for (signal_number, handler) in self._signalMap.items():
                        signal.signal(signal_number, handler)
                    
                    self._background = False
                    return True
                        
            elif self.args.action == 'start':
                # start as a daemon
                return self._dstart()
            elif self.args.action == 'stop':
                self._dstop()
                return False
            elif self.args.action == 'restart':
                self.logger.debug("Stoping old daemon")
                self._dstop()
                self.logger.debug("Starting new daemon")
                return self._dstart()
            elif self.args.action == 'status':
                self._dstatus()
                return False
                    
    def _dstart(self):
        """Kick off a daemon process
        """

        self._daemonContext = DaemonContext()
        self._daemonContext.stdin = open('/dev/null', 'r')
        self._daemonContext.stdout = open('/dev/null', 'w+')
        self._daemonContext.stderr = open('/dev/null', 'w+', buffering=0)
        self._daemonContext.pidfile = self._pidFile
        self._daemonContext.working_directory = self._path
        
        self._daemonContext.signal_map = self._signalMap
        if self._isPidfileStale(self._pidFile):
            self._pidFile.break_lock()
            self.logger.debug("Removed Stale Lock")

        try:
            self._daemonContext.open()
        except pidlockfile.AlreadyLocked:
            self.logger.warn("Already running, exiting")
            return False
        
        self._background = True
        return True

    def _dstop(self):
        """ Stop a running process base on PID file
        """
        if not self._pidFile.is_locked():
            self.logger.debug("Nothing to stop")
            return False
        
        if self._isPidfileStale(self._pidFile):
            self._pidFile.break_lock()
            self.logger.debug("Removed Stale Lock")
            return True
        else:
            pid = self._pidFile.read_pid()
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError, exc:
                self.logger.warn("Failed to terminate {}: {}: Try sudo".format(pid, exc))
                return False
            else:
                # we stopped something :)
                self.logger.debug("Stopped pid {}".format(pid))
                return True

    def _dstatus(self):
        """ Test the PID file to see if we are running some where
            Return 
                pid if running
                None if not
            """
        pid = None
        if self._isPidfileStale(self._pidFile):
            self._pidFile.break_lock()
            self.logger.debug("Removed Stale Lock")
        
        pid = self._pidFile.read_pid()
        if pid is not None:
            print("PayPhone.py is running (PID {})".format(pid))
        else:
            print("PayPhone.py is not running")

        return pid

# Setup stuff
################################################################################
# Run Stuff

    def run(self):
        """Run Everything
           At this point the Args have been checked and everything is setup if
           we are running in the foreground or as a daemon/service
        """
        
        try:
            self._readConfig()          # read in the config file
            self._initLogging()         # setup the logging options
#            self._initLCRThread()       # start the LLAPConfigRequest thread
#            self._initUDPSendThread()   # start the UDP sender
#            self.tMainStop.wait(1)
            self._initSerialThread()    # start the serial port thread
#            self.tMainStop.wait(1)
#            self._initUDPListenThread() # start the UDP listener

            self._state = self.RUNNING
            
            # main thread looks after the server status for us
            while not self.tMainStop.is_set():
                # check threads are running
#                if not self.tLCR.is_alive():
#                    self.logger.error("LCR thread stopped")
#                    self._state = self.ERROR
#                    self.tMainStop.wait(1)
#                    self._startLCR()
#                    self.tMainStop.wait(1)
#                    if self.tLCR.is_alive():
#                        self._state = self.RUNNING
#            
#                if not self.tUDPSend.is_alive():
#                    self.logger.error("UDPSend thread stopped")
#                    self._state = self.ERROR
#                    self.tMainStop.wait(1)
#                    self._startUDPSend()
#                    self.tMainStop.wait(1)
#                    if self.tUDPSend.is_alive():
#                        self._state = self.RUNNING
#                            
                if not self.tSerial.is_alive():
                    self.logger.error("Serial thread stopped, wait 1 before trying to re-establish ")
                    self._state = self.ERROR
                    self.tMainStop.wait(1)
                    self._startSerail()
                    self.tMainStop.wait(1)
                    if self.tSerial.is_alive():
                        self._state = self.RUNNING
                    else:
                        self._SerialFailCount += 1
                        if self._SerialFailCount > self._SerialFailCountLimit:
                            self.logger.error("Serial thread failed to recover after {} retries, Exiting".format(self._SerialFailCountLimit))
                            self.die()
#
#                if not self.tUDPListen.is_alive():
#                    self.logger.error("UDPListen thread stopped")
#                    self._state = self.ERROR
#                    self.tMainStop.wait(1)
#                    self._startUDPListen()
#                    self.tMainStop.wait(1)
#                    if self.tUDPSend.is_alive():
#                        self._state = self.RUNNING
#                
#                # process any "Server" messages
#                if not self.qServer.empty():
#                    self.logger.debug("Processing Server JSON")
#                    try:
#                        self.qServer.get_nowait()
#                    except Queue.Empty():
#                        pass
#                    else:
#                        self.qUDPSend.put(json.dumps({"type": "Server", "state": self._state}))
                
                self.tMainStop.wait(0.5)

        except KeyboardInterrupt:
            self.logger.info("Keyboard Interrupt - Exiting")
            self._cleanUp()
            sys.exit()
        self.logger.debug("Exiting")

    def _readConfig(self):
        """Read the server config file from disk
        """
        self.logger.info("Reading config files")
        self.config = ConfigParser.SafeConfigParser()
        
        # load defaults
        try:
            self.config.readfp(open(self._configFile))
        except:
            self.logger.error("Could Not Load Settings File")

        if not self.config.sections():
            self.logger.critical("No Config Loaded, Exiting")
            self.die()

    def _reloadProgramConfig(self):
        """ Reload the config file from disk
        """
        # TODO: do we want to be able reload config on SIGUSR1?
        pass

    def _initLogging(self):
        """ now we have the config file loaded and the command line args setup
            setup the loggers
        """
        self.logger.info("Setting up Loggers. Console output may stop here")

        # disable logging if no options are enabled
        if (self.args.debug == False and
            self.config.getboolean('Debug', 'console_debug') == False and
            self.config.getboolean('Debug', 'file_debug') == False):
            self.logger.debug("Disabling loggers")
            # disable debug output
            self.logger.setLevel(100)
            return
        # set console level
        if (self.args.debug or self.config.getboolean('Debug', 'console_debug')):
            self.logger.debug("Setting Console debug level")
            if (self.args.log):
                logLevel = self.args.log
            else:
                logLevel = self.config.get('Debug', 'console_level')
        
            numeric_level = getattr(logging, logLevel.upper(), None)
            if not isinstance(numeric_level, int):
                raise ValueError('Invalid console log level: %s' % loglevel)
            self._ch.setLevel(numeric_level)
        else:
            self._ch.setLevel(100)
            
        # add file logging if enabled
        # TODO: look at rotating log files
        # http://docs.python.org/2/library/logging.handlers.html#logging.handlers.TimedRotatingFileHandler
        if (self.config.getboolean('Debug', 'file_debug')):
            self.logger.debug("Setting file debugger")
            self._fh = logging.FileHandler(self.config.get('Debug', 'log_file'))
            self._fh.setFormatter(self._formatter)
            logLevel = self.config.get('Debug', 'file_level')
            numeric_level = getattr(logging, logLevel.upper(), None)
            if not isinstance(numeric_level, int):
                raise ValueError('Invalid console log level: %s' % loglevel)
            self._fh.setLevel(numeric_level)
            self.logger.addHandler(self._fh)
            self.logger.info("File Logging started")
                
    def _initSerialThread(self):
        """ Setup the serial port and start the thread
        """
        self.logger.info("Serial port init")

        # serial port base on config file, thread handles opening and closing
        self._serial = serial.Serial()
        self._serial.port = self.config.get('Serial', 'port')
        self._serial.baud = self.config.get('Serial', 'baudrate')
        self._serial.timeout = self._serialTimeout
        
        # setup queue
        self.qSerialOut = Queue.Queue()
        self.qSerialToQuery = Queue.Queue()
        
        # setup thread
        self.tSerialStop = threading.Event()
        
        self._startSerail()
    
    def _startSerail(self):
        self.tSerial = threading.Thread(name='tSerial', target=self._SerialThread)
        self.tSerial.daemon = False
    
        try:
            self.tSerial.start()
        except:
            self.logger.exception("Failed to Start the Serial thread")

    def _SerialThread(self):
        """ Serial Thread
        """
        self.logger.info("tSerial: Serial thread started")
        self._SerialToQueryState = 0
        self._SerialToQuery = []
        self.tSerialStop.wait(1)
        try:
            while (not self.tSerialStop.is_set()):
                # open the port
                try:
                    self._serial.open()
                    self.logger.info("tSerial: Opened the serial port")
                except serial.SerialException:
                    self.logger.exception("tSerial: Failed to open port {} Exiting".format(self._serial.port))
                    self._serial.close()
                    self.die()
                
                self.tSerialStop.wait(0.1)
                
                # we clear out any stale serial messages that might be in the buffer
                self._serial.flushInput()
                
                # main serial processing loop
                while self._serial.isOpen() and not self.tSerialStop.is_set():
                    # extrem debug message
                    # self.logger.debug("tSerial: check serial port")
                    if self._serial.inWaiting():
                        self._SerialReadIncoming()
                    
                    # do we have anything to send
                    if not self.qSerialOut.empty():
                        self.logger.debug("tSerial: got something to send")
                        try:
                            msg = self.qSerialOut.get_nowait()
                            self._serial.write(msg)
                        except Queue.Empty:
                            self.logger.debug("tSerial: failed to get item from queue")
                        except Serial.SerialException, e:
                            self.logger.warn("tSerial: failed to write to the serial port {}: {}".format(self._serial.port, e))
                        else:
                             self.logger.debug("tSerial: TX:{}".format(msg))
                             self.qSerialOut.task_done()
                
                    # sleep for a little
                    if self._SerialToQueryState or self._serial.inWaiting():
                        self.tSerialStop.wait(0.01)
                    else:
                        self.tSerialStop.wait(0.1)
                
                # port closed for some reason (or tSerialStop), if tSerialStop is not set we will try reopening
        except IOError:
            self.logger.exception("tSerail: IOError on serial port")
        
        # close the port
        self.logger.info("tSerial: Closing serial port")
        self._serial.close()
        
        self.logger.info("tSerial: Thread stoping")
        return
    
    def _SerialReadIncoming(self):
        char = self._serial.read()  # should not time out but we should check anyway
        self.logger.debug("tSerial: RX:{}".format(char))
    
        if char in self._dailDigits:
            # posible handle mutiple reads before putting on the queue? (check inWaiting)
            try:
                self.qDial.put_nowait(char);
            except Queue.Full:
                self.logger.warn("tSerial: Failed to put {} on qDial at its Full".format(char))
            return
        if char == self._onHook:
            # set on Hook Flag
            pass
        if char == self._offHook:
            # set off Hook Flag
            pass
        if char == self._followKey:
            #set follow on call flag
            pass
    
# Run Stuff
################################################################################
# Clean up stuff

    # TODO: catch errors and add logging
    def _makePidlockfile(self, path, acquire_timeout):
        """ Make a PIDLockFile instance with the given filesystem path. """
        if not isinstance(path, basestring):
            error = ValueError("Not a filesystem path: %(path)r" % vars())
            raise error
        if not os.path.isabs(path):
            error = ValueError("Not an absolute path: %(path)r" % vars())
            raise error
        lockfile = pidlockfile.TimeoutPIDLockFile(path, acquire_timeout)

        return lockfile

    def _isPidfileStale(self, pidfile):
        """ Determine whether a PID file is stale.
            
            Return ``True`` (“stale”) if the contents of the PID file are
            valid but do not match the PID of a currently-running process;
            otherwise return ``False``.
            
            """
        result = False
        
        pidfile_pid = pidfile.read_pid()
        if pidfile_pid is not None:
            try:
                os.kill(pidfile_pid, signal.SIG_DFL)
            except OSError, exc:
                if exc.errno == errno.ESRCH:
                    # The specified PID does not exist
                    result = True
        
        return result
    
    def _cleanUp(self, signal_number=None, stack_frame=None):
        """ clean up on exit
        """
        # first stop the main thread from try to restart stuff
        self.tMainStop.set()
        # now stop the other threads
#        try:
#            self.tUDPListenStop.set()
#            self.tUDPListen.join()
#        except:
#            pass
        try:
            self.tSerialStop.set()
            self.tSerial.join()
        except:
            pass
#        try:
#            self.tLCRStop.set()
#            self.tLCR.join()
#        except:
#            pass
#        try:
#            self.tUDPSendStop.set()
#            self.tUDPListen.join()
#        except:
#            pass

        if not self._background:
            if not sys.platform == 'win32':
                try:
                    self.logger.info("Removing Lock file")
                    self._pidFile.release()
                except:
                    pass

    def terminate(self, signal_number, stack_frame):
        """ Signal handler for end-process signals.
            :Return: ``None``
            
            Signal handler for the ``signal.SIGTERM`` signal. Performs the
            following step:
            
            * Raise a ``SystemExit`` exception explaining the signal.
            
            """
        exception = SystemExit(
                               "Terminating on signal %(signal_number)r"
                               % vars())
        raise exception

    def die(self):
        """For some reason we can not longer go forward
            Try cleaning up what we can and exit
        """
        self.logger.critical("DIE")
        self._cleanUp()

        sys.exit(1)

# run code
if __name__ == "__main__" :
    app = PayPhone()
    app.start()
