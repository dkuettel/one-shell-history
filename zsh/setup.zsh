# source this (eg, in zshrc) to add one-shell-history functionality

autoload -U add-zsh-hook

__osh_base=${0:a:h}/..
__osh_base=${__osh_base:a}

# I dont know if its possible to hide the functions and variables here
# as it is now, when sourced, they are all visible to the user
# ie, he could type and run '__osh_run'
# or access $__osh_base, potentially change it

function __osh_run {
    (
        cd $__osh_base
        source .venv/bin/activate
        export PYTHONPATH=python
        python $@
    )
}

function __osh_before {
    local command=${1[0,-2]}
    if [[ $command != '' ]]; then
        __osh_current_command=(
            --starttime $(date +%s)
            --command $command
            --folder "$(pwd)"
        )
    fi
}

function __osh_after {
    local exit_code=$?
    if [[ -v __osh_current_command ]]; then
        __osh_session=${__osh_session:-$(uuidgen)}
        __osh_current_command+=(
            --endtime $(date +%s)
            --exit-code $exit_code
            --machine "$(hostname)"
            --session $__osh_session
        )
        if [[ -e ~/.one-shell-history/control-socket ]]; then
            __osh_run -m osh.socket insert-event $__osh_current_command &!
        else
            print -P '\n%F{1}%Sthe osh service doesnt seem to be running%s%f'
        fi
        unset __osh_current_command
    fi
}

add-zsh-hook zshaddhistory __osh_before
add-zsh-hook precmd __osh_after

function __osh_search {
    if [[ -e ~/.one-shell-history/control-socket ]]; then
        BUFFER=$(__osh_run -m osh.socket fzf-select --query=$BUFFER)
        CURSOR=$#BUFFER
        zle reset-prompt
    fi
}

zle -N __osh_search
bindkey '^e' __osh_search

function osh-sync-zsh {
    # merge in zsh history into osh history (one way)
    __osh_run -m osh.import zsh --machine $(hostname)
}

function osh-sync-git {
    # sync osh history and remote git history (both ways)
    __osh_run -m osh.sync.git sync-now
}
