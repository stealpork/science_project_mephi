FROM golang:1.24-alpine AS build
WORKDIR /src
ADD --checksum=sha256:c9598dcce3440977e79f787f2ba0e7e4d92c8d556bd51e7cef3785bafd6635f3 https://codeload.github.com/minio/minio/tar.gz/refs/tags/RELEASE.2025-09-07T16-13-09Z /tmp/minio.tar.gz
RUN tar -xzf /tmp/minio.tar.gz --strip-components=1 && rm /tmp/minio.tar.gz
RUN --mount=type=cache,target=/go/pkg/mod go mod download
RUN --mount=type=cache,target=/go/pkg/mod --mount=type=cache,target=/root/.cache/go-build CGO_ENABLED=0 go build -p 4 -trimpath -ldflags='-s -w' -o /out/minio .
COPY bootstrap.go ./science_init/main.go
RUN --mount=type=cache,target=/go/pkg/mod --mount=type=cache,target=/root/.cache/go-build CGO_ENABLED=0 go build -p 4 -trimpath -ldflags='-s -w' -o /out/science-minio-init ./science_init

FROM alpine:3.21
RUN apk add --no-cache ca-certificates && adduser -D -u 10001 app && mkdir /data && chown app /data
COPY --from=build /out/ /usr/local/bin/
USER 10001
EXPOSE 9000 9001
ENTRYPOINT ["minio"]
