#!/bin/sh
set -eu

asterisk -rx "core waitfullybooted" >/dev/null 2>&1

for required_module in \
    app_confbridge \
    app_dial \
    app_playback \
    app_stasis \
    bridge_softmix \
    chan_pjsip \
    chan_websocket \
    codec_alaw \
    codec_resample \
    format_wav \
    pbx_config \
    res_ari \
    res_ari_asterisk \
    res_ari_bridges \
    res_ari_channels \
    res_ari_events \
    res_ari_model \
    res_http_websocket \
    res_pjsip \
    res_pjsip_authenticator_digest \
    res_pjsip_endpoint_identifier_user \
    res_pjsip_nat \
    res_pjsip_pubsub \
    res_pjsip_registrar \
    res_pjsip_sdp_rtp \
    res_pjsip_session \
    res_pjproject \
    res_rtp_asterisk \
    res_sorcery_astdb \
    res_sorcery_config \
    res_sorcery_memory \
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
