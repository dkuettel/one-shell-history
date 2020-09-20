import socketserver
from pathlib import Path

import osh.history as H


file = Path("./zsh-history.json")
history = H.read_from_file(file, or_empty=True)


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        global history
        command = self.request.recv(1024).decode()
        entry = H.Entry.from_now(command=command)
        history = H.merge([history, [entry]])
        H.write_to_file(history, file)


if __name__ == "__main__":
    with socketserver.UnixStreamServer("./server-socket", Handler) as server:
        server.serve_forever()
    # echo jo | nc -UN ./server-socket
