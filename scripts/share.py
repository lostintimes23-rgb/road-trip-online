#!/usr/bin/env python3
"""Puts a running serve.py on the internet over HTTPS, for a few people to join.

usage: share.py [port] [--cloudflared <path>] [--dry-run]

serve.py binds 127.0.0.1 by default, which is what this wants: the tunnel client runs on the same
machine, so the game is never exposed on the local network, only through the URL the tunnel prints.
A tunnel also gives the page a secure context and wss:// for /online/socket, which is what a
browser on someone else's machine needs (the page switches to wss: whenever the page is https).

What the URL gives away: everything serve.py serves, which is the build and the disc image, plus
sign-up on the online side. Treat the URL as a password: hand it only to people you trust, stop the
tunnel when they are done, and start a new one rather than reusing it. A quick tunnel is ephemeral
and changes every run; it is for testing with friends, not for a service.
"""

import os
import re
import shutil
import socket
import subprocess
import sys
import threading

URL_LINE = re.compile(r'https://[A-Za-z0-9-]+\.trycloudflare\.com')
WINDOWS_DEFAULT = r'C:\Program Files (x86)\cloudflared\cloudflared.exe'


def find_cloudflared(explicit):
    for candidate in (explicit, os.environ.get('CLOUDFLARED'), shutil.which('cloudflared'),
                      WINDOWS_DEFAULT):
        if candidate and os.path.isfile(candidate):
            return candidate
    return None


def port_answers(port):
    with socket.socket() as probe:
        probe.settimeout(1.0)
        return probe.connect_ex(('127.0.0.1', port)) == 0


def main(argv):
    port, binary, dry = 8620, None, False
    args = list(argv[1:])
    while args:
        arg = args.pop(0)
        if arg == '--dry-run':
            dry = True
        elif arg == '--cloudflared':
            binary = args.pop(0) if args else None
        elif arg.isdigit():
            port = int(arg)
        else:
            print('usage: share.py [port] [--cloudflared <path>] [--dry-run]')
            return 2
    binary = find_cloudflared(binary)
    command = [binary or 'cloudflared', 'tunnel', '--url', 'http://127.0.0.1:%d' % port, '--no-autoupdate']
    if binary is None:
        print('cloudflared is not installed. Get it from https://developers.cloudflare.com/cloudflare-one/'
              'connections/connect-networks/downloads/ (a single executable; no account needed for a '
              'quick tunnel on Windows: cloudflared-windows-amd64.exe), or try ngrok http %d.' % port)
        return 2
    print('tunnel:%s' % ' '.join(command))
    if dry:
        return 0
    if not port_answers(port):
        print('nothing is listening on 127.0.0.1:%d: start scripts/serve.py first.' % port)
        return 1
    try:
        child = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 text=True, encoding='utf-8', errors='replace', bufsize=1)
    except OSError as error:
        print('could not run cloudflared: %s' % error)
        return 1
    public, seen = None, []
    started = threading.Event()

    def watch():
        nonlocal public
        for line in child.stdout:
            line = line.rstrip()
            seen.append(line)
            if public is None:
                match = URL_LINE.search(line)
                if match:
                    public = match.group(0)
                    started.set()
                    print('')
                    print('Serving http://127.0.0.1:%d to the internet:' % port)
                    print('  page   %s/' % public)
                    print('  socket %s/online/socket' % public.replace('https://', 'wss://'))
                    print('')
                    print('Anyone with that URL can download whatever serve.py serves - the build and')
                    print('the disc image - and create accounts on it. Give it only to people you')
                    print('trust, stop this when they are done, and do not publish it. The URL is')
                    print('new every run; it is a test tunnel, not hosting.')
                    print('')
            elif 'ERR' in line or 'WARN' in line:
                print('[cloudflared] %s' % line[:200])
            if len(seen) > 200:
                del seen[:100]
        started.set()

    reader = threading.Thread(target=watch, daemon=True)
    reader.start()
    started.wait(30)
    if public is None:
        child.terminate()
        print('cloudflared did not give a URL. Last lines:')
        for line in seen[-8:]:
            print('  ' + line[:200])
        return 1
    print('press Ctrl+C to stop sharing.')
    try:
        child.wait()
    except KeyboardInterrupt:
        pass
    finally:
        # However this ends, the tunnel goes with it: nothing is left listening on the internet.
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(10)
            except subprocess.TimeoutExpired:
                child.kill()
        print('stopped sharing.')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
