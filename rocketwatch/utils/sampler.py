import logging

from utils.cfg import cfg


log = logging.getLogger("sampler")
log.setLevel(cfg["log_level"])


class CurveSampler:
    def __init__(self, max_step_size, max_y_space, max_y_wanted, max_attempts=3, max_steps=10):
        self.max_step_size = max_step_size
        self.max_steps = max_steps
        self.max_y_space = max_y_space
        self.max_y_wanted = max_y_wanted
        self.max_attempts = max_attempts
        self.step_size = max_step_size
        self.sample_function = None
        self.data = []
        self.step_size_adjustment = 2.0

    async def sample_curve(self, sample_function):
        self.sample_function = sample_function
        self.data = []
        self.step_size_adjustment = 2.0  # Start with doubling or halving the step size

        await self._sample_initial_curve()
        # inject (0,0) point at start
        self.data.insert(0, (0, 0))

        await self._refine_sampling()

        return self.data

    async def _sample_initial_curve(self):
        log.info("Sampling initial curve")
        attempt = 0
        while attempt < self.max_attempts:
            self.data = []
            x = self.step_size
            steps = 0
            while steps < self.max_steps:
                y = await self.sample_function(x)
                # if y is lower than previous sample, we skip
                if self.data and y < self.data[-1][1]:
                    log.warning(f'New y value is lower than previous: {self.data[-1][1]} {y}')
                    x += self.step_size
                    continue
                self.data.append((x, y))
                log.info(f'Sampling at x={x}: y={y}')
                if y > self.max_y_wanted:
                    break
                x += self.step_size
                steps += 1

            if steps < 3:
                self.step_size /= self.step_size_adjustment
            elif steps >= self.max_steps:
                self.step_size *= self.step_size_adjustment
            else:
                break  # Found a good step size

            attempt += 1
            self.step_size_adjustment = 1 + (self.step_size_adjustment - 1) / 2

    async def _refine_sampling(self):
        log.info("Refining sampling")
        for _ in range(self.max_attempts):
            needs_refinement = False
            i = 0
            while i < len(self.data) - 1:
                z = self.data[i + 1][1] - self.data[i][1]
                if z > self.max_y_space:
                    needs_refinement = True
                    mid_point = (self.data[i][0] + self.data[i+1][0]) / 2
                    y = await self.sample_function(mid_point)
                    if y < self.data[i][1] or y > self.data[i+1][1]:
                        log.warning(f'New y value is not in line: {self.data[i+1][1]} {y} < {self.data[i][1]}')
                        i += 1
                        continue # we dont want to keep bad data
                    log.info(f'Refining sampling at x={mid_point}: y={y}')
                    self.data.insert(i + 1, (mid_point, y))
                    i += 1
                i += 1
            if not needs_refinement:
                log.info("No more refinement needed")
                break