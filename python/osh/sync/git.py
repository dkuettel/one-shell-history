import click


def get_repo():

    from pathlib import Path

    from git import Repo

    folder = Path("~/.one-shell-history/sync/git").expanduser()

    try:
        repo = Repo(folder)
    except:
        repo = Repo.init(folder)
        history_file = folder / "events.json"
        history_file.write_text("[]")
        repo.index.add([str(history_file)])
        repo.index.commit("start with empty history")

    return repo


def sync():

    from pathlib import Path
    import socket

    import osh.history
    from osh.utils import locked_file

    repo = get_repo()
    remote_file = Path("~/.one-shell-history/sync/git/events.json").expanduser()
    local_file = Path("~/.one-shell-history/events.json").expanduser()

    with locked_file(remote_file, wait=10):

        try:
            repo.remotes["origin"].fetch()
        except IndexError:
            print("git sync uses the repository at ~/.one-shell-history/sync/git")
            print(
                "make sure that repo is set up to push to and pull from a remote 'origin'"
            )
            print("for master branch tracking a branch on that remote")
            print("dont forget to set core.sshCommand if you need special ssh keys")
            print(
                "often first: 'git remote add origin git@github.com:someone/somewhere.git'"
            )
            print("maybe then: 'git config core.sshCommand 'ssh -i ~/.ssh/your-key'")
            print("and then: 'git push -u origin master'")
            exit(1)
        repo.head.reset(hard=True)

        remote_history = osh.history.read_from_file(remote_file, or_empty=True)

        with locked_file(local_file, wait=10):
            local_history = osh.history.read_from_file(local_file, or_empty=True)

            merged = osh.history.merge([local_history, remote_history])

            if merged == local_history:
                print("no incoming changes")
            else:
                print(f"{len(merged)-len(local_history)} incoming changes")
                osh.history.write_to_file(merged, local_file)

        if merged == remote_history:
            print("no outgoing changes")
        else:
            print(f"{len(merged)-len(remote_history)} outgoing changes")
            osh.history.write_to_file(merged, remote_file)
            repo.index.add([str(remote_file)])
            repo.index.commit(
                f"add {len(merged)-len(remote_history)} new events from {socket.gethostname()}"
            )
            repo.remotes["origin"].push()


@click.group()
def cli():
    pass


@cli.command()
def sync_now():
    sync()


@cli.command()
def sync_always(interval=10 * 60):

    import time

    while True:
        sync()
        time.sleep(interval)


if __name__ == "__main__":
    cli()
