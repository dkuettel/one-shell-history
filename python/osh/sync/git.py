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


@click.group()
def cli():
    pass


@cli.command()
def setup():
    get_repo()


@cli.command()
def sync():

    from pathlib import Path

    import osh.history as H

    repo = get_repo()
    repo.remotes["origin"].fetch()
    repo.head.reset(hard=True)

    local_history_file = Path("./zsh-history.json")
    local_history = H.read_from_file(local_history_file, or_empty=True)

    remote_history_file = (
        Path("~/.one-shell-history/sync/git").expanduser() / "zsh-history.json"
    )
    remote_history = H.read_from_file(remote_history_file)

    merged_history = H.merge([local_history, remote_history])

    H.write_to_file(merged_history, local_history_file)
    H.write_to_file(merged_history, remote_history_file)

    repo.index.add([str(remote_history_file)])
    repo.index.commit("sync")
    repo.remotes["origin"].push()


if __name__ == "__main__":
    cli()
