import subprocess as S
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Optional


@dataclass
class Result:
    query: Optional[str] = None
    key: Optional[str] = None
    selection: Optional[str] = None


class Error(Exception):
    def __init__(self, returncode):
        self.returncode = returncode


class UnexpectedKeys(Error):
    pass


def fzf(entries, /, **kwargs) -> Result:

    kwargs = {key: value for key, value in kwargs.items() if value is not None}

    args = ["fzf"] + [
        f"--{key.replace('_','-')}"
        if value in {False, True}
        else f"--{key.replace('_','-')}={str(value)}"
        for key, value in kwargs.items()
    ]

    with S.Popen(
        args=args,
        stdin=S.PIPE,
        stdout=S.PIPE,
    ) as p:

        entries = iter(entries)
        for entry in entries:
            if p.poll() is not None:
                break
            try:
                p.stdin.write((str(entry) + "\n").encode("utf-8"))
                p.stdin.flush()
            except ConnectionError as e:
                if p.poll() is not None:
                    break
                raise e

        try:
            p.stdin.close()
        except:
            pass
        p.wait()

        assert p.returncode is not None
        if p.returncode == 0:
            has_match = True
        elif p.returncode == 1:
            has_match = False
        elif p.returncode == 2:
            raise Error(p.returncode)
        elif p.returncode == 130:
            raise UnexpectedKeys(p.returncode)
        else:
            raise Exception(f"unknown return code {p.returncode} from fzf")

        if kwargs.get("print0", False):
            outputs = p.stdout.read().decode("utf-8").split("\0")[:-1]
        else:
            outputs = p.stdout.read().decode("utf-8").split("\n")[:-1]

        result = Result()
        if has_match:
            result.selection = outputs.pop()
        if "expect" in kwargs:
            result.key = outputs.pop()
        if kwargs.get("print_query", False):
            result.query = outputs.pop()
        assert len(outputs) == 0, outputs

    return result
