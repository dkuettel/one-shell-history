import sys

# TODO note in systemd we are piped and by default it buffers a lot, so we dont see messages
# unless we do print(..., flush=True)
# but anyway, use a proper logger? then not an issue?
# how does logger and systemd go together? because systemd already does its own timestamp and all


def info(message):
    print(message, flush=True)


def warning(message):
    print("[warning] " + message, flush=True, file=sys.stderr)


def error(message):
    print("[error] " + message, flush=True, file=sys.stderr)
