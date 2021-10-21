import sys
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def profile_std():
    import subprocess
    from cProfile import Profile
    from pstats import Stats

    # https://docs.python.org/3/library/profile.html

    with Profile(subcalls=True, builtins=True) as p:
        yield

    stats = Stats(p)
    stats.dump_stats("std.prof")
    stats.sort_stats("cumulative", "tottime")
    stats.reverse_order()
    stats.print_stats("/osh/")

    subprocess.run(["tuna", "std.prof"])

    # stats.print_callers("/osh/")
    # stats.print_callees("/osh/")

    # to visualize
    # pip install snakeviz
    # snakeviz std.prof

    # to visualize, looks better
    # pip install tuna
    # tuna std.prof

    # to visualize, looks professional
    # pip install pyprof2calltree
    # sudo apt install kcachegrind
    # pyprof2calltree -i cachegrind.out -o cachegrind.out -k


@contextmanager
def profile_lines(funcs):
    from line_profiler import LineProfiler as Profile

    # https://github.com/pyutils/line_profiler
    # pip install line_profiler
    # kernprof -l python/osh/__main__.py profile , when used with @profile, builtins magic
    # python -m line_profiler -u 1 __main__.py.lprof

    profiler = Profile(funcs)  # TODO not sure if list of single args for funcs

    with profiler:
        yield

    profiler.dump_stats("lines.prof")
    profiler.print_stats(stream=sys.stdout, output_unit=1)
    with Path("lines.out").open("wt") as f:
        profiler.print_stats(stream=f, output_unit=1)

def profile_pp():
    assert False, "not finished"

    from pprofile import Profile, StatisticalProfile

    # https://github.com/vpelletier/pprofile
    # pip install pprofile
    # has deterministic and statistical
    # by default the output is overly complete
    # command line call looks very robust, probably better than this here
    # deterministic
    p = Profile()  # deterministic

    # statistical
    # they say dont use it for something that only runs a few seconds
    # p = StatisticalProfile()

    with p():
        _profile()

    p.print_stats()
    with open("cachegrind.out-pp", "wt") as f:
        p.callgrind(f)
        # filename=set() to limit to interesting files

    # print_stats is similar to line_profiler
    # and cachegrind as above again
    # cant quite make sense of cachegrind output with this one


def profile():
    assert False, "not finished"
    # for use with generic outside calls
    # like, eg, pyinstrument -r html -m osh profile
    # maybe use -t, I find this one also hard to read
    # pip install pyinstrument, https://pyinstrument.readthedocs.io/en/latest/guide.html

    _profile()
