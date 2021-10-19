# source this (eg, in zshrc) to add one-shell-history functionality

# TODO we dont require it in the path, up to the user if he wants to call 'osh stats' and the like
# on the other hand, we need it ready here anyway, so why not just make it available? difference of shell function vs script?
# not sure, still nice to let the user decide
# but then would also be nice to say it needs to be available some way, we dont discover it?
# but then we might not use the one of this script, but another one, a bit messy
__osh_path=$(realpath ${0:a:h}/../bin/osh)

function __osh {
    $__osh_path $@
}

__osh_session_id=$(uuidgen)
__osh_session_start=$(date '+%s.%N')
__osh_prefix_timestamp=$(date '+%s.%N')

autoload -U add-zsh-hook

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
    __osh_prefix_timestamp=$(date '+%s.%N')
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
    BUFFER=$(__osh search-backwards --query=$BUFFER --session --session-id=$__osh_session_id --session-start=$__osh_session_start)
    CURSOR=$#BUFFER
    zle reset-prompt
}

zle -N __osh_search_backwards
bindkey '^e' __osh_search_backwards
bindkey -M vicmd '^e' __osh_search_backwards
bindkey -M viins '^e' __osh_search_backwards

function __osh_previous {
    # NOTE --ignore=$BUFFER would skip consecutive duplicates, sounds good, but not typically intuitive
    if result=$(__osh previous-event --timestamp=$__osh_prefix_timestamp --prefix=$BUFFER[1,$CURSOR] --session-id=$__osh_session_id --session-start=$__osh_session_start); then
        __osh_prefix_timestamp=$result[1,21]
        BUFFER=$result[23,-1]
    fi
    zle reset-prompt
}

zle -N __osh_previous
bindkey '^p' __osh_previous
bindkey -M vicmd '^p' __osh_previous
bindkey -M viins '^p' __osh_previous

function __osh_next {
    # NOTE --ignore=$BUFFER would skip consecutive duplicates, sounds good, but not typically intuitive
    if result=$(__osh next-event --timestamp=$__osh_prefix_timestamp --prefix=$BUFFER[1,$CURSOR] --session-id=$__osh_session_id --session-start=$__osh_session_start); then
        __osh_prefix_timestamp=$result[1,21]
        BUFFER=$result[23,-1]
    fi
    zle reset-prompt
}

zle -N __osh_next
bindkey '^n' __osh_next
bindkey -M vicmd '^n' __osh_next
bindkey -M viins '^n' __osh_next
