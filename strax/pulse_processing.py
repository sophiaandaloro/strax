"""Functions that perform processing on pulses
(other than data reduction functions, which are in data_reduction.py)
"""
import numpy as np
import numba

from . import utils
from .data import hit_dtype

__all__ = 'baseline integrate find_hits'.split()

# Constant for use in record_links, to indicate there is no prev/next record
NOT_APPLICABLE = -1


@numba.jit(nopython=True, nogil=True)
def baseline(records, baseline_samples=40):
    """Subtract pulses from int(baseline), store baseline in baseline field
    :param baseline_samples: number of samples at start of pulse to average
    Assumes records are sorted in time (or at least by channel, then time)

    Assumes record_i information is accurate (so don't cut pulses before
    baselining them!)
    """
    if not len(records):
        return
    samples_per_record = len(records[0]['data'])

    # Array for looking up last baseline seen in channel
    # We only care about the channels in this set of records; a single .max()
    # is worth avoiding the hassle of passing n_channels around
    last_bl_in = np.zeros(records['channel'].max() + 1, dtype=np.int16)

    for d_i, d in enumerate(records):

        # Compute the baseline if we're the first record of the pulse,
        # otherwise take the last baseline we've seen in the channel
        if d.record_i == 0:
            bl = last_bl_in[d.channel] = d.data[:baseline_samples].mean()
        else:
            bl = last_bl_in[d.channel]

        # Subtract baseline from all data samples in the record
        # (any additional zeros are already zero)
        last = min(samples_per_record,
                   d.pulse_length - d.record_i * samples_per_record)
        d.data[:last] = int(bl) - d.data[:last]
        d.baseline = bl


@numba.jit(nopython=True, nogil=True)
def integrate(records):
    for i, r in enumerate(records):
        records[i]['area'] = r['data'].sum()


@numba.jit(nopython=True)
def record_links(records):
    """Return (prev_r, next_r), each arrays of indices of previous/next
    record in the same pulse, or -1 if this is not applicable

    Currently assumes records have not been cut!
    """
    # TODO: we cannot assume the record_i information is accurate
    # after cutting tails!
    n_channels = records['channel'].max() + 1
    previous_record = np.ones(len(records), dtype=np.int32) * NOT_APPLICABLE
    next_record = np.ones(len(records), dtype=np.int32) * NOT_APPLICABLE

    last_record_seen = np.ones(n_channels, dtype=np.int32) * NOT_APPLICABLE
    for i, r in enumerate(records):
        ch = r['channel']
        last_i = last_record_seen[ch]
        if r['record_i'] == 0:
            # Record starts a new pulse
            previous_record[i] = NOT_APPLICABLE

        else:
            # Continuing record
            previous_record[i] = last_i
            assert last_i != NOT_APPLICABLE
            next_record[last_i] = i

        last_record_seen[ch] = i

    return previous_record, next_record


# Chunk size should be at least a thousand,
# else copying buffers / switching context dominates over actual computation
@utils.growing_result(hit_dtype, chunk_size=int(1e4))
@numba.jit(nopython=True)
def find_hits(result_buffer, records, threshold=15):
    if not len(records):
        return
    samples_per_record = len(records[0]['data'])
    offset = 0

    for record_i, r in enumerate(records):
        in_interval = False
        hit_start = -1

        for i in range(samples_per_record):
            # We can't use enumerate over r['data'], numba gives error
            # TODO: file issue?
            above_threshold = r['data'][i] > threshold

            if not in_interval and above_threshold:
                # Start of a hit
                in_interval = True
                hit_start = i

            if in_interval and (not above_threshold
                                or i == samples_per_record):
                # End of the current hit
                in_interval = False

                # We want an exclusive right bound
                # so report the current sample (first beyond the hit)
                # ... except if this is the last sample in the record and
                # we're still above threshold. Then the hit ends one s later
                # TODO: This makes no sense
                hit_end = i    # if not above_threshold else i + 1

                # Add bounds to result buffer
                res = result_buffer[offset]

                res['left'] = hit_start
                res['right'] = hit_end
                res['time'] = r['time'] + hit_start * r['dt']
                res['length'] = (hit_end - hit_start + 1)
                res['dt'] = r['dt']
                res['channel'] = r['channel']
                res['record_i'] = record_i
                offset += 1

                if offset == len(result_buffer):
                    yield offset
                    offset = 0
    yield offset


find_hits.__doc__ = """
Return hits (intervals above threshold) found in records.
Hits that straddle record boundaries are split (TODO: fix this?)
"""
