
import numpy as np
import random

GRID_SIZE = 100
ACTIONS = [(1,0,0), (-1,0,0), (0,1,0), (0,-1,0), (0,0,1), (0,0,-1)]

class RLAgent:
    def __init__(self):
        self.q_table = np.zeros((GRID_SIZE, GRID_SIZE, 3, len(ACTIONS)))
        self.lr = 0.1
        self.gamma = 0.9
        self.epsilon = 0.15

    def choose_action(self, x, y, z):
        if random.random() < self.epsilon:
            return random.randint(0, len(ACTIONS)-1)
        return np.argmax(self.q_table[x][y][z])

    def update(self, x, y, z, action, reward, nx, ny, nz):
        best_next = np.max(self.q_table[nx][ny][nz])
        self.q_table[x][y][z][action] += self.lr * (
            reward + self.gamma * best_next - self.q_table[x][y][z][action]
        )
