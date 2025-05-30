
# source this in, eg, .zshrc, to add one-shell-history functionality
# NOTE we assume "osh" is in the path

autoload -U add-zsh-hook
__osh_session_id=$(uuidgen)


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
function __osh_after {
    local exit_code=$?
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
add-zsh-hook zshaddhistory __osh_before
add-zsh-hook precmd __osh_after


## global search
function __osh_search {
    BUFFER=$(mode=all query=$BUFFER session=__osh_session_id session_start=__osh_session_start folder=$PWD osh-fzf)
    CURSOR=$#BUFFER
    zle reset-prompt
}
zle -N __osh_search
bindkey '^r' __osh_search
bindkey -M vicmd '^r' __osh_search
bindkey -M viins '^r' __osh_search
