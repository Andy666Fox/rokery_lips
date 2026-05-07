import random
import time

alphabet = [
    "ALPHA", "BRAVO", "CHARLIE", "DELTA", "ECHO", "FOXTROT", "GOLF",
    "HOTEL", "INDIA", "JULIETT", "KILO", "LIMA", "MIKE", "NOVEMBER",
    "OSCAR", "PAPA", "QUEBEC", "ROMEO", "SIERRA", "TANGO", "UNIFORM",
    "VICTOR", "WHISKEY", "X-RAY", "YANKEE", "ZULU"
]

def elliptic_value():
    coeffs = [random.random() * int(time.time())**0.5 for _ in range(6)]
    point = random.random()
    letters = [random.choice(alphabet) for x in range(3)]
    a = 1
    b = coeffs[0]*point + coeffs[2]
    c = (point**3 + coeffs[1]*point**2 + coeffs[3]*point + coeffs[5]) * -1
    D = b**2 -4*a*c 
    value = (-b + abs(D)**0.5) / 2*a
    result = '_'.join(letters) + '_' + str(value.hex())[4:-3]
    return result

