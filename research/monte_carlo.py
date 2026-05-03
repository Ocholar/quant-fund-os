import numpy as np

def monte_carlo(mu=0.0002, sigma=0.02, steps=252, sims=1000):
    shocks = np.random.normal(mu, sigma, (sims, steps))
    return np.cumprod(1 + shocks, axis=1)
