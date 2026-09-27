FROM debian:bookworm-slim@sha256:88200866dfff7ea7f5cbcb6ec7c8a701889efe6fe859fe64d6990e4b07ea4171 AS build

ARG ASTERISK_SHA256=3bd5ee040509a3d3cd9b1ba9520c18e6ec0a7e7981ca68c457dcd36ba3c54d94
ARG ASTERISK_ARCHIVE=https://downloads.asterisk.org/pub/telephony/asterisk/asterisk-22.11.0.tar.gz

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        bison \
        build-essential \
        ca-certificates \
        curl \
        flex \
        libcap-dev \
        libedit-dev \
        libjansson-dev \
        libncurses-dev \
        libsqlite3-dev \
        libssl-dev \
        libsrtp2-dev \
        liburiparser-dev \
        libxml2-dev \
        pkg-config \
        uuid-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /usr/src/asterisk
RUN curl --fail --location --silent --show-error "${ASTERISK_ARCHIVE}" \
        --output /tmp/asterisk.tar.gz \
    && echo "${ASTERISK_SHA256}  /tmp/asterisk.tar.gz" | sha256sum -c - \
    && tar --extract --gzip --file /tmp/asterisk.tar.gz --strip-components=1

RUN ./configure \
        --prefix=/usr \
        --sysconfdir=/etc \
        --localstatedir=/var \
        --with-pjproject-bundled \
    && make menuselect.makeopts \
    && menuselect/menuselect \
        --disable BUILD_NATIVE \
        --disable-category MENUSELECT_CORE_SOUNDS \
        --disable-category MENUSELECT_MOH \
        --disable-category MENUSELECT_EXTRA_SOUNDS \
        --enable app_confbridge \
        --enable app_dial \
        --enable app_playback \
        --enable app_stasis \
        --enable bridge_softmix \
        --enable chan_pjsip \
        --enable chan_websocket \
        --enable codec_alaw \
        --enable codec_resample \
        --enable format_wav \
        --enable func_channel \
        --enable pbx_config \
        --enable res_ari \
        --enable res_ari_asterisk \
        --enable res_ari_bridges \
        --enable res_ari_channels \
        --enable res_ari_events \
        --enable res_ari_model \
        --enable res_crypto \
        --enable res_http_websocket \
        --enable res_pjproject \
        --enable res_pjsip \
        --enable res_pjsip_authenticator_digest \
        --enable res_pjsip_endpoint_identifier_user \
        --enable res_pjsip_nat \
        --enable res_pjsip_pubsub \
        --enable res_pjsip_registrar \
        --enable res_pjsip_sdp_rtp \
        --enable res_pjsip_session \
        --enable res_pjsip_transport_websocket \
        --enable res_rtp_asterisk \
        --enable res_srtp \
        --enable res_sorcery_astdb \
        --enable res_sorcery_config \
        --enable res_sorcery_memory \
        --enable res_stasis \
        --enable res_stasis_answer \
        --enable res_stasis_playback \
        --enable res_stasis_recording \
        --enable res_stasis_snoop \
        --enable res_timing_timerfd \
        --enable res_websocket_client \
        menuselect.makeopts \
    && make -j1 \
    && make DESTDIR=/opt/asterisk-root install \
    && rm -rf /opt/asterisk-root/var/run

FROM debian:bookworm-slim@sha256:88200866dfff7ea7f5cbcb6ec7c8a701889efe6fe859fe64d6990e4b07ea4171

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        ca-certificates \
        libcap2 \
        libedit2 \
        libjansson4 \
        libncursesw6 \
        libsqlite3-0 \
        libssl3 \
        libsrtp2-1 \
        liburiparser1 \
        libuuid1 \
        libxml2 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 asterisk \
    && useradd --uid 10001 --gid asterisk --home-dir /var/lib/asterisk \
        --no-create-home --shell /usr/sbin/nologin asterisk

COPY --from=build /opt/asterisk-root/ /
COPY asterisk.conf cdr.conf cel.conf confbridge.conf extensions.conf http.conf modules.conf rtp.conf sorcery.conf stasis.conf /etc/asterisk/
COPY ari.conf.template /usr/share/asterisk/config/ari.conf.template
COPY pjsip.conf.template /usr/share/asterisk/config/pjsip.conf.template
COPY healthcheck.sh /usr/local/bin/asterisk-healthcheck

RUN install -d -o asterisk -g asterisk \
        /var/lib/asterisk \
        /var/log/asterisk \
        /var/run/asterisk \
    && install -d -o root -g root -m 0555 /var/spool/asterisk \
    && chmod 0444 /etc/asterisk/*.conf /usr/share/asterisk/config/*.template \
    && chmod 0555 /usr/local/bin/asterisk-healthcheck

USER asterisk:asterisk

HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=3 \
    CMD ["/usr/local/bin/asterisk-healthcheck"]

CMD ["sh", "-c", "mkdir -p /var/run/asterisk/astdb && exec asterisk -f -C /etc/asterisk/asterisk.conf"]
