# drafty draft of hand-wavy overview

## install

1) checkout repository
2) install dependencies, known so far:
    - pipenv
    - fzf
    - python 3.8.2
3) install virtual environment for python using pipenv, the repo folder execute
   > PIPENV_VENV_IN_PROJECT=1 pipenv install
   note: this should create a folder `.env` in the repo folder (not somewhere else)
4) install the one-shell-service in systemd
   it will be installed as a user service only for the current user
   (it will run as that user, and only manage the shell history for that user, no root rights)
   > systemd/install
5) check if the service is running
   > systemctl status one-shell-history@$USER.service
6) either `source zsh/setup.zsh` in your zshrc or source it manually to test

## usage

- use your shell like normal, produce history
- `ctrl-e` starts the history search, very similar to fzf's `ctrl-r`
- `osh-sync-zsh` merges in all history from `~/.zsh_history` in your osh history, idempotent, can also be used if you had some shell sessions without osh activated and want to add that history too
- `osh-sync-git` will attempt to sync globally with your git repository, if you do it the first time an it's not setup yet, it will give you instructions

## likely issues

- system dependencies are not complete
- some code might still have hard-code folders or similar that doesnt work for other users
