import click


def get_repo():

    from pathlib import Path

    from git import Repo

    folder = Path("~/.one-shell-history/sync/git").expanduser()

    try:
        repo = Repo(folder)
    except:
        repo = Repo.init(folder)
        history_file = folder / "zsh-history.json"
        history_file.write_text("[]")
        repo.index.add([str(history_file)])
        repo.index.commit("sync")

    return repo


def sync():

    from pathlib import Path

    import osh.history

    repo = get_repo()
    try:
        repo.remotes["origin"].fetch()
    except IndexError:
        print("git sync uses the repository at ~/.one-shell-history/sync/git")
        print(
            "make sure that repo is set up to push to and pull from a remote 'origin'"
        )
        print("for master branch tracking a branch on that remote")
        print("dont forget to set core.sshCommand if you need special ssh keys")
        print("often first: 'git remote add origin git@github.com:someone/somewhere.git'")
        print("maybe then: 'git config core.sshCommand 'ssh -i ~/.ssh/your-key'")
        print("and then: 'git push -u origin master'")
        exit(1)
    repo.head.reset(hard=True)

    remote_file = Path("~/.one-shell-history/sync/git/zsh-history.json").expanduser()
    remote_history = osh.history.read_from_file(remote_file, or_empty=True)

    local_file = Path("zsh-history.json")
    local_history = osh.history.read_from_file(local_file, or_empty=True)

    merged = osh.history.merge([local_history, remote_history])

    if merged == local_history:
        print("no incoming changes")
    else:
        print("incoming changes")
        osh.history.write_to_file(merged, local_file)

    if merged == remote_history:
        print("no outgoing changes")
    else:
        print("outgoing changes")
        osh.history.write_to_file(merged, remote_file)
        repo.index.add([str(remote_file)])
        repo.index.commit("sync")
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
