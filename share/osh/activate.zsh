
# source this in, eg, .zshrc, to add one-shell-history functionality
# NOTE we assume "osh" is in the path
# TODO how can we mock it when testing? data, and not interfere with the real osh that is also in the path?


autoload -U add-zsh-hook


## session state
__osh_session_id=$(uuidgen)
# __osh_session_start=$(date '+%s.%N')


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
            --machine $(hostname)  # TODO $HOST could be faster? but it can also be changed maybe?
            --session $__osh_session_id
        )
        osh append-event $__osh_current_command &!
        unset __osh_current_command
    fi
}
add-zsh-hook zshaddhistory __osh_before
add-zsh-hook precmd __osh_after
# TODO if you unfunction those, they will not work as hooks anymore, so they have to really exist


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


# TODO and also make those names like the official ones: something like 'osh-previous' for easy mapping for a user
# and for the normal history we just map to the original and then switch off that confusing behaviour for the session history
# 'zle -N widget function' can have different names in and outside
# but can we totally hide the function? you cant even call it from normal
# and what about the bindkey, does it only take widgets, or can it take functions?
# maybe we can zle -N a function and then unset the function, so its gone?
# 'unfunction f' could work, does zle still work after that?
## back in local history
function __osh_previous {
    zle set-local-history 1
    # TODO not sure if this one uses a prefix search
    zle up-history
    zle reset-prompt
}
zle -N __osh_previous
bindkey '^p' __osh_previous
bindkey -M vicmd '^p' __osh_previous
bindkey -M viins '^p' __osh_previous


## forward in local history
function __osh_next {
    zle set-local-history 1
    zle down-history
    zle reset-prompt
}
zle -N __osh_next
bindkey '^n' __osh_next
bindkey -M vicmd '^n' __osh_next
bindkey -M viins '^n' __osh_next
