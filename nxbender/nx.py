#!/usr/bin/env python2
import requests
import logging
from . import ppp
import ipaddress
import atexit
import subprocess
import sys
import os
import re
import socket

from requests.adapters import HTTPAdapter
from requests.packages.urllib3.poolmanager import PoolManager

try:
    unicode
except NameError:
    unicode = str

def prompt_for_response(options, prompt_string):
    pinentry = options.pinentry
    if not pinentry:
        pinentry = 'pinentry'

    try:
        proc = subprocess.Popen([pinentry],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                encoding='utf-8',
                bufsize=0,
                )
    except FileNotFoundError:
        if options.pinentry and options.pinentry != 'none':
            # if we were explicitly told to use pinentry, this is an error
            raise
        else:
            # fall back to terminal prompt
            return input(prompt_string + ' ')

    def expect(prefix):
        line = proc.stdout.readline().strip('\n')
        if not line.startswith(prefix):
            logging.error('Unexpected response from pinentry: "%s"' % line)
            sys.exit(1)

        return line[len(prefix)+1:]

    expect('OK')

    proc.stdin.write('SETTITLE nxBender\n')
    expect('OK')

    proc.stdin.write('SETDESC %s\n' % prompt_string.strip())
    expect('OK')

    proc.stdin.write('SETPROMPT Response:\n')
    expect('OK')

    proc.stdin.write('GETPIN\n')
    response = expect('D')
    expect('OK')

    proc.stdin.close()
    proc.wait()
    return response

class FingerprintAdapter(HTTPAdapter):
    """"Transport adapter" that allows us to pin a fingerprint for the `requests` library."""
    def __init__(self, fingerprint):
        self.fingerprint = fingerprint
        super(FingerprintAdapter, self).__init__()

    def init_poolmanager(self, connections, maxsize, block=False):
        self.poolmanager = PoolManager(
            num_pools=connections, maxsize=maxsize,
            block=block, assert_fingerprint=self.fingerprint)

class NXSession(object):
    def __init__(self, options):
        self.options = options

    def run(self):
        self.host = self.options.server + ':%d' % self.options.port
        self.session = requests.Session()

        if self.options.fingerprint:
            self.session.verify = False
            self.session.mount('https://', FingerprintAdapter(self.options.fingerprint))

        self.session.headers = {
                'User-Agent': 'Dell SonicWALL NetExtender for Linux 8.1.789',
        }

        logging.info("Logging in...")
        self.login(
                self.options.username,
                self.options.password,
                self.options.domain
            )

        logging.info("Starting session...")
        self.start_session()

        logging.info("Dialing up tunnel...")
        self.tunnel()

    def login(self, username, password, domain, extra_data={}):
        data = {
                'username': username,
                'password': password,
                'domain': domain,
                'login': 'true',
                }
        data.update(extra_data)

        resp = self.session.post('https://%s/cgi-bin/userLogin' % self.host,
                                 data=data,
                                 headers={
                                     'X-NE-SESSIONPROMPT': 'true',
                                 },
                                )

        message = resp.headers.get('X-NE-Message', None)
        message = resp.headers.get('X-NE-message', message)

        two_factor = resp.headers.get('X-NE-tf', None)
        if two_factor == '5':
            logging.info('2FA required, prompting for response')
            response = prompt_for_response(self.options, message)
            return self.login(username, password, domain, extra_data={
                'pstate': resp.headers.get('X-NE-rsastate'),
                'state': 'RADIUSCHALLENGE',
                'radiusReply': response,
                })

        if two_factor == '1':
            logging.info('TOTP 2FA required, prompting for code')
            code = prompt_for_response(self.options, message or 'Enter TOTP code:')
            resp = self.session.post('https://%s/cgi-bin/otpLogin' % self.host,
                                     data={'password': code},
                                     headers={'X-NE-pda': 'true'},
                                     )
            message = resp.headers.get('X-NE-Message', None)
            message = resp.headers.get('X-NE-message', message)
            two_factor = resp.headers.get('X-NE-tf', None)

        if message:
            logging.error('Server returned error: "%s"' % message)
            sys.exit(1)

        if two_factor not in [None, '0']:
            raise IOError("Server requested two-factor auth method '%s', which we don't understand" % two_factor)

        atexit.register(self.logout)

    def logout(self):
        # We need to try, but if we went down because we can't talk to the server? - not a big deal.
        try:
            self.session.get('https://%s/cgi-bin/userLogout' % self.host)
        except:
            pass

    def start_session(self):
        """
        Start a VPN session with the server.

        Must be logged in.
        Stores srv_options and routes returned from the server.
        """

        resp = self.session.get('https://%s/cgi-bin/sslvpnclient' % self.host,
                                params={
                                    'launchplatform': 'mac',
                                    'neProto': 3,
                                    'supportipv6': 'no',
                                },
                               )

        error = resp.headers.get('X-NE-Message', None)
        error = resp.headers.get('X-NE-message', error)
        if error:
            logging.error('Server returned error: "%s"' % error)
            sys.exit(1)

        srv_options = {}
        routes = []

        # Very dodgily avoid actually parsing the HTML
        for line in resp.iter_lines():
            line = line.strip().decode('utf-8', errors='replace')
            if line.startswith('<'):
                continue
            if line.startswith('}<'):
                continue

            try:
                key, value = line.split(' = ', 1)
            except ValueError:
                logging.warn("Unexpected line in session start message: '%s'" % line)

            if key == 'Route':
                routes.append(value)
            elif key not in srv_options:
                srv_options[key] = value
            else:
                logging.info('Duplicated srv_options value %s = %s' % (key, value))

            logging.debug("srv_option '%s' = '%s'" % (key, value))

        self.srv_options = srv_options
        self.routes = routes

    def tunnel(self):
        """
        Begin PPP tunneling.
        """

        tunnel_version = self.srv_options.get('NX_TUNNEL_PROTO_VER', None)

        if tunnel_version is None:
            auth_key = self.session.cookies['swap']
        elif tunnel_version == '2.0':
            auth_key = self.srv_options['SessionId']
        else:
            logging.warn("Unknown tunnel version '%s'" % tunnel_version)
            auth_key = self.srv_options['SessionId']    # a guess

        pppd = ppp.PPPSession(self.options, auth_key, routecallback=self.setup_routes)
        pppd.run()

    def setup_routes(self, ifname):
        for route in set(self.routes):
            net = ipaddress.IPv4Network(unicode(route))
            dst = '%s/%d' % (net.network_address, net.prefixlen)
            rc = subprocess.call(['/sbin/route', '-n', 'add', '-net', dst, '-interface', ifname])
            if rc != 0:
                logging.warning("route add %s dev %s failed (rc=%d)" % (dst, ifname, rc))

        self.setup_full_tunnel(ifname)
        self.setup_dns()
        logging.info("Remote routing configured via %s, VPN is up" % ifname)

    def setup_full_tunnel(self, ifname):
        """
        Route ALL traffic through the tunnel (parity with SonicWall Mobile
        Connect) so our public egress is the VPN's IP — required for services
        that allowlist the corporate egress address.

        Two /1 routes beat the existing /0 default without deleting it, and a
        host route pins the VPN server itself to the real gateway so the tunnel
        link doesn't try to route through itself.
        """
        server = self.options.server
        try:
            server_ip = socket.gethostbyname(server)
        except Exception:
            server_ip = server

        gw = self._gateway_for(server_ip)
        if gw and re.match(r'^[0-9.]+$', gw):
            subprocess.call(['/sbin/route', '-n', 'add', '-host', server_ip, gw])
        else:
            logging.warning("No usable gateway for VPN server %s; skipping "
                            "full-tunnel to avoid blackholing the link" % server_ip)
            return

        subprocess.call(['/sbin/route', '-n', 'add', '-net', '0.0.0.0/1', '-interface', ifname])
        subprocess.call(['/sbin/route', '-n', 'add', '-net', '128.0.0.0/1', '-interface', ifname])
        logging.info("Full-tunnel routing enabled (all traffic via %s)" % ifname)

    def _gateway_for(self, dst):
        try:
            out = subprocess.check_output(['/sbin/route', '-n', 'get', dst]).decode('ascii', 'replace')
        except Exception:
            return None
        m = re.search(r'gateway:\s*([0-9.]+)', out)
        return m.group(1) if m else None

    def setup_dns(self):
        """
        macOS split-DNS: for each DNS suffix the server hands us, write an
        /etc/resolver/<suffix> file pointing at the VPN's nameservers so only
        those domains resolve through the tunnel. Cleaned up on exit.
        """
        servers = []
        for key in ('dns1', 'dns2', 'dnsServer', 'dnsServer2'):
            val = self.srv_options.get(key)
            if val and val not in ('0.0.0.0', '') and val not in servers:
                servers.append(val)

        suffixes = []
        for key in ('dnsSuffix', 'dnsSuffixes', 'pppDnsSuffix'):
            val = self.srv_options.get(key)
            if val:
                suffixes += [s.strip() for s in val.replace(',', ' ').split() if s.strip()]

        if not servers:
            logging.info("No VPN DNS servers advertised; skipping DNS setup")
            return
        if not suffixes:
            logging.info("VPN DNS servers %s but no search suffix; skipping scoped DNS "
                         "(resolve internal hosts by IP, or set a suffix manually)" % servers)
            return

        body = ''.join('nameserver %s\n' % s for s in servers)
        written = []
        try:
            os.makedirs('/etc/resolver', exist_ok=True)
        except OSError as e:
            logging.warning("Cannot create /etc/resolver: %s" % e)
            return

        for suffix in set(suffixes):
            path = '/etc/resolver/%s' % suffix
            try:
                with open(path, 'w') as f:
                    f.write(body)
                written.append(path)
                logging.info("Scoped DNS for *.%s -> %s" % (suffix, servers))
            except OSError as e:
                logging.warning("Cannot write %s: %s" % (path, e))

        def cleanup():
            for path in written:
                try:
                    os.remove(path)
                except OSError:
                    pass
        atexit.register(cleanup)
