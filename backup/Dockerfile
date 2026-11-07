FROM alpine:3.24

# pg_dumpall must match the Postgres server major version (18).
RUN apk add --no-cache \
      bash \
      tzdata \
      tar \
      gzip \
      coreutils \
      findutils \
      rclone \
      sqlite \
      postgresql18-client

COPY backup.sh upload.sh entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/backup.sh /usr/local/bin/upload.sh /usr/local/bin/entrypoint.sh

ENV RCLONE_CONFIG=/config/rclone/rclone.conf

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
