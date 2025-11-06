#!/usr/bin/env python3
"""
Simple PlantUML server renderer: reads a .puml file, encodes it and fetches PNG from plantuml.com
Usage: python tools/plantuml_render_server.py input.puml output.png
"""
import sys
import zlib
import urllib.request

ENC = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz-_'

def encode6bit(b):
    return ENC[b]

def append3bytes(b1, b2, b3):
    c1 = b1 >> 2
    c2 = ((b1 & 0x3) << 4) | (b2 >> 4)
    c3 = ((b2 & 0xF) << 2) | (b3 >> 6)
    c4 = b3 & 0x3F
    return encode6bit(c1) + encode6bit(c2) + encode6bit(c3) + encode6bit(c4)

def plantuml_encode(data: bytes) -> str:
    # raw deflate
    compressor = zlib.compressobj(level=9, wbits=-15)
    compressed = compressor.compress(data) + compressor.flush()
    res = []
    for i in range(0, len(compressed), 3):
        b1 = compressed[i]
        b2 = compressed[i+1] if i+1 < len(compressed) else 0
        b3 = compressed[i+2] if i+2 < len(compressed) else 0
        res.append(append3bytes(b1, b2, b3))
    return ''.join(res)


def render(input_path, output_path):
    txt = open(input_path, 'rb').read()
    encoded = plantuml_encode(txt)
    url = f'http://www.plantuml.com/plantuml/png/{encoded}'
    # fetch
    req = urllib.request.Request(url, headers={'User-Agent': 'plantuml-renderer/1.0'})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = resp.read()
        open(output_path, 'wb').write(data)
    print('OK')

if __name__ == '__main__':
    if len(sys.argv) < 3:
        print('Usage: plantuml_render_server.py input.puml output.png')
        sys.exit(2)
    render(sys.argv[1], sys.argv[2])
