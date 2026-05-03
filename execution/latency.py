import random

def simulate_latency_ms(base=30, jitter=80):
    return base + random.randint(0, jitter)
