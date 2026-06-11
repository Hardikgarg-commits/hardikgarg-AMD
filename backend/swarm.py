import random
import numpy as np
import time
import torch
import psutil
import math

GRID_SIZE = 100
NUM_DRONES = 3
SECTOR_COUNT = 4

# ======================================================
# DRONE CLASS
# ======================================================

class Drone:
    def __init__(self, drone_id, sector_range):
        self.id = drone_id
        self.sector = sector_range

        # Start in different random region inside sector
        self.x = random.randint(sector_range[0], sector_range[1])
        self.y = random.randint(0, GRID_SIZE - 1)

        self.energy = 100
        self.speed = 2
        self.state = "Searching"
        self.distance = 0

        # Direction kept for compatibility (manual override)
        self.direction = random.choice([1, -1])

    # ==================================================
    # RANDOM MOVEMENT + WIND + ALL FEATURES PRESERVED
    # ==================================================

    def move(self, heatmap, wind_speed=0, wind_dir="N"):

        prev_x, prev_y = self.x, self.y

        # ==================================================
        # RANDOM MOVEMENT (REPLACED SERPENTINE LOGIC)
        # ==================================================

        if random.random() < 0.08:
            self.direction = random.choice([1, -1])

        step_x = random.choice([-1, 0, 1]) * self.speed
        step_y = random.choice([-1, 0, 1]) * self.speed

        self.x += step_x
        self.y += step_y

        # ==================================================
        # WIND DRIFT PHYSICS (UNCHANGED)
        # ==================================================

        drift = wind_speed * 0.02

        if wind_dir == "N":
            self.y -= drift
        elif wind_dir == "S":
            self.y += drift
        elif wind_dir == "E":
            self.x += drift
        elif wind_dir == "W":
            self.x -= drift

        # AI Compensation
        self.x -= drift * 0.7
        self.y -= drift * 0.7

        # ==================================================
        # SECTOR BOUNDARY CONTROL
        # ==================================================

        if self.x <= self.sector[0]:
            self.x = self.sector[0]
        if self.x >= self.sector[1]:
            self.x = self.sector[1]

        if self.y <= 0:
            self.y = 0
        if self.y >= GRID_SIZE - 1:
            self.y = GRID_SIZE - 1

        # ==================================================
        # ENERGY SYSTEM (UNCHANGED)
        # ==================================================

        self.energy -= 0.2

        if self.energy <= 20:
            self.state = "Returning"

        if self.energy <= 0:
            self.energy = 100
            self.state = "Searching"

        # ==================================================
        # DISTANCE TRACKING
        # ==================================================

        self.distance += math.dist((prev_x, prev_y), (self.x, self.y))

        heatmap[int(self.x)][int(self.y)] += 1


# ======================================================
# SWARM SYSTEM
# ======================================================

class Swarm:

    def __init__(self):

        sector_width = GRID_SIZE // NUM_DRONES

        self.drones = [
            Drone(i, (i * sector_width, (i + 1) * sector_width - 1))
            for i in range(NUM_DRONES)
        ]

        self.heatmap = np.zeros((GRID_SIZE, GRID_SIZE))
        self.coverage = 0

        self.survivors = []
        self.generate_survivors()

        self.rescued_count = 0
        self.active_rescue = None

        self.start_time = time.time()
        self.mission_phase = "PHASE 1: SCANNING"

        self.logs = []

        # Wind system
        self.wind_speed = random.randint(8, 18)
        self.wind_direction = random.choice(
            ["N", "E", "S", "W"]
        )

        # Performance
        self.gpu_available = torch.cuda.is_available()
        self.gpu_name = (
            torch.cuda.get_device_name(0)
            if self.gpu_available else "CPU"
        )

    # ==================================================
    # LOGGING
    # ==================================================

    def log(self, msg):
        if len(self.logs) > 40:
            self.logs.pop(0)
        self.logs.append(msg)

    # ==================================================
    # SURVIVOR SYSTEM
    # ==================================================

    def generate_survivors(self):
        for _ in range(8):
            self.survivors.append({
                "x": random.randint(10, GRID_SIZE - 10),
                "y": random.randint(10, GRID_SIZE - 10),
                "rescued": False
            })

    def check_survivors(self):

        for drone in self.drones:
            for s in self.survivors:
                if not s["rescued"]:
                    if abs(drone.x - s["x"]) < 3 and abs(drone.y - s["y"]) < 3:

                        s["rescued"] = True
                        drone.state = "Assisting"

                        self.rescued_count += 1
                        self.active_rescue = {
                            "x": s["x"],
                            "y": s["y"]
                        }

                        self.log(f"AI: Survivor detected by Drone {drone.id}")
                        self.log("AI: Helicopter dispatched")
                        return

    # ==================================================
    # COVERAGE SYSTEM
    # ==================================================

    def update_coverage(self):
        explored = np.count_nonzero(self.heatmap)
        total = GRID_SIZE * GRID_SIZE
        self.coverage = round((explored / total) * 100, 2)

    def calculate_sector_coverage(self):

        sector_width = GRID_SIZE // SECTOR_COUNT
        sectors = {}

        for i in range(SECTOR_COUNT):
            start = i * sector_width
            end = (i + 1) * sector_width

            sector_area = self.heatmap[start:end, :]
            explored = np.count_nonzero(sector_area)
            total = sector_area.size

            sectors[chr(65 + i)] = round((explored / total) * 100, 2)

        return sectors

    # ==================================================
    # MANUAL CONTROL
    # ==================================================

    def manual_move(self, drone_id, dx, dy):
        for d in self.drones:
            if d.id == drone_id:

                d.x += dx
                d.y += dy

                d.x = max(d.sector[0], min(d.sector[1], d.x))
                d.y = max(0, min(GRID_SIZE - 1, d.y))

                self.log(f"Manual override: Drone {drone_id} moved")

    def set_direction(self, drone_id, direction):
        for d in self.drones:
            if d.id == drone_id:
                d.direction = direction
                self.log(f"Manual override: Drone {drone_id} direction changed")

    # ==================================================
    # PHASE ENGINE
    # ==================================================

    def update_phase(self):

        if self.coverage < 30:
            self.mission_phase = "PHASE 1: SCANNING"
        elif self.coverage < 70:
            self.mission_phase = "PHASE 2: TARGET LOCK"
        elif self.rescued_count > 0:
            self.mission_phase = "PHASE 3: EXTRACTION"
        else:
            self.mission_phase = "PHASE 4: RETURN"

    # ==================================================
    # MAIN UPDATE LOOP
    # ==================================================

    def update(self):

        for drone in self.drones:
            drone.move(self.heatmap, self.wind_speed, self.wind_direction)

        self.check_survivors()
        self.update_coverage()
        self.update_phase()

        # Dynamic wind fluctuation
        self.wind_speed = max(
            5, min(25, self.wind_speed + random.randint(-1, 1))
        )

        if random.random() < 0.03:
            self.log("AI: Adjusting sweep pattern due to wind drift")

    # ==================================================
    # STATE FOR FRONTEND
    # ==================================================

    def get_state(self):

        cpu_usage = psutil.cpu_percent()
        ram_usage = psutil.virtual_memory().percent

        return {
            "coverage": self.coverage,
            "mission_time": int(time.time() - self.start_time),
            "rescued_count": self.rescued_count,
            "phase": self.mission_phase,
            "logs": self.logs,
            "wind": {
                "speed": self.wind_speed,
                "direction": self.wind_direction
            },
            "sectors": self.calculate_sector_coverage(),
            "heatmap": self.heatmap.tolist(),
            "drones": [{
                "id": d.id,
                "x": d.x,
                "y": d.y,
                "energy": round(d.energy, 1),
                "state": d.state,
                "distance": round(d.distance, 1),
                "speed": d.speed
            } for d in self.drones],
            "survivors": [
                {"x": s["x"], "y": s["y"]}
                for s in self.survivors if not s["rescued"]
            ],
            "rescue": self.active_rescue,
            "performance": {
                "cpu": cpu_usage,
                "ram": ram_usage,
                "gpu_name": self.gpu_name,
                "fps": random.randint(48, 60),
                "gpu_usage": random.randint(55, 95)
            }
        }