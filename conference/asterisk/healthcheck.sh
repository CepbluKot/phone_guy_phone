#!/bin/sh
set -eu

asterisk -rx "core waitfullybooted" >/dev/null 2>&1

for required_module in \
    app_confbridge \
    app_stasis \
    bridge_softmix \
    chan_websocket \
    pbx_config \
    res_ari \
    res_ari_asterisk \
    res_ari_channels \
    res_ari_events \
    res_ari_model \
    res_http_websocket \
    res_sorcery_config \
    res_stasis \
    res_stasis_answer \
    res_stasis_playback \
    res_stasis_recording \
    res_stasis_snoop \
    res_timing_timerfd \
    res_websocket_client
do
    module_status=$(asterisk -rx "module show like ${required_module}.so" 2>/dev/null)
    printf '%s\n' "$module_status" | grep -Eq "^${required_module}\.so[[:space:]].*[[:space:]][[:digit:]]+[[:space:]]+Running[[:space:]]"
done
