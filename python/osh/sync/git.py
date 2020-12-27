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


setup_help_string = """
One-shell-history's git sync uses a repository at ~/.one-shell-history/sync/git.
The local repository has been initialized but needs to be setup with a remote to be useful.
Make sure that the branch 'master' is set up to pull and push from 'origin/master'.
Something like:
> git remote add origin git@github.com:user/one-shell-history-data.git
> git branch -u origin/master
Dont forget to configure ssh keys if needed:
> git config core.sshCommand 'ssh -i ~/.ssh/your-key'
"""


def sync():

    import socket
    from pathlib import Path

    import osh.history
    from osh.utils import locked_file

    repo = get_repo()
    remote_file = Path("~/.one-shell-history/sync/git/events.json").expanduser()
    local_file = Path("~/.one-shell-history/events.json").expanduser()

    with locked_file(remote_file, wait=10):

        try:
            repo.remotes["origin"].fetch()
        except IndexError:
            print(setup_help_string)
            exit(1)
        # TODO not sure if that works universally, also hard=True is not documented
        repo.head.reset(commit="origin/master", hard=True, working_tree=True)

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
            # TODO it could be that somewhere else we pushed from in the mean time and then this will fail
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
