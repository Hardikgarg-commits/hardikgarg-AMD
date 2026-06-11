import logging

import numpy as np
import random

logger = logging.getLogger("rl_agent")

GRID_SIZE = 100
ACTIONS = [(1,0,0), (-1,0,0), (0,1,0), (0,-1,0), (0,0,1), (0,0,-1)]


class RLAgent:
    def __init__(self):
        self.q_table = np.zeros((GRID_SIZE, GRID_SIZE, 3, len(ACTIONS)))
        self.lr = 0.1
        self.gamma = 0.9
        self.epsilon = 0.15

    def _clamp(self, x, y, z):
        x = max(0, min(GRID_SIZE - 1, int(x)))
        y = max(0, min(GRID_SIZE - 1, int(y)))
        z = max(0, min(2, int(z)))
        return x, y, z

    def choose_action(self, x, y, z):
        x, y, z = self._clamp(x, y, z)
        if random.random() < self.epsilon:
            return random.randint(0, len(ACTIONS)-1)
        return int(np.argmax(self.q_table[x][y][z]))

    def update(self, x, y, z, action, reward, nx, ny, nz):
        x, y, z = self._clamp(x, y, z)
        nx, ny, nz = self._clamp(nx, ny, nz)

        if not (0 <= action < len(ACTIONS)):
            logger.error("Invalid action index %d (expected 0–%d)", action, len(ACTIONS) - 1)
            return

        best_next = np.max(self.q_table[nx][ny][nz])
        self.q_table[x][y][z][action] += self.lr * (
            reward + self.gamma * best_next - self.q_table[x][y][z][action]
        )
