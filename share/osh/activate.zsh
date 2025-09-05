
# source this in, eg, .zshrc, to add one-shell-history functionality
# NOTE we assume "osh" is in the path


autoload -U add-zsh-hook

# NOTE anything that needs to spawn a command, like $(uuidgen) is a magnitude slower
# try not to have it in the zshrc sourcing path, but delay it, if needed at all
# zsh-own functions are much faster than forking/spawning commands
# __osh_session_id=$(uuidgen)


## append events
function __osh_before {
    local command=${1[0,-2]}
    if [[ $command != '' ]]; then
        __osh_current_command=(
            --starttime $(date '+%s.%N')
            --command $command
            --folder $PWD
        )
    fi
}
# after confirming a command, but before running
add-zsh-hook zshaddhistory __osh_before

function __osh_after {
    # TODO if others have added precmd hooks, is it guaranteed that this is the return code of the just-run command?
    local exit_code=$?
    if [[ ! -v __osh_session_id ]]; then
        __osh_session_id=$(uuidgen)
    fi
    if [[ -v __osh_current_command ]]; then
        __osh_current_command+=(
            --endtime $(date '+%s.%N')
            --exit-code $exit_code
            --machine $(hostname)  # NOTE $HOST could be faster? but it can also be changed maybe?
            --session $__osh_session_id
        )
        osh append-event $__osh_current_command &!
        unset __osh_current_command
    fi
}
# runs just before the prompt after a command, but not on redraw
add-zsh-hook precmd __osh_after


## global search
function __osh_search {
    if [[ ! -v __osh_session_id ]]; then
        __osh_session_id=$(uuidgen)
    fi
    BUFFER=$(mode=all query=$BUFFER session=$__osh_session_id folder=$PWD osh-fzf)
    CURSOR=$#BUFFER
    zle reset-prompt
}
zle -N __osh_search
bindkey '^r' __osh_search
bindkey -M vicmd '^r' __osh_search
bindkey -M viins '^r' __osh_search
