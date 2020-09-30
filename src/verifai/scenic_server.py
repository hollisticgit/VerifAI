"""Specialized server for using Scenic's dynamic simulator interfaces."""

import time

from dotmap import DotMap

from verifai.server import Server
from verifai.samplers.scenic_sampler import ScenicSampler
from scenic.core.simulators import SimulationCreationError
import ray

ray.init(ignore_reinit_error=True)

class ScenicServer(Server):
    def __init__(self, sampling_data, monitor, options={}):
        if sampling_data.sampler is None:
            raise RuntimeError('ScenicServer created without sampler')
        self.sampler = sampling_data.sampler
        if not isinstance(self.sampler, ScenicSampler):
            raise RuntimeError('only a ScenicSampler can be used with ScenicServer')
        self.sample_space = self.sampler.space
        self.simulator = self.sampler.scenario.getSimulator()
        extSampler = self.sampler.scenario.externalSampler
        if extSampler is None:
            self.rejectionFeedback = None
        else:
            self.rejectionFeedback = extSampler.rejectionFeedback
        self.monitor = monitor
        self.lastValue = None
        defaults = DotMap(maxSteps=None, verbosity=0, maxIterations=1)
        defaults.update(options)
        self.maxSteps = defaults.maxSteps
        self.verbosity = defaults.verbosity
        self.maxIterations = defaults.maxIterations

    def run_server(self):
        sample = self.sampler.nextSample(self.lastValue)
        scene = self.sampler.lastScene
        assert scene
        result = self._simulate(scene)
        if result is None:
            self.lastValue = self.rejectionFeedback
        else:
            self.lastValue = (0 if self.monitor is None
                              else self.monitor.evaluate(result.trajectory))
        return sample, self.lastValue

    def _simulate(self, scene):
        startTime = time.time()
        if self.verbosity >= 1:
            print('  Beginning simulation...')
        try:
            result = self.simulator.simulate(scene,
                maxSteps=self.maxSteps, verbosity=self.verbosity,
                maxIterations=self.maxIterations)
        except SimulationCreationError as e:
            if self.verbosity >= 1:
                print(f'  Failed to create simulation: {e}')
            return None
        if self.verbosity >= 1:
            totalTime = time.time() - startTime
            print(f'  Ran simulation in {totalTime:.4g} seconds.')
        return result

    def terminate(self):
        pass

@ray.remote
class ParallelScenicServer(ScenicServer):

    def __init__(self, worker_number, total_workers, sampling_data, scenic_path, monitor, options={}):
        self.worker_number = worker_number
        self.total_workers = total_workers
        sampler = ScenicSampler.fromScenario(scenic_path)
        sampling_data.sampler = sampler
        super().__init__(sampling_data, monitor, options)
        print(f'Sampler class is {type(self.sampler)}')
        for _ in range(self.worker_number):
            self.sampler.nextSample(self.lastValue)

    def run_server(self):
        for _ in range(self.total_workers):
            sample = self.sampler.nextSample(self.lastValue)
        scene = self.sampler.lastScene
        assert scene
        result = self._simulate(scene)
        if result is None:
            self.lastValue = self.rejectionFeedback
        else:
            self.lastValue = (0 if self.monitor is None
                              else self.monitor.evaluate(result.trajectory))
        return sample, self.lastValue
