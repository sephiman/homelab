FROM alpine:3.24

# pg_dumpall must match the Postgres server major version (17).
RUN apk add --no-cache \
      bash \
      tzdata \
      tar \
      gzip \
      coreutils \
      findutils \
      rclone \
      postgresql17-client

COPY backup.sh /usr/local/bin/backup.sh
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/backup.sh /usr/local/bin/entrypoint.sh

ENV RCLONE_CONFIG=/config/rclone/rclone.conf

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
