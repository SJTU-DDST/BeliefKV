#!/bin/sh
unset BASH_ENV ENV PROMPT_COMMAND
exec bash --noprofile --norc "$@"
