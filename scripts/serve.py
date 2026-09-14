"""Loopback-only launcher. Bootstrap credentials must never reach the web process."""
import argparse
import os
import socket
import sys


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--port',type=int,default=int(os.getenv('EQA_WEB_PORT','8010')))
    args=p.parse_args()
    try:
        with socket.socket() as s:
            s.bind(('127.0.0.1',args.port))
    except OSError:
        sys.exit(f'Port {args.port} is occupied. Set EQA_WEB_PORT or --port; existing service was not stopped.')
    if os.getenv('EQA_BOOTSTRAP_PASSWORD'):
        sys.exit('Bootstrap credentials detected: start with only .local/runtime.env.')
    import uvicorn
    uvicorn.run('enterprise_query.api:app',host='127.0.0.1',port=args.port)


if __name__=='__main__':
    main()
