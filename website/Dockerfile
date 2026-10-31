FROM --platform=$BUILDPLATFORM node:22-alpine@sha256:fe4f6d74e20370acb04ad889077d14d0f64a9c8ea6a42bc60cddf1e844699914 AS build
WORKDIR /build
ENV ASTRO_TELEMETRY_DISABLED=1
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run check:i18n && npm run lint && npm run build && npm run check:links

# Remove the upstream binary's low-port capability before copying it. This also
# avoids retaining curl, libcap, certificates and a second binary-sized layer.
FROM caddy:2.10.2-alpine@sha256:4c6e91c6ed0e2fa03efd5b44747b625fec79bc9cd06ac5235a779726618e530d AS server
RUN setcap -r /usr/bin/caddy

# Caddy is a static binary; Alpine supplies BusyBox wget without extra packages.
FROM alpine:3.22@sha256:2e0b81d60597f449baa56dc25d355367869d2dca4cd57e866f1e2b52af3eb55f
RUN addgroup -S -g 10001 site && adduser -S -D -H -u 10001 -G site site
ENV XDG_DATA_HOME=/data XDG_CONFIG_HOME=/config
COPY --from=server /usr/bin/caddy /usr/bin/caddy
COPY Caddyfile /etc/caddy/Caddyfile
COPY --from=build /build/dist/ /usr/share/caddy/
USER 10001:10001
WORKDIR /usr/share/caddy
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=6 \
    CMD wget -q -O /dev/null -T 2 http://127.0.0.1:8080/ || exit 1
CMD ["caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]
