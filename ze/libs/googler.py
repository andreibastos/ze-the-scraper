#!/usr/bin/env python3
#
# Copyright © 2008 Henri Hakkinen
# Copyright © 2015-2017 Arun Prakash Jana <engineerarun@gmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import argparse
import atexit
import collections
import codecs
import functools
import gzip
import html.entities
import html.parser
import http.client
from http.client import HTTPSConnection
import locale
import logging
import os
import signal
import socket
import ssl
import sys
import textwrap
import urllib.parse
import webbrowser

# Python optional dependency compatibility layer
try:
    import readline
except ImportError:
    pass


# Basic setup

try:
    import setproctitle
    setproctitle.setproctitle('googler')
except Exception:
    pass

logging.basicConfig(format='[%(levelname)s] %(message)s')
logger = logging.getLogger()


def sigint_handler(signum, frame):
    print('\nInterrupted.', file=sys.stderr)
    sys.exit(1)

signal.signal(signal.SIGINT, sigint_handler)


# Constants

_VERSION_ = '3.0'

COLORMAP = {k: '\x1b[%sm' % v for k, v in {
    'a': '30', 'b': '31', 'c': '32', 'd': '33',
    'e': '34', 'f': '35', 'g': '36', 'h': '37',
    'i': '90', 'j': '91', 'k': '92', 'l': '93',
    'm': '94', 'n': '95', 'o': '96', 'p': '97',
    'A': '30;1', 'B': '31;1', 'C': '32;1', 'D': '33;1',
    'E': '34;1', 'F': '35;1', 'G': '36;1', 'H': '37;1',
    'I': '90;1', 'J': '91;1', 'K': '92;1', 'L': '93;1',
    'M': '94;1', 'N': '95;1', 'O': '96;1', 'P': '97;1',
    'x': '0', 'X': '1', 'y': '7', 'Y': '7;1',
}.items()}

# Disguise as Firefox on Ubuntu
USER_AGENT = ('Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:52.0) Gecko/20100101 Firefox/52.0')
ua = True  # User Agent is enabled by default

# Self-upgrade parameters
#
# Downstream packagers are recommended to turn off the entire self-upgrade
# mechanism through
#
#     make disable-self-upgrade
#
# before running `make install'.

ENABLE_SELF_UPGRADE_MECHANISM = True
API_REPO_BASE = 'https://api.github.com/repos/jarun/googler'
RAW_DOWNLOAD_REPO_BASE = 'https://raw.githubusercontent.com/jarun/googler'


# Global helper functions

def open_url(url):
    """Open an URL in the user's default web browser.

    Whether the browser's output (both stdout and stderr) are suppressed
    depends on the boolean attribute ``open_url.suppress_browser_output``.
    If the attribute is not set upon a call, set it to a default value,
    which means False if BROWSER is set to a known text-based browser --
    elinks, links, lynx or w3m; or True otherwise.
    """
    if not hasattr(open_url, 'suppress_browser_output'):
        open_url.suppress_browser_output = (os.getenv('BROWSER') not in
                                            ['elinks', 'links', 'lynx', 'w3m'])
    logger.debug('Opening %s', url)
    if open_url.suppress_browser_output:
        _stderr = os.dup(2)
        os.close(2)
        _stdout = os.dup(1)
        os.close(1)
        fd = os.open(os.devnull, os.O_RDWR)
        os.dup2(fd, 2)
        os.dup2(fd, 1)
    try:
        webbrowser.open(url)
    finally:
        if open_url.suppress_browser_output:
            os.close(fd)
            os.dup2(_stderr, 2)
            os.dup2(_stdout, 1)


def printerr(msg):
    """Print message, verbatim, to stderr.

    ``msg`` could be any stringifiable value.
    """
    print(msg, file=sys.stderr)


def unwrap(text):
    """Unwrap text."""
    lines = text.split('\n')
    result = ''
    for i in range(len(lines) - 1):
        result += lines[i]
        if not lines[i]:
            # Paragraph break
            result += '\n\n'
        elif lines[i + 1]:
            # Next line is not paragraph break, add space
            result += ' '
    # Handle last line
    result += lines[-1] if lines[-1] else '\n'
    return result


def check_stdout_encoding():
    """Make sure stdout encoding is utf-8.

    If not, print error message and instructions, then exit with
    status 1.

    This function is a no-op on win32 because encoding on win32 is
    messy, and let's just hope for the best. /s
    """
    if sys.platform == 'win32':
        return

    # Use codecs.lookup to resolve text encoding alias
    encoding = codecs.lookup(sys.stdout.encoding).name
    if encoding != 'utf-8':
        locale_lang, locale_encoding = locale.getlocale()
        if locale_lang is None:
            locale_lang = '<unknown>'
        if locale_encoding is None:
            locale_encoding = '<unknown>'
        ioencoding = os.getenv('PYTHONIOENCODING', 'not set')
        sys.stderr.write(unwrap(textwrap.dedent("""\
        stdout encoding '{encoding}' detected. googler requires utf-8 to
        work properly. The wrong encoding may be due to a non-UTF-8
        locale or an improper PYTHONIOENCODING. (For the record, your
        locale language is {locale_lang} and locale encoding is
        {locale_encoding}; your PYTHONIOENCODING is {ioencoding}.)

        Please set a UTF-8 locale (e.g., en_US.UTF-8) or set
        PYTHONIOENCODING to utf-8.
        """.format(
            encoding=encoding,
            locale_lang=locale_lang,
            locale_encoding=locale_encoding,
            ioencoding=ioencoding,
        ))))
        sys.exit(1)


# Classes

class TLS1_2Connection(HTTPSConnection):
    """Overrides HTTPSConnection.connect to specify TLS version

    NOTE: TLS 1.2 is supported from Python 3.4
    """

    def __init__(self, host, **kwargs):
        HTTPSConnection.__init__(self, host, **kwargs)

    def connect(self, notweak=False):
        sock = socket.create_connection((self.host, self.port),
                                        self.timeout, self.source_address)

        # Optimizations not available on OS X
        if not notweak and sys.platform.startswith('linux'):
            sock.setsockopt(socket.SOL_TCP, socket.TCP_DEFER_ACCEPT, 1)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_QUICKACK, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 524288)

        if getattr(self, '_tunnel_host', None):
            self.sock = sock
        elif not notweak:
            # Try to use TLS 1.2
            ssl_context = None
            if hasattr(ssl, 'PROTOCOL_TLS'):
                # Since Python 3.5.3
                ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS)
                ssl_context.options |= (ssl.OP_NO_SSLv2 | ssl.OP_NO_SSLv3 |
                                        ssl.OP_NO_TLSv1 | ssl.OP_NO_TLSv1_1)
            elif hasattr(ssl, 'PROTOCOL_TLSv1_2'):
                # Since Python 3.4
                ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLSv1_2)
            if ssl_context:
                self.sock = ssl_context.wrap_socket(sock)
                return

        # Fallback
        HTTPSConnection.connect(self)


class GoogleUrl(object):
    """
    This class constructs the Google Search/News URL.

    This class is modelled on urllib.parse.ParseResult for familiarity,
    which means it supports reading of all six attributes -- scheme,
    netloc, path, params, query, fragment -- of
    urllib.parse.ParseResult, as well as the geturl() method.

    However, the attributes (properties) and methods listed below should
    be the preferred methods of access to this class.

    Parameters
    ----------
    opts : dict or argparse.Namespace, optional
        See the ``opts`` parameter of `update`.

    Other Parameters
    ----------------
    See "Other Parameters" of `update`.

    Attributes
    ----------
    hostname : str
        Read-write property.
    keywords : str or list of strs
        Read-write property.
    news : bool
        Read-only property.
    url : str
        Read-only property.

    Methods
    -------
    full()
    relative()
    update(opts=None, **kwargs)
    set_queries(**kwargs)
    unset_queries(*args)
    next_page()
    prev_page()
    first_page()

    """

    def __init__(self, opts=None, **kwargs):
        self.scheme = 'https'
        # self.netloc is a calculated property
        self.path = '/search'
        self.params = ''
        # self.query is a calculated property
        self.fragment = ''

        self._tld = None
        self._num = 10
        self._start = 0
        self._keywords = []
        self._site = None
        self._query_dict = {
            'ie': 'UTF-8',
            'oe': 'UTF-8',
        }
        self.update(opts, **kwargs)

    def __str__(self):
        return self.url

    @property
    def url(self):
        """The full Google URL you want."""
        return self.full()

    @property
    def hostname(self):
        """The hostname."""
        return self.netloc

    @hostname.setter
    def hostname(self, hostname):
        self.netloc = hostname

    @property
    def keywords(self):
        """The keywords, either a str or a list of strs."""
        return self._keywords

    @keywords.setter
    def keywords(self, keywords):
        self._keywords = keywords

    @property
    def news(self):
        """Whether the URL is for Google News."""
        return 'tbm' in self._query_dict and self._query_dict['tbm'] == 'nws'

    def full(self):
        """Return the full URL.

        Returns
        -------
        str

        """
        url = (self.scheme + ':') if self.scheme else ''
        url += '//' + self.netloc + self.relative()
        return url

    def relative(self):
        """Return the relative URL (without scheme and authority).

        Authority (see RFC 3986 section 3.2), or netloc in the
        terminology of urllib.parse, basically means the hostname
        here. The relative URL is good for making HTTP(S) requests to a
        known host.

        Returns
        -------
        str

        """
        rel = self.path
        if self.params:
            rel += ';' + self.params
        if self.query:
            rel += '?' + self.query
        if self.fragment:
            rel += '#' + self.fragment
        return rel

    def update(self, opts=None, **kwargs):
        """Update the URL with the given options.

        Parameters
        ----------
        opts : dict or argparse.Namespace, optional
            Carries options that affect the Google Search/News URL. The
            list of currently recognized option keys with expected value
            types:

                duration: str (GooglerArgumentParser.is_duration)
                exact: bool
                keywords: str or list of strs
                lang: str
                news: bool
                num: int
                site: str
                start: int
                tld: str

        Other Parameters
        ----------------
        kwargs
            The `kwargs` dict extends `opts`, that is, options can be
            specified either way, in `opts` or as individual keyword
            arguments.

        """

        if opts is None:
            opts = {}
        if hasattr(opts, '__dict__'):
            opts = opts.__dict__
        opts.update(kwargs)

        qd = self._query_dict
        if 'duration' in opts and opts['duration']:
            qd['tbs'] = 'qdr:%s' % opts['duration']
        if 'exact' in opts:
            if opts['exact']:
                qd['nfpr'] = 1
            else:
                qd.pop('nfpr', None)
        if 'keywords' in opts:
            self._keywords = opts['keywords']
        if 'lang' in opts and opts['lang']:
            qd['hl'] = opts['lang']
        if 'news' in opts:
            if opts['news']:
                qd['tbm'] = 'nws'
            else:
                qd.pop('tbm', None)
        if 'num' in opts:
            self._num = opts['num']
        if 'site' in opts:
            self._site = opts['site']
        if 'start' in opts:
            self._start = opts['start']
        if 'tld' in opts:
            self._tld = opts['tld']

    def set_queries(self, **kwargs):
        """Forcefully set queries outside the normal `update` mechanism.

        Other Parameters
        ----------------
        kwargs
            Arbitrary key value pairs to be set in the query string. All
            keys and values should be stringifiable.

            Note that certain keys, e.g., ``q``, have their values
            constructed on the fly, so setting those has no actual
            effect.

        """
        for k, v in kwargs.items():
            self._query_dict[k] = v

    def unset_queries(self, *args):
        """Forcefully unset queries outside the normal `update` mechanism.

        Other Parameters
        ----------------
        args
            Arbitrary keys to be unset. No exception is raised if a key
            does not exist in the first place.

            Note that certain keys, e.g., ``q``, are always included in
            the resulting URL, so unsetting those has no actual effect.

        """
        for k in args:
            self._query_dict.pop(k, None)

    def next_page(self):
        """Navigate to the next page."""
        self._start += self._num

    def prev_page(self):
        """Navigate to the previous page.

        Raises
        ------
        ValueError
            If already at the first page (``start=0`` in the current
            query string).

        """
        if self._start == 0:
            raise ValueError('Already at the first page.')
        self._start = (self._start - self._num) if self._start > self._num else 0

    def first_page(self):
        """Navigate to the first page.

        Raises
        ------
        ValueError
            If already at the first page (``start=0`` in the current
            query string).

        """
        if self._start == 0:
            raise ValueError('Already at the first page.')
        self._start = 0

    # Data source: https://en.wikipedia.org/wiki/List_of_Google_domains
    # Scraper script: https://gist.github.com/zmwangx/b976e83c14552fe18b71
    TLD_TO_DOMAIN_MAP = {
        'ac': 'google.ac',      'ad': 'google.ad',      'ae': 'google.ae',
        'af': 'google.com.af',  'ag': 'google.com.ag',  'ai': 'google.com.ai',
        'al': 'google.al',      'am': 'google.am',      'ao': 'google.co.ao',
        'ar': 'google.com.ar',  'as': 'google.as',      'at': 'google.at',
        'au': 'google.com.au',  'az': 'google.az',      'ba': 'google.ba',
        'bd': 'google.com.bd',  'be': 'google.be',      'bf': 'google.bf',
        'bg': 'google.bg',      'bh': 'google.com.bh',  'bi': 'google.bi',
        'bj': 'google.bj',      'bn': 'google.com.bn',  'bo': 'google.com.bo',
        'br': 'google.com.br',  'bs': 'google.bs',      'bt': 'google.bt',
        'bw': 'google.co.bw',   'by': 'google.by',      'bz': 'google.com.bz',
        'ca': 'google.ca',      'cat': 'google.cat',    'cc': 'google.cc',
        'cd': 'google.cd',      'cf': 'google.cf',      'cg': 'google.cg',
        'ch': 'google.ch',      'ci': 'google.ci',      'ck': 'google.co.ck',
        'cl': 'google.cl',      'cm': 'google.cm',      'cn': 'google.cn',
        'co': 'google.com.co',  'cr': 'google.co.cr',   'cu': 'google.com.cu',
        'cv': 'google.cv',      'cy': 'google.com.cy',  'cz': 'google.cz',
        'de': 'google.de',      'dj': 'google.dj',      'dk': 'google.dk',
        'dm': 'google.dm',      'do': 'google.com.do',  'dz': 'google.dz',
        'ec': 'google.com.ec',  'ee': 'google.ee',      'eg': 'google.com.eg',
        'es': 'google.es',      'et': 'google.com.et',  'fi': 'google.fi',
        'fj': 'google.com.fj',  'fm': 'google.fm',      'fr': 'google.fr',
        'ga': 'google.ga',      'ge': 'google.ge',      'gf': 'google.gf',
        'gg': 'google.gg',      'gh': 'google.com.gh',  'gi': 'google.com.gi',
        'gl': 'google.gl',      'gm': 'google.gm',      'gp': 'google.gp',
        'gr': 'google.gr',      'gt': 'google.com.gt',  'gy': 'google.gy',
        'hk': 'google.com.hk',  'hn': 'google.hn',      'hr': 'google.hr',
        'ht': 'google.ht',      'hu': 'google.hu',      'id': 'google.co.id',
        'ie': 'google.ie',      'il': 'google.co.il',   'im': 'google.im',
        'in': 'google.co.in',   'io': 'google.io',      'iq': 'google.iq',
        'is': 'google.is',      'it': 'google.it',      'je': 'google.je',
        'jm': 'google.com.jm',  'jo': 'google.jo',      'jp': 'google.co.jp',
        'ke': 'google.co.ke',   'kg': 'google.kg',      'kh': 'google.com.kh',
        'ki': 'google.ki',      'kr': 'google.co.kr',   'kw': 'google.com.kw',
        'kz': 'google.kz',      'la': 'google.la',      'lb': 'google.com.lb',
        'lc': 'google.com.lc',  'li': 'google.li',      'lk': 'google.lk',
        'ls': 'google.co.ls',   'lt': 'google.lt',      'lu': 'google.lu',
        'lv': 'google.lv',      'ly': 'google.com.ly',  'ma': 'google.co.ma',
        'md': 'google.md',      'me': 'google.me',      'mg': 'google.mg',
        'mk': 'google.mk',      'ml': 'google.ml',      'mm': 'google.com.mm',
        'mn': 'google.mn',      'ms': 'google.ms',      'mt': 'google.com.mt',
        'mu': 'google.mu',      'mv': 'google.mv',      'mw': 'google.mw',
        'mx': 'google.com.mx',  'my': 'google.com.my',  'mz': 'google.co.mz',
        'na': 'google.com.na',  'ne': 'google.ne',      'nf': 'google.com.nf',
        'ng': 'google.com.ng',  'ni': 'google.com.ni',  'nl': 'google.nl',
        'no': 'google.no',      'np': 'google.com.np',  'nr': 'google.nr',
        'nu': 'google.nu',      'nz': 'google.co.nz',   'om': 'google.com.om',
        'pa': 'google.com.pa',  'pe': 'google.com.pe',  'pg': 'google.com.pg',
        'ph': 'google.com.ph',  'pk': 'google.com.pk',  'pl': 'google.pl',
        'pn': 'google.co.pn',   'pr': 'google.com.pr',  'ps': 'google.ps',
        'pt': 'google.pt',      'py': 'google.com.py',  'qa': 'google.com.qa',
        'ro': 'google.ro',      'rs': 'google.rs',      'ru': 'google.ru',
        'rw': 'google.rw',      'sa': 'google.com.sa',  'sb': 'google.com.sb',
        'sc': 'google.sc',      'se': 'google.se',      'sg': 'google.com.sg',
        'sh': 'google.sh',      'si': 'google.si',      'sk': 'google.sk',
        'sl': 'google.com.sl',  'sm': 'google.sm',      'sn': 'google.sn',
        'so': 'google.so',      'sr': 'google.sr',      'st': 'google.st',
        'sv': 'google.com.sv',  'td': 'google.td',      'tg': 'google.tg',
        'th': 'google.co.th',   'tj': 'google.com.tj',  'tk': 'google.tk',
        'tl': 'google.tl',      'tm': 'google.tm',      'tn': 'google.tn',
        'to': 'google.to',      'tr': 'google.com.tr',  'tt': 'google.tt',
        'tw': 'google.com.tw',  'tz': 'google.co.tz',   'ua': 'google.com.ua',
        'ug': 'google.co.ug',   'uk': 'google.co.uk',   'uy': 'google.com.uy',
        'uz': 'google.co.uz',   'vc': 'google.com.vc',  've': 'google.co.ve',
        'vg': 'google.vg',      'vi': 'google.co.vi',   'vn': 'google.com.vn',
        'vu': 'google.vu',      'ws': 'google.ws',      'za': 'google.co.za',
        'zm': 'google.co.zm',   'zw': 'google.co.zw',
    }

    @property
    def netloc(self):
        """The hostname."""
        try:
            return 'www.' + self.TLD_TO_DOMAIN_MAP[self._tld]
        except KeyError:
            return 'www.google.com'

    @property
    def query(self):
        """The query string."""
        qd = {}
        qd.update(self._query_dict)
        qd['num'] = self._num
        qd['start'] = self._start

        # Construct the q query
        q = ''
        keywords = self._keywords
        if keywords:
            if isinstance(keywords, list):
                q += '+'.join([urllib.parse.quote_plus(kw) for kw in keywords])
            else:
                q += urllib.parse.quote_plus(keywords)
        if self._site:
            q += '+site:' + urllib.parse.quote_plus(self._site)
        qd['q'] = q

        return '&'.join(['%s=%s' % (k, qd[k]) for k in sorted(qd.keys())])


class GoogleConnectionError(Exception):
    pass


class GoogleConnection(object):
    """
    This class facilitates connecting to and fetching from Google.

    Parameters
    ----------
    See http.client.HTTPSConnection for documentation of the
    parameters.

    Raises
    ------
    GoogleConnectionError

    Attributes
    ----------
    host : str
        The currently connected host. Read-only property. Use
        `new_connection` to change host.

    Methods
    -------
    new_connection(host=None, port=None, timeout=45)
    renew_connection(timeout=45)
    fetch_page(url)
    close()

    """

    def __init__(self, host, port=None, timeout=45, proxy=None, notweak=False):
        self._host = None
        self._port = None
        self._proxy = proxy
        self._notweak = notweak
        self._conn = None
        self.new_connection(host, port=port, timeout=timeout)
        self.cookie = ''

    @property
    def host(self):
        """The host currently connected to."""
        return self._host

    def new_connection(self, host=None, port=None, timeout=45):
        """Close the current connection (if any) and establish a new one.

        Parameters
        ----------
        See http.client.HTTPSConnection for documentation of the
        parameters. Renew the connection (i.e., reuse the current host
        and port) if host is None or empty.

        Raises
        ------
        GoogleConnectionError

        """
        if self._conn:
            self._conn.close()

        if not host:
            host = self._host
            port = self._port
        self._host = host
        self._port = port
        host_display = host + (':%d' % port if port else '')

        proxy = self._proxy
        if proxy:
            logger.debug('Connecting to proxy server %s', proxy)
            self._conn = TLS1_2Connection(proxy, timeout=timeout)

            logger.debug('Tunnelling to host %s' % host_display)
            self._conn.set_tunnel(host, port=port)

            try:
                self._conn.connect(self._notweak)
            except Exception as e:
                msg = 'Failed to connect to proxy server %s: %s.' % (proxy, e)
                raise GoogleConnectionError(msg)
        else:
            logger.debug('Connecting to new host %s', host_display)
            self._conn = TLS1_2Connection(host, port=port, timeout=timeout)
            try:
                self._conn.connect(self._notweak)
            except Exception as e:
                msg = 'Failed to connect to %s: %s.' % (host_display, e)
                raise GoogleConnectionError(msg)

    def renew_connection(self, timeout=45):
        """Renew current connection.

        Equivalent to ``new_connection(timeout=timeout)``.

        """
        self.new_connection(timeout=timeout)

    def fetch_page(self, url):
        """Fetch a URL.

        Allows one reconnection and multiple redirections before failing
        and raising GoogleConnectionError.

        Parameters
        ----------
        url : str
            The URL to fetch, relative to the host.

        Raises
        ------
        GoogleConnectionError
            When not getting HTTP 200 even after the allowed one
            reconnection and/or one redirection, or when Google is
            blocking query due to unusual activity.

        Returns
        -------
        str
            Response payload, gunzipped (if applicable) and decoded (in UTF-8).

        """
        try:
            self._raw_get(url)
        except (http.client.HTTPException, OSError) as e:
            logger.debug('Got exception: %s.', e)
            logger.debug('Attempting to reconnect...')
            self.renew_connection()
            try:
                self._raw_get(url)
            except http.client.HTTPException as e:
                logger.debug('Got exception: %s.', e)
                raise GoogleConnectionError("Failed to get '%s'." % url)

        resp = self._resp
        redirect_counter = 0
        while resp.status != 200 and redirect_counter < 3:
            if resp.status in {301, 302, 303, 307, 308}:
                redirection_url = resp.getheader('location', '')
                if 'sorry/IndexRedirect?' in redirection_url or 'sorry/index?' in redirection_url:
                    raise GoogleConnectionError('Connection blocked due to unusual activity.')
                self._redirect(redirection_url)
                resp = self._resp
                redirect_counter += 1
            else:
                break

        if resp.status != 200:
            raise GoogleConnectionError('Got HTTP %d: %s' % (resp.status, resp.reason))

        payload = resp.read()
        try:
            return gzip.decompress(payload).decode('utf-8')
        except OSError:
            # Not gzipped
            return payload.decode('utf-8')

    def _redirect(self, url):
        """Redirect to and fetch a new URL.

        Like `_raw_get`, the response is stored in ``self._resp``. A new
        connection is made if redirecting to a different host.

        Parameters
        ----------
        url : str
            If absolute and points to a different host, make a new
            connection.

        Raises
        ------
        GoogleConnectionError

        """
        logger.debug('Redirecting to URL %s', url)
        segments = urllib.parse.urlparse(url)

        host = segments.netloc
        if host != self._host:
            self.new_connection(host)

        relurl = urllib.parse.urlunparse(('', '') + segments[2:])
        try:
            self._raw_get(relurl)
        except http.client.HTTPException as e:
            logger.debug('Got exception: %s.', e)
            raise GoogleConnectionError("Failed to get '%s'." % url)

    def _raw_get(self, url):
        """Make a raw HTTP GET request.

        No status check (which implies no redirection). Response can be
        accessed from ``self._resp``.

        Parameters
        ----------
        url : str
            URL relative to the host, used in the GET request.

        Raises
        ------
        http.client.HTTPException

        """
        logger.debug('Fetching URL %s', url)
        self._conn.request('GET', url, None, {
            'Accept-Encoding': 'gzip',
            'User-Agent': USER_AGENT if ua else '',
            'Cookie': self.cookie,
            'Connection': 'keep-alive',
            'DNT': '1',
        })
        self._resp = self._conn.getresponse()
        if self.cookie == '':
            complete_cookie = self._resp.getheader('Set-Cookie')
            # Cookie won't be available is already blocked
            if complete_cookie is not None:
                self.cookie = complete_cookie[:complete_cookie.find(';')]
                logger.debug('Cookie: %s' % self.cookie)

    def close(self):
        """Close the connection (if one is active)."""
        if self._conn:
            self._conn.close()


def annotate_tag(annotated_starttag_handler):
    # See parser logic within the GoogleParser class for documentation.
    #
    # In particular, search for "Ignore List" to view detailed
    # documentation of the ignore list.
    #
    # annotated_starttag_handler(self, tag: str, attrsdict: dict) -> annotation
    # Returns: HTMLParser.handle_starttag(self, tag: str, attrs: list) -> None

    def handler(self, tag, attrs):
        # Get context; assumes that the handler is called SCOPE_start
        context = annotated_starttag_handler.__name__[:-6]

        # If context is 'ignore', ignore all tests
        if context == 'ignore':
            self.insert_annotation(tag, None)
            return

        attrs = dict(attrs)

        # Compare against ignore list
        ignored = False
        for selector in self.IGNORE_LIST:
            for attr in selector:
                if attr == 'tag':
                    if tag != selector['tag']:
                        break
                elif attr == 'class':
                    tag_classes = set(self.classes(attrs))
                    selector_classes = set(self.classes(selector))
                    if not selector_classes.issubset(tag_classes):
                        break
                else:
                    if attrs[attr] != selector[attr]:
                        break
            else:
                # Passed all criteria of the selector
                ignored = True
                break

        # If tag matches ignore list, annotate and hand over to ignore_*
        if ignored:
            self.insert_annotation(tag, context + '_ignored')
            self.set_handlers_to('ignore')
            return

        # Standard
        annotation = annotated_starttag_handler(self, tag, attrs)
        self.insert_annotation(tag, annotation)

    return handler


def retrieve_tag_annotation(annotated_endtag_handler):
    # See parser logic within the GoogleParser class for documentation.
    #
    # annotated_endtag_handler(self, tag: str, annotation) -> None
    # Returns: HTMLParser.handle_endtag(self, tag: str) -> None

    def handler(self, tag):
        try:
            annotation = self.tag_annotations[tag].pop()
        except IndexError:
            # Malformed HTML -- more close tags than open tags
            annotation = None
        annotated_endtag_handler(self, tag, annotation)

    return handler


class GoogleParser(html.parser.HTMLParser):
    """The members of this class parse the result
    HTML page fetched from Google server for a query.

    The custom parser looks for tags enclosing search
    results and extracts the URL, title and text for
    each search result.

    After parsing the complete HTML page results are
    returned in a list of objects of class Result.
    """

    # Parser logic:
    #
    # - Guiding principles:
    #
    #   1. Tag handlers are contextual;
    #
    #   2. Contextual starttag and endtag handlers should come in pairs
    #      and have a clear hierarchy;
    #
    #   3. starttag handlers should only yield control to a pair of
    #      child handlers (that is, one level down the hierarchy), and
    #      correspondingly, endtag handlers should only return control
    #      to the parent (that is, the pair of handlers that gave it
    #      control in the first place).
    #
    #   Principle 3 is meant to enforce a (possibly implicit) stack
    #   structure and thus prevent careless jumps that result in what's
    #   essentially spaghetti code with liberal use of GOTOs.
    #
    # - HTMLParser.handle_endtag gives us a bare tag name without
    #   context, which is not good for enforcing principle 3 when we
    #   have, say, nested div tags.
    #
    #   In order to precisely identify the matching opening tag, we
    #   maintain a stack for each tag name with *annotations*. Important
    #   opening tags (e.g., the ones where child handlers are
    #   registered) can be annotated so that when we can watch for the
    #   annotation in the endtag handler, and when the appropriate
    #   annotation is popped, we perform the corresponding action (e.g.,
    #   switch back to old handlers).
    #
    #   To facilitate this, each starttag handler is decorated with
    #   @annotate_tag, which accepts a return value that is the
    #   annotation (None by default), and additionally converts attrs to
    #   a dict, which is much easier to work with; and each endtag
    #   handler is decorated with @retrieve_tag_annotation which sends
    #   an additional parameter that is the retrieved annotation to the
    #   handler.
    #
    #   Note that some of our tag annotation stacks leak over time: this
    #   happens to tags like <img> and <hr> which are not
    #   closed. However, these tags play no structural role, and come
    #   only in small quantities, so it's not really a problem.
    #
    # - All textual data (result title, result abstract, etc.) are
    #   processed through a set of shared handlers. These handlers store
    #   text in a shared buffer self.textbuf which can be retrieved and
    #   cleared at appropriate times.
    #
    #   Data (including charrefs and entityrefs) are ignored initially,
    #   and when data needs to be recorded, the start_populating_textbuf
    #   method is called to register the appropriate data, charref and
    #   entityref handlers so that they append to self.textbuf. When
    #   recording ends, pop_textbuf should be called to extract the text
    #   and clear the buffer. stop_populating_textbuf returns the
    #   handlers to their pristine state (ignoring data).
    #
    #   Methods:
    #   - start_populating_textbuf(self, data_transformer: Callable[[str], str]) -> None
    #   - pop_textbuf(self) -> str
    #   - stop_populating_textbuf(self) -> None
    #
    # - Outermost starttag and endtag handler methods: root_*. The whole
    #   parser starts and ends in this state.
    #
    # - Each result is wrapped in a <div> tag with class "g".
    #
    #   <!-- within the scope of root_* -->
    #   <div class="g">  <!-- annotate as 'result', hand over to result_* -->
    #   </div>           <!-- hand back to root_*, register result -->
    #
    # - For each result, the first <h3> tag with class "r" contains the
    #   hyperlinked title, and the (optional) first <div> tag with class
    #   "s" contains the abstract of the result.
    #
    #   <!-- within the scope of result_* -->
    #   <h3 class="r">   <!-- annotate as 'title', hand over to title_* -->
    #   </h3>            <!-- hand back to result_* -->
    #   <div class="s">  <!-- annotate as 'abstract', hand over to abstract_* -->
    #   </div>           <!-- hand back to result_* -->
    #
    # - Each title looks like
    #
    #   <h3 class="r">
    #     <!-- within the scope of title_* -->
    #     <span>                 <!-- filetype (optional), annotate as title_filetype,
    #                                 start_populating_textbuf -->
    #       file type (e.g. [PDF])
    #     </span>                <!-- stop_populating_textbuf -->
    #     <a href="result url">  <!-- register self.url, annotate as 'title_link',
    #                                 start_populating_textbuf -->
    #       result title
    #     </a>                   <!-- stop_populating_textbuf, pop to self.title -->
    #   </h3>
    #
    # - For each abstract, the first <span> tag with class "st" contains
    #   the body text of the abstract.
    #
    #   <!-- within the scope of abstract_* -->
    #   <span class="st">  <!-- annotate as 'abstract_text', start_populating_textbuf -->
    #     abstract text with <em> markup on keywords
    #   </span>            <!-- stop_populating_textbuf, pop to self.abstract -->
    #
    # - Certain results may come with sitelinks, secondary results that
    #   are usually subdomains or deep links within the primary
    #   result. They are organized into a <table> tag, and each sitelink
    #   is in a separate <td>:
    #
    #   <!-- within the scope of result_* -->
    #   <table>    <!-- annotate as 'sitelink_table', hand over to sitelink_table_* -->
    #     <tr>
    #       <td>   <!-- annotate as 'sitelink', hand over to sitelink_* -->
    #       </td>  <!-- append to self.sitelinks, hand back to sitelink_table_* -->
    #       <td></td>
    #       ...
    #     </tr>
    #     <tr></tr>
    #     ...
    #   </table>   <!-- hand back to result_* -->
    #
    #   Then for each sitelink, the hyperlinked title is in an <h3> tag
    #   with class "r", and the abstract is in a <div> tag with class
    #   "st". They are not necessarily on the same level, but we don't
    #   really care.
    #
    #   <!-- within the scope of sitelink_* -->
    #   <h3 class="r">             <!-- annotate as 'sitelink_title',
    #                                   hand over to sitelink_title_* -->
    #     <a href="sitelink url">  <!-- register sitelink url, annotate as 'sitelink_title_link',
    #                                   start_populating_textbuf -->
    #       sitelink title
    #     </a>                     <!-- stop_populating_textbuf, pop to sitelink title -->
    #   </h3>                      <!-- hand back to sitelink_* -->
    #
    #   <!-- still within the scope of sitelink_* -->
    #   <div class="st">  <!-- annotate as 'sitelink_abstract', start_populating_textbuf -->
    #     abstract text
    #   </div>            <!-- stop_populating_textbuf, pop to sitelink abstract -->
    #
    # - Sometimes Google autocorrects a query. Whenever this happens
    #   there will be a block whose English version reads "Showing
    #   results for ... <newline> Search instead for ...", and the HTML
    #   looks like
    #
    #   <span class="spell">Showing results for</span>
    #   <a class="spell" href="/search?q=google..."><b><i>google</i></b></a>
    #   <br>
    #   <span class="spell_orig"></span>
    #
    #   We collect the text inside a.spell as the suggested spelling
    #   (self.suggested_spelling).
    #
    #   Note that:
    #
    #   1. When npfr=1 (exact), there could still be an
    #      a.spell, in a block that reads (English version) "Did you mean:
    #      ...". Therefore, we only consider the query autocorrected when a
    #      meaningful .spell_orig is also present (self.autocorrected).
    #
    #   2. A few garbage display:none, empty tags related to spell
    #      appear to be always present: span#srfm.spell, a#srfl.spell,
    #      span#sifm.spell_orig, a#sifl.spell_orig. We need to exclude
    #      the ids srfm, srfl, sifm and sifl from our consideration.
    #
    # - Sometimes Google omits similar (more like duplicate) result
    #   entries. Whenever this happens there will be a notice in p#ofr. The way
    #   to unfilter is to simply add '&filter=0' to the query string.
    #
    #
    # Google News
    #
    # - Google News results differ from Google Search results in the
    #   following ways:
    #
    #   For each result, the title in the same format, but there's a
    #   metadata field in a <div> tag with class "slp", and the abstract
    #   isn't as deeply embedded: it's in a <div> tag on the same level
    #   with class "st".
    #
    #   <!-- within the scope of result_* -->
    #   <h3 class="r"></h3>  <!-- as before -->
    #   <div class="slp">    <!-- annotate as 'news_metadata', start_populating_textbuf -->
    #     ...
    #     <span>source</span>
    #     <span>-</span>     <!-- transform to ', ' -->
    #     <span>publishing time</span>
    #   </div>               <!-- stop_populating_textbuf, pop to self.metadata -->
    #   <div class="st">     <!-- annotate as 'news_abstract', start_populating_textbuf -->
    #     abstract text again with <em> markup on keywords
    #   </div>               <!-- stop_populating_textbuf, pop to self.abstract -->
    #
    #
    # Ignore List
    #
    # - As good as our result criteria might be, sometimes results of
    #   dubious value (usually from Google's value-add features) slip
    #   through. The "People also ask" feature is a good example of this
    #   type (a sample query is "VPN"; see screenshot
    #   https://i.imgur.com/yfcsoQz.png). In these cases, we may want to
    #   skip enclosing containers entirely. The ignore list feature is
    #   designed for this purpose.
    #
    #   The current ignore list is available in self.IGNORE_LIST. Each
    #   entry (called a "selector") is a dict of attribute-value
    #   pairs. Each attribute is matched verbatim to a tag's attribute,
    #   except the "class" attribute, where we test for inclusion
    #   instead (e.g. "c b a" matches "a b", just like it matches the
    #   CSS selector ".a.b"). There's also a special "attribute" -- tag,
    #   the meaning of which is obvious. A tag has to match all given
    #   attributes to be considered a match for the selector.
    #
    #   When a match is found, the tag is annotated as SCOPE_ignored,
    #   where SCOPE is the current handler scope (e.g., root, result,
    #   title, etc.), and the scope is switched to 'ignore'. All
    #   descendants of the tag are ignored. When the corresponding end
    #   tag is finally reach, the former scope is restored.
    #
    #
    # User Agent disabled (differences)
    #
    #   1. For Google News results, <div class="g"> is followed by <table> tag
    #       <div class="g">
    #           <table>
    #
    #   2. File mime type follows <div class="g">
    #       <div class="g"><span style="float:left"><span class="mime">[PDF]</span>&nbsp;</span>
    #
    #   3. News metadata (source and time) comes within a single tag
    #       <div class="slp"><span class="f">Reuters - 3 hours ago</span>
    #
    #   4. URLs are wrapped
    #       <a href="/url?q=http://...&sa=...">
    #
    #   5. URLs are quoted
    #       'https://vk.com/doc206446660_429188746%3Fhash%3D6097a8b0a41185cb90%26dl%3D03c63c1be5c02e8620'
    #
    #   6. Google Services links are returned as regular results,
    #      start with '/search?q=' but no following 'http' or 'https'
    #       <div class="g">
    #           <div>
    #               <h3 class="r"><a href="/search?q=india&...&sa=...">News for <b>india</b></a></h3>
    #
    #   7. YouTube specific results are returned within <table class="ts">
    #       e.g. search - '3 hours youtube'
    #
    #       <span class="st">
    #           <span class="f"><span class="nobr">10 Jun 2014</span> - <span class="nobr">179 min</span> -
    #               <span class="nobr">Uploaded by Meditation Relax Music</span>
    #           </span>
    #           <br><b>3 HOURS Best Relaxing Music</b> &#39;Romantic <b>Piano</b>&quot; Background <b>Music</b> for Stress ... 3:03 <b>...</b>
    #       </span>
    #
    #   8. There's no a.spell_orig when the query is autocorrected; the
    #      <a> tag (linking to the exact search) is wrapped in the
    #      span.spell_orig.

    def __init__(self, news=False):
        html.parser.HTMLParser.__init__(self)

        self.news = news

        self.autocorrected = False
        self.suggested_spelling = None
        self.filtered = False
        self.results = []

        self.index = 0
        self.textbuf = ''
        self.tag_annotations = {}

        self.set_handlers_to('root')

    # Ignore list
    IGNORE_LIST = [
        # "People also ask"
        # Sample query: VPN
        # Screenshot: https://i.imgur.com/yfcsoQz.png
        {
            'tag': 'div',
            'class': 'related-question-pair'
        },
        # We omit Google's "smart card" results (term coined by me) by
        # guarding against the 'g-blk' class (sample response: https://git.io/voJgB)
        {
            'tag': 'div',
            'class': 'g-blk'
        },
        # We also guard against "smart-card" results with `--noua` option
        {
            'tag': 'div',
            'class': 'hp-xpdbox'
        }
    ]

    # Tag handlers

    @annotate_tag
    def root_start(self, tag, attrs):
        if tag == 'div' and 'g' in self.classes(attrs):
            # Initialize result field registers
            self.title = ''
            self.url = ''
            self.abstract = ''
            self.metadata = ''  # Only used for Google News
            self.sitelinks = []

            # Guard against sitelinks, which also have titles and
            # abstracts.  In the case of news, guard against "card
            # sections" (secondary results to the same event).
            self.title_registered = False
            self.abstract_registered = False
            self.metadata_registered = False  # Only used for Google News

            self.set_handlers_to('result')
            return 'result'

        # Autocorrect
        if tag == 'span' and 'spell_orig' in self.classes(attrs) and attrs.get('id') != 'sifm':
            self.autocorrected = True
            return
        if tag == 'a' and 'spell' in self.classes(attrs) and attrs.get('id') != 'srfl':
            self.start_populating_textbuf()
            return 'spell'

        # Omitted results
        if tag == 'p' and attrs.get('id') == 'ofr':
            self.filtered = True

    @retrieve_tag_annotation
    def root_end(self, tag, annotation):
        if annotation == 'spell':
            self.stop_populating_textbuf()
            self.suggested_spelling = self.pop_textbuf()

    @annotate_tag
    def result_start(self, tag, attrs):
        if not ua and tag == 'span' and 'mime' in self.classes(attrs):
            self.start_populating_textbuf()
            return 'title_filetype'

        if not self.title_registered and tag == 'h3' and 'r' in self.classes(attrs):
            self.set_handlers_to('title')
            return 'title'

        if not self.abstract_registered and tag == 'div' and 's' in self.classes(attrs):
            self.set_handlers_to('abstract')
            return 'abstract'

        if not ua and not self.abstract_registered \
                and tag == 'span' and 'st' in self.classes(attrs):
            self.start_populating_textbuf(lambda text: text + ' ')
            return 'abstract_gservices'

        if not self.sitelinks and tag == 'table':
            if ua or (not self.news and 'ts' not in self.classes(attrs)):
                self.set_handlers_to('sitelink_table')
                return 'sitelink_table'

        if self.news:
            if not self.metadata_registered and tag == 'div' and 'slp' in self.classes(attrs):
                # Change metadata field separator from '-' to ', ' for better appearance
                if ua:
                    self.start_populating_textbuf(lambda text: ', ' if text == '-' else text)
                else:
                    self.start_populating_textbuf(lambda text:
                                                  text.replace(' -', ',', 1) if ' - ' in text else text)
                return 'news_metadata'

            if not self.abstract_registered and tag == 'div' and 'st' in self.classes(attrs):
                self.start_populating_textbuf()
                return 'news_abstract'

    @retrieve_tag_annotation
    def result_end(self, tag, annotation):
        if annotation == 'result':
            if self.url:
                self.index += 1
                result = Result(self.index, self.title, self.url, self.abstract,
                                metadata=self.metadata if self.metadata else None,
                                sitelinks=self.sitelinks)
                self.results.append(result)
            self.set_handlers_to('root')
        elif annotation == 'news_metadata':
            self.stop_populating_textbuf()
            self.metadata = self.pop_textbuf()
            self.metadata_registered = True
        elif annotation == 'news_abstract':
            self.stop_populating_textbuf()
            self.abstract = self.pop_textbuf()
            self.abstract_registered = True
        elif annotation == 'abstract_gservices':
            self.stop_populating_textbuf()
            self.abstract = self.pop_textbuf().replace('  ', ' ')
            self.abstract_registered = False

    @annotate_tag
    def title_start(self, tag, attrs):
        if ua and tag == 'span':
            # Print a space after the filetype indicator
            self.start_populating_textbuf(lambda text: text + ' ')
            return 'title_filetype'
        if tag == 'a' and 'href' in attrs:
            # Skip 'News for', 'Images for' search links
            if attrs['href'].startswith('/search'):
                return

            self.url = attrs['href']
            try:
                start = self.url.index('?q=') + len('?q=')
                end = self.url.index('&sa=', start)
                self.url = urllib.parse.unquote_plus(self.url[start:end])
            except ValueError:
                pass
            self.start_populating_textbuf()
            return 'title_link'

    @retrieve_tag_annotation
    def title_end(self, tag, annotation):
        if annotation == 'title_filetype':
            self.stop_populating_textbuf()
        elif annotation == 'title_link':
            self.stop_populating_textbuf()
            self.title = self.pop_textbuf()
            self.title_registered = True
        elif annotation == 'title':
            self.set_handlers_to('result')

    @annotate_tag
    def abstract_start(self, tag, attrs):
        if tag == 'span' and 'st' in self.classes(attrs):
            self.start_populating_textbuf()
            return 'abstract_text'

    @retrieve_tag_annotation
    def abstract_end(self, tag, annotation):
        if annotation == 'abstract_text':
            self.stop_populating_textbuf()
            self.abstract = self.pop_textbuf()
            self.abstract_registered = False
        elif annotation == 'abstract':
            self.set_handlers_to('result')

    @annotate_tag
    def sitelink_table_start(self, tag, attrs):
        if tag == 'td':
            # Initialize a new sitelink
            self.current_sitelink = Sitelink('', '', '')
            self.set_handlers_to('sitelink')
            return 'sitelink'

    @retrieve_tag_annotation
    def sitelink_table_end(self, tag, annotation):
        if annotation == 'sitelink_table':
            self.set_handlers_to('result')

    @annotate_tag
    def sitelink_start(self, tag, attrs):
        if tag == 'h3' and 'r' in self.classes(attrs):
            self.set_handlers_to('sitelink_title')
            return 'sitelink_title'
        if tag == 'div' and 'st' in self.classes(attrs):
            self.start_populating_textbuf()
            return 'sitelink_abstract'

    @retrieve_tag_annotation
    def sitelink_end(self, tag, annotation):
        if annotation == 'sitelink_abstract':
            self.stop_populating_textbuf()
            self.current_sitelink.abstract = self.pop_textbuf()
        elif annotation == 'sitelink':
            if self.current_sitelink.url:
                self.sitelinks.append(self.current_sitelink)
            self.set_handlers_to('sitelink_table')

    @annotate_tag
    def sitelink_title_start(self, tag, attrs):
        if tag == 'a' and 'href' in attrs:
            self.current_sitelink.url = attrs['href']
            try:
                start = self.current_sitelink.url.index('?q=') + len('?q=')
                end = self.current_sitelink.url.index('&sa=', start)
                self.current_sitelink.url = urllib.parse.unquote_plus(self.current_sitelink.url[start:end])
            except ValueError:
                pass
            self.start_populating_textbuf()
            return 'sitelink_title_link'

    @retrieve_tag_annotation
    def sitelink_title_end(self, tag, annotation):
        if annotation == 'sitelink_title_link':
            self.stop_populating_textbuf()
            self.current_sitelink.title = self.pop_textbuf()
        elif annotation == 'sitelink_title':
            self.set_handlers_to('sitelink')

    # Generic methods

    # Set handle_starttag to SCOPE_start, and handle_endtag to SCOPE_end.
    def set_handlers_to(self, scope):
        self.handle_starttag = getattr(self, scope + '_start')
        self.handle_endtag = getattr(self, scope + '_end')

    def insert_annotation(self, tag, annotation):
        if tag not in self.tag_annotations:
            self.tag_annotations[tag] = []
        self.tag_annotations[tag].append(annotation)

    @annotate_tag
    def ignore_start(self, tag, attrs):
        pass

    @retrieve_tag_annotation
    def ignore_end(self, tag, annotation):
        if annotation and annotation.endswith('_ignored'):
            # Strip '-ignore' suffix from annotation to obtain the outer
            # context name.
            context = annotation[:-8]
            self.set_handlers_to(context)

    def start_populating_textbuf(self, data_transformer=None):
        if data_transformer is None:
            # Record data verbatim
            self.handle_data = self.record_data
        else:
            def record_transformed_data(data):
                self.textbuf += data_transformer(data)

            self.handle_data = record_transformed_data

        self.handle_entityref = self.record_entityref
        self.handle_charref = self.record_charref

    def pop_textbuf(self):
        text = self.textbuf
        self.textbuf = ''
        return text

    def stop_populating_textbuf(self):
        self.handle_data = lambda data: None
        self.handle_entityref = lambda ref: None
        self.handle_charref = lambda ref: None

    def record_data(self, data):
        self.textbuf += data

    def record_entityref(self, ref):
        try:
            self.textbuf += chr(html.entities.name2codepoint[ref])
        except KeyError:
            # Entity name not found; most likely rather sloppy HTML
            # where a literal ampersand is not escaped; For instance,
            # the HTML response returned by
            #
            #     googler -c au -l ko expected
            #
            # contains the following tag
            #
            #     <p class="_e4b"><a href="...">expected market return s&p 500</a></p>
            #
            # where &p is interpreted by HTMLParser as an entity (this
            # behaviour seems to be specific to Python 2.7).
            self.textbuf += '&' + ref

    def record_charref(self, ref):
        if ref.startswith('x'):
            char = chr(int(ref[1:], 16))
        else:
            char = chr(int(ref))
        self.textbuf += char

    @staticmethod
    def classes(attrs):
        """Get tag's classes from its attribute dict."""
        return attrs.get('class', '').split()


class Sitelink(object):
    """Container for a sitelink."""

    def __init__(self, title, url, abstract):
        self.title = title
        self.url = url
        self.abstract = abstract
        self.index = ''


Colors = collections.namedtuple('Colors', 'index, title, url, metadata, abstract, prompt, reset')


class Result(object):
    """
    Container for one search result, with output helpers.

    Parameters
    ----------
    index : int or str
    title : str
    url : str
    abstract : str
    metadata : str, optional
        Only applicable to Google News results, with publisher name and
        publishing time.
    sitelinks : list, optional
        List of ``SiteLink`` objects.

    Attributes
    ----------
    index : str
    title : str
    url : str
    abstract : str
    metadata : str or None
    sitelinks : list

    Class Variables
    ---------------
    colors : str

    Methods
    -------
    print()
    jsonizable_object()
    urltable()

    """

    # Class variables
    colors = None

    def __init__(self, index, title, url, abstract, metadata=None, sitelinks=None):
        index = str(index)
        self.index = index
        self.title = title
        self.url = url
        self.abstract = abstract
        self.metadata = metadata
        self.sitelinks = [] if sitelinks is None else sitelinks

        self._urltable = {index: url}
        subindex = 'a'
        for sitelink in sitelinks:
            fullindex = index + subindex
            sitelink.index = fullindex
            self._urltable[fullindex] = sitelink.url
            subindex = chr(ord(subindex) + 1)

    def _print_title_and_url(self, index, title, url, indent=0):
        colors = self.colors

        # Pad index and url with `indent` number of spaces
        index = ' ' * indent + str(index)
        url = ' ' * indent + url
        if colors:
            print(colors.index + index + colors.reset, end='')
            print(' ' + colors.title + title + colors.reset)
            print(colors.url + url + colors.reset)
        else:
            print(' %s %s\n%s' % (index, title, url))

    def _print_metadata_and_abstract(self, abstract, metadata=None, indent=0):
        colors = self.colors
        try:
            columns, _ = os.get_terminal_size()
        except OSError:
            columns = 0

        if metadata:
            if colors:
                print(colors.metadata + metadata + colors.reset)
            else:
                print(metadata)

        if colors:
            print(colors.abstract, end='')
        if columns > indent + 1:
            # Try to fill to columns
            fillwidth = columns - indent - 1
            for line in textwrap.wrap(abstract.replace('\n', ''), width=fillwidth):
                print('%s%s' % (' ' * indent, line))
            print('')
        else:
            print('%s\n' % abstract.replace('\n', ' '))
        if colors:
            print(colors.reset, end='')

    def print(self):
        """Print the result entry."""
        self._print_title_and_url(self.index, self.title, self.url)
        self._print_metadata_and_abstract(self.abstract, metadata=self.metadata)

        for sitelink in self.sitelinks:
            self._print_title_and_url(sitelink.index, sitelink.title, sitelink.url, indent=4)
            self._print_metadata_and_abstract(sitelink.abstract, indent=4)

    def jsonizable_object(self):
        """Return a JSON-serializable dict representing the result entry."""
        obj = {
            'title': self.title,
            'url': self.url,
            'abstract': self.abstract
        }
        if self.metadata:
            obj['metadata'] = self.metadata
        if self.sitelinks:
            obj['sitelinks'] = [sitelink.__dict__ for sitelink in self.sitelinks]
        return obj

    def urltable(self):
        """Return a index-to-URL table for the current result.

        Normally, the table contains only a single entry, but when the result
        contains sitelinks, all sitelinks are included in this table.

        Returns
        -------
        dict
            A dict mapping indices (strs) to URLs (also strs). Indices of
            sitelinks are the original index appended by lowercase letters a,
            b, c, etc.

        """
        return self._urltable


class GooglerCmdException(Exception):
    pass


class NoKeywordsException(GooglerCmdException):
    pass


def require_keywords(method):
    # Require keywords to be set before we run a GooglerCmd method. If
    # no keywords have been set, raise a NoKeywordsException.
    @functools.wraps(method)
    def enforced_method(self, *args, **kwargs):
        if not self.keywords:
            raise NoKeywordsException('No keywords.')
        method(self, *args, **kwargs)

    return enforced_method


def no_argument(method):
    # Normalize a do_* method of GooglerCmd that takes no argument to
    # one that takes an arg, but issue a warning when an nonempty
    # argument is given.
    @functools.wraps(method)
    def enforced_method(self, arg):
        if arg:
            method_name = arg.__name__
            command_name = method_name[3:] if method_name.startswith('do_') else method_name
            logger.warning("Argument to the '%s' command ignored.", command_name)
        method(self)

    return enforced_method


class GooglerCmd(object):
    """
    Command line interpreter and executor class for googler.

    Inspired by PSL cmd.Cmd.

    Parameters
    ----------
    opts : argparse.Namespace
        Options and/or arguments.

    Attributes
    ----------
    options : argparse.Namespace
        Options that are currently in effect. Read-only attribute.
    keywords : str or list or strs
        Current keywords. Read-only attribute

    Methods
    -------
    fetch()
    display_results(prelude='\n', json_output=False)
    fetch_and_display(prelude='\n', json_output=False, interactive=True)
    read_next_command()
    help()
    cmdloop()
    """

    # Class variables
    colors = None

    def __init__(self, opts):
        super().__init__()

        self._opts = opts

        self._google_url = GoogleUrl(opts)
        proxy = opts.proxy if hasattr(opts, 'proxy') else None
        self._conn = GoogleConnection(self._google_url.hostname, proxy=proxy,
                                      notweak=opts.notweak)
        atexit.register(self._conn.close)

        self.results = []
        self._autocorrected_to = None
        self._results_filtered = False
        self._urltable = {}

    @property
    def options(self):
        """Current options."""
        return self._opts

    @property
    def keywords(self):
        """Current keywords."""
        return self._google_url.keywords

    @require_keywords
    def fetch(self):
        """Fetch a page and parse for results.

        Results are stored in ``self.results``.

        Raises
        ------
        GoogleConnectionError

        See Also
        --------
        fetch_and_display

        """
        # This method also sets self._results_filtered and
        # self._urltable.
        page = self._conn.fetch_page(self._google_url.relative())

        if logger.isEnabledFor(logging.DEBUG):
            import tempfile
            fd, tmpfile = tempfile.mkstemp(prefix='googler-response-')
            os.close(fd)
            with open(tmpfile, 'w', encoding='utf-8') as fp:
                fp.write(page)
            logger.debug("Response body written to '%s'.", tmpfile)

        parser = GoogleParser(news=self._google_url.news)
        parser.feed(page)

        self.results = parser.results
        self._autocorrected_to = parser.suggested_spelling if parser.autocorrected else None
        self._results_filtered = parser.filtered
        self._urltable = {}
        for r in self.results:
            self._urltable.update(r.urltable())

    @require_keywords
    def display_results(self, prelude='\n', json_output=False):
        """Display results stored in ``self.results``.

        Parameters
        ----------
        See `fetch_and_display`.

        """
        if json_output:
            # JSON output
            import json
            results_object = [r.jsonizable_object() for r in self.results]
            print(json.dumps(results_object, indent=2, sort_keys=True, ensure_ascii=False))
        else:
            # Regular output
            if not self.results:
                print('No results.', file=sys.stderr)
            else:
                sys.stderr.write(prelude)
                for r in self.results:
                    r.print()

    @require_keywords
    def fetch_and_display(self, prelude='\n', json_output=False, interactive=True):
        """Fetch a page and display results.

        Results are stored in ``self.results``.

        Parameters
        ----------
        prelude : str, optional
            A string that is written to stderr before showing actual results,
            usually serving as a separator. Default is an empty line.
        json_output : bool, optional
            Whether to dump results in JSON format. Default is False.
        interactive : bool, optional
            Whether to show contextual instructions, when e.g. Google
            has filtered the results. Default is True.

        Raises
        ------
        GoogleConnectionError

        See Also
        --------
        fetch
        display_results

        """
        self.fetch()
        colors = self.colors
        if self._autocorrected_to:
            if colors:
                # Underline the keywords
                autocorrected_to = '\x1b[4m' + self._autocorrected_to + '\x1b[24m'
            else:
                autocorrected_to = self._autocorrected_to
            autocorrect_info = ('Showing results for %s; enter "x" for an exact search.' %
                                autocorrected_to)
            printerr('')
            if colors:
                printerr(colors.prompt + autocorrect_info + colors.reset)
            else:
                printerr('** ' + autocorrect_info)
        self.display_results(prelude=prelude, json_output=json_output)
        if self._results_filtered:
            unfilter_info = 'Enter "unfilter" to show similar results Google omitted.'
            if colors:
                printerr(colors.prompt + unfilter_info + colors.reset)
            else:
                printerr('** ' + unfilter_info)
            printerr('')

    def read_next_command(self):
        """Show omniprompt and read user command line.

        Command line is always stripped, and each consecutive group of
        whitespace is replaced with a single space character. If the
        command line is empty after stripping, when ignore it and keep
        reading. Exit with status 0 if we get EOF or an empty line
        (pre-strip, that is, a raw <enter>) twice in a row.

        The new command line (non-empty) is stored in ``self.cmd``.

        """
        colors = self.colors
        message = 'googler (? for help)'
        prompt = (colors.prompt + message + colors.reset + ' ') if colors else (message + ': ')
        enter_count = 0
        while True:
            try:
                cmd = input(prompt)
            except EOFError:
                sys.exit(0)

            if not cmd:
                enter_count += 1
                if enter_count == 2:
                    # Double <enter>
                    sys.exit(0)
            else:
                enter_count = 0

            cmd = ' '.join(cmd.split())
            if cmd:
                self.cmd = cmd
                break

    @staticmethod
    def help():
        GooglerArgumentParser.print_omniprompt_help(sys.stderr)
        printerr('')

    @require_keywords
    @no_argument
    def do_first(self):
        self._google_url.first_page()
        self.fetch_and_display()

    def do_google(self, arg):
        # Update keywords and reconstruct URL
        self._opts.keywords = arg
        self._google_url = GoogleUrl(self._opts)
        self.fetch_and_display()

    @require_keywords
    @no_argument
    def do_next(self):
        # If > 5 results are being fetched each time,
        # block next when no parsed results in current fetch
        if not self.results and self._google_url._num > 5:
            printerr('No results.')
        else:
            self._google_url.next_page()
            self.fetch_and_display()

    @require_keywords
    def do_open(self, *args):
        if not args:
            open_url(self._google_url.full())
            return

        for nav in args:
            if nav == 'a':
                for key, value in sorted(self._urltable.items()):
                    open_url(self._urltable[key])
            elif nav in self._urltable:
                open_url(self._urltable[nav])
            else:
                printerr("Invalid index %s." % nav)

    @require_keywords
    @no_argument
    def do_previous(self):
        try:
            self._google_url.prev_page()
        except ValueError as e:
            print(e, file=sys.stderr)
            return

        self.fetch_and_display()

    @require_keywords
    @no_argument
    def do_exact(self):
        # Reset start to 0 when exact is applied.
        self._google_url.update(start=0, exact=True)
        self.fetch_and_display()

    @require_keywords
    @no_argument
    def do_unfilter(self):
        # Reset start to 0 when unfilter is applied.
        self._google_url.update(start=0)
        self._google_url.set_queries(filter=0)
        self.fetch_and_display()

    def cmdloop(self):
        """Run REPL."""
        if self.keywords:
            self.fetch_and_display()
        else:
            printerr('Please initiate a query.')

        while True:
            self.read_next_command()
            # TODO: Automatic dispatcher
            #
            # We can't write a dispatcher for now because that could
            # change behaviour of the prompt. However, we have already
            # laid a lot of ground work for the dispatcher, e.g., the
            # `no_argument' decorator.
            try:
                cmd = self.cmd
                if cmd == 'f':
                    self.do_first('')
                elif cmd.startswith('g '):
                    self.do_google(cmd[2:])
                elif cmd == 'n':
                    self.do_next('')
                elif cmd == 'o':
                    self.do_open()
                elif cmd.startswith('o '):
                    self.do_open(*cmd[2:].split())
                elif cmd == 'p':
                    self.do_previous('')
                elif cmd == 'q':
                    break
                elif cmd == 'x':
                    self.do_exact('')
                elif cmd == 'unfilter':
                    self.do_unfilter('')
                elif cmd == '?':
                    self.help()
                elif cmd in self._urltable:
                    open_url(self._urltable[cmd])
                elif self.keywords and cmd.isdigit() and int(cmd) < 100:
                    printerr('Index out of bound. To search for the number, use g.')
                else:
                    self.do_google(cmd)
            except NoKeywordsException:
                printerr('Initiate a query first.')


class GooglerArgumentParser(argparse.ArgumentParser):
    """Custom argument parser for googler."""

    # Print omniprompt help
    @staticmethod
    def print_omniprompt_help(file=None):
        file = sys.stderr if file is None else file
        file.write(textwrap.dedent("""
        omniprompt keys:
          n, p                  fetch the next or previous set of search results
          index                 open the result corresponding to index in browser
          f                     jump to the first page
          o [index ...] [a]     open space-separated result indices, or all, in browser
                                open the current search in browser, if no arguments
          g keywords            new Google search for 'keywords' with original options
                                should be used to search omniprompt keys and indices
          q, ^D, double Enter   exit googler
          ?                     show omniprompt help
          *                     other inputs issue a new search with original options
        """))

    # Print information on googler
    @staticmethod
    def print_general_info(file=None):
        file = sys.stderr if file is None else file
        file.write(textwrap.dedent("""
        Version %s
        Copyright © 2008 Henri Hakkinen
        Copyright © 2015-2017 Arun Prakash Jana <engineerarun@gmail.com>
        Zhiming Wang <zmwangx@gmail.com>
        License: GPLv3
        Webpage: https://github.com/jarun/googler
        """ % _VERSION_))

    # Augment print_help to print more than synopsis and options
    def print_help(self, file=None):
        super().print_help(file)
        self.print_omniprompt_help(file)
        self.print_general_info(file)

    # Automatically print full help text on error
    def error(self, message):
        sys.stderr.write('%s: error: %s\n\n' % (self.prog, message))
        self.print_help(sys.stderr)
        self.exit(2)

    # Type guards
    @staticmethod
    def positive_int(arg):
        """Try to convert a string into a positive integer."""
        try:
            n = int(arg)
            assert n > 0
            return n
        except (ValueError, AssertionError):
            raise argparse.ArgumentTypeError('%s is not a positive integer' % arg)

    @staticmethod
    def nonnegative_int(arg):
        """Try to convert a string into a nonnegative integer."""
        try:
            n = int(arg)
            assert n >= 0
            return n
        except (ValueError, AssertionError):
            raise argparse.ArgumentTypeError('%s is not a non-negative integer' % arg)

    @staticmethod
    def is_duration(arg):
        """Check if a string is a valid duration accepted by Google.

        A valid duration is of the form dNUM, where d is a single letter h
        (hour), d (day), w (week), m (month), or y (year), and NUM is a
        non-negative integer.
        """
        try:
            if arg[0] not in ('h', 'd', 'w', 'm', 'y') or int(arg[1:]) < 0:
                raise ValueError
        except (TypeError, IndexError, ValueError):
            raise argparse.ArgumentTypeError('%s is not a valid duration' % arg)
        return arg

    @staticmethod
    def is_colorstr(arg):
        """Check if a string is a valid color string."""
        try:
            assert len(arg) == 6
            for c in arg:
                assert c in COLORMAP
        except AssertionError:
            raise argparse.ArgumentTypeError('%s is not a valid color string' % arg)
        return arg


# Self-upgrade mechanism

def system_is_windows():
    """Checks if the underlying system is Windows (Cygwin included)."""
    return sys.platform in {'win32', 'cygwin'}


def download_latest_googler(include_git=False):
    """Download latest googler to a temp file.

    By default, the latest released version is downloaded, but if
    `include_git` is specified, then the latest git master is downloaded
    instead.

    Parameters
    ----------
    include_git : bool, optional
        Download from git master. Default is False.

    Returns
    -------
    (git_ref, path): tuple
         A tuple containing the git reference (either name of the latest
         tag or SHA of the latest commit) and path to the downloaded
         file.

    """
    import urllib.request

    if include_git:
        # Get SHA of latest commit on master
        request = urllib.request.Request('%s/commits/master' % API_REPO_BASE,
                                         headers={'Accept': 'application/vnd.github.v3.sha'})
        response = urllib.request.urlopen(request)
        if response.status != 200:
            raise http.client.HTTPException(response.reason)
        git_ref = response.read().decode('utf-8')
    else:
        # Get name of latest tag
        request = urllib.request.Request('%s/releases?per_page=1' % API_REPO_BASE,
                                         headers={'Accept': 'application/vnd.github.v3+json'})
        response = urllib.request.urlopen(request)
        if response.status != 200:
            raise http.client.HTTPException(response.reason)
        import json
        git_ref = json.loads(response.read().decode('utf-8'))[0]['tag_name']

    # Download googler to a tempfile
    googler_download_url = '%s/%s/googler' % (RAW_DOWNLOAD_REPO_BASE, git_ref)
    printerr('Downloading %s' % googler_download_url)
    request = urllib.request.Request(googler_download_url,
                                     headers={'Accept-Encoding': 'gzip'})
    import tempfile
    fd, path = tempfile.mkstemp()
    atexit.register(lambda: os.remove(path) if os.path.exists(path) else None)
    os.close(fd)
    with open(path, 'wb') as fp:
        with urllib.request.urlopen(request) as response:
            if response.status != 200:
                raise http.client.HTTPException(response.reason)
            payload = response.read()
            try:
                fp.write(gzip.decompress(payload))
            except OSError:
                fp.write(payload)
    return git_ref, path


def self_replace(path):
    """Replace the current script with a specified file.

    Both paths (the specified path and path to the current script) are
    resolved to absolute, symlink-free paths. Upon replacement, the
    owner and mode signatures of the current script are preserved. The
    caller needs to have the necessary permissions.

    Replacement won't happen if the specified file is the same
    (content-wise) as the current script.

    Parameters
    ----------
    path : str
        Path to the replacement file.

    Returns
    -------
    bool
        True if replaced, False if skipped (specified file is the same
        as the current script).

    """
    if system_is_windows():
        raise NotImplementedError('Self upgrade not supported on Windows.')

    import filecmp
    import shutil

    path = os.path.realpath(path)
    self_path = os.path.realpath(__file__)

    if filecmp.cmp(path, self_path):
        return False

    self_stat = os.stat(self_path)
    os.chown(path, self_stat.st_uid, self_stat.st_gid)
    os.chmod(path, self_stat.st_mode)

    shutil.move(path, self_path)
    return True


def self_upgrade(include_git=False):
    """Perform in-place self-upgrade.

    Parameters
    ----------
    include_git : bool, optional
        See `download_latest_googler`. Default is False.

    """
    git_ref, path = download_latest_googler(include_git=include_git)
    if self_replace(path):
        printerr('Upgraded to %s.' % git_ref)
    else:
        printerr('Already up to date.')


# Miscellaneous functions

def https_proxy_from_environment():
    if 'https_proxy' not in os.environ:
        return None
    proxy = urllib.parse.urlparse(os.environ['https_proxy']).netloc
    # urlparse recognizes a netloc only if it is introduced by '//'
    # (https://docs.python.org/3/library/urllib.parse.html#urllib.parse.urlparse).
    # This means a bare host:port like 'localhost:8118' won't be
    # recognized. In that case, try again with the '//' prefix.
    if not proxy:
        proxy = urllib.parse.urlparse('//' + os.environ['https_proxy']).netloc
    return proxy if proxy else None


def parse_args(args=None, namespace=None):
    """Parse googler arguments/options.

    Parameters
    ----------
    args : list, optional
        Arguments to parse. Default is ``sys.argv``.
    namespace : argparse.Namespace
        Namespace to write to. Default is a new namespace.

    Returns
    -------
    argparse.Namespace
        Namespace with parsed arguments / options.

    """

    colorstr_env = os.getenv('GOOGLER_COLORS')

    argparser = GooglerArgumentParser(description='Google from the command-line.')
    addarg = argparser.add_argument
    addarg('-s', '--start', type=argparser.nonnegative_int, default=0,
           metavar='N', help='start at the Nth result')
    addarg('-n', '--count', dest='num', type=argparser.positive_int,
           default=10, metavar='N', help='show N results (default 10)')
    addarg('-N', '--news', action='store_true',
           help='show results from news section')
    addarg('-c', '--tld', metavar='TLD',
           help="""country-specific search with top-level domain .TLD, e.g., 'in'
           for India. Ref: https://en.wikipedia.org/wiki/List_of_Google_domains""")
    addarg('-l', '--lang', metavar='LANG', help='display in language LANG')
    addarg('-x', '--exact', action='store_true',
           help='disable automatic spelling correction')
    addarg('-C', '--nocolor', dest='colorize', action='store_false',
           help='disable color output')
    addarg('--colors', dest='colorstr', type=argparser.is_colorstr,
           default=colorstr_env if colorstr_env else 'GKlgxy', metavar='COLORS',
           help='set output colors (see man page for details)')
    addarg('-j', '--first', '--lucky', dest='lucky', action='store_true',
           help='open the first result in web browser and exit')
    addarg('-t', '--time', dest='duration', type=argparser.is_duration,
           metavar='dN', help='time limit search '
           '[h5 (5 hrs), d5 (5 days), w5 (5 weeks), m5 (5 months), y5 (5 years)]')
    addarg('-w', '--site', metavar='SITE', help='search a site using Google')
    addarg('-p', '--proxy', default=https_proxy_from_environment(),
           help='tunnel traffic through an HTTPS proxy (HOST:PORT)')
    addarg('--noua', action='store_true', help='disable user agent')
    addarg('--notweak', action='store_true',
           help='disable TCP optimizations and forced TLS 1.2')
    addarg('--json', action='store_true',
           help='output in JSON format; implies --exact and --noprompt')
    addarg('--show-browser-logs', action='store_true',
           help='do not suppress browser output (stdout and stderr)')
    addarg('--np', '--noprompt', dest='noninteractive', action='store_true',
           help='search and exit, do not prompt')
    addarg('keywords', nargs='*', metavar='KEYWORD', help='search keywords')
    if ENABLE_SELF_UPGRADE_MECHANISM and not system_is_windows():
        addarg('-u', '--upgrade', action='store_true',
               help='perform in-place self-upgrade')
        addarg('--include-git', action='store_true',
               help='when used with --upgrade, upgrade to latest git master')
    addarg('-v', '--version', action='version', version=_VERSION_)
    addarg('-d', '--debug', action='store_true', help='enable debugging')

    return argparser.parse_args(args, namespace)


def main():
    global ua

    try:
        opts = parse_args()

        # --json implies --exact
        if opts.json:
            opts.exact = True

        # Set logging level
        if opts.debug:
            logger.setLevel(logging.DEBUG)
            logger.debug('Version %s', _VERSION_)

        # Handle self-upgrade
        if hasattr(opts, 'upgrade') and opts.upgrade:
            self_upgrade(include_git=opts.include_git)
            sys.exit(0)

        check_stdout_encoding()

        if opts.keywords:
            try:
                # Add cmdline args to readline history
                readline.add_history(' '.join(opts.keywords))
            except Exception:
                pass

        # Set colors
        if opts.colorize:
            colors = Colors(*[COLORMAP[c] for c in opts.colorstr], reset=COLORMAP['x'])
        else:
            colors = None
        Result.colors = colors
        GooglerCmd.colors = colors

        # Handle browser output suppression
        if opts.show_browser_logs:
            open_url.suppress_browser_output = False

        if opts.noua:
            logger.debug('User Agent is disabled')
            ua = False

        repl = GooglerCmd(opts)

        if opts.json or opts.lucky or opts.noninteractive:
            # Non-interactive mode
            repl.fetch()
            if opts.lucky:
                if repl.results:
                    open_url(repl.results[0].url)
                else:
                    print('No results.', file=sys.stderr)
            else:
                repl.display_results(prelude='', json_output=opts.json)
            sys.exit(0)
        else:
            # Interactive mode
            repl.cmdloop()
    except Exception as e:
        # With debugging on, let the exception through for a traceback;
        # otherwise, only print the exception error message.
        if logger.isEnabledFor(logging.DEBUG):
            raise
        else:
            logger.error(e)
            sys.exit(1)

if __name__ == '__main__':
    main()