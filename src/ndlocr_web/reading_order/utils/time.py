# Copyright (c) 2023, National Diet Library, Japan
#
# This software is released under the CC BY 4.0.
# https://creativecommons.org/licenses/by/4.0/


class TimeMeasuredScope:
    def __init__(self, keeper, key):
        from time import perf_counter
        current_time = perf_counter()
        self.keeper = keeper
        self.key = key
        self.beg = current_time

    def __enter__(self):
        return self

    def __exit__(self, *_):
        from time import perf_counter
        current_time = perf_counter()
        dur = current_time - self.beg
        self.keeper.record(self.key, dur)


class TimeKeeper:
    def __init__(self):
        self.keys = list()
        self.times = dict()

    def measure_time(self, key):
        if key not in self.keys:
            self.keys.append(key)
            self.times[key] = list()
        return TimeMeasuredScope(self, key)

    def record(self, key, time):
        self.times[key].append(time)

    def num(self, key):
        return len(self.times[key])

    def total(self, key):
        return sum(self.times[key])

    def mean(self, key):
        return self.total(key) / self.num(key)

    def median(self, key):
        return sorted(self.times[key])[self.num(key) // 2]

    def print(self, logger=None):
        if not logger:
            from .logger import get_logger
            logger = get_logger(__name__)
        for key in self.keys:
            if 1 < self.num(key):
                logger.info("Total time for %s [ms]: %.3f" %
                            (key, self.total(key) * 1e3))
                logger.info("Average time for %s [ms]: %.3f" %
                            (key, self.mean(key) * 1e3))
            elif 1 == self.num(key):
                logger.info("Time for %s [ms]: %.3f" %
                            (key, self.times[key][0] * 1e3))
