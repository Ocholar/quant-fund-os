class OnlineLearner:
    def __init__(self):
        self.samples = 0
        self.mean_reward = 0.0

    def update(self, reward: float):
        self.samples += 1
        self.mean_reward += (reward - self.mean_reward) / self.samples
        return self.mean_reward
