FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    iproute2 iputils-ping net-tools screen sudo curl ca-certificates gnupg \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# IOUYAP from containerlab Gemfury repo
RUN echo "deb [trusted=yes] https://netdevops.fury.site/apt/ /" | \
    tee /etc/apt/sources.list.d/netdevops.list && \
    apt-get update && apt-get install -y iouyap && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

WORKDIR /iol
# IOL binary is copied during build as iol.bin
COPY iol.bin /iol/iol.bin
RUN chmod +x /iol/iol.bin

ENTRYPOINT ["/entrypoint.sh"]
