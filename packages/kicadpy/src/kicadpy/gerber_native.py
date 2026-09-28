"""Adapt KiCad X2 multi-quadrant arcs to the independent linear reader.

Only absolute metric 4.6 coordinates are accepted. Chord error is <= 1 um.
The shipped Gerber files are never rewritten.
"""
import math
import re


def linearize(text):
    if '%FSLAX46Y46*%' not in text or '%MOMM*%' not in text:
        raise ValueError('native Gerber verifier requires absolute metric 4.6 coordinates')
    x = y = 0
    mode, multi = 'G01', False
    output = []
    for line in text.splitlines():
        if line == 'G74*':
            raise ValueError('single-quadrant Gerber arcs unsupported')
        if line == 'G75*':
            multi = True
        match = re.match(r'^(G0[123])', line)
        if match:
            mode = match[1]
            line = line[3:]
            if line == '*':
                output.append('G01*')
                continue
        coord = re.fullmatch(r'(?:X([+-]?\d+))?(?:Y([+-]?\d+))?(?:I([+-]?\d+))?(?:J([+-]?\d+))?D0?([123])\*', line)
        if not coord:
            output.append(line)
            continue
        nx, ny = (int(coord[i]) if coord[i] is not None else old for i,old in [(1,x),(2,y)])
        if mode != 'G01' and coord[5] == '1':
            if not multi:
                raise ValueError('arc before G75')
            cx, cy = x + int(coord[3] or 0), y + int(coord[4] or 0)
            radius = math.hypot(x-cx,y-cy)
            if radius <= 0 or abs(math.hypot(nx-cx,ny-cy)-radius) > 10:
                raise ValueError('invalid Gerber arc radius')
            a, b = math.atan2(y-cy,x-cx), math.atan2(ny-cy,nx-cx)
            sweep = (b-a) % (2*math.pi) if mode == 'G03' else -((a-b) % (2*math.pi))
            if nx == x and ny == y:
                sweep = 2*math.pi if mode == 'G03' else -2*math.pi
            step = 2*math.acos(max(-1, 1-min(1000/radius,1)))
            count = max(1, math.ceil(abs(sweep)/step))
            if count > 100000:
                raise ValueError('Gerber arc too large')
            output.append('G01*')
            for j in range(1,count+1):
                px, py = (nx,ny) if j == count else (round(cx+radius*math.cos(a+sweep*j/count)), round(cy+radius*math.sin(a+sweep*j/count)))
                output.append(f'X{px}Y{py}D01*')
        else:
            output.append(line)
        x,y = nx,ny
    return '\n'.join(output)
