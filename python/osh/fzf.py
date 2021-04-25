import subprocess as S
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Optional


@dataclass
class Result:
    query: Optional[str] = None
    key: Optional[str] = None
    selection: Optional[str] = None


class NonZeroExit(Exception):
    def __init__(self, returncode):
        self.returncode = returncode


def fzf(entries, /, **kwargs) -> Result:

    args = ["fzf"] + [
        f"--{key.replace('_','-')}"
        if value == True
        else f"--{key.replace('_','-')}={str(value)}"
        for key, value in kwargs.items()
    ]

    with S.Popen(
        args=args,
        stdin=S.PIPE,
        stdout=S.PIPE,
    ) as p:

        for entry in entries:
            if p.poll() is not None:
                break
            p.stdin.write((str(entry) + "\n").encode("utf-8"))
            p.stdin.flush()

        try:
            # TODO maybe that only needs to happen in the for else case?
            p.stdin.close()
        except:
            pass
        p.wait()

        assert p.returncode is not None
        if p.returncode != 0:
            raise NonZeroExit(p.returncode)

        if "print0" in kwargs:
            outputs = p.stdout.read().decode("utf-8").split("\0")[:-1]
        else:
            outputs = p.stdout.read().decode("utf-8").split("\n")[:-1]

        result = Result()
        if "print_query" in kwargs:
            result.query = outputs.pop(0)
        if "expect" in kwargs:
            result.key = outputs.pop(0)
        result.selection = outputs.pop()
        assert len(outputs) == 0, outputs

    return result
