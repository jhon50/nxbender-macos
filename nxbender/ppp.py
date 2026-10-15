import subprocess
import threading
import pty
import os
import re
import time
import logging
import sys
from . import sslconn
import ssl
import signal
import select
import socket

class PPPSession(object):
    def __init__(self, options, session_id, routecallback=None):
        self.options = options
        self.session_id = session_id
        self.routecallback = routecallback

        self.pppargs = [
                'debug', 'debug',
                'dump',
                'logfd', '2',   # we extract the remote IP thru this

                'lcp-echo-interval', '10',
                'lcp-echo-failure',  '2',

                'local',
                'noipdefault',
                'noccp',    # server is buggy
                'noauth',
                'usepeerdns',
        ]

    def run(self):
        master, slave = pty.openpty()
        self.pty = master

        try:
            self.pppd = subprocess.Popen(['pppd'] + self.pppargs,
                                         stdin = slave,
                                         stdout = slave,
                                         stderr = subprocess.PIPE)
        except OSError as e:
            logging.error("Unable to start pppd: %s" % e.strerror)
            sys.exit(1)

        os.close(slave)

        self.tunsock = sslconn.SSLTunnel(self.session_id, self.options, self.options.server, self.options.port)
        self.pty = master

        # macOS pppd (2.4.2) logs to syslog, not fd 2, so we can't scrape the
        # "remote IP address" line to learn the link is up. Poll for the ppp
        # interface instead, then fire the route/DNS callback once.
        if self.routecallback:
            threading.Thread(target=self._watch_interface, daemon=True).start()

        def sigint_twice(*args):
            logging.info('caught SIGINT again, killing pppd')
            self.pppd.send_signal(signal.SIGKILL)

        def sigint(*args):
            logging.info('caught SIGINT, signalling pppd')
            self.killing_pppd = True
            self.pppd.send_signal(signal.SIGTERM)
            signal.signal(signal.SIGINT, sigint_twice)
            os.kill(os.getpid(), signal.SIGHUP) # break out of select()

        old_sigint = signal.signal(signal.SIGINT, sigint)
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
        signal.signal(signal.SIGWINCH, signal.SIG_IGN)

        try:
            while self.pppd.poll() is None:
                stop = self._pump()
                if stop:
                    break
        except ssl.SSLError as e:     # unexpected
            logging.exception(e)
        except socket.error as e:     # expected (peer disconnect)
            logging.error(e.strerror)
        finally:
            code = self.pppd.poll()
            if code is not None:    # pppd caused termination
                try:
                    rest = self.pppd.stderr.read()
                    if rest:
                        for line in rest.decode('utf-8', errors='replace').splitlines():
                            if line.strip():
                                logging.error("pppd: %s" % line.strip())
                except Exception:
                    pass

                logger = logging.error
                if getattr(self, 'killing_pppd', False) and code == 5:
                    logger = logging.info
                logger("pppd exited with code %d" % code)

                if code in [2, 3]:
                    logging.warn("Are you root? You almost certainly need to be root")
            else:
                self.pppd.send_signal(signal.SIGHUP)

            logging.info("Shutting down...")
            os.close(self.pty)
            self.pppd.wait()
            signal.signal(signal.SIGINT, old_sigint)
            self.tunsock.close()

    def _pump(self):
        r_set = [self.tunsock, self.pppd.stderr]
        w_set = []

        # If the SSL tunnel is blocked on writes, apply backpressure (stop reading from pppd)
        if self.tunsock.writes_pending:
            w_set.append(self.tunsock)
        else:
            r_set.append(self.pty)

        try:
            r, w, x = select.select(r_set, w_set, [])
        except select.error:
            return True   # interrupted

        if self.tunsock in r:
            self.tunsock.read_to(self.pty)

        if self.pty in r:
            stop = self.tunsock.write_from(self.pty)
            if stop:
                return stop

        if self.tunsock in w:
            self.tunsock.write_pump()

        if self.pppd.stderr in r:
            line = self.pppd.stderr.readline().strip().decode('utf-8', errors='replace')

            if self.options.show_ppp_log:
                print("pppd: %s" % line)

    def _ppp_ifaces(self):
        try:
            out = subprocess.check_output(['ifconfig', '-l']).decode('ascii', 'replace')
        except Exception:
            return []
        return [w for w in out.split() if w.startswith('ppp')]

    def _iface_peer(self, ifn):
        try:
            out = subprocess.check_output(['ifconfig', ifn]).decode('ascii', 'replace')
        except Exception:
            return None
        m = re.search(r'-->\s*([0-9.]+)', out)
        return m.group(1) if m else None

    def _watch_interface(self):
        """Wait for a ppp interface with a negotiated peer, then set up routes/DNS."""
        before = set(self._ppp_ifaces())
        for _ in range(120):   # ~60s
            time.sleep(0.5)
            if self.pppd.poll() is not None:
                return
            candidates = [i for i in self._ppp_ifaces() if i not in before]
            candidates += [i for i in self._ppp_ifaces() if i in before]
            for ifn in candidates:
                if self._iface_peer(ifn):
                    try:
                        self.routecallback(ifn)
                    except Exception as e:
                        logging.error("route/DNS setup failed: %s" % e)
                    return
        logging.warning("ppp interface did not come up within timeout; routes/DNS not set")
