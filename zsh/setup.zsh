# source this (eg, in zshrc) to add one-shell-history functionality

# TODO we dont require it in the path, up to the user if he wants to call 'osh stats' and the like
__osh=$(realpath ${0:a:h}/../bin/osh)

if [[ -v __osh_session_id ]]; then
    __osh_session_id=$(uuidgen)
fi

if [[ -v __osh_session_start ]]; then
    __osh_session_start=$(date '+%s.%N')
fi

autoload -U add-zsh-hook

__osh_base=${0:a:h}/..
__osh_base=${__osh_base:a}

function __osh_before {
    local command=${1[0,-2]}
    if [[ $command != '' ]]; then
        __osh_current_command=(
            --starttime $(date '+%s.%N')
            --command $command
            --folder "$(pwd)"
        )
    fi
}

function __osh_after {
    local exit_code=$?
    if [[ -v __osh_current_command ]]; then
        __osh_current_command+=(
            --endtime $(date '+%s.%N')
            --exit-code $exit_code
            --machine "$(hostname)"
            --session $__osh_session_id
        )
        __osh append-event $__osh_current_command &!
        unset __osh_current_command
    fi
}

add-zsh-hook zshaddhistory __osh_before
add-zsh-hook precmd __osh_after

function __osh_search {
    BUFFER=$(__osh search --query=$BUFFER)
    CURSOR=$#BUFFER
    zle reset-prompt
}

zle -N __osh_search
bindkey '^r' __osh_search
bindkey -M vicmd '^r' __osh_search
bindkey -M viins '^r' __osh_search

function __osh_search_backwards {
    BUFFER=$(__osh search-backwards --query=$BUFFER --session --session-id=$__osh_session_id, --sesion-start=$__osh_session_start)
    CURSOR=$#BUFFER
    zle reset-prompt
}

zle -N __osh_search_backwards
bindkey '^e' __osh_search_backwards
bindkey -M vicmd '^e' __osh_search_backwards
bindkey -M viins '^e' __osh_search_backwards
